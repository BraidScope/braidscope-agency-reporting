# SPDX-License-Identifier: AGPL-3.0-only
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.account_settings import _filter_notification_scope
from app.supabase_admin import create_user_notification, notify_branding_published


class FakeResponse:
    def __init__(self, status_code=201, body="", json_data=None):
        self.status_code = status_code
        self.text = body
        self._json_data = json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.text)

    def json(self):
        return self._json_data or []


class FakePreferenceResponse:
    def json(self):
        return [{"user_id": "owner-1", "notification_prefs": {
            "agency": {"categories": {"branding": {"inApp": True, "email": True}}}
        }}]

    def raise_for_status(self):
        return None


class FakeClient:
    def __init__(self, response, preferences=None):
        self.response = response
        self.preferences = preferences or {"agency": {"categories": {"branding": {"inApp": True, "email": True}}}}
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *_args, **_kwargs):
        response = FakePreferenceResponse()
        response.json = lambda: [{"user_id": "owner-1", "notification_prefs": self.preferences}]
        return response

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.response


class FakeBrandingClient:
    def __init__(self, insert_response=None, owner_prefs=None):
        self.insert_response = insert_response or FakeResponse()
        self.owner_prefs = owner_prefs or {"agency": {"categories": {"branding": {"inApp": True, "email": True}}}}
        self.get_calls = []
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _url, **kwargs):
        self.get_calls.append(kwargs)
        params = kwargs.get("params") or {}
        select = params.get("select", "")
        if select == "role":
            return FakeResponse(json_data=[{"role": "admin"}])
        if select == "user_id,role,notification_prefs":
            return FakeResponse(json_data=[
                {"user_id": "admin-1", "role": "admin", "notification_prefs": {}},
                {"user_id": "owner-1", "role": "owner", "notification_prefs": self.owner_prefs},
            ])
        if select == "user_id,notification_prefs":
            return FakeResponse(json_data=[{"user_id": "owner-1", "notification_prefs": self.owner_prefs}])
        return FakeResponse(json_data=[])

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.insert_response


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_agency_payload_is_owner_only_and_agency_scoped(self):
        fake = FakeClient(FakeResponse())
        with (
            patch.dict(os.environ, {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "service", "RESEND_API_KEY": "resend", "EMAIL_FROM_ADDRESS": "Braidscope <test@example.com>"}, clear=True),
            patch("app.supabase_admin.httpx.AsyncClient", return_value=fake),
            patch("app.supabase_admin.get_user_email", new=AsyncMock(return_value="owner@example.com")),
            patch("app.supabase_admin.send_notification_email", new=AsyncMock(return_value=True)) as send_email,
        ):
            inserted = await create_user_notification(
                ["owner-1"],
                "org-1",
                "Agency branding published",
                "Your agency branding changes are now live.",
                event_type="branding_published",
                category="branding",
                client_id=None,
                email_eligible=True,
                notification_scope="agency",
                role_by_user={"owner-1": "owner"},
                raise_on_insert_failure=True,
            )

        self.assertTrue(inserted)
        payload = fake.post_calls[0][1]["json"][0]
        self.assertEqual(payload["user_id"], "owner-1")
        self.assertEqual(payload["organization_id"], "org-1")
        self.assertIsNone(payload["client_id"])
        self.assertEqual(payload["scope"], "agency")
        self.assertEqual(payload["metadata"]["workspace_scope"], "agency")
        self.assertEqual(payload["category"], "branding")
        self.assertEqual(payload["event_type"], "branding_published")
        send_email.assert_awaited_once()
        self.assertEqual(send_email.await_args.args[0], "owner@example.com")

    def test_scope_filter_never_promotes_legacy_client_rows_to_agency(self):
        rows = [
            {"scope": "client", "metadata": {"workspace_scope": "client"}},
            {"scope": "agency", "metadata": {"workspace_scope": "agency"}},
            {"scope": "client", "metadata": {}},
            {"metadata": {"workspace_scope": "agency"}},
        ]
        self.assertEqual(_filter_notification_scope(rows, "agency"), [rows[1]])
        self.assertEqual(_filter_notification_scope(rows, "client"), [rows[0], rows[2]])

    def test_scope_filter_uses_metadata_only_when_scope_column_is_absent(self):
        rows = [
            {"metadata": {"workspace_scope": "agency"}},
            {"metadata": {"workspace_scope": "client"}},
            {"metadata": {}},
        ]
        self.assertEqual(_filter_notification_scope(rows, "agency"), [rows[0]])
        self.assertEqual(_filter_notification_scope(rows, "client"), [rows[1], rows[2]])

    async def test_failed_supabase_insert_is_raised_for_strict_producers(self):
        fake = FakeClient(FakeResponse(400, '{"message":"scope constraint failed"}'))
        with (
            patch.dict(os.environ, {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "service"}, clear=True),
            patch("app.supabase_admin.httpx.AsyncClient", return_value=fake),
        ):
            with self.assertRaises(RuntimeError):
                await create_user_notification(
                    ["owner-1"],
                    "org-1",
                    "Agency branding published",
                    event_type="branding_published",
                    category="branding",
                    notification_scope="agency",
                    raise_on_insert_failure=True,
                )

    async def test_agency_in_app_preference_controls_row_and_email(self):
        fake = FakeClient(
            FakeResponse(),
            {"agency": {"categories": {"branding": {"inApp": False, "email": False}}}},
        )
        with (
            patch.dict(os.environ, {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "service", "RESEND_API_KEY": "resend"}, clear=True),
            patch("app.supabase_admin.httpx.AsyncClient", return_value=fake),
            patch("app.supabase_admin.send_notification_email", new=AsyncMock(return_value=True)) as send_email,
        ):
            inserted = await create_user_notification(
                ["owner-1"],
                "org-1",
                "Agency branding published",
                event_type="branding_published",
                category="branding",
                email_eligible=True,
                notification_scope="agency",
            )

        self.assertTrue(inserted)
        self.assertEqual(fake.post_calls, [])
        send_email.assert_not_awaited()

    async def test_notify_branding_published_targets_owner_only(self):
        fake = FakeBrandingClient()
        with (
            patch.dict(os.environ, {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "service", "RESEND_API_KEY": "resend"}, clear=True),
            patch("app.supabase_admin.httpx.AsyncClient", return_value=fake),
            patch("app.supabase_admin.get_user_email", new=AsyncMock(return_value="owner@example.com")),
            patch("app.supabase_admin.send_notification_email", new=AsyncMock(return_value=True)) as send_email,
        ):
            result = await notify_branding_published("admin-1", "org-1", None, "publish-1")

        self.assertTrue(result["inserted"])
        self.assertEqual(result["owner_id"], "owner-1")
        payload = fake.post_calls[0][1]["json"]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["user_id"], "owner-1")
        self.assertEqual(payload[0]["organization_id"], "org-1")
        self.assertIsNone(payload[0]["client_id"])
        self.assertEqual(payload[0]["scope"], "agency")
        self.assertEqual(payload[0]["dedupe_key"], "branding_published:agency:org-1:publish-1")
        send_email.assert_awaited_once()
        self.assertEqual(send_email.await_args.args[0], "owner@example.com")

    async def test_notify_branding_published_respects_disabled_branding_email_preference(self):
        fake = FakeBrandingClient(owner_prefs={"agency": {"categories": {"branding": {"inApp": True, "email": False}}}})
        with (
            patch.dict(os.environ, {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "service", "RESEND_API_KEY": "resend"}, clear=True),
            patch("app.supabase_admin.httpx.AsyncClient", return_value=fake),
            patch("app.supabase_admin.get_user_email", new=AsyncMock(return_value="owner@example.com")),
            patch("app.supabase_admin.send_notification_email", new=AsyncMock(return_value=True)) as send_email,
        ):
            result = await notify_branding_published("owner-1", "org-1", None, "publish-2")

        self.assertTrue(result["inserted"])
        self.assertEqual(len(fake.post_calls[0][1]["json"]), 1)
        send_email.assert_not_awaited()
