# SPDX-License-Identifier: AGPL-3.0-only
"""Client invitation lifecycle: a failed/undelivered/stale invitation must
never block a retry with "already pending", and there is at most one live
invitation per CLIENT + EMAIL."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException

from app import client_access as ca

FUTURE = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
PAST = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
WORKSPACE = {"organization_id": "org-1", "name": "Shawn"}


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeHttp:
    def __init__(self):
        self.posted = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        self.posted.append(kw.get("json"))
        return _Resp([{"id": "inv-new", "token": "tok"}])


class CreateInvitationTests(unittest.IsolatedAsyncioTestCase):
    def _patch(self, pending_rows, email_ok=True):
        http = _FakeHttp()
        self.set_status = AsyncMock()
        self.send = AsyncMock(return_value=email_ok)
        for p in (
            patch.object(ca, "assert_client_role", AsyncMock(return_value="admin")),
            patch.object(ca, "get_client_workspace", AsyncMock(return_value=WORKSPACE)),
            patch.object(ca, "find_user_id_by_email", AsyncMock(return_value=None)),
            patch.object(ca, "_rows", AsyncMock(side_effect=lambda t, params: pending_rows if t == "client_invitations" else [{"name": "Agency"}])),
            patch.object(ca, "_set_invitation_status", self.set_status),
            patch.object(ca, "send_client_invitation_email", self.send),
            patch.object(ca.httpx, "AsyncClient", lambda **kw: http),
        ):
            p.start()
            self.addCleanup(p.stop)
        return http

    async def test_new_invitation_sent(self):
        http = self._patch([])
        res = await ca.create_client_invitation("c1", "A@x.com", "editor", "u1")
        self.assertTrue(res["email_sent"])
        self.assertEqual(http.posted[0]["role"], "editor")
        self.assertEqual(http.posted[0]["client_id"], "c1")
        self.set_status.assert_not_called()

    async def test_email_failure_does_not_leave_pending_row(self):
        self._patch([], email_ok=False)
        with self.assertRaises(HTTPException) as cm:
            await ca.create_client_invitation("c1", "a@x.com", "viewer", "u1")
        self.assertEqual(cm.exception.status_code, 502)
        self.set_status.assert_awaited_once_with("inv-new", "cancelled")

    async def test_active_pending_returns_resendable_conflict(self):
        http = self._patch([{"id": "inv-old", "expires_at": FUTURE}])
        with self.assertRaises(HTTPException) as cm:
            await ca.create_client_invitation("c1", "a@x.com", "viewer", "u1")
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(cm.exception.detail["code"], "invitation_pending")
        self.assertEqual(cm.exception.detail["invitation_id"], "inv-old")
        self.assertEqual(http.posted, [])

    async def test_expired_pending_does_not_block(self):
        self._patch([{"id": "inv-old", "expires_at": PAST}])
        res = await ca.create_client_invitation("c1", "a@x.com", "viewer", "u1")
        self.assertTrue(res["ok"])
        self.set_status.assert_any_await("inv-old", "expired")


class ResendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ca._last_resend.clear()
        self.addCleanup(ca._last_resend.clear)
        self.assert_role = AsyncMock(return_value="admin")
        self.set_status = AsyncMock()
        self.send = AsyncMock(return_value=True)

    def _patch(self, row):
        for p in (
            patch.object(ca, "assert_client_role", self.assert_role),
            patch.object(ca, "get_client_workspace", AsyncMock(return_value=WORKSPACE)),
            patch.object(ca, "_rows", AsyncMock(side_effect=lambda t, params: [row] if t == "client_invitations" else [{"name": "Agency"}])),
            patch.object(ca, "_set_invitation_status", self.set_status),
            patch.object(ca, "send_client_invitation_email", self.send),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _row(self, status="pending", expires=FUTURE):
        return {"id": "inv-1", "client_id": "c1", "email": "a@x.com", "role": "editor", "status": status, "token": "tok", "expires_at": expires}

    async def test_resend_checks_admin_on_invitations_own_client_and_keeps_role(self):
        self._patch(self._row())
        res = await ca.resend_invitation("inv-1", "u1")
        self.assert_role.assert_awaited_once_with("c1", "u1", "admin")
        self.assertEqual(self.send.await_args.args[3], "editor")
        self.assertTrue(res["email_sent"])

    async def test_resend_cooldown(self):
        self._patch(self._row())
        await ca.resend_invitation("inv-1", "u1")
        with self.assertRaises(HTTPException) as cm:
            await ca.resend_invitation("inv-1", "u1")
        self.assertEqual(cm.exception.status_code, 429)

    async def test_failed_resend_stays_retryable(self):
        self._patch(self._row())
        self.send.return_value = False
        with self.assertRaises(HTTPException) as cm:
            await ca.resend_invitation("inv-1", "u1")
        self.assertEqual(cm.exception.status_code, 502)
        self.send.return_value = True
        await ca.resend_invitation("inv-1", "u1")

    async def test_expired_is_reissued_on_same_row(self):
        self._patch(self._row(status="expired", expires=PAST))
        await ca.resend_invitation("inv-1", "u1")
        self.assertEqual(self.set_status.await_args.args[:2], ("inv-1", "pending"))

    async def test_accepted_cannot_be_resent(self):
        self._patch(self._row(status="accepted"))
        with self.assertRaises(HTTPException) as cm:
            await ca.resend_invitation("inv-1", "u1")
        self.assertEqual(cm.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
