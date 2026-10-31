# SPDX-License-Identifier: AGPL-3.0-only
"""Codex model-list loading: never block the picker on the CLI, never cache a
failure as fresh, one shared discovery per client, progressive results."""

import asyncio
import json
import sys
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import codex as cx
from app.core import utc_now

GOOD = [
    {"id": "gpt-6-luna", "display_name": "Luna", "supported": True, "default": True},
    {"id": "gpt-5.5", "display_name": "5.5", "supported": True, "default": False},
]
NONE_ENTRIES = [
    {"id": "gpt-6-astra", "display_name": "Astra", "supported": None, "default": False},
    {"id": "gpt-5.5", "display_name": "5.5", "supported": None, "default": False},
]


class Store:
    """In-memory connector_oauth_tokens: {client_id: token_payload}."""

    def __init__(self, **tokens):
        self.rows = {cid: json.loads(json.dumps(tok)) for cid, tok in tokens.items()}
        self.upserts = 0

    async def get(self, client_id, _source_type):
        row = self.rows.get(client_id)
        return json.loads(json.dumps(row)) if row else None

    async def upsert(self, client_id, _source_type, token, user_id=None):
        self.upserts += 1
        self.rows[client_id] = json.loads(json.dumps(token))


def token(models=None, checked_minutes_ago=None, **extra):
    row = {"auth_type": "codex_chatgpt", "codex_home": "C:/fake/home", "model": "gpt-6-luna", **extra}
    if models is not None:
        row["available_models"] = models
        row["models_checked_at"] = (utc_now() - timedelta(minutes=checked_minutes_ago or 0)).isoformat()
    return row


class Base(unittest.IsolatedAsyncioTestCase):
    def patch_store(self, store):
        for p in (
            patch("app.codex.get_connector_token", new=store.get),
            patch("app.codex.upsert_connector_token", new=store.upsert),
            patch("app.codex.Path.is_dir", return_value=True),
        ):
            p.start()
            self.addCleanup(p.stop)

    async def asyncTearDown(self):
        for client_id in list(cx._CODEX_REFRESH_TASKS):
            await cx.wait_for_codex_refresh(client_id)


