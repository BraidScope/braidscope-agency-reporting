# SPDX-License-Identifier: AGPL-3.0-only
"""A provider connection must announce itself (and reset its model cache) ONCE
per login, not on every status poll.

Both status paths call finalize_*_login() whenever the in-memory login session
says "connected", and that session lives on after finalizing, so every poll
used to re-run the connect step: a new "<Provider> connected" notification per
poll, a refreshed data_sources.last_sync_at, and (Claude) a wiped model cache.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import claude_code, codex


def _codex_session(attempt="attempt-1", status="connected"):
    return {"status": status, "home": "/tmp/codex-home", "client_id": "client-1", "user_id": "user-1",
            "model": "gpt-x", "login_attempt_id": attempt}


def _claude_session(attempt="attempt-1", status="connected"):
    return {"status": status, "home": "/tmp/claude-home", "client_id": "client-1", "user_id": "user-1",
            "login_attempt_id": attempt, "subscription_type": "max"}


class CodexFinalizeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        codex.CODEX_SESSIONS.clear()
        self.addCleanup(codex.CODEX_SESSIONS.clear)
        self.upsert_source = AsyncMock()
        self.upsert_token = AsyncMock()
        self.valid = patch.object(codex, "_codex_login_status_valid", return_value=True)
        self.check = self.valid.start()
        self.addCleanup(self.valid.stop)
        for p in (patch.object(codex, "upsert_data_source_connected", self.upsert_source),
                  patch.object(codex, "upsert_connector_token", self.upsert_token)):
            p.start()
            self.addCleanup(p.stop)

    async def test_repeated_status_polls_announce_only_once(self):
        codex.CODEX_SESSIONS["s"] = _codex_session()
        results = [await codex.finalize_codex_login("s") for _ in range(6)]
        self.assertEqual(results, [True] * 6, "callers still see a successful finalize every time")
        self.assertEqual(self.upsert_source.await_count, 1, "data source / notification only on the first finalize")
        self.assertEqual(self.upsert_token.await_count, 1)

    async def test_login_health_check_still_runs_on_every_call(self):
        codex.CODEX_SESSIONS["s"] = _codex_session()
        for _ in range(4):
            await codex.finalize_codex_login("s")
        self.assertEqual(self.check.call_count, 4)

    async def test_a_login_that_stops_being_valid_is_still_detected_after_the_first_finalize(self):
        codex.CODEX_SESSIONS["s"] = _codex_session()
        self.assertTrue(await codex.finalize_codex_login("s"))
        self.check.return_value = False
        self.assertFalse(await codex.finalize_codex_login("s"))
        self.assertEqual(codex.CODEX_SESSIONS["s"]["status"], "error")

    async def test_a_genuine_reconnect_announces_again(self):
        codex.CODEX_SESSIONS["s"] = _codex_session("attempt-1")
        await codex.finalize_codex_login("s")
        await codex.finalize_codex_login("s")
        codex.CODEX_SESSIONS["s"] = _codex_session("attempt-2")      # start_codex_login() replaces the session
        await codex.finalize_codex_login("s")
        await codex.finalize_codex_login("s")
        self.assertEqual(self.upsert_source.await_count, 2, "one announcement per login attempt")

    async def test_pending_or_missing_sessions_do_nothing(self):
        self.assertFalse(await codex.finalize_codex_login("missing"))
        codex.CODEX_SESSIONS["p"] = _codex_session(status="pending")
        self.assertFalse(await codex.finalize_codex_login("p"))
        self.assertEqual(self.upsert_source.await_count, 0)

    async def test_a_failed_first_finalize_is_retried_not_marked_done(self):
        codex.CODEX_SESSIONS["s"] = _codex_session()
        self.upsert_source.side_effect = [RuntimeError("supabase down"), None]
        with self.assertRaises(RuntimeError):
            await codex.finalize_codex_login("s")
        self.assertTrue(await codex.finalize_codex_login("s"))
        self.assertEqual(self.upsert_source.await_count, 2, "a failed attempt must be retried on the next poll")


class ClaudeFinalizeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        claude_code.CLAUDE_SESSIONS.clear()
        claude_code._AUTH_FAILED.clear()
        self.addCleanup(claude_code.CLAUDE_SESSIONS.clear)
        self.addCleanup(claude_code._AUTH_FAILED.clear)
        self.upsert_source = AsyncMock()
        self.update_record = AsyncMock(return_value={})
        for p in (patch.object(claude_code, "upsert_data_source_connected", self.upsert_source),
                  patch.object(claude_code, "_update_user_record", self.update_record),
                  patch.object(claude_code, "_claude_auth_status", return_value={"loggedIn": True}),
                  patch.object(claude_code, "_is_subscription_login", return_value=True)):
            p.start()
            self.addCleanup(p.stop)

    async def test_repeated_status_polls_announce_and_reset_models_only_once(self):
        claude_code.CLAUDE_SESSIONS["s"] = _claude_session()
        results = [await claude_code.finalize_claude_login("s") for _ in range(6)]
        self.assertEqual(results, [True] * 6)
        self.assertEqual(self.upsert_source.await_count, 1, "one 'connected' notification per login")
        self.assertEqual(self.update_record.await_count, 1, "the model cache is reset once, not on every poll")
        changes = self.update_record.await_args.args[2]
        self.assertEqual(changes["available_models"], [], "the fresh-sign-in reset itself is unchanged")

    async def test_status_endpoint_path_polled_repeatedly(self):
        claude_code.CLAUDE_SESSIONS[claude_code._session_id("user-1", "client-1")] = _claude_session()
        record = {"config_dir": "/tmp/claude-home", "model": "opus", "available_models": [{"id": "opus"}], "models_checked_at": "t"}
        with patch.object(claude_code, "_get_user_record", AsyncMock(return_value=record)):
            statuses = [await claude_code.get_claude_connection_status("user-1", "client-1") for _ in range(5)]
        self.assertTrue(all(s["connected"] for s in statuses))
        self.assertEqual(self.upsert_source.await_count, 1)
        self.assertEqual(self.update_record.await_count, 1)

    async def test_auth_failed_marker_still_cleared_on_every_call_as_before(self):
        claude_code.CLAUDE_SESSIONS["s"] = _claude_session()
        await claude_code.finalize_claude_login("s")
        claude_code._AUTH_FAILED["/tmp/claude-home"] = 1.0
        await claude_code.finalize_claude_login("s")
        self.assertNotIn("/tmp/claude-home", claude_code._AUTH_FAILED)

    async def test_an_invalid_login_is_still_detected_after_the_first_finalize(self):
        claude_code.CLAUDE_SESSIONS["s"] = _claude_session()
        self.assertTrue(await claude_code.finalize_claude_login("s"))
        with patch.object(claude_code, "_is_subscription_login", return_value=False):
            self.assertFalse(await claude_code.finalize_claude_login("s"))
        self.assertEqual(claude_code.CLAUDE_SESSIONS["s"]["status"], "error")

    async def test_a_genuine_reconnect_announces_and_resets_again(self):
        claude_code.CLAUDE_SESSIONS["s"] = _claude_session("attempt-1")
        await claude_code.finalize_claude_login("s")
        await claude_code.finalize_claude_login("s")
        claude_code.CLAUDE_SESSIONS["s"] = _claude_session("attempt-2")
        await claude_code.finalize_claude_login("s")
        await claude_code.finalize_claude_login("s")
        self.assertEqual(self.upsert_source.await_count, 2)
        self.assertEqual(self.update_record.await_count, 2)

    async def test_a_failed_first_finalize_is_retried_not_marked_done(self):
        claude_code.CLAUDE_SESSIONS["s"] = _claude_session()
        self.upsert_source.side_effect = [RuntimeError("supabase down"), None]
        with self.assertRaises(RuntimeError):
            await claude_code.finalize_claude_login("s")
        self.assertTrue(await claude_code.finalize_claude_login("s"))
        self.assertEqual(self.upsert_source.await_count, 2)

    async def test_pending_or_missing_sessions_do_nothing(self):
        self.assertFalse(await claude_code.finalize_claude_login("missing"))
        claude_code.CLAUDE_SESSIONS["p"] = _claude_session(status="pending")
        self.assertFalse(await claude_code.finalize_claude_login("p"))
        self.assertEqual(self.upsert_source.await_count, 0)


if __name__ == "__main__":
    unittest.main()
