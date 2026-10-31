# SPDX-License-Identifier: AGPL-3.0-only
"""Regression tests for transient network-error handling in the GA4 and
Google Ads dashboard paths.

GA4:        a transient httpx error while discovering properties must become a
            structured ok:false result, not an unhandled HTTP 500.
Google Ads: a transient network_error from resolve_google_ads_customer() must
            NOT be stored in the 10-minute resolver cache (successes and
            definite account-state errors still are).
"""
import os
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import ga4_dashboard, google_ads_dashboard
from app.auth import verify_bearer_token
from app.main import app
from app.schemas import AuthUser


def _status_error(code: int, text: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://analyticsadmin.googleapis.com/v1beta/accountSummaries")
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=httpx.Response(code, text=text, request=request))


class _DummyClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


PROPERTY = {"id": "111", "name": "Main site", "account": "Acme"}


def _ga4_patches(stack: ExitStack, *, list_properties, cached=None, select=None):
    """Patch everything build_ga4_dashboard touches except the code under test."""
    stack.enter_context(patch.object(ga4_dashboard, "get_ga4_access_token", AsyncMock(return_value="tok")))
    stack.enter_context(patch.object(ga4_dashboard, "_get_org_id", AsyncMock(return_value="org-1")))
    stack.enter_context(patch.object(ga4_dashboard.httpx, "AsyncClient", lambda **kw: _DummyClient()))
    stack.enter_context(patch.object(ga4_dashboard, "_list_properties", list_properties))
    stack.enter_context(patch.object(ga4_dashboard, "_probe_properties", AsyncMock(return_value=[])))
    stack.enter_context(patch.object(ga4_dashboard, "_get_cached_dashboard", AsyncMock(return_value=cached)))
    stack.enter_context(patch.object(ga4_dashboard, "_run_report", AsyncMock(return_value={"rows": []})))
    if select is not None:
        stack.enter_context(patch.object(ga4_dashboard, "_select_property", select))


class GA4PropertyDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def _build(self, **patch_kwargs):
        with ExitStack() as stack:
            _ga4_patches(stack, **patch_kwargs)
            return await ga4_dashboard.build_ga4_dashboard("client-1", days=30)

    # ---- unchanged behaviour -------------------------------------------------

    async def test_1_successful_property_discovery(self):
        result = await self._build(list_properties=AsyncMock(return_value=[PROPERTY]), select=MagicMock(return_value=PROPERTY))
        self.assertTrue(result["ok"])
        self.assertEqual(result["selected_property"], PROPERTY)
        self.assertEqual(result["properties"], [PROPERTY])

    async def test_2_no_ga4_properties_message_unchanged(self):
        result = await self._build(list_properties=AsyncMock(return_value=[]))
        self.assertEqual(result, {
            "ok": False,
            "reason": "No GA4 properties were found for this Google account.",
            "properties": [],
            "property_diagnostics": [],
            "selected_property": None,
        })

    async def test_8_successful_cached_response_unchanged(self):
        cached = {"ok": True, "has_data": True, "cards": [1, 2, 3]}
        result = await self._build(list_properties=AsyncMock(return_value=[PROPERTY]), cached=dict(cached), select=MagicMock(return_value=PROPERTY))
        self.assertTrue(result["ok"])
        self.assertEqual(result["cards"], [1, 2, 3])
        self.assertEqual(result["selected_property"], PROPERTY)

    async def test_not_connected_unchanged(self):
        with patch.object(ga4_dashboard, "get_ga4_access_token", AsyncMock(return_value=None)):
            result = await ga4_dashboard.build_ga4_dashboard("client-1", days=30)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "Google Analytics 4 is not connected for this client.")

    # ---- the fix -------------------------------------------------------------

    async def test_6_transient_connect_error_is_a_structured_failure(self):
        result = await self._build(list_properties=AsyncMock(side_effect=httpx.ConnectError("[Errno 11001] getaddrinfo failed")))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "Could not reach the Google Analytics Admin API.")
        self.assertEqual(result["properties"], [])
        self.assertEqual(result["property_diagnostics"], [])
        self.assertIsNone(result["selected_property"])

    async def test_6b_timeout_is_also_transient(self):
        result = await self._build(list_properties=AsyncMock(side_effect=httpx.ReadTimeout("slow")))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "Could not reach the Google Analytics Admin API.")

    async def test_7_http_error_is_a_structured_failure_with_detail(self):
        result = await self._build(list_properties=AsyncMock(side_effect=_status_error(429, "quota exceeded")))
        self.assertFalse(result["ok"])
        self.assertIn("quota exceeded", result["reason"])
        self.assertIn("property discovery", result["reason"].lower())
        self.assertEqual(result["properties"], [])
        self.assertIsNone(result["selected_property"])

    async def test_unrelated_programming_errors_still_propagate(self):
        for exc in (ValueError("bug"), KeyError("bug"), TypeError("bug")):
            with self.subTest(exc=type(exc).__name__):
                with self.assertRaises(type(exc)):
                    await self._build(list_properties=AsyncMock(side_effect=exc))

    async def test_failure_is_not_fabricated_data(self):
        result = await self._build(list_properties=AsyncMock(side_effect=httpx.ConnectError("dns")))
        for key in ("cards", "overview", "trend", "channels", "pages", "devices", "events", "countries"):
            self.assertNotIn(key, result)


