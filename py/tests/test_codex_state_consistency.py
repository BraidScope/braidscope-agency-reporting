# SPDX-License-Identifier: AGPL-3.0-only
"""Integrations and Ask AI must agree on whether a stored Codex connection is
usable: a token row whose codex_home directory is gone is NOT connected."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import codex


def _row(home):
    return {"auth_type": "codex_chatgpt", "codex_home": home, "model": "gpt-x"}


class CodexStateConsistencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        codex.CODEX_SESSIONS.clear()
        self.addCleanup(codex.CODEX_SESSIONS.clear)

    async def _both(self, row):
        with patch.object(codex, "get_connector_token", AsyncMock(return_value=row)):
            status = await codex.get_codex_connection_status("u", "c")
            context = await codex.get_codex_chat_context("c")
        return status, context

    async def test_existing_home_is_connected_everywhere(self):
        with tempfile.TemporaryDirectory() as home:
            status, context = await self._both(_row(home))
        self.assertTrue(status["connected"])
        self.assertIsNotNone(context)

    async def test_missing_home_is_disconnected_everywhere(self):
        status, context = await self._both(_row("/nonexistent/codex-home"))
        self.assertFalse(status["connected"])
        self.assertEqual(status["status"], "needs_reconnect")
        self.assertIsNone(context)

    async def test_no_row_is_disconnected_everywhere(self):
        status, context = await self._both(None)
        self.assertFalse(status["connected"])
        self.assertIsNone(context)


if __name__ == "__main__":
    unittest.main()
