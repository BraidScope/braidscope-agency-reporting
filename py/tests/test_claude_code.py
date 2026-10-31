# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import claude_code as cc
from app.chat_service import (
    get_available_ai_providers,
    list_ai_provider_models,
    stream_claude_code_provider,
)
from app.schemas import ChatRequest


def run(coro):
    return asyncio.run(coro)


class ConfigDirIsolationTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="claude-test-")
        patcher = patch.dict(os.environ, {"CLAUDE_CONFIG_ROOT": self.root})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_each_user_and_client_gets_its_own_directory(self):
        a = cc._config_dir("user-a", "client-1")
        b = cc._config_dir("user-b", "client-1")
        c = cc._config_dir("user-a", "client-2")
        self.assertEqual(len({a, b, c}), 3)
        for path in (a, b, c):
            self.assertTrue(cc._inside_root(path))

    def test_root_and_outside_paths_are_never_treated_as_removable(self):
        self.assertFalse(cc._inside_root(Path(self.root)))
        self.assertFalse(cc._inside_root(Path(self.root).parent))

    def test_subprocess_env_is_an_allowlist_with_isolated_home(self):
        home = cc._config_dir("user-a", "client-1")
        secrets = {
            "SUPABASE_SERVICE_ROLE_KEY": "srk",
            "SHARED_JWT_SECRET": "jwt",
            "ANTHROPIC_API_KEY": "sk-ant-x",
            "OPENAI_API_KEY": "sk-x",
        }
        with patch.dict(os.environ, secrets):
            env = cc._cli_env(home)
        for name in secrets:
            self.assertNotIn(name, env)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(home))
        self.assertTrue(env["HOME"].startswith(str(home)))


class ExecGuardTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="claude-test-")
        patcher = patch.dict(os.environ, {"CLAUDE_CONFIG_ROOT": self.root})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_model_id_is_validated_before_any_process_starts(self):
        home = cc._config_dir("user-a", "client-1")
        with patch.object(cc.subprocess, "Popen") as popen:
            for bad in ("opus; rm -rf /", "--dangerously-skip-permissions", "a b", ""):
                if bad == "":
                    continue
                with self.assertRaises(ValueError):
                    cc._claude_exec_raw(str(home), "hi", None, bad, 5)
            popen.assert_not_called()

    def test_config_dir_outside_root_is_refused(self):
        outside = tempfile.mkdtemp(prefix="claude-outside-")
        with self.assertRaises(cc.ClaudeAuthError):
            cc._claude_exec_raw(outside, "hi", None, None, 5)

    def test_result_payload_parsing_and_actual_model(self):
        payload = cc._parse_result_payload(json.dumps({
            "type": "result", "is_error": False, "result": "ok",
            "modelUsage": {"claude-haiku-4-5": {"outputTokens": 1}, "claude-opus-5-5": {"outputTokens": 9}},
        }))
        self.assertEqual(cc._pick_actual_model(payload), "claude-opus-5-5")
        self.assertIsNone(cc._parse_result_payload("not json"))

    def test_alias_match_detects_silent_substitution(self):
        self.assertTrue(cc._alias_matches("opus", "claude-opus-5-5[1m]"))
        self.assertFalse(cc._alias_matches("fable", "claude-opus-5-5"))
        self.assertFalse(cc._alias_matches("opus", None))

    def test_login_code_shape_is_strictly_validated(self):
        self.assertTrue(cc.LOGIN_CODE_RE.match("abcDEF123_-.~#stateSTATE456"))
        for bad in ("short", "has space in it", "a;b;c;d;e;f;g;h", "x" * 700, "$(id)$(id)$(id)"):
            self.assertIsNone(cc.LOGIN_CODE_RE.match(bad))

    def test_submit_without_pending_login_is_rejected(self):
        with self.assertRaises(ValueError):
            run(cc.submit_claude_login_code("user-a", "client-1", "abcdefgh12345678"))


class PerUserProviderTests(unittest.TestCase):
    def test_provider_is_only_offered_to_the_user_who_connected(self):
        records = {"user-a": {"config_dir": tempfile.mkdtemp(), "model": "opus"}}

        async def fake_record(client_id, user_id):
            return records.get(user_id)

        with patch.object(cc, "_get_user_record", fake_record):
            mine = run(cc.get_claude_chat_context("client-1", "user-a"))
            teammate = run(cc.get_claude_chat_context("client-1", "user-b"))
            anonymous = run(cc.get_claude_chat_context("client-1", None))
        self.assertEqual(mine["model"], "opus")
        self.assertIsNone(teammate)
        self.assertIsNone(anonymous)

    def test_auth_failure_hides_the_provider_until_reconnect(self):
        home = tempfile.mkdtemp()
        cc._AUTH_FAILED[home] = 1.0
        self.addCleanup(cc._AUTH_FAILED.pop, home, None)

        async def fake_record(client_id, user_id):
            return {"config_dir": home}

        with patch.object(cc, "_get_user_record", fake_record):
            self.assertIsNone(run(cc.get_claude_chat_context("client-1", "user-a")))

    def test_available_providers_passes_user_through_and_keeps_byok(self):
        async def none(*_a, **_k):
            return None

        with patch("app.chat_service.get_openrouter_chat_config", none), \
             patch("app.chat_service.get_codex_chat_context", none), \
             patch("app.chat_service.get_byok_chat_config", AsyncMock(return_value={"provider": "anthropic", "model": "m", "api_key": "k"})), \
             patch("app.chat_service.get_claude_chat_context", AsyncMock(return_value={"config_dir": "/x", "model": "opus"})):
            providers = run(get_available_ai_providers("client-1", "user-a"))
        names = [p["provider"] for p in providers]
        self.assertIn("claude_code", names)
        self.assertIn("anthropic", names)  # Claude BYOK still recoverable/active in backend

    def test_models_list_requires_a_user_and_only_returns_verified(self):
        with self.assertRaises(ValueError):
            run(list_ai_provider_models("client-1", "claude_code", user_id=None))
        models = [
            {"id": "opus", "display_name": "Opus", "supported": True, "default": True},
            {"id": "fable", "display_name": "Fable", "supported": False},
            {"id": "haiku", "display_name": "Haiku", "supported": None},
        ]
        with patch("app.chat_service.list_claude_models", AsyncMock(return_value=models)):
            result = run(list_ai_provider_models("client-1", "claude_code", user_id="user-a"))
        self.assertEqual([m["id"] for m in result], ["opus"])