class GA4EndpointTests(unittest.TestCase):
    """End to end through FastAPI: status codes and the 500 -> structured failure change."""

    URL = "/py/dashboards/google-analytics-4"

    def setUp(self):
        app.dependency_overrides[verify_bearer_token] = lambda: AuthUser(sub="user-1", access_token="jwt")
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(app.dependency_overrides.clear)

    def _get(self, client_id="client-1"):
        return self.client.get(self.URL, params={"client_id": client_id, "client_name": "x", "days": 30})

    def test_transient_connect_error_returns_200_ok_false_not_500(self):
        with ExitStack() as stack:
            stack.enter_context(patch("app.main.verify_client_access", AsyncMock(return_value=None)))
            stack.enter_context(patch("app.dashboard_cache._redis", return_value=None))
            _ga4_patches(stack, list_properties=AsyncMock(side_effect=httpx.ConnectError("getaddrinfo failed")))
            response = self._get()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["reason"], "Could not reach the Google Analytics Admin API.")

    def test_successful_response_unchanged(self):
        with ExitStack() as stack:
            stack.enter_context(patch("app.main.verify_client_access", AsyncMock(return_value=None)))
            stack.enter_context(patch("app.dashboard_cache._redis", return_value=None))
            _ga4_patches(stack, list_properties=AsyncMock(return_value=[PROPERTY]), select=MagicMock(return_value=PROPERTY))
            response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def _get_days(self, days):
        with ExitStack() as stack:
            stack.enter_context(patch("app.main.verify_client_access", AsyncMock(return_value=None)))
            stack.enter_context(patch("app.dashboard_cache._redis", return_value=None))
            _ga4_patches(stack, list_properties=AsyncMock(return_value=[PROPERTY]), select=MagicMock(return_value=PROPERTY))
            return self.client.get(self.URL, params={"client_id": "client-1", "client_name": "x", "days": days})

    def test_today_and_yesterday_presets_are_accepted(self):
        # "Today" sends days=1 and "Yesterday" days=2; both used to be a 422
        # whose validation-error list crashed the dashboard when rendered.
        for days in (1, 2):
            with self.subTest(days=days):
                response = self._get_days(days)
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertTrue(body["ok"])
                self.assertEqual(body["date_range"]["days"], days)

    def test_today_is_a_single_day_window(self):
        body = self._get_days(1).json()
        today = ga4_dashboard._iso_date(0)
        self.assertEqual(body["date_range"]["start"], today)
        self.assertEqual(body["date_range"]["end"], today)

    def test_existing_ranges_unchanged(self):
        body = self._get_days(30).json()
        self.assertEqual(body["date_range"]["start"], ga4_dashboard._iso_date(29))
        self.assertEqual(body["date_range"]["end"], ga4_dashboard._iso_date(0))

    def test_out_of_range_days_still_rejected(self):
        for days in (0, 366):
            with self.subTest(days=days):
                self.assertEqual(self._get_days(days).status_code, 422)

    # ── custom date range ─────────────────────────────────────────────
    def _get_range(self, **params):
        with ExitStack() as stack:
            stack.enter_context(patch("app.main.verify_client_access", AsyncMock(return_value=None)))
            stack.enter_context(patch("app.dashboard_cache._redis", return_value=None))
            _ga4_patches(stack, list_properties=AsyncMock(return_value=[PROPERTY]), select=MagicMock(return_value=PROPERTY))
            cache_read = stack.enter_context(patch.object(ga4_dashboard, "_get_cached_dashboard", AsyncMock(return_value=None)))
            cache_write = stack.enter_context(patch.object(ga4_dashboard, "_save_cache", AsyncMock(return_value=None)))
            report = stack.enter_context(patch.object(ga4_dashboard, "_run_report", AsyncMock(return_value={"rows": []})))
            response = self.client.get(self.URL, params={"client_id": "client-1", "client_name": "x", "days": 30, **params})
            return response, cache_read, cache_write, report

    def test_custom_range_is_the_report_window(self):
        response, _, _, report = self._get_range(start_date="2026-08-01", end_date="2026-08-10")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["date_range"], {"start": "2026-08-01", "end": "2026-08-10", "days": 10})
        windows = {(c.kwargs["start_date"], c.kwargs["end_date"]) for c in report.await_args_list}
        self.assertEqual(windows, {("2026-08-01", "2026-08-10")})
        trend_call = next(c for c in report.await_args_list if c.kwargs.get("dimensions") == ["date"])
        self.assertEqual(trend_call.kwargs["limit"], 10)

    def test_single_day_custom_range(self):
        body = self._get_range(start_date="2026-08-05", end_date="2026-08-05")[0].json()
        self.assertEqual(body["date_range"], {"start": "2026-08-05", "end": "2026-08-05", "days": 1})

    def test_custom_range_never_touches_the_days_keyed_snapshot_cache(self):
        _, cache_read, cache_write, _ = self._get_range(start_date="2026-08-01", end_date="2026-08-10")
        cache_read.assert_not_awaited()
        cache_write.assert_not_called()

    def test_presets_still_use_the_snapshot_cache(self):
        _, cache_read, _, _ = self._get_range()
        cache_read.assert_awaited_once()

    def test_invalid_custom_ranges_are_422_with_a_readable_message(self):
        future = (ga4_dashboard.utc_now().date() + ga4_dashboard.timedelta(days=5)).isoformat()
        cases = {
            "only start": ({"start_date": "2026-08-01"}, "both a start and an end"),
            "only end": ({"end_date": "2026-08-01"}, "both a start and an end"),
            "reversed": ({"start_date": "2026-08-10", "end_date": "2026-08-01"}, "on or before the end"),
            "future": ({"start_date": "2026-08-01", "end_date": future}, "can't be in the future"),
            "too long": ({"start_date": "2024-01-01", "end_date": "2025-06-01"}, "at most 365 days"),
            "before GA4": ({"start_date": "2015-01-01", "end_date": "2015-01-10"}, "no data before"),
        }
        for name, (params, expected) in cases.items():
            with self.subTest(name):
                response = self._get_range(**params)[0]
                self.assertEqual(response.status_code, 422)
                detail = response.json()["detail"]
                self.assertIsInstance(detail, str)  # a sentence, never a validation-error list
                self.assertIn(expected, detail)

    def test_malformed_date_is_still_a_422(self):
        self.assertEqual(self._get_range(start_date="yesterday", end_date="2026-08-01")[0].status_code, 422)

    def test_3_unauthorized_client_is_403(self):
        forbidden = AsyncMock(side_effect=HTTPException(status_code=403, detail="Client workspace is not accessible"))
        with patch("app.main.verify_client_access", forbidden):
            response = self._get("someone-elses-client")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Client workspace is not accessible")

    def test_4_invalid_client_behaviour_unchanged(self):
        # An empty client id fails ChatRequest validation inside the handler
        # (a pre-existing 500); this pins that it is not changed by the fix.
        response = self._get("")
        self.assertEqual(response.status_code, 500)

    def test_5_missing_or_invalid_auth_is_401(self):
        app.dependency_overrides.clear()
        env = {k: v for k, v in os.environ.items() if k not in (
            "SUPABASE_JWT_SECRET", "SHARED_JWT_SECRET", "SUPABASE_URL", "VITE_SUPABASE_URL",
            "SUPABASE_ANON_KEY", "VITE_SUPABASE_ANON_KEY")}
        with patch.dict(os.environ, env, clear=True):
            no_header = self.client.get(self.URL, params={"client_id": "client-1"})
            bad_token = self.client.get(self.URL, params={"client_id": "client-1"}, headers={"Authorization": "Bearer not.a.jwt"})
        self.assertEqual(no_header.status_code, 401)
        self.assertEqual(bad_token.status_code, 401)