class ListNeverBlocksTests(Base):
    async def test_uncached_returns_immediately_and_discovers_exactly_once(self):
        store = Store(a=token())
        self.patch_store(store)
        calls = []

        def slow_discover(home, on_progress=None):
            calls.append(threading.get_ident())
            time.sleep(0.4)
            return [dict(m) for m in GOOD]

        with patch("app.codex.discover_codex_models", new=slow_discover):
            t0 = time.perf_counter()
            results = await asyncio.gather(*[cx.list_codex_models("a") for _ in range(6)])
            elapsed = time.perf_counter() - t0
            self.assertLess(elapsed, 0.2, "picker read must not wait for the CLI")
            self.assertTrue(all(r == [] for r in results))
            self.assertTrue(cx.is_codex_refresh_running("a"))
            await cx.wait_for_codex_refresh("a")
            self.assertEqual(len(calls), 1, "six concurrent picker reads -> ONE discovery")
            after = await cx.list_codex_models("a")
        self.assertEqual([m["id"] for m in after], ["gpt-6-luna", "gpt-5.5"])

    async def test_fresh_verified_cache_is_instant_and_never_rediscovers(self):
        store = Store(a=token(GOOD, checked_minutes_ago=5))
        self.patch_store(store)
        with patch("app.codex.discover_codex_models") as discover:
            models = await cx.list_codex_models("a")
            await asyncio.sleep(0.05)
        self.assertEqual(models, GOOD)
        discover.assert_not_called()
        self.assertFalse(cx.is_codex_refresh_running("a"))

    async def test_stale_verified_cache_is_served_instantly_then_revalidated(self):
        store = Store(a=token(GOOD[:1], checked_minutes_ago=60 * 25))
        self.patch_store(store)

        def discover(home, on_progress=None):
            time.sleep(0.3)
            return [dict(m) for m in GOOD]

        with patch("app.codex.discover_codex_models", new=discover):
            t0 = time.perf_counter()
            models = await cx.list_codex_models("a")
            self.assertLess(time.perf_counter() - t0, 0.15)
            self.assertEqual([m["id"] for m in models], ["gpt-6-luna"])  # old list, instantly
            await cx.wait_for_codex_refresh("a")
        self.assertEqual([m["id"] for m in store.rows["a"]["available_models"]], ["gpt-6-luna", "gpt-5.5"])

    async def test_slow_discovery_does_not_block_the_event_loop(self):
        store = Store(a=token())
        self.patch_store(store)
        gaps = []

        async def ticker():
            last = time.perf_counter()
            for _ in range(30):
                await asyncio.sleep(0.02)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        def blocking_discover(home, on_progress=None):
            time.sleep(0.5)  # a hung CLI
            return [dict(m) for m in GOOD]

        with patch("app.codex.discover_codex_models", new=blocking_discover):
            await cx.list_codex_models("a")
            await asyncio.gather(ticker(), cx.wait_for_codex_refresh("a"))
        self.assertLess(max(gaps), 0.2, f"event loop stalled: {max(gaps):.2f}s")

    async def test_login_status_check_runs_off_the_event_loop(self):
        cx.CODEX_SESSIONS["u:c"] = {"status": "connected", "home": "C:/x", "model": None}
        self.addCleanup(cx.CODEX_SESSIONS.pop, "u:c", None)
        gaps = []

        async def ticker():
            last = time.perf_counter()
            for _ in range(25):
                await asyncio.sleep(0.02)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        def slow_status(home):
            time.sleep(0.4)
            return True

        with patch("app.codex._codex_login_status_valid", new=slow_status), \
             patch("app.codex._session_id", return_value="u:c"):
            result, _ = await asyncio.gather(cx.get_codex_connection_status("u", "c"), ticker())
        self.assertTrue(result["connected"])
        self.assertLess(max(gaps), 0.2)


