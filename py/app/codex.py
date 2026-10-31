# SPDX-License-Identifier: AGPL-3.0-only
"""Isolated Codex CLI authentication for ChatGPT-backed OpenAI connections."""

import asyncio
import hashlib
import itertools
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import debug_log, urlsafe_token, utc_now
from .supabase_admin import (
    delete_connector_token,
    get_connector_token,
    mark_data_source_disconnected,
    upsert_connector_token,
    upsert_data_source_connected,
)

OPENAI_SOURCE_TYPE = "openai_ai"
CODEX_TOKEN_SOURCE_TYPE = "openai_codex"
# There is deliberately NO codex-specific "default model" constant here
# anymore (there used to be one, hardcoded to "gpt-4.1-mini"). Confirmed
# live against an authenticated ChatGPT-backed session on 2026-09-23: that
# value is an OpenAI Responses-API model id, not one this auth mode accepts
# at all ("The 'gpt-4.1-mini' model is not supported when using Codex with a
# ChatGPT account.", HTTP 400). It was never a real Codex default - it was a
# frontend-only placeholder (web/src/components/AiDrawer.jsx's old
# DEFAULT_MODELS.openai) that got persisted into this connector's stored
# `model` field at OAuth-login time and never corrected afterward. The real
# default is only knowable by asking the authenticated CLI itself - see
# discover_codex_models() below - so "no stored model yet" now means exactly
# that (None), and run_codex_exec() already treats an empty model as "let
# Codex pick its own real default" rather than forcing anything. byok.py's
# own DEFAULT_OPENAI_MODEL is a separate, unrelated constant for the raw
# OpenAI-API-key path (a real, different auth mode where gpt-4.1-mini is a
# genuinely valid model) and is untouched by this.
CODEX_SESSIONS: dict[str, dict[str, Any]] = {}
URL_RE = re.compile(r"https?://[^\s<>()]+")
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
DEVICE_CODE_RE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{5}\b", re.IGNORECASE)
# `codex exec` (non-interactive, no --json) prints a plaintext session banner to
# stderr before the turn output, e.g. "model: gpt-6-luna" - confirmed live
# against an authenticated CODEX_HOME on 2026-09-23 (`codex login status` ->
# "Logged in using ChatGPT"): a default-model run reported `model: gpt-6-luna`
# on this line while stdout stayed clean prose, and a run forced with
# `-m gpt-4.1-mini` failed outright with "The 'gpt-4.1-mini' model is not
# supported when using Codex with a ChatGPT account." This is the only signal
# available for which model actually answered a given exec call, since Codex's
# own internal model family (gpt-6-*) is never the one this connector stores.
CODEX_MODEL_LINE_RE = re.compile(r"(?m)^model:\s*(\S+)\s*$")


def _session_id(user_id: str, client_id: str) -> str:
    return hashlib.sha256(f"{user_id}:{client_id}".encode("utf-8")).hexdigest()[:32]


def _codex_home(user_id: str, client_id: str) -> Path:
    configured_root = os.getenv("CODEX_HOME_ROOT")
    if os.name == "nt" and configured_root and configured_root.startswith("/") and not configured_root.startswith("//"):
        # Linux deploys commonly use /var/lib/..., but on Windows that becomes
        # a drive-root path such as D:\var\... and is often not writable from
        # a normal dev shell. Keep local Codex auth state inside the service
        # workspace unless the user configured a real Windows path.
        configured_root = None
    if configured_root:
        default_root = Path(configured_root)
    elif os.name == "nt":
        # NOT Path.cwd()/".codex-runners" anymore - Path.cwd() here is
        # py/ INSIDE this git repo, and Codex CLI walks UP from wherever
        # it's run looking for an AGENTS.md/project-doc to load as its own
        # system instructions (the same mechanism Claude Code's CLAUDE.md
        # uses) - confirmed directly: it found and loaded this repo's own
        # AGENTS.md (rules for building Braidscope itself) and started
        # answering as if bound by those engineering rules instead of just
        # summarizing the MCP data it was handed, which is exactly why real
        # questions were coming back as generic "ready to help" replies
        # instead of real answers. A per-user app-data directory has no
        # such file anywhere in its ancestry.
        root_base = os.getenv("LOCALAPPDATA") or str(Path.home())
        default_root = Path(root_base) / "Braidscope" / "codex-runners"
    else:
        default_root = Path.cwd() / ".codex-runners"
    root = default_root.resolve()
    identity = hashlib.sha256(f"{user_id}:{client_id}".encode("utf-8")).hexdigest()
    home = root / identity
    home.mkdir(parents=True, exist_ok=True)
    try:
        home.chmod(0o700)
    except OSError:
        pass
    return home


def _codex_command() -> str:
    command = os.getenv("CODEX_CLI_COMMAND", "codex").strip()
    if not command:
        raise ValueError("CODEX_CLI_COMMAND is empty")

    resolved = shutil.which(command)
    if os.name == "nt":
        # npm exposes PowerShell, cmd and shell shims on Windows. Python's
        # create_subprocess_exec cannot reliably launch the .ps1 shim directly,
        # so prefer the .cmd shim that cmd/PowerShell users normally get via
        # PATHEXT.
        if resolved and resolved.lower().endswith(".ps1"):
            cmd_shim = f"{resolved[:-4]}.cmd"
            if os.path.exists(cmd_shim):
                resolved = cmd_shim
        elif not resolved and not command.lower().endswith(".cmd"):
            resolved = shutil.which(f"{command}.cmd")

    if resolved is None:
        raise ValueError(
            "The official Codex CLI is not installed on the backend. "
            "Install it and make `codex` available on PATH."
        )
    return resolved


def _popen_args(command: str, args: list[str]) -> list[str]:
    """Windows-only fix: npm installs its CLI shims as .cmd batch files, and
    launching a .cmd directly via subprocess.Popen(['codex.cmd', ...]) (no
    shell) is unreliable - verified directly on this machine: the exact same
    login call that works instantly through a real shell (PowerShell's `&`,
    or a plain terminal) intermittently either fails inside the Codex binary
    itself with a raw network error ("error sending request for url ...",
    a Rust reqwest-level failure, not a Braidscope/Python one) or, if
    terminated, leaves an orphaned grandchild process holding its stdout/
    stderr pipes open forever (cmd.exe spawns the real process and
    terminate() only ever signals that wrapper, never its child) - both
    symptoms line up with what the Codex login/exec flow has been showing
    intermittently. Routing through `cmd.exe /c` explicitly (still with a
    plain argv list, never a shell-interpolated string - no injection risk
    since none of this touches shell=True) is exactly how a real shell
    already invokes it successfully, and matches the direct comparison this
    was verified against. No-op on Linux/macOS (production), where `codex`
    resolves straight to the real executable with no batch-file layer to
    route around at all.
    """
    if os.name == "nt" and command.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", command, *args]
    return [command, *args]


def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
    """process.terminate()/kill() alone only ever signals the immediate
    child - on Windows that's the cmd.exe wrapper _popen_args() routes
    through, never the real grandchild process it spawns, which is exactly
    what was left running as an orphan (holding stdout/stderr open, so a
    caller doing process.communicate() after terminate() hung forever
    waiting for EOF that was never going to come) in direct testing. Killing
    the whole tree by PID via taskkill is the standard, reliable fix for
    that on Windows; plain terminate() already works fine on Linux/macOS
    (no wrapper process there), so this only branches for os.name == 'nt'.
    """
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.terminate()
    except OSError:
        pass