class StreamProviderTests(unittest.TestCase):
    def _request(self):
        return ChatRequest(question="how many sessions?", client_id="client-1")

    def _tool_result(self):
        return {"server_label": "google_analytics_4", "intent": "x", "selected_tool": "t", "result": {"content": [{"type": "text", "text": "sessions: 42"}]}}

    def test_no_prefetched_tool_result_does_not_call_the_cli(self):
        with patch("app.chat_service.run_claude_exec") as run_exec:
            events = list(stream_claude_code_provider(self._request(), "sys", None, {"claude_config_dir": "/x"}))
        run_exec.assert_not_called()
        self.assertIn("No relevant read-only MCP tool", "".join(events))

    def test_auth_error_is_reported_as_reconnect_not_a_crash(self):
        with patch("app.chat_service.run_claude_exec", side_effect=cc.ClaudeAuthError("Reconnect Claude")):
            events = "".join(stream_claude_code_provider(self._request(), "sys", self._tool_result(), {"claude_config_dir": "/x", "model": "opus"}))
        self.assertIn("Reconnect Claude", events)
        self.assertIn("event: done", events)

    def test_system_prompt_model_and_text_are_passed_through(self):
        result = cc.ClaudeExecResult(text="There were 42 sessions.", actual_model="claude-opus-5-5")
        with patch("app.chat_service.run_claude_exec", return_value=result) as run_exec:
            events = "".join(stream_claude_code_provider(self._request(), "SYSTEM", self._tool_result(), {"claude_config_dir": "/x", "model": "opus"}))
        args, kwargs = run_exec.call_args
        self.assertEqual(kwargs["system_prompt"], "SYSTEM")
        self.assertEqual(kwargs["model"], "opus")
        self.assertEqual(args[0], "/x")
        self.assertIn("42 sessions", events)


class ModelPickerTests(unittest.TestCase):
    def test_real_names_come_from_the_reported_model_id(self):
        self.assertEqual(cc._humanize_claude_model("claude-opus-5-5[1m]"), "Opus 5.5")
        self.assertEqual(cc._humanize_claude_model("claude-haiku-4-5-20251001"), "Haiku 4.5")
        self.assertEqual(cc._humanize_claude_model("claude-sonnet-5"), "Sonnet 5")
        self.assertIsNone(cc._humanize_claude_model("something-else"))

    def test_credits_required_model_is_listed_not_hidden(self):
        def fake_exec(config_dir, prompt, system_prompt=None, model=None):
            return cc.ClaudeExecResult(text="ok", actual_model="claude-opus-5-5")

        def fake_raw(config_dir, prompt, system_prompt, model, timeout):
            if model == "fable":
                raise ValueError("Fable 5.1 requires usage credits. Switch to another model, or manage usage credits at claude.ai")
            if model == "haiku":
                return cc.ClaudeExecResult(text="ok", actual_model="claude-opus-5-5")  # silently substituted
            return cc.ClaudeExecResult(text="ok", actual_model=f"claude-{model}-5")

        with patch.object(cc, "run_claude_exec", fake_exec), patch.object(cc, "_claude_exec_raw", fake_raw),              patch.object(cc, "CLAUDE_MODEL_PROBE_PACING_SECONDS", 0):
            models = {m["id"]: m for m in cc.discover_claude_models("/x")}
        self.assertEqual(list(models), ["default", "opus", "fable", "sonnet", "haiku"])
        self.assertEqual(models["default"]["display_name"], "Default (Opus 5.5)")
        self.assertTrue(models["default"]["default"])
        self.assertEqual(models["fable"]["supported"], "requires_credits")
        self.assertEqual(models["fable"]["display_name"], "Fable 5.1")
        self.assertIs(models["haiku"]["supported"], False)  # substituted -> hidden downstream

    def test_picker_output_includes_credit_flag_and_hides_substituted(self):
        models = [
            {"id": "default", "display_name": "Default (Opus 5.5)", "description": "d", "supported": True, "default": True},
            {"id": "fable", "display_name": "Fable 5.1", "description": "f", "supported": "requires_credits"},
            {"id": "haiku", "display_name": "Haiku", "description": "h", "supported": False},
        ]
        with patch("app.chat_service.list_claude_models", AsyncMock(return_value=models)):
            result = run(list_ai_provider_models("client-1", "claude_code", user_id="user-a"))
        self.assertEqual([m["id"] for m in result], ["default", "fable"])
        self.assertTrue(result[1]["requires_credits"])
        self.assertFalse(result[0]["requires_credits"])


if __name__ == "__main__":
    unittest.main()