class FailureHandlingTests(Base):
    async def test_failed_discovery_never_replaces_a_good_cache_and_backs_off(self):
        store = Store(a=token(GOOD, checked_minutes_ago=60 * 25))
        self.patch_store(store)
        calls = []

        def failing(home, on_progress=None):
            calls.append(1)
            return [dict(m) for m in NONE_ENTRIES]  # default detection failed -> nothing verified

        with patch("app.codex.discover_codex_models", new=failing):
            first = await cx.list_codex_models("a")
            await cx.wait_for_codex_refresh("a")
            row = store.rows["a"]
            self.assertEqual(row["available_models"], GOOD, "good cache must survive a failed round")
            self.assertIn("models_check_failed_at", row)
            second = await cx.list_codex_models("a")  # within the backoff window
            await asyncio.sleep(0.05)
        self.assertEqual(first, GOOD)
        self.assertEqual(second, GOOD)  # still served instantly
        self.assertEqual(len(calls), 1, "no retry storm while the CLI is failing")

    async def test_failed_first_discovery_is_not_stored_as_a_fresh_empty_cache(self):
        # The live bug: 5 unverified entries stamped "fresh" hid every model for 6h.
        store = Store(a=token())
        self.patch_store(store)
        with patch("app.codex.discover_codex_models", return_value=[dict(m) for m in NONE_ENTRIES]):
            self.assertEqual(await cx.list_codex_models("a"), [])
            await cx.wait_for_codex_refresh("a")
        row = store.rows["a"]
        self.assertNotIn("available_models", row)
        self.assertIn("models_check_failed_at", row)

    async def test_retry_happens_after_the_backoff_window(self):
        old_failure = (utc_now() - timedelta(seconds=cx.CODEX_MODELS_RETRY_BACKOFF_SECONDS + 5)).isoformat()
        store = Store(a=token(models_check_failed_at=old_failure))
        self.patch_store(store)
        with patch("app.codex.discover_codex_models", return_value=[dict(m) for m in GOOD]) as discover:
            await cx.list_codex_models("a")
            await cx.wait_for_codex_refresh("a")
        discover.assert_called_once()
        self.assertEqual(len(store.rows["a"]["available_models"]), 2)
        self.assertNotIn("models_check_failed_at", store.rows["a"])

    async def test_the_already_poisoned_live_cache_heals_itself(self):
        # Exactly the row found live: 5 entries, all supported=None, 70 minutes old.
        store = Store(a=token(NONE_ENTRIES, checked_minutes_ago=70))
        self.patch_store(store)
        with patch("app.codex.discover_codex_models", return_value=[dict(m) for m in GOOD]):
            self.assertEqual(await cx.list_codex_models("a"), [])
            await cx.wait_for_codex_refresh("a")
        self.assertEqual([m["id"] for m in store.rows["a"]["available_models"]], ["gpt-6-luna", "gpt-5.5"])

    async def test_a_crashing_cli_degrades_instead_of_raising_into_ask_ai(self):
        store = Store(a=token())
        self.patch_store(store)
        with patch("app.codex.discover_codex_models", side_effect=OSError("cannot launch codex")):
            self.assertEqual(await cx.list_codex_models("a"), [])
            await cx.wait_for_codex_refresh("a")
            self.assertEqual(await cx.list_codex_models("a"), [])  # backoff, still no error
        self.assertIn("models_check_failed_at", store.rows["a"])

    async def test_inconclusive_entries_are_rechecked_soon_not_after_six_hours(self):
        partial = [dict(GOOD[0]), {"id": "gpt-5.5", "supported": None}]
        store = Store(a=token(partial, checked_minutes_ago=15))  # > partial recheck (10 min), < 6h
        self.patch_store(store)
        with patch("app.codex.discover_codex_models", return_value=[dict(m) for m in GOOD]) as discover:
            served = await cx.list_codex_models("a")
            await cx.wait_for_codex_refresh("a")
        self.assertEqual(served, partial)  # instant
        discover.assert_called_once()


