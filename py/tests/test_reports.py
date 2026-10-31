# SPDX-License-Identifier: AGPL-3.0-only
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SUPABASE_URL", "http://supabase.test")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "service-key")

from fastapi.testclient import TestClient  # noqa: E402

from app import reports, report_ai  # noqa: E402
from app.auth import verify_bearer_token  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas import AuthUser  # noqa: E402

CLIENT_A = "aaaaaaaa-0000-0000-0000-000000000001"
CLIENT_B = "bbbbbbbb-0000-0000-0000-000000000002"
ORG = "oooooooo-0000-0000-0000-000000000001"
SNAPSHOT = {
    "sources": {
        "shopify": {
            "ok": True,
            "label": "Shopify",
            "dateRange": {"start": "2026-08-28", "end": "2026-09-26"},
            "cards": [{"label": "Total revenue", "value": "$35.0k"}, {"label": "Orders", "value": "342"}],
            "trend": {"label": "Revenue", "points": [{"date": "2026-09-01", "value": 1000}, {"date": "2026-09-02", "value": 1500}]},
            "table": {"title": "Product", "columns": [{"key": "product", "label": "Product"}, {"key": "revenue", "label": "Revenue"}], "rows": [{"product": "Hoodie", "revenue": "$8.5k"}]},
        }
    }
}


class FakeResponse:
    def __init__(self, status_code=200, data=None, content=b"", headers=None):
        self.status_code = status_code
        self._data = data
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSupabase:
    """Just enough PostgREST/Storage to exercise reports.py: eq./neq./in./ilike.
    filters, insert/patch/delete on pages, data_sources reads and a storage bucket."""

    def __init__(self):
        self.pages: list[dict] = []
        self.data_sources = [
            {"client_id": CLIENT_A, "type": "shopify", "status": "connected"},
            {"client_id": CLIENT_A, "type": "google_ads", "status": "connected"},
            {"client_id": CLIENT_B, "type": "shopify", "status": "connected"},
        ]
        self.files: dict[str, bytes] = {}
        self.tick = 0

    def _now(self):
        self.tick += 1
        return f"2026-09-26T10:00:{self.tick:02d}.000000+00:00"

    @staticmethod
    def _match(row, params):
        for key, expr in params.items():
            if key in ("select", "order", "limit", "offset", "on_conflict"):
                continue
            value = row.get(key)
            if expr.startswith("eq."):
                if str(value) != expr[3:]:
                    return False
            elif expr.startswith("neq."):
                if str(value) == expr[4:]:
                    return False
            elif expr.startswith("in.("):
                if str(value) not in expr[4:-1].split(","):
                    return False
            elif expr.startswith("ilike."):
                if expr[6:].strip("*").lower() not in str(value or "").lower():
                    return False
        return True

    def client(self):
        outer = self

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, params=None, headers=None):
                params = params or {}
                if url.endswith("/pages"):
                    rows = [r for r in outer.pages if outer._match(r, params)]
                    total = len(rows)
                    start = int(params.get("offset", 0))
                    rows = rows[start:start + int(params.get("limit", 1000))]
                    return FakeResponse(200, [dict(r) for r in rows], headers={"content-range": f"0-{len(rows)}/{total}"})
                if url.endswith("/data_sources"):
                    return FakeResponse(200, [r for r in outer.data_sources if outer._match(r, params)])
                if "/object/report-files/" in url:
                    path = url.split("/object/report-files/")[1]
                    if path in outer.files:
                        return FakeResponse(200, content=outer.files[path])
                    return FakeResponse(404)
                raise AssertionError(f"unexpected GET {url}")

            async def post(self, url, params=None, headers=None, json=None, content=None):
                if url.endswith("/pages"):
                    row = {
                        "id": str(uuid.uuid4()), "created_at": outer._now(), "pdf_path": None, "pdf_version": 0,
                        "pdf_generated_at": None, "pdf_size_bytes": None, "page_count": None, **json,
                    }
                    outer.pages.append(row)
                    return FakeResponse(201, [dict(row)])
                if "/object/report-files/" in url:
                    outer.files[url.split("/object/report-files/")[1]] = content
                    return FakeResponse(200, {})
                raise AssertionError(f"unexpected POST {url}")

            async def patch(self, url, params=None, headers=None, json=None):
                rows = [r for r in outer.pages if outer._match(r, params or {})]
                for row in rows:
                    row.update(json)
                return FakeResponse(200, [dict(r) for r in rows])

            async def delete(self, url, params=None, headers=None):
                outer.pages[:] = [r for r in outer.pages if not outer._match(r, params or {})]
                return FakeResponse(204)

            async def request(self, method, url, headers=None, json=None):
                for path in (json or {}).get("prefixes", []):
                    outer.files.pop(path, None)
                return FakeResponse(200, {})

        return _Client()


class ReportsApiTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeSupabase()
        self.roles: dict[tuple[str, str], str] = {}
        self.user = "user-1"
        app.dependency_overrides[verify_bearer_token] = lambda: AuthUser(sub=self.user, email="u@test.dev", access_token="t")

        async def fake_role(client_id, user_id):
            return self.roles.get((client_id, user_id))

        self.notify = AsyncMock()
        patches = [
            patch("app.client_access.get_effective_client_role", side_effect=fake_role),
            patch("app.reports.httpx.AsyncClient", side_effect=lambda **kw: self.db.client()),
            patch("app.reports.get_client_workspace", new=AsyncMock(side_effect=lambda cid: {"id": cid, "name": "Shawn", "organization_id": ORG})),
            patch("app.reports.create_client_notification", new=self.notify),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(app.dependency_overrides.clear)
        self.http = TestClient(app)

    def as_role(self, role, client=CLIENT_A, user=None):
        self.user = user or self.user
        self.roles[(client, self.user)] = role

    def create(self, client=CLIENT_A, **overrides):
        body = {"name": "May report", "template": "monthly", "periodDays": 30, "sources": ["shopify"], **overrides}
        return self.http.post(f"/py/clients/{client}/reports", json=body)

    # ── lifecycle & permissions ──────────────────────────────────────────

    def test_editor_creates_draft_with_client_org_and_default_sections(self):
        self.as_role("editor")
        response = self.create()
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertEqual(data["status"], "draft")
        self.assertEqual(data["clientId"], CLIENT_A)
        self.assertEqual(data["organizationId"], ORG)
        self.assertEqual(data["config"]["sources"], ["shopify"])
        self.assertIn("commentary:shopify", [s["id"] for s in data["config"]["sections"]])
        self.assertNotIn("commentary:google_ads", [s["id"] for s in data["config"]["sections"]])

    def test_viewer_can_list_but_not_create(self):
        self.as_role("editor")
        self.create()
        self.as_role("viewer")
        self.assertEqual(self.create().status_code, 403)
        listed = self.http.get(f"/py/clients/{CLIENT_A}/reports")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["total"], 1)

    def test_create_rejects_unconnected_source_and_bad_period(self):
        self.as_role("editor")
        self.assertEqual(self.create(sources=["klaviyo"]).status_code, 422)
        self.assertEqual(self.create(periodDays=45).status_code, 422)
        self.assertEqual(self.create(sources=[]).status_code, 422)

    def test_delete_is_owner_only_and_archive_needs_manage(self):
        self.as_role("editor")
        report = self.create().json()
        base = f"/py/clients/{CLIENT_A}/reports/{report['id']}"
        self.assertEqual(self.http.delete(base).status_code, 403)
        self.assertEqual(self.http.post(f"{base}/status", json={"status": "archived"}).status_code, 403)
        self.as_role("admin")
        self.assertEqual(self.http.delete(base).status_code, 403)
        self.assertEqual(self.http.post(f"{base}/status", json={"status": "archived"}).status_code, 200)
        self.as_role("owner")
        self.assertEqual(self.http.delete(base).status_code, 204)
        self.assertEqual(self.http.get(base).status_code, 404)

    def test_archived_reports_hidden_from_default_list_but_filterable(self):
        self.as_role("admin")
        report = self.create().json()
        self.http.post(f"/py/clients/{CLIENT_A}/reports/{report['id']}/status", json={"status": "archived"})
        self.assertEqual(self.http.get(f"/py/clients/{CLIENT_A}/reports").json()["total"], 0)
        self.assertEqual(self.http.get(f"/py/clients/{CLIENT_A}/reports?status=archived").json()["total"], 1)

    def test_search_and_unknown_sort(self):
        self.as_role("editor")
        self.create(name="April recap")
        self.create(name="Q3 review")
        found = self.http.get(f"/py/clients/{CLIENT_A}/reports?q=april").json()
        self.assertEqual([r["name"] for r in found["reports"]], ["April recap"])
        self.assertEqual(self.http.get(f"/py/clients/{CLIENT_A}/reports?sort=bogus").status_code, 422)

    def test_finalize_requires_data_and_locks_editing(self):
        self.as_role("editor")
        report = self.create().json()
        base = f"/py/clients/{CLIENT_A}/reports/{report['id']}"
        self.assertEqual(self.http.post(f"{base}/status", json={"status": "final"}).status_code, 409)
        saved = self.http.patch(base, json={"expectedUpdatedAt": report["updatedAt"], "content": {"snapshot": SNAPSHOT, "narratives": {}}})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.http.post(f"{base}/status", json={"status": "final"}).status_code, 200)
        locked = self.http.patch(base, json={"expectedUpdatedAt": saved.json()["updatedAt"], "name": "x"})
        self.assertEqual(locked.status_code, 409)
        self.assertEqual(self.http.post(f"{base}/status", json={"status": "draft"}).status_code, 200)

    def test_stale_update_is_a_conflict_not_an_overwrite(self):
        self.as_role("editor")
        report = self.create().json()
        base = f"/py/clients/{CLIENT_A}/reports/{report['id']}"
        first = self.http.patch(base, json={"expectedUpdatedAt": report["updatedAt"], "name": "First"})
        self.assertEqual(first.status_code, 200)
        stale = self.http.patch(base, json={"expectedUpdatedAt": report["updatedAt"], "name": "Second"})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["detail"]["code"], "report_conflict")
        self.assertEqual(self.http.get(base).json()["name"], "First")

    def test_narrative_text_is_bounded_and_section_ids_validated(self):
        self.as_role("editor")
        report = self.create().json()
        base = f"/py/clients/{CLIENT_A}/reports/{report['id']}"
        bad = self.http.patch(base, json={"expectedUpdatedAt": report["updatedAt"], "content": {"narratives": {"kpis": "x"}}})
        self.assertEqual(bad.status_code, 422)
        long = self.http.patch(base, json={"expectedUpdatedAt": report["updatedAt"], "content": {"narratives": {"summary": "x" * 7000}}})
        self.assertEqual(long.status_code, 422)

    def test_duplicate_copies_structure_but_not_data_or_narrative(self):
        self.as_role("editor")
        report = self.create().json()
        base = f"/py/clients/{CLIENT_A}/reports/{report['id']}"
        self.http.patch(base, json={"expectedUpdatedAt": report["updatedAt"], "content": {"snapshot": SNAPSHOT, "narratives": {"summary": "Old numbers"}}})
        copy = self.http.post(f"{base}/duplicate")
        self.assertEqual(copy.status_code, 201, copy.text)
        data = copy.json()
        self.assertNotEqual(data["id"], report["id"])
        self.assertEqual(data["status"], "draft")
        self.assertEqual(data["content"], {})
        self.assertEqual(data["name"], "May report (copy)")
        self.assertFalse(data["pdf"]["available"])

    # ── client isolation ────────────────────────────────────────────────

    def test_client_a_reports_are_invisible_and_immutable_from_client_b(self):
        self.as_role("owner", CLIENT_A)
        self.as_role("owner", CLIENT_B)
        report = self.create(CLIENT_A).json()
        other = self.create(CLIENT_B).json()
        under_b = f"/py/clients/{CLIENT_B}/reports/{report['id']}"
        self.assertEqual(self.http.get(under_b).status_code, 404)
        self.assertEqual(self.http.patch(under_b, json={"expectedUpdatedAt": report["updatedAt"], "name": "hijack"}).status_code, 404)
        self.assertEqual(self.http.post(f"{under_b}/status", json={"status": "archived"}).status_code, 404)
        self.assertEqual(self.http.post(f"{under_b}/duplicate").status_code, 404)
        self.assertEqual(self.http.post(f"{under_b}/narrative", json={"section": "summary"}).status_code, 404)
        self.assertEqual(self.http.put(f"{under_b}/pdf", content=b"%PDF-1.4 x").status_code, 404)
        self.assertEqual(self.http.get(f"{under_b}/pdf").status_code, 404)
        self.assertEqual(self.http.delete(under_b).status_code, 404)
        listed_b = [r["id"] for r in self.http.get(f"/py/clients/{CLIENT_B}/reports").json()["reports"]]
        self.assertEqual(listed_b, [other["id"]])
        self.assertEqual(self.http.get(f"/py/clients/{CLIENT_A}/reports/{report['id']}").json()["name"], "May report")

    def test_user_without_a_role_on_the_client_is_rejected_everywhere(self):
        self.as_role("owner", CLIENT_A)
        report = self.create(CLIENT_A).json()
        self.roles.clear()  # no membership / share on client A any more
        base = f"/py/clients/{CLIENT_A}/reports"
        self.assertEqual(self.http.get(base).status_code, 403)
        self.assertEqual(self.http.get(f"{base}/{report['id']}").status_code, 403)
        self.assertEqual(self.http.get(f"{base}/{report['id']}/pdf").status_code, 403)
        self.assertEqual(self.create(CLIENT_A).status_code, 403)

    def test_role_on_client_a_does_not_grant_access_to_client_b(self):
        self.as_role("owner", CLIENT_B, user="someone-else")
        self.user = "user-1"
        self.as_role("owner", CLIENT_A)
        self.assertEqual(self.http.get(f"/py/clients/{CLIENT_B}/reports").status_code, 403)

    # ── PDF ─────────────────────────────────────────────────────────────

    def test_pdf_upload_store_download_and_single_notification(self):
        self.as_role("editor")
        report = self.create().json()
        base = f"/py/clients/{CLIENT_A}/reports/{report['id']}"
        bad = self.http.put(f"{base}/pdf", content=b"<html>not a pdf</html>")
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(self.http.get(f"{base}/pdf").status_code, 404)  # nothing exported yet
        ok = self.http.put(f"{base}/pdf", content=b"%PDF-1.4 hello", headers={"x-page-count": "3"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["pdf"]["version"], 1)
        self.assertEqual(ok.json()["pageCount"], 3)
        self.assertEqual(self.notify.await_count, 1)
        self.assertEqual(self.notify.await_args.kwargs["dedupe_key"], f"report_generated:{report['id']}:v1")
        self.assertEqual(self.notify.await_args.kwargs["event_type"], "report_generated")
        self.as_role("viewer")
        download = self.http.get(f"{base}/pdf")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, b"%PDF-1.4 hello")
        self.assertIn("Shawn_May_report", download.headers["content-disposition"])
        self.assertEqual(download.headers["cache-control"], "private, no-store")
        self.as_role("editor")
        self.http.put(f"{base}/pdf", content=b"%PDF-1.4 v2")
        self.assertNotIn(f"{CLIENT_A}/{report['id']}/v1.pdf", self.db.files)
        self.assertEqual(self.notify.await_count, 2)

    def test_notification_failure_never_fails_the_export(self):
        self.as_role("editor")
        report = self.create().json()
        self.notify.side_effect = RuntimeError("smtp down")
        response = self.http.put(f"/py/clients/{CLIENT_A}/reports/{report['id']}/pdf", content=b"%PDF-1.4 x")
        self.assertEqual(response.status_code, 200)

    def test_viewer_cannot_upload_pdf(self):
        self.as_role("editor")
        report = self.create().json()
        self.as_role("viewer")
        self.assertEqual(self.http.put(f"/py/clients/{CLIENT_A}/reports/{report['id']}/pdf", content=b"%PDF-1.4 x").status_code, 403)

    def test_missing_stored_file_is_a_clear_404(self):
        self.as_role("editor")
        report = self.create().json()
        base = f"/py/clients/{CLIENT_A}/reports/{report['id']}"
        self.http.put(f"{base}/pdf", content=b"%PDF-1.4 x")
        self.db.files.clear()
        self.assertEqual(self.http.get(f"{base}/pdf").status_code, 404)

    # ── AI narrative endpoint ───────────────────────────────────────────

    def _report_with_snapshot(self):
        report = self.create().json()
        base = f"/py/clients/{CLIENT_A}/reports/{report['id']}"
        self.http.patch(base, json={"expectedUpdatedAt": report["updatedAt"], "content": {"snapshot": SNAPSHOT}})
        return base

    def test_narrative_without_provider_is_a_clean_409(self):
        self.as_role("editor")
        base = self._report_with_snapshot()
        with patch("app.reports.get_ai_provider_config", new=AsyncMock(return_value={})):
            response = self.http.post(f"{base}/narrative", json={"section": "summary"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "no_ai_provider")

    def test_narrative_requires_loaded_data(self):
        self.as_role("editor")
        report = self.create().json()
        with patch("app.reports.get_ai_provider_config", new=AsyncMock(return_value={"provider": "openrouter"})):
            response = self.http.post(f"/py/clients/{CLIENT_A}/reports/{report['id']}/narrative", json={"section": "summary"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "no_data")

    def test_narrative_success_and_failure_paths(self):
        self.as_role("editor")
        base = self._report_with_snapshot()
        ai = {"provider": "openrouter", "api_key": "k", "model": "m"}
        good = {"text": "Revenue reached $35.0k.", "provider": "openrouter", "model": "m", "warnings": []}
        with patch("app.reports.get_ai_provider_config", new=AsyncMock(return_value=ai)), \
                patch("app.reports.generate_report_text", return_value=good) as generate:
            response = self.http.post(f"{base}/narrative", json={"section": "commentary:shopify"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["text"], "Revenue reached $35.0k.")
        self.assertEqual(generate.call_args.kwargs["section"], "commentary:shopify")
        self.assertEqual(generate.call_args.kwargs["snapshot"], SNAPSHOT)
        with patch("app.reports.get_ai_provider_config", new=AsyncMock(return_value=ai)), \
                patch("app.reports.generate_report_text", side_effect=report_ai.ReportAIError("provider exploded")):
            failed = self.http.post(f"{base}/narrative", json={"section": "summary"})
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(failed.json()["detail"]["code"], "ai_failed")
        self.assertEqual(self.http.post(f"{base}/narrative", json={"section": "kpis"}).status_code, 422)

    def test_viewer_cannot_generate_narrative(self):
        self.as_role("editor")
        base = self._report_with_snapshot()
        self.as_role("viewer")
        self.assertEqual(self.http.post(f"{base}/narrative", json={"section": "summary"}).status_code, 403)


class ReportHelpersTests(unittest.TestCase):
    def test_default_sections_only_include_selected_sources(self):
        ids = [s["id"] for s in reports.default_sections("monthly", ["meta_ads"])]
        self.assertIn("commentary:meta_ads", ids)
        self.assertNotIn("commentary:shopify", ids)

    def test_normalize_sections_rejects_unknown_and_drops_unselected_commentary(self):
        self.assertEqual(
            reports.normalize_sections([{"id": "kpis"}, {"id": "kpis"}, {"id": "commentary:klaviyo"}], ["shopify"]),
            [{"id": "kpis", "enabled": True}],
        )
        with self.assertRaises(Exception):
            reports.normalize_sections([{"id": "<script>"}], ["shopify"])

    def test_download_filename_is_safe(self):
        self.assertEqual(reports.download_filename("Shawn & Co.", "May '26 / Perf", "2026-05-31"), "Shawn_Co_May_26_Perf_2026-05-31.pdf")


class ReportAIHelpersTests(unittest.TestCase):
    def test_facts_are_scoped_to_the_requested_source(self):
        snapshot = {"sources": {**SNAPSHOT["sources"], "meta_ads": {"ok": True, "cards": [{"label": "Spend", "value": "$9.9k"}]}}}
        facts = report_ai.build_facts("commentary:shopify", snapshot)
        self.assertIn("Total revenue: $35.0k", facts)
        self.assertNotIn("Meta", facts)
        self.assertIn("Meta Ads", report_ai.build_facts("summary", snapshot))

    def test_invented_figures_are_flagged_and_real_ones_are_not(self):
        facts = report_ai.build_facts("summary", SNAPSHOT)
        self.assertEqual(report_ai.ungrounded_figures("Revenue was $35.0k across 342 orders.", facts), [])
        flagged = report_ai.ungrounded_figures("Revenue grew 12% to $48.2k.", facts)
        self.assertIn("12%", flagged)
        self.assertIn("$48.2k", flagged)

    def test_generated_text_is_stripped_of_markup(self):
        cleaned = report_ai.clean_generated_text("**Great** month <script>alert(1)</script> for <b>Shawn</b>")
        self.assertNotIn("<", cleaned)
        self.assertNotIn("*", cleaned)

    def test_generate_maps_provider_errors_to_a_clean_message(self):
        with patch.object(report_ai, "_call_provider", side_effect=RuntimeError("401 secret-key-abc")):
            with self.assertRaises(report_ai.ReportAIError) as ctx:
                report_ai.generate_report_text({"provider": "openrouter"}, section="summary", client_name="Shawn",
                                               report_name="R", period_start=None, period_end=None, snapshot=SNAPSHOT)
        self.assertNotIn("secret-key-abc", str(ctx.exception))

    def test_generate_returns_text_and_warnings(self):
        with patch.object(report_ai, "_call_provider", return_value="Revenue hit $35.0k, up 12%."):
            result = report_ai.generate_report_text({"provider": "openrouter", "model": "m"}, section="summary", client_name="Shawn",
                                                    report_name="R", period_start="2026-08-28", period_end="2026-09-26", snapshot=SNAPSHOT)
        self.assertEqual(result["text"], "Revenue hit $35.0k, up 12%.")
        self.assertTrue(any("12%" in w for w in result["warnings"]))

    def test_no_data_is_a_clear_error_before_any_model_call(self):
        with patch.object(report_ai, "_call_provider") as call:
            with self.assertRaises(report_ai.ReportAIError):
                report_ai.generate_report_text({"provider": "openrouter"}, section="commentary:klaviyo", client_name="Shawn",
                                               report_name="R", period_start=None, period_end=None, snapshot=SNAPSHOT)
        call.assert_not_called()


class MigrationMissingTests(unittest.TestCase):
    def test_missing_column_gives_actionable_error_not_generic_502(self):
        import httpx

        response = httpx.Response(400, text='{"code":"42703","message":"column pages.organization_id does not exist"}',
                                  request=httpx.Request("GET", "http://x"))
        error = reports._upstream_error(httpx.HTTPStatusError("bad", request=response.request, response=response))
        self.assertEqual(error.status_code, 503)
        self.assertEqual(error.detail["code"], "migration_missing")
        other = httpx.Response(500, text="boom", request=httpx.Request("GET", "http://x"))
        self.assertEqual(reports._upstream_error(httpx.HTTPStatusError("bad", request=other.request, response=other)).status_code, 502)


class ReportsPeriodAndChartTests(unittest.TestCase):
    setUp = ReportsApiTests.setUp
    as_role = ReportsApiTests.as_role
    create = ReportsApiTests.create

    def test_new_report_needs_no_template(self):
        self.as_role("editor")
        body = {"name": "No template", "periodDays": 30, "sources": ["shopify"]}
        response = self.http.post(f"/py/clients/{CLIENT_A}/reports", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertIsNone(response.json()["template"])
        self.assertTrue(response.json()["config"]["sections"])  # full default sections

    def test_valid_custom_range_is_stored_and_drives_the_period(self):
        self.as_role("editor")
        response = self.create(periodMode="custom", customStart="2026-09-01", customEnd="2026-09-20")
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertEqual((data["periodStart"], data["periodEnd"]), ("2026-09-01", "2026-09-20"))
        self.assertEqual(data["config"]["periodMode"], "custom")
        self.assertEqual(data["config"]["customStart"], "2026-09-01")
        self.assertEqual(data["config"]["periodDays"], 20)

    def test_invalid_custom_ranges_are_rejected(self):
        self.as_role("editor")
        cases = [
            {"periodMode": "custom"},  # no dates
            {"periodMode": "custom", "customStart": "2026-09-20", "customEnd": "2026-09-01"},  # reversed
            {"periodMode": "custom", "customStart": "2025-01-01", "customEnd": "2026-09-20"},  # > 365 days
            {"periodMode": "custom", "customStart": "2026-09-01", "customEnd": "2999-01-01"},  # future
            {"periodMode": "custom", "customStart": "2001-01-01", "customEnd": "2001-02-01"},  # too early
        ]
        for extra in cases:
            self.assertEqual(self.create(**extra).status_code, 422, extra)

    def test_legacy_14_day_preset_still_valid_but_45_is_not(self):
        self.as_role("editor")
        self.assertEqual(self.create(periodDays=14).status_code, 201)
        self.assertEqual(self.create(periodDays=45).status_code, 422)

    def test_chart_types_persist_and_are_validated(self):
        self.as_role("editor")
        report = self.create().json()
        base = f"/py/clients/{CLIENT_A}/reports/{report['id']}"
        config = dict(report["config"])
        config["sections"] = [{"id": "kpis", "enabled": True}, {"id": "chart:shopify:revenue", "enabled": True}, {"id": "chart:shopify:orders", "enabled": False}]
        config["chartTypes"] = {"chart:shopify:revenue": "hbar", "chart:shopify:gone": "line"}
        saved = self.http.patch(base, json={"expectedUpdatedAt": report["updatedAt"], "config": config})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["config"]["chartTypes"], {"chart:shopify:revenue": "hbar"})  # stale entry dropped
        reopened = self.http.get(base).json()
        self.assertEqual(reopened["config"]["chartTypes"]["chart:shopify:revenue"], "hbar")
        bad = dict(config, chartTypes={"chart:shopify:revenue": "pie"})
        self.assertEqual(self.http.patch(base, json={"expectedUpdatedAt": saved.json()["updatedAt"], "config": bad}).status_code, 422)

    def test_chart_sections_for_unselected_sources_are_dropped(self):
        self.as_role("editor")
        report = self.create().json()
        base = f"/py/clients/{CLIENT_A}/reports/{report['id']}"
        config = dict(report["config"], sections=[{"id": "chart:shopify:revenue", "enabled": True}, {"id": "chart:klaviyo:sent", "enabled": True}])
        saved = self.http.patch(base, json={"expectedUpdatedAt": report["updatedAt"], "config": config}).json()
        self.assertEqual([s["id"] for s in saved["config"]["sections"]], ["chart:shopify:revenue"])
        bad = dict(config, sections=[{"id": "chart:shopify:<script>", "enabled": True}])
        self.assertEqual(self.http.patch(base, json={"expectedUpdatedAt": saved["updatedAt"], "config": bad}).status_code, 422)

    def test_duplicate_keeps_chart_types_and_custom_range_but_not_data(self):
        self.as_role("editor")
        report = self.create(periodMode="custom", customStart="2026-09-01", customEnd="2026-09-20").json()
        base = f"/py/clients/{CLIENT_A}/reports/{report['id']}"
        config = dict(report["config"], sections=[{"id": "chart:shopify:revenue", "enabled": True}], chartTypes={"chart:shopify:revenue": "area"})
        self.http.patch(base, json={"expectedUpdatedAt": report["updatedAt"], "config": config, "content": {"snapshot": SNAPSHOT}})
        copy = self.http.post(f"{base}/duplicate").json()
        self.assertEqual(copy["config"]["chartTypes"], {"chart:shopify:revenue": "area"})
        self.assertEqual(copy["config"]["customStart"], "2026-09-01")
        self.assertEqual(copy["content"], {})

    def test_sent_report_can_be_reopened_as_draft_by_an_editor(self):
        self.as_role("admin")
        report = self.create().json()
        base = f"/py/clients/{CLIENT_A}/reports/{report['id']}"
        self.db.pages[0]["status"] = "sent"
        self.as_role("editor")
        self.assertEqual(self.http.post(f"{base}/status", json={"status": "draft"}).status_code, 200)

    def test_silent_pdf_upload_does_not_notify(self):
        self.as_role("editor")
        report = self.create().json()
        base = f"/py/clients/{CLIENT_A}/reports/{report['id']}"
        ok = self.http.put(f"{base}/pdf", content=b"%PDF-1.4 x", headers={"x-silent": "1"})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["pdf"]["available"])
        self.assertEqual(self.notify.await_count, 0)


TEAM_A = [
    {"user_id": "user-1", "email": "owner@a.test", "display_name": "Owner", "role": "owner"},
    {"user_id": "mate-1", "email": "mate1@a.test", "display_name": "Mate One", "role": "editor"},
    {"user_id": "mate-2", "email": "mate2@a.test", "display_name": "Mate Two", "role": "viewer"},
]


class SendToTeammatesTests(unittest.TestCase):
    setUp = ReportsApiTests.setUp
    as_role = ReportsApiTests.as_role
    create = ReportsApiTests.create

    def start(self, **patches):
        self.email = AsyncMock(return_value=True)
        self.user_notify = AsyncMock()
        stack = [
            patch("app.reports.get_client_team", new=AsyncMock(return_value=TEAM_A)),
            patch("app.reports.send_email_with_attachment", new=self.email),
            patch("app.reports.email_configured", return_value=patches.get("configured", True)),
            patch("app.reports.create_user_notification", new=self.user_notify),
        ]
        for item in stack:
            item.start()
            self.addCleanup(item.stop)

    def report_with_pdf(self, status="final"):
        self.as_role("admin")
        report = self.create().json()
        base = f"/py/clients/{CLIENT_A}/reports/{report['id']}"
        self.http.put(f"{base}/pdf", content=b"%PDF-1.4 hello")
        self.db.pages[-1]["status"] = status
        return base

    def test_only_manage_roles_can_send(self):
        self.start()
        base = self.report_with_pdf()
        self.as_role("editor")
        self.assertEqual(self.http.post(f"{base}/send", json={"recipientIds": ["mate-1"]}).status_code, 403)
        self.as_role("viewer")
        self.assertEqual(self.http.post(f"{base}/send", json={"recipientIds": ["mate-1"]}).status_code, 403)
        self.assertEqual(self.email.await_count, 0)

    def test_send_emails_each_selected_teammate_the_pdf_and_marks_sent(self):
        self.start()
        base = self.report_with_pdf()
        response = self.http.post(f"{base}/send", json={"recipientIds": ["mate-1", "mate-2"], "sendId": "s1"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["sent"], 2)
        self.assertEqual(self.email.await_count, 2)
        first = self.email.await_args_list[0].args
        self.assertEqual(first[0], "mate1@a.test")
        self.assertTrue(first[3].endswith(".pdf"))
        self.assertEqual(first[4], b"%PDF-1.4 hello")  # the stored, current PDF
        self.assertEqual(self.db.pages[-1]["status"], "sent")
        self.assertEqual(self.user_notify.await_count, 1)
        self.assertEqual(self.user_notify.await_args.args[0], ["mate-1", "mate-2"])
        self.assertEqual(self.user_notify.await_args.kwargs["event_type"], "report_delivered")

    def test_recipient_outside_the_client_is_rejected_before_any_email(self):
        self.start()
        base = self.report_with_pdf()
        response = self.http.post(f"{base}/send", json={"recipientIds": ["mate-1", "someone-from-another-client"]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "recipient_not_allowed")
        self.assertEqual(self.email.await_count, 0)

    def test_empty_recipient_list_is_rejected(self):
        self.start()
        base = self.report_with_pdf()
        self.assertEqual(self.http.post(f"{base}/send", json={"recipientIds": []}).status_code, 422)

    def test_double_click_does_not_email_twice(self):
        self.start()
        base = self.report_with_pdf()
        body = {"recipientIds": ["mate-1"], "sendId": "same-click"}
        self.assertEqual(self.http.post(f"{base}/send", json=body).json()["duplicate"], False)
        self.assertEqual(self.http.post(f"{base}/send", json=body).json()["duplicate"], True)
        self.assertEqual(self.email.await_count, 1)

    def test_report_without_a_pdf_cannot_be_sent(self):
        self.start()
        self.as_role("admin")
        report = self.create().json()
        response = self.http.post(f"/py/clients/{CLIENT_A}/reports/{report['id']}/send", json={"recipientIds": ["mate-1"]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "no_pdf")

    def test_email_not_configured_and_provider_failure_are_clean_errors(self):
        self.start(configured=False)
        base = self.report_with_pdf()
        self.assertEqual(self.http.post(f"{base}/send", json={"recipientIds": ["mate-1"]}).status_code, 503)

    def test_all_emails_failing_is_an_error_and_can_be_retried(self):
        self.start()
        base = self.report_with_pdf()
        self.email.return_value = False
        body = {"recipientIds": ["mate-1"], "sendId": "retry-me"}
        self.assertEqual(self.http.post(f"{base}/send", json=body).status_code, 502)
        self.email.return_value = True
        retry = self.http.post(f"{base}/send", json=body)
        self.assertEqual(retry.status_code, 200)
        self.assertFalse(retry.json()["duplicate"])
        self.assertEqual(self.db.pages[-1]["status"], "sent")

    def test_client_b_report_cannot_be_sent_through_client_a(self):
        self.start()
        base = self.report_with_pdf()
        report_id = base.rsplit("/", 1)[1]
        self.as_role("owner", CLIENT_B)
        response = self.http.post(f"/py/clients/{CLIENT_B}/reports/{report_id}/send", json={"recipientIds": ["mate-1"]})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.email.await_count, 0)


if __name__ == "__main__":
    unittest.main()