def _clean_url(value: str) -> str:
    return ANSI_RE.sub("", value).rstrip(".,;)]}\x1b")


def _is_poisoned_url(value: str | None) -> bool:
    if not value:
        return False
    return "\x1b" in value or "%1B" in value.upper()


def _read_stream(session: dict[str, Any], stream: Any, stream_name: str = "?") -> None:
    if stream is None:
        return
    for line in iter(stream.readline, b""):
        text = ANSI_RE.sub("", line.decode("utf-8", errors="replace")).strip()
        if not text:
            continue
        session["last_output"] = text[-1000:]
        debug_log("    codex login %s: %r", stream_name, text[:300])
        match = URL_RE.search(text)
        if match and not session.get("authorization_url"):
            session["authorization_url"] = _clean_url(match.group(0))
            debug_log("    codex login: captured authorization_url=%s", session["authorization_url"])
        code_match = DEVICE_CODE_RE.search(text)
        if code_match and not session.get("user_code"):
            session["user_code"] = code_match.group(0).upper()
            debug_log("    codex login: captured user_code=%s", session["user_code"])
    try:
        stream.close()
    except OSError:
        pass


def _codex_login_status_valid(codex_home: str) -> bool:
    """Verify the CLI actually reports a logged-in state in its CODEX_HOME.

    A clean process exit is not enough: a finished interactive sequence can still
    leave the CODEX_HOME un-authenticated or stale. The command-line client itself
    is the source of truth here, so we query `codex login status` in the target
    home before treating the login as ready.
    """
    home = Path(codex_home)
    if not home.is_dir():
        return False
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)
    working_dir = home / "workspace"
    working_dir.mkdir(mode=0o700, exist_ok=True)
    process = subprocess.Popen(
        _popen_args(_codex_command(), ["login", "status"]),
        cwd=str(working_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        _terminate_tree(process)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return False

    output = f"{stdout or ''}\n{stderr or ''}".strip()
    cleaned = ANSI_RE.sub("", output).lower()
    if not cleaned:
        return False
    if any(marker in cleaned for marker in ("not logged in", "login required", "please sign in", "expired")):
        return False
    return process.returncode == 0


def _read_login_output(session_id: str, process: subprocess.Popen[bytes]) -> None:
    session = CODEX_SESSIONS[session_id]
    try:
        readers = (
            threading.Thread(target=_read_stream, args=(session, process.stdout, "stdout"), daemon=True),
            threading.Thread(target=_read_stream, args=(session, process.stderr, "stderr"), daemon=True),
        )
        for reader in readers:
            reader.start()
        process.wait()
        for reader in readers:
            reader.join(timeout=1)
        session["status"] = "connected" if process.returncode == 0 else "error"
        if process.returncode == 0 and not _codex_login_status_valid(session["home"]):
            session["status"] = "error"
            session["error"] = "Codex login exited, but the CLI is not logged in for this CODEX_HOME"
        debug_log(
            "  codex login process exited: pid=%d returncode=%d final_status=%s user_code_captured=%s",
            process.pid, process.returncode, session["status"], bool(session.get("user_code")),
        )
        if process.returncode != 0:
            session["error"] = session.get("last_output") or "Codex login failed"
    except Exception as exc:
        session["status"] = "error"
        session["error"] = f"Codex login runner failed: {type(exc).__name__}"
        debug_log("  codex login reader thread FAILED: %s: %s", type(exc).__name__, exc)


async def start_codex_login(
    user_id: str,
    client_id: str,
    model: str | None = None,
    oauth_context: str | None = None,
) -> dict[str, Any]:
    command = _codex_command()
    session_id = _session_id(user_id, client_id)
    default_mode = "browser" if os.name == "nt" else "device"
    mode = os.getenv("CODEX_LOGIN_MODE", default_mode).strip().lower()
    debug_log("start_codex_login called: client_id=%s session_id=%s mode=%s command=%s", client_id, session_id, mode, command)

    existing = CODEX_SESSIONS.get(session_id)
    debug_log(
        "  existing session: %s",
        {"status": existing["status"], "has_url": bool(existing.get("authorization_url")), "has_code": bool(existing.get("user_code"))} if existing else None,
    )
    stale_pending = False
    if existing and existing["status"] == "pending":
        process = existing.get("process")
        # Two independent staleness signals, on top of the existing
        # poisoned-URL check: (1) the reader thread's process is already
        # dead but nothing ever flipped status off "pending" - shouldn't
        # normally happen (_read_login_output always sets a final status),
        # but a dead process can never produce a code, so never trust its
        # leftover state; (2) in device mode, a real login always prints
        # the code within a couple seconds of the URL (verified directly:
        # both appear in the same short burst of CLI output) - if the URL
        # showed up but no code followed for 20+ seconds, the CLI process
        # is stuck (a network-level failure, e.g. this exact account's
        # authorization being invalidated mid-flow - see the OpenAI
        # community thread on Codex CLI OAuth session management for why
        # that's an OpenAI-side gap with no visibility into it) - retrying
        # with a fresh process is strictly better than returning the same
        # code-less "pending" state to the user forever.
        if process and process.poll() is not None:
            stale_pending = True
        elif (
            mode == "device"
            and existing.get("authorization_url")
            and not existing.get("user_code")
            and (utc_now() - existing["started_at"]).total_seconds() > 20
        ):
            stale_pending = True
    if existing and existing["status"] == "pending" and (
        stale_pending or _is_poisoned_url(existing.get("authorization_url"))
    ):
        process = existing.get("process")
        if process:
            _terminate_tree(process)
        CODEX_SESSIONS.pop(session_id, None)
        existing = None

    if existing and existing["status"] == "pending":
        response = {"session_id": session_id, "status": "pending"}
        if existing.get("authorization_url"):
            response["authorization_url"] = existing["authorization_url"]
        if existing.get("user_code"):
            response["user_code"] = existing["user_code"]
        elif mode == "browser":
            response["opened_in_browser"] = True
        debug_log("  start_codex_login returning (reused existing pending session): %s", response)
        return response

    home = _codex_home(user_id, client_id)
    working_dir = home / "workspace"
    working_dir.mkdir(mode=0o700, exist_ok=True)
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)
    # File storage keeps each runner's auth state under its own private home.
    env.setdefault("CODEX_CLI_AUTH_CREDENTIALS_STORE", "file")

    default_mode = "browser" if os.name == "nt" else "device"
    args = [command, "login"]
    if mode == "device":
        args.append("--device-auth")
    elif mode != "browser":
        raise ValueError("CODEX_LOGIN_MODE must be browser or device")

    process = subprocess.Popen(
        _popen_args(command, args[1:]),
        cwd=str(working_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    CODEX_SESSIONS[session_id] = {
        "user_id": user_id,
        "client_id": client_id,
        # Whatever the frontend passed at connect time (now no longer a
        # hardcoded "gpt-4.1-mini" placeholder - see the module docstring
        # above), or None. This gets overwritten with the real, verified
        # default the moment discovery runs after login finishes
        # (finalize_codex_login schedules that in main.py); it is never
        # itself treated as an authoritative model.
        "model": (model or "").strip() or None,
        "oauth_context": oauth_context or "app",
        "status": "pending",
        "process": process,
        "home": str(home),
        "started_at": utc_now(),
        # Non-secret, per-connection-ATTEMPT identifier - deliberately
        # distinct from session_id, which is sha256(user_id:client_id) and
        # therefore IDENTICAL across a reconnect by the same user for the
        # same client. main.py uses this (not session_id) to decide whether
        # a models-discovery refresh has already been scheduled for THIS
        # specific login, so a genuine reconnect (different ChatGPT
        # account/plan, e.g.) still gets a fresh refresh rather than being
        # silently skipped because session_id was already seen before.
        "login_attempt_id": urlsafe_token(16),
    }
    threading.Thread(target=_read_login_output, args=(session_id, process), daemon=True).start()

    url_found_at: int | None = None
    for i in range(100):
        await asyncio.sleep(0.1)
        session = CODEX_SESSIONS[session_id]
        if session.get("authorization_url"):
            if url_found_at is None:
                url_found_at = i
            # Real logins print the device code within a couple lines of
            # the URL, in the same short burst of CLI output (verified
            # directly against the live CLI: both appear together, code
            # right after the URL). Returning the instant the URL alone
            # shows up was a genuine race - on a fast run the URL could be
            # captured by one poll tick before the code line had even been
            # written yet, handing the frontend a response with a URL but
            # no code to show on the card, exactly the reported symptom.
            # Waiting up to 2 more seconds (20 more 0.1s ticks) for the
            # code specifically, once the URL is already in hand, closes
            # that window without meaningfully slowing down the normal
            # case (it returns immediately once both are present).
            if session.get("user_code") or i - url_found_at >= 20 or mode != "device":
                response = {
                    "session_id": session_id,
                    "status": "pending",
                    "authorization_url": session["authorization_url"],
                }
                if session.get("user_code"):
                    response["user_code"] = session["user_code"]
                else:
                    debug_log("  start_codex_login: returning WITHOUT user_code after waiting - last_output=%r", session.get("last_output"))
                debug_log("  start_codex_login returning (new session): %s", response)
                return response
        if session["status"] == "error":
            raise ValueError(session["error"])
        if mode == "browser" and i >= 10:
            return {
                "session_id": session_id,
                "status": "pending",
                "opened_in_browser": True,
            }

    raise ValueError(
        "Codex login did not provide an authorization URL. "
        "Check the Codex CLI installation and backend logs."
    )


def _stored_codex_home_exists(token: dict[str, Any]) -> bool:
    """The one usability test for a persisted Codex connection, shared by the
    Integrations status and Ask AI's provider lookup so they cannot disagree."""
    codex_home = token.get("codex_home")
    return bool(codex_home) and Path(codex_home).is_dir()


async def get_codex_connection_status(
    user_id: str,
    client_id: str,
) -> dict[str, Any]:
    session_id = _session_id(user_id, client_id)
    session = CODEX_SESSIONS.get(session_id)
    if session and session["status"] == "pending":
        return {
            "connected": False,
            "status": "pending",
            "session_id": session_id,
            "user_code": session.get("user_code"),
            "authorization_url": session.get("authorization_url"),
        }
    if session and session["status"] == "error":
        return {"connected": False, "status": "error", "error": session.get("error")}
    if session and session["status"] == "connected":
        if not await asyncio.to_thread(_codex_login_status_valid, session["home"]):
            session["status"] = "error"
            session["error"] = "Codex CLI is no longer authenticated in its CODEX_HOME"
            return {"connected": False, "status": "error", "error": session["error"]}
        return {
            "connected": True,
            "status": "connected",
            "session_id": session_id,
            "storage": "codex_home",
            "model": session["model"],
            "login_attempt_id": session.get("login_attempt_id"),
        }
    token = await get_connector_token(client_id, CODEX_TOKEN_SOURCE_TYPE)
    if token and token.get("auth_type") == "codex_chatgpt":
        if not _stored_codex_home_exists(token):
            # The row survived but the CLI login it points at is gone (e.g.
            # the container was recreated without a persistent CODEX_HOME).
            # Ask AI (get_codex_chat_context) already refuses such a row, so
            # reporting "connected" here made Integrations and Ask AI disagree.
            return {
                "connected": False,
                "status": "needs_reconnect",
                "needs_reconnect": True,
                "error": "Your ChatGPT sign-in is no longer available on the server. Reconnect to continue.",
                "storage": "codex_home",
                "model": (token.get("model") or "").strip() or None,
                "available_models": [],
            }
        available_models = token.get("available_models")
        return {
            "connected": True,
            "status": "connected",
            "storage": "codex_home",
            # None (not a fake "gpt-4.1-mini") until either a real user pick
            # or a discovery pass has actually run - see the module
            # docstring at the top of this file for why a fabricated value
            # here was the whole original bug.
            "model": (token.get("model") or "").strip() or None,
            "available_models": available_models if isinstance(available_models, list) else [],
            "models_checked_at": token.get("models_checked_at"),
        }
    return {"connected": False, "status": "disabled", "storage": "none", "model": None, "available_models": []}


async def finalize_codex_login(session_id: str) -> bool:
    session = CODEX_SESSIONS.get(session_id)
    if not session or session["status"] != "connected":
        return False
    if not await asyncio.to_thread(_codex_login_status_valid, session["home"]):
        session["status"] = "error"
        session["error"] = "Codex login was not fully authenticated in its CODEX_HOME"
        return False
    if session.get("finalized"):
        # This login attempt was already persisted and announced. The status
        # endpoint calls this on every poll while the session stays in memory;
        # repeating the connect step would post a new "connected" notification
        # each time. The login-validity check above still runs on every call.
        return True
    await upsert_connector_token(
        session["client_id"],
        CODEX_TOKEN_SOURCE_TYPE,
        {
            "auth_type": "codex_chatgpt",
            "codex_home": session["home"],
            "model": session["model"],
            "created_at": utc_now().isoformat(),
        },
        user_id=session["user_id"],
    )
    await upsert_data_source_connected(
        session["client_id"],
        OPENAI_SOURCE_TYPE,
        user_id=session["user_id"],
    )
    session["finalized"] = True  # only after every step succeeded, so a failure is retried
    return True


async def disconnect_codex(user_id: str, client_id: str) -> None:
    session_id = _session_id(user_id, client_id)
    session = CODEX_SESSIONS.pop(session_id, None)
    if session and session["status"] == "pending":
        process = session.get("process")
        if process:
            _terminate_tree(process)
    await delete_connector_token(client_id, CODEX_TOKEN_SOURCE_TYPE)
    await mark_data_source_disconnected(client_id, OPENAI_SOURCE_TYPE, user_id=user_id)


# ---------------------------------------------------------------------------
# Ask AI execution bridge - the ONLY new surface added on top of the existing
# login/status/disconnect functions above. Nothing above this line changed.
# ---------------------------------------------------------------------------

CODEX_EXEC_TIMEOUT_SECONDS = float(os.getenv("OPENAI_CHAT_TIMEOUT_SECONDS", "90"))


async def get_codex_chat_context(client_id: str) -> dict[str, Any] | None:
    """The client-scoped lookup Ask AI uses to decide "is ChatGPT/Codex the
    connected provider for THIS client, and if so, from which Codex home".

    Deliberately reads the persisted connector_oauth_tokens row by client_id
    alone (via the existing get_connector_token(client_id, source_type)
    helper every other provider already uses - openrouter.py, byok.py) -
    never by the CURRENT request's user_id. That's what makes this safe for
    client isolation across shared clients: whichever teammate originally
    connected ChatGPT for this client wrote codex_home into THIS row, and
    any other teammate who later asks Ask AI a question on the SAME client
    must resolve to that same stored home, not one re-derived from their own
    user_id (which would point at a different, never-authenticated
    directory - _codex_home() is keyed by (user_id, client_id) and is only
    ever used for the LOGIN flow, never for execution).
    """
    token = await get_connector_token(client_id, CODEX_TOKEN_SOURCE_TYPE)
    if not token or token.get("auth_type") != "codex_chatgpt":
        return None
    if not _stored_codex_home_exists(token):
        return None
    return {
        "codex_home": token["codex_home"],
        # None (never a fabricated "gpt-4.1-mini") when no verified model has
        # been stored yet - run_codex_exec() already treats an empty model as
        # "use the CLI's own real default", which is the only honest option
        # when nothing has been verified/selected for this client yet.
        "model": (token.get("model") or "").strip() or None,
    }


@dataclass(frozen=True)
class CodexExecResult:
    """Result of one `codex exec` turn.

    `actual_model` is the model Codex itself reports having used for this
    turn (parsed from its stderr banner - see CODEX_MODEL_LINE_RE), which is
    frequently NOT the same string as the `model` this function was called
    with (see that parameter's docstring below). It is None only when the
    banner line could not be found, e.g. an unexpected CLI output format;
    callers must not treat that as an error on its own.
    """

    text: str
    actual_model: str | None


# Substring of the CLI's own explicit rejection message when a `-m` value is
# not usable under ChatGPT-account auth - confirmed live twice, verbatim
# except for the model name: "The 'gpt-4.1-mini' model is not supported when
# using Codex with a ChatGPT account." and "The 'gpt-6-astra' model is not
# supported when using Codex with a ChatGPT account." Used only to recognize
# this one specific, well-understood rejection class for the safety-net
# retry in run_codex_exec() - never to swallow a different, unrelated error.
CODEX_MODEL_NOT_SUPPORTED_MARKER = "is not supported when using codex with a chatgpt account"


def _codex_exec_raw(
    codex_home: str,
    prompt: str,
    model: str | None,
    timeout_seconds: float,
) -> tuple[int, str, str]:
    """Low-level, single `codex exec` subprocess call shared by
    run_codex_exec() (a real answer, using the CLI's own already-persisted
    ChatGPT auth) and the model-discovery/verification functions below (a
    minimal probe turn). Returns (returncode, stdout, stderr), both decoded
    and ANSI-stripped; raises ValueError only on a hard timeout, after
    killing the process tree - a rejected model is a normal, expected
    non-zero return code here, not an exception, so callers that just need a
    yes/no ("is this model usable") don't need to catch one for that case.

    Uses `codex exec` (the CLI's documented non-interactive, single-shot
    mode - the interactive TUI is never touched) against an
    already-authenticated CODEX_HOME: it already holds that session's
    credentials on disk, so exec reuses them the way the CLI would for any
    other authenticated command. Nothing secret is passed as a command
    argument or read into this process - only the home directory PATH goes
    over env (CODEX_HOME), the same mechanism start_codex_login() already
    uses; the credential material inside that directory is never opened,
    copied, or logged here.
    """
    command = _codex_command()
    home = Path(codex_home)
    working_dir = home / "workspace"
    working_dir.mkdir(mode=0o700, exist_ok=True)

    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)

    exec_args = ["exec", "--skip-git-repo-check"]
    if model:
        exec_args.extend(["-m", model])
    exec_args.append(prompt)

    # Popen (not subprocess.run) deliberately, so a timeout can be cleaned
    # up with _terminate_tree() - subprocess.run's own built-in timeout
    # handling only ever kills the Popen object IT created, which on
    # Windows is just the cmd.exe wrapper _popen_args() routes through, not
    # the real grandchild process. Left as subprocess.run before, a timed-
    # out Codex call would leave an orphaned process running in the
    # background indefinitely.
    popen_args = _popen_args(command, exec_args)
    debug_log("    _codex_exec_raw: launching args=%s cwd=%s timeout=%.0fs", popen_args[:4], working_dir, timeout_seconds)
    launched_at = time.monotonic()
    process = subprocess.Popen(
        popen_args,
        cwd=str(working_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        debug_log("    _codex_exec_raw: pid=%d TIMED OUT after %.2fs - killing tree", process.pid, time.monotonic() - launched_at)
        _terminate_tree(process)
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        raise ValueError("Codex took too long to respond. Try a narrower question.") from exc

    elapsed = time.monotonic() - launched_at
    stdout = ANSI_RE.sub("", stdout_bytes.decode("utf-8", errors="replace")).strip()
    stderr = ANSI_RE.sub("", stderr_bytes.decode("utf-8", errors="replace")).strip()
    debug_log(
        "    _codex_exec_raw: pid=%d exited in %.2fs returncode=%d stdout_len=%d stderr_preview=%r",
        process.pid, elapsed, process.returncode, len(stdout), stderr[:500],
    )
    return process.returncode, stdout, stderr


def run_codex_exec(
    codex_home: str,
    prompt: str,
    model: str | None = None,
    timeout_seconds: float | None = None,
) -> CodexExecResult:
    """Runs one non-interactive Codex CLI turn against an already-authenticated
    CODEX_HOME and returns its final text output plus the model Codex actually
    used.

    Unlike an earlier version of this function, `model` (when given) IS now
    passed to the CLI as `-m`. That used to be deliberately skipped, because
    the one model this connector ever stored ("gpt-4.1-mini", a frontend
    placeholder - see the module docstring) was itself unusable and forcing
    it broke every call. Now that callers are expected to only ever pass a
    model that came from discover_codex_models()'s real, live verification
    (see below), forcing it is what makes explicit model selection
    (Phase 7) actually reach Codex instead of being silently ignored.

    A forced model can still be rejected live even after verification - an
    account's available models can change between a cache refresh and this
    call (plan change, model deprecated, etc.). If that happens, this
    retries once, unforced, so one stale entry degrades to "Codex's own
    current default" rather than hard-failing the user's question - the
    retry is narrowly scoped to CODEX_MODEL_NOT_SUPPORTED_MARKER specifically
    so an unrelated real error is never swallowed or retried away.
    """
    debug_log("  run_codex_exec: model=%s prompt_len=%d", model, len(prompt))
    timeout = timeout_seconds or CODEX_EXEC_TIMEOUT_SECONDS
    returncode, stdout, stderr = _codex_exec_raw(codex_home, prompt, model, timeout)

    if returncode != 0:
        error_text = stderr or stdout or "Codex CLI exited with an error"
        if model and CODEX_MODEL_NOT_SUPPORTED_MARKER in error_text.lower():
            debug_log(
                "  run_codex_exec: forced model=%s rejected live (%s) - retrying once unforced",
                model, error_text[:200],
            )
            returncode, stdout, stderr = _codex_exec_raw(codex_home, prompt, None, timeout)
            if returncode != 0:
                raise ValueError(stderr or stdout or "Codex CLI exited with an error")
        else:
            raise ValueError(error_text)

    model_match = CODEX_MODEL_LINE_RE.search(stderr)
    actual_model = model_match.group(1) if model_match else None
    if actual_model and model and actual_model != model:
        debug_log(
            "  run_codex_exec: requested model=%s but Codex actually used model=%s",
            model, actual_model,
        )
    return CodexExecResult(text=stdout or stderr, actual_model=actual_model)


# ---------------------------------------------------------------------------
# Model discovery - what this specific, authenticated ChatGPT/Codex session
# can actually run, discovered from the real installed CLI rather than
# assumed from any hardcoded list. See the module docstring for why the old
# codex-specific DEFAULT_OPENAI_MODEL constant is gone.
# ---------------------------------------------------------------------------

CODEX_MODEL_DISCOVERY_TIMEOUT_SECONDS = float(os.getenv("CODEX_MODEL_DISCOVERY_TIMEOUT_SECONDS", "30"))
CODEX_MODEL_VERIFY_TIMEOUT_SECONDS = float(os.getenv("CODEX_MODEL_VERIFY_TIMEOUT_SECONDS", "25"))
CODEX_MODELS_CACHE_TTL_SECONDS = float(os.getenv("CODEX_MODELS_CACHE_TTL_SECONDS", str(6 * 3600)))
# Confirmed live this matters (see verify_codex_model_selectable's
# docstring): firing verification calls back-to-back with zero pacing
# triggered a rate-limit/lock pattern after only 1-2 calls, silently losing
# 6 of 7 real, valid models from a real account's discovered list.
CODEX_MODEL_VERIFY_PACING_SECONDS = float(os.getenv("CODEX_MODEL_VERIFY_PACING_SECONDS", "2"))
# Serializes refresh_codex_models() per client so two overlapping triggers
# (e.g. the post-login background warm-up in main.py racing an on-demand
# picker-open call) never run two full, real, multi-call discovery rounds
# against the SAME CODEX_HOME concurrently - itself a plausible contributor
# to the same rate-limit/lock symptom, on top of the missing pacing above.
_CODEX_MODELS_REFRESH_LOCKS: dict[str, asyncio.Lock] = {}
# If another caller's refresh finished this recently while we were waiting
# on the lock, reuse its result instead of redundantly re-running the whole
# expensive, multi-call discovery round again immediately.
CODEX_MODELS_REFRESH_DEDUPE_SECONDS = 30.0  # kept for compatibility; one shared in-flight task now dedupes
# Discovery's default-model probe used to inherit the 90s ANSWER timeout: one
# hung CLI process stalled discovery for 1.5 minutes (seen live) before anything
# else could happen. A probe is a one-word turn; ~10s is normal.
CODEX_MODEL_DEFAULT_PROBE_TIMEOUT_SECONDS = float(os.getenv("CODEX_MODEL_DEFAULT_PROBE_TIMEOUT_SECONDS", "45"))
# Hard ceiling for one whole discovery round (healthy: ~2-3 minutes because each
# candidate is a real `codex exec`); past it the remaining candidates stay unverified.
CODEX_DISCOVERY_BUDGET_SECONDS = float(os.getenv("CODEX_DISCOVERY_BUDGET_SECONDS", "240"))
# After a discovery that verified NOTHING (CLI down / hung / unauthenticated) wait
# this long before starting another one, instead of re-running it on every open.
CODEX_MODELS_RETRY_BACKOFF_SECONDS = float(os.getenv("CODEX_MODELS_RETRY_BACKOFF_SECONDS", "120"))
# A list with inconclusive entries (rate-limited probes) or a progress snapshot is
# re-checked after this long, not after the full multi-hour TTL.
CODEX_MODELS_PARTIAL_RECHECK_SECONDS = float(os.getenv("CODEX_MODELS_PARTIAL_RECHECK_SECONDS", "600"))
CODEX_MODEL_PROBE_PROMPT = "Reply with exactly one word: ok"


def _humanize_model_id(model_id: str) -> str:
    """Best-effort display label for a model id the catalog itself didn't
    name (see discover_codex_models()'s docstring for why that can happen -
    the account's real live default, "gpt-6-luna", is not present in this
    account's own `codex debug models` catalog at all). Purely cosmetic
    string formatting, never claimed as an authoritative name from Codex."""
    parts = model_id.split("-")
    return " ".join(part.upper() if part.lower() == "gpt" else part.capitalize() for part in parts)


def get_codex_model_catalog(codex_home: str) -> list[dict[str, Any]]:
    """The authoritative, structured model catalog for the CURRENT
    ChatGPT-authenticated session, via `codex debug models` - confirmed
    present in the installed codex-cli 0.155.1 (`codex debug --help` lists
    it: "Render the raw model catalog as JSON"). Deliberately NOT
    `--bundled`: that flag explicitly skips the live refresh and dumps only
    the catalog shipped with the binary (Phase 2's category B, "models the
    installed CLI knows about in general"); the default (no --bundled) call
    refreshes against the account first, which is what makes this category C
    ("models this authenticated account can actually run") rather than B.

    Only entries with visibility == "list" are returned - that field is the
    CLI's own signal for "show this in a model picker". Confirmed live
    against a real account's catalog: codex-auto-review, both
    gpt-daybreak-*-latest entries, and gpt-5.4 all report visibility="hide"
    and are clearly not meant to appear in a chat model picker (one is an
    internal code-review-only model).

    Catalog presence alone is NOT sufficient to mark a model selectable -
    confirmed live, directly: gpt-6-astra appeared in a real account's
    catalog with visibility="list" AND supported_in_api=true, yet
    `codex exec -m gpt-6-astra` was rejected outright by that ChatGPT-account
    session with the exact same rejection class gpt-4.1-mini gets. This
    function only returns candidates; verify_codex_model_selectable() below
    does the real check before anything is exposed as selectable.

    Never raises - a discovery failure (CLI too old, no `debug models`
    subcommand, timeout, unparsable output) returns an empty list and is
    logged, never faked as "zero models available" being confused with "the
    discovery itself failed" at the call site (discover_codex_models() logs
    which one happened).
    """
    command = _codex_command()
    home = Path(codex_home)
    working_dir = home / "workspace"
    working_dir.mkdir(mode=0o700, exist_ok=True)
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)

    popen_args = _popen_args(command, ["debug", "models"])
    try:
        process = subprocess.Popen(
            popen_args,
            cwd=str(working_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = process.communicate(timeout=CODEX_MODEL_DISCOVERY_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _terminate_tree(process)
        debug_log("    get_codex_model_catalog: 'codex debug models' timed out")
        return []
    except OSError as exc:
        debug_log("    get_codex_model_catalog: could not launch 'codex debug models': %s", exc)
        return []

    if process.returncode != 0:
        debug_log(
            "    get_codex_model_catalog: 'codex debug models' exited %d: %s",
            process.returncode, stderr_bytes.decode("utf-8", errors="replace")[:300],
        )
        return []

    try:
        payload = json.loads(stdout_bytes.decode("utf-8", errors="replace"))
    except ValueError as exc:
        debug_log("    get_codex_model_catalog: could not parse JSON output: %s", exc)
        return []

    raw_models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(raw_models, list):
        debug_log("    get_codex_model_catalog: unexpected payload shape, no 'models' list")
        return []

    catalog: list[dict[str, Any]] = []
    for entry in raw_models:
        if not isinstance(entry, dict) or entry.get("visibility") != "list":
            continue
        slug = entry.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        catalog.append(
            {
                "id": slug,
                "display_name": entry.get("display_name") or _humanize_model_id(slug),
                "description": entry.get("description") or "",
                "reasoning_support": bool(entry.get("supported_reasoning_levels")),
                # Codex CLI's OWN agentic tool mode (shell/apply_patch inside
                # its sandbox), not MCP compatibility - Braidscope's Codex
                # provider never gives the model live MCP tool schemas at
                # all (see stream_codex_provider's docstring in
                # chat_service.py: the backend pre-fetches MCP data and
                # embeds it as plain text). This field is honest about what
                # it measures rather than conflating the two.
                "tool_support": bool(entry.get("tool_mode")),
            }
        )
    return catalog


def verify_codex_model_selectable(codex_home: str, model_id: str) -> bool | None:
    """Real, live, lightweight verification that `model_id` can actually
    execute through the current ChatGPT-authenticated Codex session - never
    inferred from catalog metadata alone (get_codex_model_catalog()'s
    docstring has the concrete live counterexample: gpt-6-astra passes every
    catalog-metadata check and is still rejected). Uses the smallest
    reasonable real `codex exec -m <model_id>` turn and a shorter timeout
    than a real answer gets, since this only needs a yes/no.

    Returns True (confirmed usable), False (confirmed rejected - the CLI
    produced its specific, well-understood "not supported" error), or None
    (inconclusive - do NOT treat as rejected). That third state is not
    theoretical: confirmed live on 2026-09-23 running discover_codex_models()
    for a real account, running 7 verification calls back-to-back (no
    pacing between them) - the first 1-2 calls behaved normally (10-14s,
    real banner output, a real decision), then every call after that failed
    in under 1 second with COMPLETELY EMPTY stdout/stderr - no banner, no
    error message, nothing. That duration/output pattern is the signature
    of the ChatGPT-account session being rate-limited or lock-contended by
    firing too many real Codex CLI calls in a tight sequence, not of a real,
    computed "this model is not supported" decision (which took the earlier
    calls 10+ seconds and always produced a real banner plus a specific
    error message either way). Blindly treating that as "rejected" silently
    and permanently excluded 6 out of 7 real, valid models from a real
    client's model list. A genuine rejection is only ever recorded when the
    CLI's own specific error text is actually present.
    """
    try:
        returncode, stdout, stderr = _codex_exec_raw(
            codex_home, CODEX_MODEL_PROBE_PROMPT, model_id, CODEX_MODEL_VERIFY_TIMEOUT_SECONDS
        )
    except ValueError as exc:
        debug_log("    verify_codex_model_selectable(%s): timed out - inconclusive: %s", model_id, exc)
        return None
    if returncode == 0:
        return True
    combined = (stderr or stdout or "").strip()
    if not combined:
        debug_log(
            "    verify_codex_model_selectable(%s): failed with NO output at all - "
            "inconclusive (likely rate-limited/throttled), not a confirmed rejection",
            model_id,
        )
        return None
    if CODEX_MODEL_NOT_SUPPORTED_MARKER not in combined.lower():
        debug_log(
            "    verify_codex_model_selectable(%s): failed with an error that isn't the "
            "known 'not supported' rejection - inconclusive, not a confirmed rejection: %s",
            model_id, combined[:200],
        )
        return None
    debug_log("    verify_codex_model_selectable(%s): confirmed rejected live: %s", model_id, combined[:200])
    return False


def discover_codex_models(
    codex_home: str,
    on_progress: Any = None,
) -> list[dict[str, Any]]:
    """Real, verified model list for THIS authenticated Codex session - the
    get_codex_available_models() this feature is built around. Two real
    subprocess phases, never inferred from catalog metadata alone:

      1. One unforced `codex exec` call both confirms the connection still
         works AND discovers the actual live default model from its own
         stderr banner (CODEX_MODEL_LINE_RE) - confirmed live this can be a
         slug that never appears in get_codex_model_catalog()'s own output
         at all. Bounded by CODEX_MODEL_DEFAULT_PROBE_TIMEOUT_SECONDS, NOT the
         90s answer timeout.
      2. Every OTHER catalog candidate (visibility="list", excluding
         whichever slug step 1 already proved works) is verified with its
         own real, cheap exec call via verify_codex_model_selectable().

    Returns every candidate discovered, default-first, each flagged with
    `supported`/`default` - callers that want only what's actually
    selectable must filter on `supported is True`. Unusable entries are
    still returned (not silently dropped) so a rejection stays visible in
    logs/the persisted cache rather than disappearing without a trace.

    `on_progress(models_so_far)` (optional) is called after the default is
    known and after every verification, so a caller can publish what has been
    verified SO FAR instead of making the user wait for the whole round. A
    misbehaving callback never affects discovery.

    If the default-detection call itself fails (session not actually
    authenticated, CLI unreachable/hung), no other model can be trusted as
    verified either - the raw catalog candidates are returned with
    `supported: None` ("unknown, not verified") rather than either faking
    them as usable or discarding them as if zero models exist.
    """
    started = time.monotonic()
    catalog = get_codex_model_catalog(codex_home)
    catalog_by_id = {entry["id"]: entry for entry in catalog}

    def report(snapshot: list[dict[str, Any]]) -> None:
        if on_progress is None:
            return
        try:
            on_progress([dict(entry) for entry in snapshot])
        except Exception as exc:  # noqa: BLE001 - progress is best-effort
            debug_log("    discover_codex_models: progress callback failed: %s", type(exc).__name__)

    default_result = None
    for attempt in (1, 2):
        try:
            default_result = run_codex_exec(
                codex_home,
                CODEX_MODEL_PROBE_PROMPT,
                timeout_seconds=CODEX_MODEL_DEFAULT_PROBE_TIMEOUT_SECONDS,
            )
            break
        except ValueError as exc:
            # One quick retry, only for a TIMEOUT: `codex exec` is a real round-trip to
            # OpenAI and times out intermittently (seen live, in clusters). Everything
            # depends on this one call, so a transient timeout must not cost a full
            # retry-backoff window. Auth/other errors are not retried.
            timed_out = "took too long" in str(exc).lower()
            debug_log("    discover_codex_models: default-detection call failed (attempt %d): %s", attempt, exc)
            if attempt == 1 and timed_out:
                continue
            return [{**entry, "supported": None, "default": False} for entry in catalog]
    if default_result is None:
        return [{**entry, "supported": None, "default": False} for entry in catalog]

    default_id = default_result.actual_model
    results: list[dict[str, Any]] = []
    if default_id:
        base = catalog_by_id.get(
            default_id,
            {
                "id": default_id,
                "display_name": _humanize_model_id(default_id),
                "description": "",
                "reasoning_support": None,
                "tool_support": None,
            },
        )
        if default_id not in catalog_by_id:
            debug_log(
                "    discover_codex_models: live default '%s' is not in the 'codex debug models' "
                "catalog - exposing it anyway since it just answered successfully",
                default_id,
            )
        results.append({**base, "supported": True, "default": True})
        report(results)
    else:
        debug_log("    discover_codex_models: default-detection call succeeded but reported no model")

    over_budget = False
    for index, entry in enumerate(catalog):
        if entry["id"] == default_id:
            continue
        if not over_budget and time.monotonic() - started > CODEX_DISCOVERY_BUDGET_SECONDS:
            over_budget = True
            debug_log(
                "    discover_codex_models: budget of %.0fs exhausted - remaining candidates stay unverified",
                CODEX_DISCOVERY_BUDGET_SECONDS,
            )
        if over_budget:
            results.append({**entry, "supported": None, "default": False})
            continue
        if index > 0:
            # Confirmed live to matter: firing verification calls back-to-back
            # with no pacing at all triggered a rate-limit/lock pattern after
            # only 1-2 calls, silently losing valid models to throttling.
            time.sleep(CODEX_MODEL_VERIFY_PACING_SECONDS)
        supported = verify_codex_model_selectable(codex_home, entry["id"])
        results.append({**entry, "supported": supported, "default": False})
        report(results)

    return results


# One shared discovery task per client. Everything that wants a fresh list -
# the post-login warm-up, an on-demand picker open, an explicit refresh - awaits
# or fire-and-forgets the SAME task, so a slow Codex CLI is only ever asked
# once at a time per client (no duplicate/parallel `codex exec` rounds).
_CODEX_REFRESH_TASKS: dict[str, "asyncio.Task[list[dict[str, Any]]]"] = {}
_CODEX_PERSIST_LOCKS: dict[str, asyncio.Lock] = {}
# Monotonic ticket per persisted snapshot: a slow, late progress write can never
# overwrite the newer/final result (writes are scheduled from a worker thread).
_CODEX_PERSIST_SEQ = itertools.count(1)
_CODEX_PERSIST_LAST: dict[str, int] = {}


def is_codex_refresh_running(client_id: str) -> bool:
    task = _CODEX_REFRESH_TASKS.get(client_id)
    return task is not None and not task.done()


async def wait_for_codex_refresh(client_id: str) -> None:
    """Awaits an in-flight discovery (if any); never raises. Mainly for tests."""
    task = _CODEX_REFRESH_TASKS.get(client_id)
    if task is not None:
        try:
            await asyncio.shield(task)
        except Exception:  # noqa: BLE001
            pass


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _codex_models_cache_state(token: dict[str, Any]) -> tuple[list[dict[str, Any]], bool, bool, bool]:
    """(cached_list, has_verified_models, is_fresh, in_retry_backoff)."""
    raw = token.get("available_models")
    cached = [entry for entry in raw if isinstance(entry, dict)] if isinstance(raw, list) else []
    usable = any(entry.get("supported") is True for entry in cached)
    # Legacy rows (written before `models_partial` existed) are recognised by
    # inconclusive entries: those are re-checked soon, not after the full TTL.
    partial = bool(token.get("models_partial")) or any(entry.get("supported") is None for entry in cached)
    ttl = CODEX_MODELS_PARTIAL_RECHECK_SECONDS if partial else CODEX_MODELS_CACHE_TTL_SECONDS
    now = utc_now()
    checked_at = _parse_iso(token.get("models_checked_at"))
    fresh = bool(cached) and checked_at is not None and 0 <= (now - checked_at).total_seconds() < ttl
    failed_at = _parse_iso(token.get("models_check_failed_at"))
    backoff = failed_at is not None and 0 <= (now - failed_at).total_seconds() < CODEX_MODELS_RETRY_BACKOFF_SECONDS
    return cached, usable, fresh, backoff


def _merge_with_verified(
    previous: Any,
    models: list[dict[str, Any]],
    *,
    final: bool,
) -> list[dict[str, Any]]:
    """Keeps the picker's list stable across rounds. Each verification is a real
    round-trip to OpenAI and times out intermittently (seen live: 4 of 7 probes
    timing out in one round, all fine the next), so:

      - an INCONCLUSIVE result (supported=None) never revokes a model that was
        verified earlier - only an explicit rejection (supported=False) does;
      - a progress snapshot (verified-so-far) never makes the cached list
        shrink mid-round: earlier-verified models not re-checked yet stay.
    """
    verified_before = {
        entry["id"]: entry
        for entry in (previous if isinstance(previous, list) else [])
        if isinstance(entry, dict) and entry.get("supported") is True and entry.get("id")
    }
    merged: list[dict[str, Any]] = []
    for entry in models:
        if entry.get("supported") is None and entry.get("id") in verified_before:
            merged.append({**entry, "supported": True})
        else:
            merged.append(entry)
    if not final:
        present = {entry.get("id") for entry in merged}
        has_default = any(entry.get("default") for entry in merged)
        for entry_id, entry in verified_before.items():
            if entry_id not in present:
                merged.append({**entry, "default": False if has_default else entry.get("default", False)})
    return merged


async def _persist_codex_models(
    client_id: str,
    models: list[dict[str, Any]],
    *,
    final: bool,
    seq: int,
) -> None:
    """Read-modify-write into this client's EXISTING connector_oauth_tokens row
    (token_payload JSON - no schema change; per AGENTS.md Node owns the Prisma
    schema). upsert_connector_token replaces token_payload wholesale, so every
    key (codex_home, auth_type, created_at, ...) is carried over.

    A discovery that verified NOTHING is a FAILURE and is never stored as the
    cache: it used to be, stamped "fresh", which hid every model for the whole
    6h TTL after one CLI timeout (seen live). It only records
    `models_check_failed_at`, which drives a short retry backoff."""
    lock = _CODEX_PERSIST_LOCKS.setdefault(client_id, asyncio.Lock())
    async with lock:
        if seq < _CODEX_PERSIST_LAST.get(client_id, 0):
            return  # a newer snapshot already landed
        _CODEX_PERSIST_LAST[client_id] = seq
        token = await get_connector_token(client_id, CODEX_TOKEN_SOURCE_TYPE)
        if not token or token.get("auth_type") != "codex_chatgpt":
            return
        updated = dict(token)
        now_iso = utc_now().isoformat()
        # Failure is judged on the RAW round result: a round in which nothing was
        # verified (CLI down / hung / unauthenticated) is a failure even if older
        # verified entries exist - those are kept by NOT overwriting the cache.
        if not any(entry.get("supported") is True for entry in models):
            supported_ids: set[str] = set()
        else:
            models = _merge_with_verified(token.get("available_models"), models, final=final)
            supported_ids = {entry["id"] for entry in models if entry.get("supported")}
        if not supported_ids:
            if final:
                updated["models_check_failed_at"] = now_iso
                await upsert_connector_token(client_id, CODEX_TOKEN_SOURCE_TYPE, updated)
            return
        updated["available_models"] = models
        updated["models_checked_at"] = now_iso
        updated["models_partial"] = (not final) or any(entry.get("supported") is None for entry in models)
        updated.pop("models_check_failed_at", None)
        default_entry = next((m for m in models if m.get("default")), None)
        current_model = (updated.get("model") or "").strip()
        if final and default_entry and (not current_model or current_model not in supported_ids):
            # Corrects two real cases, not just "nothing was ever chosen":
            #   1. Nothing stored yet (current_model == "").
            #   2. What IS stored is not a live-verified-supported model
            #      right now - every client that connected before this
            #      discovery system existed has the old never-valid
            #      "gpt-4.1-mini" placeholder stored, and nothing else was
            #      ever going to correct that row.
            debug_log(
                "  refresh_codex_models(client_id=%s): correcting stored model %r -> %r (not in verified supported set)",
                client_id, current_model or None, default_entry["id"],
            )
            updated["model"] = default_entry["id"]
        await upsert_connector_token(client_id, CODEX_TOKEN_SOURCE_TYPE, updated)


async def _refresh_codex_models_impl(client_id: str) -> list[dict[str, Any]]:
    token = await get_connector_token(client_id, CODEX_TOKEN_SOURCE_TYPE)
    if not token or token.get("auth_type") != "codex_chatgpt":
        raise ValueError("ChatGPT/Codex is not connected for this client")
    codex_home = token.get("codex_home")
    if not codex_home or not Path(codex_home).is_dir():
        raise ValueError("ChatGPT/Codex connection is missing its CODEX_HOME")

    loop = asyncio.get_running_loop()

    def on_progress(partial: list[dict[str, Any]]) -> None:
        # Runs in the discovery worker thread: publish what is verified so far
        # (the picker can show the default within ~10s instead of after the
        # whole round) without waiting for the write.
        asyncio.run_coroutine_threadsafe(
            _persist_codex_models(client_id, partial, final=False, seq=next(_CODEX_PERSIST_SEQ)),
            loop,
        )

    started = time.monotonic()
    try:
        models = await asyncio.to_thread(discover_codex_models, codex_home, on_progress)
    except Exception as exc:  # noqa: BLE001 - a broken CLI must degrade, not raise into Ask AI
        debug_log("  refresh_codex_models(client_id=%s): discovery raised %s: %s", client_id, type(exc).__name__, exc)
        models = []
    await _persist_codex_models(client_id, models, final=True, seq=next(_CODEX_PERSIST_SEQ))
    default_entry = next((m for m in models if m.get("default")), None)
    debug_log(
        "  refresh_codex_models(client_id=%s): %d candidates, %d supported, default=%s (%.1fs)",
        client_id, len(models), sum(1 for m in models if m.get("supported")),
        default_entry and default_entry["id"], time.monotonic() - started,
    )
    return models


def _start_codex_refresh(client_id: str) -> "asyncio.Task[list[dict[str, Any]]]":
    task = _CODEX_REFRESH_TASKS.get(client_id)
    if task is not None and not task.done():
        return task
    task = asyncio.get_running_loop().create_task(_refresh_codex_models_impl(client_id))
    _CODEX_REFRESH_TASKS[client_id] = task

    def _done(finished: "asyncio.Task[list[dict[str, Any]]]") -> None:
        if _CODEX_REFRESH_TASKS.get(client_id) is finished:
            _CODEX_REFRESH_TASKS.pop(client_id, None)
        if not finished.cancelled() and finished.exception() is not None:
            debug_log(
                "  refresh_codex_models(client_id=%s) failed: %s: %s",
                client_id, type(finished.exception()).__name__, finished.exception(),
            )

    task.add_done_callback(_done)
    return task


async def codex_models_loading(client_id: str, models: list[dict[str, Any]]) -> bool:
    """True while the picker should keep asking: a discovery round is running, or
    nothing is verified yet and a failed round is waiting out its retry backoff
    (the next poll after the backoff starts the retry by itself)."""
    if is_codex_refresh_running(client_id):
        return True
    if models:
        return False
    token = await get_connector_token(client_id, CODEX_TOKEN_SOURCE_TYPE)
    if not token or token.get("auth_type") != "codex_chatgpt":
        return False
    return _codex_models_cache_state(token)[3]


async def refresh_codex_models(client_id: str) -> list[dict[str, Any]]:
    """Runs real discovery now (or joins the one already running for this
    client) and persists the verified result. Every caller shares a single
    in-flight task per client, so two overlapping triggers (post-login warm-up
    racing a picker open, e.g.) never run two multi-call `codex exec` rounds
    against the same CODEX_HOME. The blocking subprocess work runs in a worker
    thread, so a slow/hung Codex CLI cannot stall the backend's event loop."""
    return await asyncio.shield(_start_codex_refresh(client_id))


async def list_codex_models(client_id: str, force_refresh: bool = False) -> list[dict[str, Any]]:
    """The Ask AI model picker read - it NEVER waits on the Codex CLI (unless
    force_refresh is passed). Each verification is a real `codex exec` (6-12s,
    ~2-3 minutes for a whole round), so:

      - fresh, verified cache  -> returned immediately;
      - stale, verified cache  -> returned immediately, re-verified in the
                                  background (stale-while-revalidate);
      - nothing verified yet   -> [] immediately and discovery starts in the
                                  background; the response's `loading` flag
                                  (see main.py) tells the picker to keep asking.
    A recent failed discovery is not retried for
    CODEX_MODELS_RETRY_BACKOFF_SECONDS, so a broken CLI is not hammered."""
    token = await get_connector_token(client_id, CODEX_TOKEN_SOURCE_TYPE)
    if not token or token.get("auth_type") != "codex_chatgpt":
        raise ValueError("ChatGPT/Codex is not connected for this client")

    if force_refresh:
        return await refresh_codex_models(client_id)

    cached, usable, fresh, backoff = _codex_models_cache_state(token)
    if usable:
        if not fresh and not backoff:
            _start_codex_refresh(client_id)
        return cached
    if not backoff:
        _start_codex_refresh(client_id)
    return []


async def set_codex_selected_model(client_id: str, model_id: str) -> dict[str, Any]:
    """Persists an explicit user model pick for this client's Codex
    connection - client-scoped via the same connector_oauth_tokens row every
    other read/write in this module uses (see get_codex_chat_context's
    docstring for why that is what keeps this safe across a shared client's
    teammates; it is never a global/process-wide setting, so switching
    clients can never leak one client's selection into another's).

    Verifies against the cached model list first (refreshing once if the
    cache is empty) rather than accepting any string on faith - a
    mistyped or stale model id must never become the stored selection.
    """
    model_id = (model_id or "").strip()
    if not model_id:
        raise ValueError("A model id is required")

    token = await get_connector_token(client_id, CODEX_TOKEN_SOURCE_TYPE)
    if not token or token.get("auth_type") != "codex_chatgpt":
        raise ValueError("ChatGPT/Codex is not connected for this client")

    models = token.get("available_models")
    if not isinstance(models, list) or not models:
        models = await refresh_codex_models(client_id)
        token = await get_connector_token(client_id, CODEX_TOKEN_SOURCE_TYPE)

    supported_ids = {entry["id"] for entry in models if isinstance(entry, dict) and entry.get("supported")}
    if model_id not in supported_ids:
        raise ValueError(f"Model '{model_id}' is not available for this Codex connection")

    updated = dict(token or {})
    updated["model"] = model_id
    await upsert_connector_token(client_id, CODEX_TOKEN_SOURCE_TYPE, updated)
    debug_log("  set_codex_selected_model(client_id=%s): model=%s", client_id, model_id)
    return {"model": model_id}