class SharedTaskAndProgressTests(Base):
    async def test_concurrent_refresh_callers_share_one_discovery(self):
        store = Store(a=token())
        self.patch_store(store)
        calls = []

        def discover(home, on_progress=None):
            calls.append(1)
            time.sleep(0.2)
            return [dict(m) for m in GOOD]

        with patch("app.codex.discover_codex_models", new=discover):
            results = await asyncio.gather(*[cx.refresh_codex_models("a") for _ in range(4)])
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(len(r) == 2 for r in results))

    async def test_progress_snapshots_publish_early_and_the_final_result_wins(self):
        store = Store(a=token())
        self.patch_store(store)
        seen_partial = asyncio.Event()

        def discover(home, on_progress=None):
            on_progress([dict(GOOD[0])])                     # default verified after ~10s in real life
            time.sleep(0.25)                                 # the slow rest of the round
            on_progress([dict(GOOD[0]), dict(GOOD[1])])
            return [dict(m) for m in GOOD]

        with patch("app.codex.discover_codex_models", new=discover):
            await cx.list_codex_models("a")
            for _ in range(40):                              # a picker poll mid-round already sees the default
                await asyncio.sleep(0.02)
                row = store.rows["a"]
                if row.get("available_models"):
                    seen_partial.set()
                    self.assertTrue(cx.is_codex_refresh_running("a"))
                    mid = await cx.list_codex_models("a")
                    self.assertEqual([m["id"] for m in mid], ["gpt-6-luna"])
                    break
            self.assertTrue(seen_partial.is_set(), "no partial result became visible during the round")
            await cx.wait_for_codex_refresh("a")
        final = store.rows["a"]
        self.assertEqual([m["id"] for m in final["available_models"]], ["gpt-6-luna", "gpt-5.5"])
        self.assertIs(final["models_partial"], False)
        # a late, lower-numbered progress write must never clobber the final result
        await cx._persist_codex_models("a", [dict(GOOD[0])], final=False, seq=1)
        self.assertEqual(len(store.rows["a"]["available_models"]), 2)

    async def test_client_isolation_between_a_b_a(self):
        store = Store(a=token(GOOD[:1], checked_minutes_ago=5, codex_home="C:/a"), b=token(codex_home="C:/b"))
        self.patch_store(store)
        homes = []

        def discover(home, on_progress=None):
            homes.append(home)
            return [{"id": "only-b", "supported": True, "default": True}]

        with patch("app.codex.discover_codex_models", new=discover):
            a1 = await cx.list_codex_models("a")            # instant from cache
            b = await cx.list_codex_models("b")             # empty -> discovers for b only
            await cx.wait_for_codex_refresh("b")
            a2 = await cx.list_codex_models("a")
        self.assertEqual(homes, ["C:/b"])
        self.assertEqual(a1, a2)
        self.assertEqual(store.rows["a"]["available_models"][0]["id"], "gpt-6-luna")
        self.assertEqual(store.rows["b"]["available_models"][0]["id"], "only-b")
        self.assertEqual(b, [])

    async def test_set_selected_model_still_works_from_the_verified_cache(self):
        store = Store(a=token(GOOD, checked_minutes_ago=5))
        self.patch_store(store)
        self.assertEqual(await cx.set_codex_selected_model("a", "gpt-5.5"), {"model": "gpt-5.5"})
        self.assertEqual(store.rows["a"]["model"], "gpt-5.5")


class DiscoveryBoundsTests(unittest.TestCase):
    def test_default_probe_uses_the_short_timeout_not_the_90s_answer_timeout(self):
        with patch("app.codex.get_codex_model_catalog", return_value=[]), \
             patch("app.codex.run_codex_exec", return_value=cx.CodexExecResult(text="ok", actual_model="m")) as run:
            cx.discover_codex_models("C:/x")
        self.assertEqual(run.call_args.kwargs["timeout_seconds"], cx.CODEX_MODEL_DEFAULT_PROBE_TIMEOUT_SECONDS)
        self.assertLess(cx.CODEX_MODEL_DEFAULT_PROBE_TIMEOUT_SECONDS, cx.CODEX_EXEC_TIMEOUT_SECONDS)

    def test_a_timed_out_default_probe_is_retried_once_but_other_errors_are_not(self):
        ok = cx.CodexExecResult(text="ok", actual_model="m0")
        with patch("app.codex.get_codex_model_catalog", return_value=[]),              patch("app.codex.run_codex_exec", side_effect=[ValueError("Codex took too long to respond."), ok]) as run:
            models = cx.discover_codex_models("C:/x")
        self.assertEqual(run.call_count, 2)
        self.assertEqual([m["id"] for m in models], ["m0"])
        with patch("app.codex.get_codex_model_catalog", return_value=[]),              patch("app.codex.run_codex_exec", side_effect=ValueError("Codex CLI is no longer authenticated")) as run:
            cx.discover_codex_models("C:/x")
        self.assertEqual(run.call_count, 1)       # auth failure: no retry
        with patch("app.codex.get_codex_model_catalog", return_value=[]),              patch("app.codex.run_codex_exec", side_effect=ValueError("Codex took too long to respond.")) as run:
            cx.discover_codex_models("C:/x")
        self.assertEqual(run.call_count, 2)       # bounded: never more than one retry

    def test_run_codex_exec_forwards_a_custom_timeout(self):
        with patch("app.codex._codex_exec_raw", return_value=(0, "ok", "model: m")) as raw:
            cx.run_codex_exec("C:/x", "hi", timeout_seconds=12)
        self.assertEqual(raw.call_args.args[3], 12)
        with patch("app.codex._codex_exec_raw", return_value=(0, "ok", "model: m")) as raw:
            cx.run_codex_exec("C:/x", "hi")
        self.assertEqual(raw.call_args.args[3], cx.CODEX_EXEC_TIMEOUT_SECONDS)

    def test_budget_stops_verifying_and_leaves_the_rest_unverified(self):
        catalog = [{"id": f"m{i}", "display_name": f"m{i}", "description": "", "reasoning_support": None, "tool_support": None} for i in range(4)]
        with patch("app.codex.get_codex_model_catalog", return_value=catalog), \
             patch("app.codex.run_codex_exec", return_value=cx.CodexExecResult(text="ok", actual_model="m0")), \
             patch("app.codex.CODEX_DISCOVERY_BUDGET_SECONDS", -1), \
             patch("app.codex.verify_codex_model_selectable") as verify, \
             patch("app.codex.time.sleep"):
            models = cx.discover_codex_models("C:/x")
        verify.assert_not_called()
        by_id = {m["id"]: m for m in models}
        self.assertTrue(by_id["m0"]["supported"] and by_id["m0"]["default"])
        self.assertTrue(all(by_id[i]["supported"] is None for i in ("m1", "m2", "m3")))

    def test_a_failing_progress_callback_cannot_break_discovery(self):
        catalog = [{"id": "m1", "display_name": "m1", "description": "", "reasoning_support": None, "tool_support": None}]
        with patch("app.codex.get_codex_model_catalog", return_value=catalog), \
             patch("app.codex.run_codex_exec", return_value=cx.CodexExecResult(text="ok", actual_model="m0")), \
             patch("app.codex.verify_codex_model_selectable", return_value=True), \
             patch("app.codex.time.sleep"):
            models = cx.discover_codex_models("C:/x", on_progress=MagicMock(side_effect=RuntimeError("boom")))
        self.assertEqual([m["id"] for m in models], ["m0", "m1"])