# ----------------------------- Google Ads --------------------------------------

def _ok(cid="1549328398"):
    return {"ok": True, "reason_code": None, "reason": None, "customer_id": cid, "login_customer_id": None, "name": "Acme", "customers": []}


def _fail(code, reason="x"):
    return {"ok": False, "reason_code": code, "reason": reason, "customer_id": None, "login_customer_id": None, "name": None, "customers": []}


NETWORK_ERROR = _fail("network_error", "Could not reach the Google Ads API. Please check your internet connection and try again.")


class GoogleAdsResolverCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        google_ads_dashboard._resolution_cache.clear()
        self.addCleanup(google_ads_dashboard._resolution_cache.clear)

    async def _resolve(self, uncached, times=1, client_id="client-1"):
        results = []
        with patch.object(google_ads_dashboard, "_resolve_google_ads_customer_uncached", uncached):
            for _ in range(times):
                results.append(await google_ads_dashboard.resolve_google_ads_customer(client_id))
        return results

    # ---- unchanged behaviour: successes and definite errors ARE cached -----------

    async def test_1_and_2_successful_resolution_is_cached(self):
        uncached = AsyncMock(return_value=_ok())
        first, second = await self._resolve(uncached, times=2)
        self.assertEqual(first, _ok())
        self.assertEqual(second, _ok())
        self.assertEqual(uncached.await_count, 1, "second call must be served from the resolver cache")

    async def test_definite_account_state_errors_are_still_cached(self):
        for code in ("no_usable_customer", "disabled", "manager_requested", "no_accounts", "api_error", "not_connected"):
            with self.subTest(reason_code=code):
                google_ads_dashboard._resolution_cache.clear()
                uncached = AsyncMock(return_value=_fail(code))
                first, second = await self._resolve(uncached, times=2)
                self.assertEqual(first["reason_code"], code)
                self.assertEqual(second["reason_code"], code)
                self.assertEqual(uncached.await_count, 1, f"{code} must stay cached exactly as before")

    async def test_cache_is_per_client(self):
        uncached = AsyncMock(side_effect=[_ok("111"), _ok("222")])
        with patch.object(google_ads_dashboard, "_resolve_google_ads_customer_uncached", uncached):
            a = await google_ads_dashboard.resolve_google_ads_customer("client-a")
            b = await google_ads_dashboard.resolve_google_ads_customer("client-b")
        self.assertEqual((a["customer_id"], b["customer_id"]), ("111", "222"))

    async def test_force_refresh_still_bypasses_cache(self):
        uncached = AsyncMock(return_value=_ok())
        with patch.object(google_ads_dashboard, "_resolve_google_ads_customer_uncached", uncached):
            await google_ads_dashboard.resolve_google_ads_customer("client-1")
            await google_ads_dashboard.resolve_google_ads_customer("client-1", force_refresh=True)
        self.assertEqual(uncached.await_count, 2)

    # ---- the fix: transient network errors are NOT cached ---------------------------

    async def test_6_network_error_is_returned_but_not_cached(self):
        uncached = AsyncMock(return_value=NETWORK_ERROR)
        (result,) = await self._resolve(uncached)
        self.assertEqual(result["reason_code"], "network_error")
        self.assertNotIn("client-1:auto", google_ads_dashboard._resolution_cache, "network_error must not be stored")

    async def test_7_second_request_after_network_error_really_retries(self):
        uncached = AsyncMock(return_value=NETWORK_ERROR)
        first, second, third = await self._resolve(uncached, times=3)
        self.assertEqual([r["reason_code"] for r in (first, second, third)], ["network_error"] * 3)
        self.assertEqual(uncached.await_count, 3, "every request must perform a fresh resolution attempt")

    async def test_8_success_after_a_previous_network_failure_and_then_cached(self):
        uncached = AsyncMock(side_effect=[NETWORK_ERROR, _ok(), _ok("SHOULD-NOT-BE-USED")])
        first, second, third = await self._resolve(uncached, times=3)
        self.assertEqual(first["reason_code"], "network_error")
        self.assertTrue(second["ok"])
        self.assertEqual(second["customer_id"], "1549328398")
        self.assertEqual(third["customer_id"], "1549328398", "the success is cached as before")
        self.assertEqual(uncached.await_count, 2)

    async def test_network_error_does_not_evict_an_existing_good_cache_entry(self):
        with patch.object(google_ads_dashboard, "_resolve_google_ads_customer_uncached", AsyncMock(return_value=_ok())):
            await google_ads_dashboard.resolve_google_ads_customer("client-1")
        with patch.object(google_ads_dashboard, "_resolve_google_ads_customer_uncached", AsyncMock(return_value=NETWORK_ERROR)) as uncached:
            forced = await google_ads_dashboard.resolve_google_ads_customer("client-1", force_refresh=True)
            cached = await google_ads_dashboard.resolve_google_ads_customer("client-1")
        self.assertEqual(forced["reason_code"], "network_error")
        self.assertEqual(cached["customer_id"], "1549328398", "a good entry is left alone by a failed forced refresh")
        self.assertEqual(uncached.await_count, 1)

    # ---- same proof one level down: the PROVIDER call is attempted again ------------

    async def test_provider_is_actually_called_again_after_a_dns_failure(self):
        listing = AsyncMock(side_effect=[httpx.ConnectError("[Errno 11001] getaddrinfo failed"), httpx.ConnectError("still down")])
        with ExitStack() as stack:
            stack.enter_context(patch.object(google_ads_dashboard, "get_google_ads_access_token", AsyncMock(return_value="tok")))
            stack.enter_context(patch.object(google_ads_dashboard.httpx, "AsyncClient", lambda **kw: _DummyClient()))
            stack.enter_context(patch.object(google_ads_dashboard, "_list_customers", listing))
            first = await google_ads_dashboard.resolve_google_ads_customer("client-1")
            second = await google_ads_dashboard.resolve_google_ads_customer("client-1")
        self.assertEqual(first["reason_code"], "network_error")
        self.assertEqual(second["reason_code"], "network_error")
        self.assertEqual(listing.await_count, 2, "listAccessibleCustomers must be attempted on BOTH requests")

    async def test_provider_recovers_on_the_next_request_after_a_dns_failure(self):
        listing = AsyncMock(side_effect=[httpx.ConnectError("getaddrinfo failed"), ["1549328398"]])
        probe = AsyncMock(return_value=([{"id": "1549328398", "name": "Acme", "accessible": True, "manager": False}], ["1549328398"], {}))
        verify = AsyncMock(return_value={"id": "1549328398", "name": "Acme", "status": "ENABLED"})
        with ExitStack() as stack:
            stack.enter_context(patch.object(google_ads_dashboard, "get_google_ads_access_token", AsyncMock(return_value="tok")))
            stack.enter_context(patch.object(google_ads_dashboard.httpx, "AsyncClient", lambda **kw: _DummyClient()))
            stack.enter_context(patch.object(google_ads_dashboard, "_list_customers", listing))
            stack.enter_context(patch.object(google_ads_dashboard, "_probe_customers", probe))
            stack.enter_context(patch.object(google_ads_dashboard, "_verify_candidate", verify))
            first = await google_ads_dashboard.resolve_google_ads_customer("client-1")
            second = await google_ads_dashboard.resolve_google_ads_customer("client-1")
        self.assertEqual(first["reason_code"], "network_error")
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["customer_id"], "1549328398")


if __name__ == "__main__":
    unittest.main()


class ResolveDateWindowTests(unittest.TestCase):
    def test_presets_unchanged(self):
        self.assertEqual(ga4_dashboard.resolve_date_window(30), (ga4_dashboard._iso_date(29), ga4_dashboard._iso_date(0), 30))
        self.assertEqual(ga4_dashboard.resolve_date_window(1), (ga4_dashboard._iso_date(0), ga4_dashboard._iso_date(0), 1))

    def test_custom_range_ignores_days(self):
        from datetime import date
        self.assertEqual(ga4_dashboard.resolve_date_window(90, date(2026, 3, 1), date(2026, 3, 31)), ("2026-03-01", "2026-03-31", 31))

    def test_max_span_boundary(self):
        from datetime import date
        self.assertEqual(ga4_dashboard.resolve_date_window(30, date(2025, 1, 1), date(2025, 12, 31))[2], 365)
        with self.assertRaises(ValueError):
            ga4_dashboard.resolve_date_window(30, date(2024, 1, 1), date(2024, 12, 31))  # 366 (leap year)