class StabilityTests(Base):
    async def test_inconclusive_result_never_revokes_a_previously_verified_model(self):
        store = Store(a=token(GOOD, checked_minutes_ago=60 * 25))
        self.patch_store(store)
        flaky = [
            {"id": "gpt-6-luna", "display_name": "Luna", "supported": True, "default": True},
            {"id": "gpt-5.5", "display_name": "5.5", "supported": None, "default": False},  # probe timed out this time
        ]
        with patch("app.codex.discover_codex_models", return_value=flaky):
            await cx.list_codex_models("a")
            await cx.wait_for_codex_refresh("a")
        by_id = {m["id"]: m for m in store.rows["a"]["available_models"]}
        self.assertIs(by_id["gpt-5.5"]["supported"], True)

    async def test_an_explicit_rejection_does_revoke(self):
        store = Store(a=token(GOOD, checked_minutes_ago=60 * 25))
        self.patch_store(store)
        rejected = [
            {"id": "gpt-6-luna", "display_name": "Luna", "supported": True, "default": True},
            {"id": "gpt-5.5", "display_name": "5.5", "supported": False, "default": False},  # CLI said "not supported"
        ]
        with patch("app.codex.discover_codex_models", return_value=rejected):
            await cx.list_codex_models("a")
            await cx.wait_for_codex_refresh("a")
        by_id = {m["id"]: m for m in store.rows["a"]["available_models"]}
        self.assertIs(by_id["gpt-5.5"]["supported"], False)

    async def test_progress_snapshot_never_shrinks_the_cached_list_mid_round(self):
        store = Store(a=token(GOOD, checked_minutes_ago=60 * 25))
        self.patch_store(store)
        seen = {}

        def discover(home, on_progress=None):
            on_progress([dict(GOOD[0])])          # only the default verified so far
            time.sleep(0.3)
            return [dict(m) for m in GOOD]

        async def watcher():
            for _ in range(15):
                await asyncio.sleep(0.02)
                seen[time.perf_counter()] = [m["id"] for m in store.rows["a"]["available_models"] if m.get("supported")]

        with patch("app.codex.discover_codex_models", new=discover):
            await cx.list_codex_models("a")
            await asyncio.gather(watcher(), cx.wait_for_codex_refresh("a"))
        self.assertTrue(seen)
        self.assertTrue(all(len(ids) == 2 for ids in seen.values()), f"list shrank mid-round: {list(seen.values())}")

    def test_merge_unit(self):
        prev = [{"id": "a", "supported": True, "default": True}, {"id": "b", "supported": True}, {"id": "c", "supported": False}]
        final = cx._merge_with_verified(prev, [{"id": "a", "supported": True, "default": True}, {"id": "b", "supported": None}, {"id": "c", "supported": None}], final=True)
        self.assertEqual([(m["id"], m["supported"]) for m in final], [("a", True), ("b", True), ("c", None)])
        partial = cx._merge_with_verified(prev, [{"id": "a", "supported": True, "default": True}], final=False)
        self.assertEqual([m["id"] for m in partial], ["a", "b"])          # b carried, rejected c is not
        self.assertFalse(partial[1].get("default"))

    async def test_loading_stays_true_while_a_failed_round_waits_out_its_backoff(self):
        recent = utc_now().isoformat()
        old = (utc_now() - timedelta(seconds=cx.CODEX_MODELS_RETRY_BACKOFF_SECONDS + 5)).isoformat()
        store = Store(a=token(models_check_failed_at=recent), b=token(models_check_failed_at=old), c=token())
        self.patch_store(store)
        self.assertTrue(await cx.codex_models_loading("a", []))            # waiting to retry -> keep polling
        self.assertFalse(await cx.codex_models_loading("a", GOOD))         # models exist -> done
        self.assertFalse(await cx.codex_models_loading("b", []))           # backoff over: the next list starts the retry
        self.assertFalse(await cx.codex_models_loading("c", []))           # nothing running, nothing failed

    async def test_next_poll_after_the_backoff_retries_without_user_action(self):
        old = (utc_now() - timedelta(seconds=cx.CODEX_MODELS_RETRY_BACKOFF_SECONDS + 5)).isoformat()
        store = Store(a=token(models_check_failed_at=old))
        self.patch_store(store)
        with patch("app.codex.discover_codex_models", return_value=[dict(m) for m in GOOD]) as discover:
            self.assertEqual(await cx.list_codex_models("a"), [])          # the poll itself starts the retry
            self.assertTrue(await cx.codex_models_loading("a", []))        # ...and reports it
            await cx.wait_for_codex_refresh("a")
            after = await cx.list_codex_models("a")
        discover.assert_called_once()
        self.assertEqual([m["id"] for m in after], ["gpt-6-luna", "gpt-5.5"])


class ModelsEndpointFlagTests(unittest.IsolatedAsyncioTestCase):
    async def test_codex_response_carries_loading_and_other_providers_do_not(self):
        from app import main
        from app.schemas import AuthUser

        user = AuthUser(sub="user-1", email="u@example.com", role="authenticated", access_token="t")
        with patch("app.main.load_client_context", new=AsyncMock()), \
             patch("app.main.list_ai_provider_models", new=AsyncMock(return_value=[])), \
             patch("app.main.codex_models_loading", new=AsyncMock(return_value=True)):
            codex = await main.ai_provider_models("openai_codex", "client-1", None, user)
            claude = await main.ai_provider_models("claude_code", "client-1", None, user)
            router = await main.ai_provider_models("openrouter", "client-1", None, user)
        self.assertTrue(codex["loading"])
        self.assertNotIn("loading", claude)  # Claude's response is byte-for-byte what it was
        self.assertNotIn("loading", router)


if __name__ == "__main__":
    unittest.main()
