# SPDX-License-Identifier: AGPL-3.0-only
"""Isolated Claude Code CLI authentication + execution for per-user Claude
subscription connections.

Deliberately NOT a copy of codex.py: Claude Code has no device-code flow.
`claude auth login` prints an authorize URL, then waits on stdin for a code
the user copies from Anthropic's own page. Credentials only ever live inside
the per-(user, client) CLAUDE_CONFIG_DIR that the CLI itself manages - this
module never opens, copies, stores or logs them, and never logs the URL, the
pasted code or any CLI output that could contain either.

Connections are PER USER (not per client like Codex): each teammate signs in
with their own Claude account. They share one `connector_oauth_tokens` row
(source_type "claude_code") whose token_payload holds a `users` map, because
that table is unique on (client_id, source_type) and Node owns the schema.
"""

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .codex import ANSI_RE, URL_RE, _popen_args, _terminate_tree
from .core import debug_log, urlsafe_token, utc_now
from .supabase_admin import (
    delete_connector_token,
    get_connector_token,
    mark_data_source_disconnected,
    upsert_connector_token,
    upsert_data_source_connected,
)

# Same data_sources card as Claude BYOK (which stays intact and recoverable);
# the token rows are separate (BYOK uses source_type "anthropic_ai").
CLAUDE_SOURCE_TYPE = "anthropic_ai"
CLAUDE_TOKEN_SOURCE_TYPE = "claude_code"
CLAUDE_AUTH_TYPE = "claude_code_oauth"

CLAUDE_SESSIONS: dict[str, dict[str, Any]] = {}
CLAUDE_LOGIN_TIMEOUT_SECONDS = float(os.getenv("CLAUDE_LOGIN_TIMEOUT_SECONDS", "600"))
CLAUDE_EXEC_TIMEOUT_SECONDS = float(os.getenv("CLAUDE_EXEC_TIMEOUT_SECONDS", os.getenv("OPENAI_CHAT_TIMEOUT_SECONDS", "90")))
CLAUDE_STATUS_TIMEOUT_SECONDS = 15.0
CLAUDE_MODEL_PROBE_TIMEOUT_SECONDS = float(os.getenv("CLAUDE_MODEL_PROBE_TIMEOUT_SECONDS", "45"))
CLAUDE_MODEL_PROBE_PACING_SECONDS = float(os.getenv("CLAUDE_MODEL_PROBE_PACING_SECONDS", "1"))
CLAUDE_MODELS_CACHE_TTL_SECONDS = float(os.getenv("CLAUDE_MODELS_CACHE_TTL_SECONDS", str(6 * 3600)))
CLAUDE_MODELS_PARTIAL_RECHECK_SECONDS = float(os.getenv("CLAUDE_MODELS_PARTIAL_RECHECK_SECONDS", "300"))
CLAUDE_MODELS_REFRESH_DEDUPE_SECONDS = 30.0
CLAUDE_BUSY_WAIT_SECONDS = 30.0
CLAUDE_PROBE_PROMPT = "Reply with exactly one word: ok"

# Aliases are what `--model` accepts and what Claude Code resolves to the
# newest model of that family per account (Claude Code's own /model picker
# lists exactly these: Default, Opus, Fable, Sonnet, Haiku). There is no
# CLI/API to list what an account can run (verified against the docs), so each
# alias is PROBED, and the real name/version ("Opus 5.5") comes from the model
# Claude Code reports having used - never hardcoded. The one-line
# descriptions are the picker's own family blurbs, not per-version facts.
CLAUDE_MODEL_ALIASES: tuple[tuple[str, str, str], ...] = (
    ("opus", "Opus", "Best for everyday, complex tasks"),
    ("fable", "Fable", "Most capable for your hardest and longest-running tasks"),
    ("sonnet", "Sonnet", "Efficient for routine tasks"),
    ("haiku", "Haiku", "Fastest for quick answers"),
)
# Confirmed live (Pro plan, HTTP 429): a model the account can see but not run
# without usage credits fails with "<Name> <ver> requires usage credits...".
# Claude Code's picker still lists it with that note, so it is listed here too
# (marked) instead of being hidden as if it did not exist.
CREDITS_REQUIRED = "requires_credits"
CREDITS_MARKER = "requires usage credits"
_CREDITS_NAME_RE = re.compile(r"^([A-Za-z]+ [\d.]+) requires usage credits", re.IGNORECASE)
_MODEL_ID_NAME_RE = re.compile(r"^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?(?:\[[^\]]*\])?$")
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-\[\]]{0,63}$")
# What the user pastes back is "<code>#<state>"-shaped. Strict allowlist so
# nothing shell/argv-significant can ever reach the CLI's stdin.
LOGIN_CODE_RE = re.compile(r"^[A-Za-z0-9._~#\-]{8,600}$")
AUTH_ERROR_MARKERS = ("not logged in", "login expired", "please run /login", "invalid api key", "oauth token")

# Only these variables reach the CLI. os.environ.copy() (what codex.py does)
# would hand the Supabase service key and SHARED_JWT_SECRET to a subprocess.
_ENV_ALLOWLIST = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "COMSPEC", "WINDIR", "TEMP", "TMP", "TMPDIR",
    "LANG", "LC_ALL", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
    "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS", "NODE_OPTIONS",
)

_TOKEN_ROW_LOCKS: dict[str, asyncio.Lock] = {}
_MODELS_REFRESH_LOCKS: dict[str, asyncio.Lock] = {}
_EXEC_LOCKS: dict[str, threading.Lock] = {}
_EXEC_LOCKS_GUARD = threading.Lock()
# config_dir -> monotonic time an auth failure was seen while executing.
# `claude auth status` only reads local files, so it cannot see a revoked or
# expired token; this is how a failed real run flips the card to Reconnect.
_AUTH_FAILED: dict[str, float] = {}


class ClaudeAuthError(ValueError):
    """The CLI ran but the account is not (or no longer) signed in."""


def _identity(user_id: str, client_id: str) -> str:
    return hashlib.sha256(f"{user_id}:{client_id}".encode("utf-8")).hexdigest()


def _session_id(user_id: str, client_id: str) -> str:
    return _identity(user_id, client_id)[:32]


def _config_root() -> Path:
    configured = os.getenv("CLAUDE_CONFIG_ROOT")
    if os.name == "nt" and configured and configured.startswith("/") and not configured.startswith("//"):
        configured = None
    if configured:
        root = Path(configured)
    elif os.name == "nt":
        # Outside the git repo on purpose: Claude Code walks UP from its cwd
        # loading CLAUDE.md, and this repo's own rules must never leak into
        # the assistant's context (same failure codex.py documents for AGENTS.md).
        root = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "Braidscope" / "claude-runners"
    else:
        root = Path.cwd() / ".claude-runners"
    return root.resolve()


def _config_dir(user_id: str, client_id: str) -> Path:
    root = _config_root()
    home = root / _identity(user_id, client_id)
    home.mkdir(parents=True, exist_ok=True)
    try:
        home.chmod(0o700)
    except OSError:
        pass
    return home


def _workspace(config_dir: Path) -> Path:
    workspace = config_dir / "workspace"
    workspace.mkdir(mode=0o700, exist_ok=True)
    return workspace


def _inside_root(path: Path) -> bool:
    try:
        path.resolve().relative_to(_config_root())
        return path.resolve() != _config_root()
    except ValueError:
        return False


def _claude_command() -> str:
    command = os.getenv("CLAUDE_CLI_COMMAND", "claude").strip()
    if not command:
        raise ValueError("CLAUDE_CLI_COMMAND is empty")
    resolved = shutil.which(command)
    if os.name == "nt":
        if resolved and resolved.lower().endswith(".ps1"):
            cmd_shim = f"{resolved[:-4]}.cmd"
            if os.path.exists(cmd_shim):
                resolved = cmd_shim
        elif not resolved and not command.lower().endswith(".cmd"):
            resolved = shutil.which(f"{command}.cmd")
    if resolved is None:
        raise ValueError(
            "The official Claude Code CLI is not installed on the backend. "
            "Install it (npm i -g @anthropic-ai/claude-code) and make `claude` available on PATH."
        )
    return resolved


def _cli_env(config_dir: Path) -> dict[str, str]:
    env = {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    # HOME points inside the isolated dir too, so nothing falls back to the
    # backend operator's own ~/.claude or ~/.claude.json.
    home = config_dir / "home"
    home.mkdir(mode=0o700, exist_ok=True)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["DISABLE_AUTOUPDATER"] = "1"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    # The user's own browser opens the URL; never let the server try to.
    env["BROWSER"] = "true" if os.name != "nt" else "cmd /c exit 0"
    return env


def _looks_like_auth_failure(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in AUTH_ERROR_MARKERS)


# ---------------------------------------------------------------------------
# Auth status (local, offline - reads only what the CLI itself reports)
# ---------------------------------------------------------------------------


def _claude_auth_status(config_dir: str) -> dict[str, Any] | None:
    """`claude auth status --json` for this config dir. Returns the parsed
    dict, or None when the CLI could not be run / parsed. Only whitelisted
    fields are returned (never email or org identifiers)."""
    home = Path(config_dir)
    if not home.is_dir():
        return None
    try:
        command = _claude_command()
        process = subprocess.Popen(
            _popen_args(command, ["auth", "status", "--json"]),
            cwd=str(_workspace(home)),
            env=_cli_env(home),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        debug_log("    claude auth status: could not launch: %s", type(exc).__name__)
        return None
    try:
        stdout, _ = process.communicate(timeout=CLAUDE_STATUS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _terminate_tree(process)
        return None
    try:
        payload = json.loads(ANSI_RE.sub("", stdout.decode("utf-8", errors="replace")))
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    return {
        "logged_in": bool(payload.get("loggedIn")),
        "auth_method": payload.get("authMethod"),
        "subscription_type": payload.get("subscriptionType"),
    }


def _is_subscription_login(status: dict[str, Any] | None) -> bool:
    return bool(status and status["logged_in"] and status.get("auth_method") == "claude.ai")


# ---------------------------------------------------------------------------
# Persistence: one connector_oauth_tokens row per client, users[] map inside
# ---------------------------------------------------------------------------


async def _read_users(client_id: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    token = await get_connector_token(client_id, CLAUDE_TOKEN_SOURCE_TYPE) or {}
    users = token.get("users")
    return token, users if isinstance(users, dict) else {}


async def _write_users(client_id: str, token: dict[str, Any], users: dict[str, dict[str, Any]], user_id: str | None = None) -> None:
    payload = {k: v for k, v in token.items() if k not in {"users", "oauth_client_id", "expires_at", "scope"}}
    payload["auth_type"] = CLAUDE_AUTH_TYPE
    payload["users"] = users
    await upsert_connector_token(client_id, CLAUDE_TOKEN_SOURCE_TYPE, payload, user_id=user_id)


def _row_lock(client_id: str) -> asyncio.Lock:
    return _TOKEN_ROW_LOCKS.setdefault(client_id, asyncio.Lock())


async def _get_user_record(client_id: str, user_id: str) -> dict[str, Any] | None:
    _, users = await _read_users(client_id)
    record = users.get(user_id)
    return record if isinstance(record, dict) else None


async def _update_user_record(client_id: str, user_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    async with _row_lock(client_id):
        token, users = await _read_users(client_id)
        record = dict(users.get(user_id) or {})
        record.update(changes)
        users[user_id] = record
        await _write_users(client_id, token, users, user_id=user_id)
        return record


# ---------------------------------------------------------------------------
# Login flow: CLI prints URL -> user signs in on Anthropic's page -> user pastes
# the code Anthropic shows -> we hand it to the CLI's stdin -> CLI stores creds
# ---------------------------------------------------------------------------


def _read_login_stream(session: dict[str, Any], stream: Any) -> None:
    if stream is None:
        return
    for raw in iter(stream.readline, b""):
        text = ANSI_RE.sub("", raw.decode("utf-8", errors="replace")).strip()
        if not text:
            continue
        # Output can contain the authorize URL (with PKCE state) - never logged.
        match = URL_RE.search(text)
        if match and not session.get("authorization_url") and "oauth/authorize" in match.group(0):
            session["authorization_url"] = match.group(0).rstrip(".,;)]}")
            debug_log("    claude login: authorization url captured")
        session["last_output_len"] = len(text)
    try:
        stream.close()
    except OSError:
        pass


def _watch_login(session_id: str, process: subprocess.Popen[bytes]) -> None:
    session = CLAUDE_SESSIONS.get(session_id)
    if session is None:
        return
    try:
        readers = (
            threading.Thread(target=_read_login_stream, args=(session, process.stdout), daemon=True),
            threading.Thread(target=_read_login_stream, args=(session, process.stderr), daemon=True),
        )
        for reader in readers:
            reader.start()
        try:
            process.wait(timeout=CLAUDE_LOGIN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            _terminate_tree(process)
            session["status"] = "error"
            session["error"] = "Claude sign-in timed out. Start again."
            return
        for reader in readers:
            reader.join(timeout=1)
        if session.get("status") == "cancelled":
            return
        status = _claude_auth_status(session["home"]) if process.returncode == 0 else None
        if _is_subscription_login(status):
            session["status"] = "connected"
            session["subscription_type"] = status.get("subscription_type")
        else:
            session["status"] = "error"
            session["error"] = "Claude sign-in did not complete. Check the code and try again."
        debug_log("  claude login process exited: returncode=%s final_status=%s", process.returncode, session["status"])
    except Exception as exc:  # noqa: BLE001 - reader thread must never die silently
        session["status"] = "error"
        session["error"] = f"Claude login runner failed: {type(exc).__name__}"


def _pending_payload(session_id: str, session: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "status": "pending",
        "authorization_url": session.get("authorization_url"),
        "awaiting_code": not session.get("code_submitted"),
    }


async def start_claude_login(user_id: str, client_id: str, oauth_context: str | None = None) -> dict[str, Any]:
    command = _claude_command()
    session_id = _session_id(user_id, client_id)

    existing = CLAUDE_SESSIONS.get(session_id)
    if existing and existing["status"] == "pending":
        process = existing.get("process")
        fresh = (utc_now() - existing["started_at"]).total_seconds() < CLAUDE_LOGIN_TIMEOUT_SECONDS
        if process and process.poll() is None and existing.get("authorization_url") and fresh and not existing.get("code_submitted"):
            return _pending_payload(session_id, existing)
        if process:
            _terminate_tree(process)
    CLAUDE_SESSIONS.pop(session_id, None)

    home = _config_dir(user_id, client_id)
    # A previous failed/half-finished login must not leave stale state behind.
    _AUTH_FAILED.pop(str(home), None)
    process = subprocess.Popen(
        _popen_args(command, ["auth", "login", "--claudeai"]),
        cwd=str(_workspace(home)),
        env=_cli_env(home),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    CLAUDE_SESSIONS[session_id] = {
        "user_id": user_id,
        "client_id": client_id,
        "oauth_context": oauth_context or "app",
        "status": "pending",
        "process": process,
        "home": str(home),
        "started_at": utc_now(),
        "code_submitted": False,
        "login_attempt_id": urlsafe_token(16),
    }
    threading.Thread(target=_watch_login, args=(session_id, process), daemon=True).start()

    for _ in range(150):
        await asyncio.sleep(0.1)
        session = CLAUDE_SESSIONS[session_id]
        if session.get("authorization_url"):
            return _pending_payload(session_id, session)
        if session["status"] == "error":
            raise ValueError(session.get("error") or "Claude login failed")
        if process.poll() is not None:
            break
    _terminate_tree(process)
    CLAUDE_SESSIONS.pop(session_id, None)
    raise ValueError("Claude login did not provide a sign-in URL. Check the Claude Code CLI installation.")


async def submit_claude_login_code(user_id: str, client_id: str, code: str) -> dict[str, Any]:
    session_id = _session_id(user_id, client_id)
    session = CLAUDE_SESSIONS.get(session_id)
    clean = (code or "").strip()
    if not LOGIN_CODE_RE.match(clean):
        raise ValueError("That does not look like the code Claude shows after sign-in. Copy it exactly and try again.")
    if not session or session["status"] != "pending":
        raise ValueError("No Claude sign-in is in progress. Start the connection again.")
    process = session.get("process")
    if not process or process.poll() is not None or process.stdin is None:
        raise ValueError("The Claude sign-in expired. Start the connection again.")
    if session.get("code_submitted"):
        return _pending_payload(session_id, session)

    session["code_submitted"] = True
    try:
        process.stdin.write((clean + "\n").encode("utf-8"))
        process.stdin.flush()
    except OSError as exc:
        session["status"] = "error"
        session["error"] = "Could not pass the code to the Claude CLI. Start again."
        raise ValueError(session["error"]) from exc

    for _ in range(int(CLAUDE_STATUS_TIMEOUT_SECONDS * 4) * 4):
        await asyncio.sleep(0.25)
        if session["status"] != "pending":
            break
    else:
        _terminate_tree(process)
        session["status"] = "error"
        session["error"] = "Claude did not confirm the code in time. Start again."
    try:
        if process.stdin:
            process.stdin.close()
    except OSError:
        pass
    if session["status"] == "error":
        message = session.get("error") or "Claude sign-in failed"
        CLAUDE_SESSIONS.pop(session_id, None)
        raise ValueError(message)
    if session["status"] == "connected":
        await finalize_claude_login(session_id)
    return await get_claude_connection_status(user_id, client_id)


async def finalize_claude_login(session_id: str) -> bool:
    session = CLAUDE_SESSIONS.get(session_id)
    if not session or session["status"] != "connected":
        return False
    status = await asyncio.to_thread(_claude_auth_status, session["home"])
    if not _is_subscription_login(status):
        session["status"] = "error"
        session["error"] = "Claude sign-in was not fully authenticated"
        return False
    if session.get("finalized"):
        # This sign-in was already persisted and announced. The status call
        # runs this on every poll while the session stays in memory; repeating
        # the connect step would post a new "connected" notification and wipe
        # the discovered model list each time. The validity check above and the
        # auth-failure marker reset below still run on every call.
        _AUTH_FAILED.pop(session["home"], None)
        return True
    await _update_user_record(
        session["client_id"],
        session["user_id"],
        {
            "config_dir": session["home"],
            "auth_method": "claude.ai",
            "subscription_type": session.get("subscription_type") or status.get("subscription_type"),
            "connected_at": utc_now().isoformat(),
            # Fresh sign-in (possibly a different account/plan): drop the old
            # model cache so it is rediscovered instead of served stale.
            "available_models": [],
            "models_checked_at": None,
        },
    )
    await upsert_data_source_connected(session["client_id"], CLAUDE_SOURCE_TYPE, user_id=session["user_id"])
    session["finalized"] = True  # only after every step succeeded, so a failure is retried
    _AUTH_FAILED.pop(session["home"], None)
    return True


async def get_claude_connection_status(user_id: str, client_id: str) -> dict[str, Any]:
    session_id = _session_id(user_id, client_id)
    session = CLAUDE_SESSIONS.get(session_id)
    if session and session["status"] == "pending":
        return {"connected": False, **_pending_payload(session_id, session)}
    if session and session["status"] == "error":
        return {"connected": False, "status": "error", "error": session.get("error")}
    if session and session["status"] == "connected":
        await finalize_claude_login(session_id)

    record = await _get_user_record(client_id, user_id)
    if not record or not record.get("config_dir"):
        return {"connected": False, "status": "disabled", "storage": "none", "model": None, "available_models": []}

    config_dir = record["config_dir"]
    status = await asyncio.to_thread(_claude_auth_status, config_dir)
    if not _is_subscription_login(status) or config_dir in _AUTH_FAILED:
        return {
            "connected": False,
            "status": "needs_reconnect",
            "needs_reconnect": True,
            "error": "Your Claude sign-in expired or was revoked. Reconnect to continue.",
            "storage": "claude_config_dir",
            "model": (record.get("model") or None),
            "available_models": [],
        }
    available = record.get("available_models")
    return {
        "connected": True,
        "status": "connected",
        "session_id": session_id if session else None,
        "storage": "claude_config_dir",
        "model": (record.get("model") or "").strip() or None,
        "available_models": available if isinstance(available, list) else [],
        "models_checked_at": record.get("models_checked_at"),
        "subscription_type": record.get("subscription_type"),
        "login_attempt_id": (session or {}).get("login_attempt_id"),
    }


async def disconnect_claude_code(user_id: str, client_id: str) -> None:
    """Sign this user out and delete their isolated config dir. Only THIS
    user's entry is touched; teammates' connections are left alone."""
    session_id = _session_id(user_id, client_id)
    session = CLAUDE_SESSIONS.pop(session_id, None)
    if session:
        session["status"] = "cancelled"
        if session.get("process"):
            _terminate_tree(session["process"])

    record = await _get_user_record(client_id, user_id)
    config_dir = (record or {}).get("config_dir") or str(_config_dir(user_id, client_id))
    path = Path(config_dir)
    if path.is_dir() and _inside_root(path):
        await asyncio.to_thread(_logout_and_remove, path)
    _AUTH_FAILED.pop(str(path), None)

    async with _row_lock(client_id):
        token, users = await _read_users(client_id)
        users.pop(user_id, None)
        if users:
            await _write_users(client_id, token, users, user_id=user_id)
        else:
            await delete_connector_token(client_id, CLAUDE_TOKEN_SOURCE_TYPE)
            await mark_data_source_disconnected(client_id, CLAUDE_SOURCE_TYPE, user_id=user_id)


def _logout_and_remove(path: Path) -> None:
    try:
        process = subprocess.Popen(
            _popen_args(_claude_command(), ["auth", "logout"]),
            cwd=str(_workspace(path)),
            env=_cli_env(path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            _terminate_tree(process)
    except (OSError, ValueError):
        pass
    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaudeExecResult:
    text: str
    actual_model: str | None


def _exec_lock(config_dir: str) -> threading.Lock:
    with _EXEC_LOCKS_GUARD:
        return _EXEC_LOCKS.setdefault(config_dir, threading.Lock())


def _pick_actual_model(payload: dict[str, Any]) -> str | None:
    usage = payload.get("modelUsage")
    if not isinstance(usage, dict) or not usage:
        return None

    def output_tokens(item: Any) -> int:
        return int(item.get("outputTokens") or 0) if isinstance(item, dict) else 0

    return max(usage, key=lambda name: output_tokens(usage[name]))


def _parse_result_payload(stdout: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(stdout)
    except ValueError:
        return None
    if isinstance(parsed, list):
        parsed = next((item for item in reversed(parsed) if isinstance(item, dict) and item.get("type") == "result"), None)
    return parsed if isinstance(parsed, dict) else None


def _claude_exec_raw(
    config_dir: str,
    prompt: str,
    system_prompt: str | None,
    model: str | None,
    timeout_seconds: float,
) -> ClaudeExecResult:
    """One locked-down, non-interactive `claude -p` turn in this user's
    isolated config dir. The prompt goes over STDIN (no argv length limit, no
    cmd.exe quoting), the system prompt via a temp file inside the private
    workspace. All built-in tools, MCP, skills, settings files and session
    persistence are off: the model can only answer from the text it is given.
    Raises ClaudeAuthError for sign-in problems and ValueError otherwise."""
    if model is not None and not MODEL_ID_RE.match(model):
        raise ValueError("Invalid Claude model id")
    home = Path(config_dir)
    if not home.is_dir() or not _inside_root(home):
        raise ClaudeAuthError("Claude is not connected for this user")

    command = _claude_command()
    workspace = _workspace(home)
    args = [
        "-p",
        "--output-format", "json",
        "--no-session-persistence",
        "--tools", "",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--disable-slash-commands",
        "--permission-mode", "dontAsk",
    ]
    prompt_file: Path | None = None
    if system_prompt:
        prompt_file = workspace / f"system-{urlsafe_token(8)}.txt"
        prompt_file.write_text(system_prompt, encoding="utf-8")
        args.extend(["--system-prompt-file", prompt_file.name])
    if model:
        args.extend(["--model", model])

    lock = _exec_lock(config_dir)
    if not lock.acquire(timeout=CLAUDE_BUSY_WAIT_SECONDS):
        raise ValueError("Claude is busy with another request for your account. Try again in a moment.")
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            _popen_args(command, args),
            cwd=str(workspace),
            env=_cli_env(home),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = process.communicate(prompt.encode("utf-8"), timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            _terminate_tree(process)
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            raise ValueError("Claude took too long to respond. Try a narrower question.") from exc
    finally:
        lock.release()
        if prompt_file is not None:
            try:
                prompt_file.unlink()
            except OSError:
                pass

    stdout = ANSI_RE.sub("", stdout_bytes.decode("utf-8", errors="replace")).strip()
    stderr = ANSI_RE.sub("", stderr_bytes.decode("utf-8", errors="replace")).strip()
    debug_log(
        "    claude exec: exited in %.2fs returncode=%s stdout_len=%d stderr_len=%d",
        time.monotonic() - started, process.returncode, len(stdout), len(stderr),
    )
    payload = _parse_result_payload(stdout)
    if payload is None:
        if _looks_like_auth_failure(stdout) or _looks_like_auth_failure(stderr):
            _AUTH_FAILED[config_dir] = time.monotonic()
            raise ClaudeAuthError("Your Claude sign-in expired. Reconnect Claude from Integrations.")
        raise ValueError(stderr[:300] or "Claude Code exited without a result")

    result_text = str(payload.get("result") or "").strip()
    if payload.get("is_error") or process.returncode != 0:
        if _looks_like_auth_failure(result_text):
            _AUTH_FAILED[config_dir] = time.monotonic()
            raise ClaudeAuthError("Your Claude sign-in expired. Reconnect Claude from Integrations.")
        raise ValueError(result_text[:300] or "Claude Code reported an error")
    return ClaudeExecResult(text=result_text, actual_model=_pick_actual_model(payload))


def run_claude_exec(
    config_dir: str,
    prompt: str,
    system_prompt: str | None = None,
    model: str | None = None,
) -> ClaudeExecResult:
    debug_log("  run_claude_exec: model=%s prompt_len=%d", model, len(prompt))
    return _claude_exec_raw(config_dir, prompt, system_prompt, model, CLAUDE_EXEC_TIMEOUT_SECONDS)


async def get_claude_chat_context(client_id: str, user_id: str | None) -> dict[str, Any] | None:
    """Per-USER lookup for Ask AI: the requester's own config dir, never a
    teammate's. No user (or no record) -> None, so the provider is simply not
    offered and can never fall back to someone else's account."""
    if not user_id:
        return None
    record = await _get_user_record(client_id, user_id)
    if not record:
        return None
    config_dir = record.get("config_dir")
    if not config_dir or not Path(config_dir).is_dir() or config_dir in _AUTH_FAILED:
        return None
    return {"config_dir": config_dir, "model": (record.get("model") or "").strip() or None}


# ---------------------------------------------------------------------------
# Model discovery: no CLI/API lists models, so each alias is really run once
# and the model Claude Code actually used (modelUsage) is compared with it.
# `--model` for a blocked/unknown model is silently swapped for the default,
# so "the command succeeded" is NOT proof the requested model ran.
# ---------------------------------------------------------------------------


def _humanize_claude_model(model_id: str | None) -> str | None:
    """"claude-opus-5-5[1m]" -> "Opus 5.5", "claude-haiku-4-5-20251001" ->
    "Haiku 4.5", "claude-sonnet-5" -> "Sonnet 5". None if the id has an
    unrecognized shape (callers then fall back to the plain alias label)."""
    match = _MODEL_ID_NAME_RE.match(model_id or "")
    if not match:
        return None
    family, major, minor = match.groups()
    return f"{family.capitalize()} {major}.{minor}" if minor else f"{family.capitalize()} {major}"


def _alias_matches(alias: str, actual_model: str | None) -> bool:
    return bool(actual_model) and alias.lower() in actual_model.lower()


def _previously_verified(previous: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Entries an earlier discovery really verified, by id. An inconclusive
    probe (timeout / throttle / busy CLI) keeps these instead of erasing them."""
    return {
        entry["id"]: entry
        for entry in (previous or [])
        if isinstance(entry, dict) and entry.get("id")
        and (entry.get("supported") is True or entry.get("supported") == CREDITS_REQUIRED)
    }


def discover_claude_models(
    config_dir: str,
    previous: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Mirrors Claude Code's own model picker for THIS signed-in account:
    Default, then each alias. Per entry `supported` is True (really ran, on
    the family asked for), "requires_credits" (visible but needs usage
    credits on this plan), False (Claude Code silently substituted another
    family - hidden) or None (inconclusive - hidden, retried soon). A None
    probe keeps the entry `previous` had verified, so one flaky run never
    shrinks the picker down to just Default."""
    kept = _previously_verified(previous)
    unknown = [
        kept.get(alias) or {
            "id": alias, "display_name": label, "description": description,
            "actual_model": None, "supported": None, "default": False,
        }
        for alias, label, description in CLAUDE_MODEL_ALIASES
    ]
    try:
        default_result = run_claude_exec(config_dir, CLAUDE_PROBE_PROMPT)
    except ValueError as exc:
        debug_log("    discover_claude_models: default probe failed: %s", type(exc).__name__)
        return ([kept["default"]] if "default" in kept else []) + unknown

    default_actual = default_result.actual_model
    default_name = _humanize_claude_model(default_actual)
    results: list[dict[str, Any]] = [
        {
            "id": "default",
            "display_name": f"Default ({default_name})" if default_name else "Default",
            "description": "Claude Code's recommended model for your account",
            "actual_model": default_actual,
            "supported": True,
            "default": True,
        }
    ]
    for index, (alias, label, description) in enumerate(CLAUDE_MODEL_ALIASES):
        if index:
            time.sleep(CLAUDE_MODEL_PROBE_PACING_SECONDS)
        supported: bool | str | None
        actual: str | None = None
        name = label
        try:
            probe = _claude_exec_raw(config_dir, CLAUDE_PROBE_PROMPT, None, alias, CLAUDE_MODEL_PROBE_TIMEOUT_SECONDS)
            actual = probe.actual_model
            # Ran, but on a different family -> Claude Code substituted it.
            supported = True if _alias_matches(alias, actual) else (False if actual else None)
            name = _humanize_claude_model(actual) or label
        except ClaudeAuthError:
            supported = None
        except ValueError as exc:
            message = str(exc)
            if CREDITS_MARKER in message.lower():
                supported = CREDITS_REQUIRED
                named = _CREDITS_NAME_RE.match(message)
                name = named.group(1) if named else label
            else:
                debug_log("    discover_claude_models(%s): probe failed: %s", alias, type(exc).__name__)
                supported = None
        debug_log("    discover_claude_models(%s): supported=%s actual=%s", alias, supported, actual)
        if supported is None and alias in kept:
            results.append(kept[alias])
            continue
        results.append(
            {
                "id": alias,
                "display_name": name,
                "description": description,
                "actual_model": actual,
                "supported": supported,
                "default": False,
            }
        )
    return results


async def refresh_claude_models(client_id: str, user_id: str) -> list[dict[str, Any]]:
    lock = _MODELS_REFRESH_LOCKS.setdefault(f"{client_id}:{user_id}", asyncio.Lock())
    async with lock:
        record = await _get_user_record(client_id, user_id)
        if not record or not record.get("config_dir"):
            raise ValueError("Claude is not connected for your account")
        cached, checked_at = record.get("available_models"), record.get("models_checked_at")
        if isinstance(cached, list) and cached and isinstance(checked_at, str):
            try:
                age = (utc_now() - datetime.fromisoformat(checked_at)).total_seconds()
            except ValueError:
                age = None
            if age is not None and 0 <= age < CLAUDE_MODELS_REFRESH_DEDUPE_SECONDS:
                return cached
        config_dir = record["config_dir"]
        if not Path(config_dir).is_dir():
            raise ValueError("Claude connection is missing its config directory")

        try:
            models = await asyncio.to_thread(discover_claude_models, config_dir, cached)
        except ClaudeAuthError as exc:
            raise ValueError(str(exc)) from exc
        default_entry = next((m for m in models if m.get("default")), None)
        supported_ids = {m["id"] for m in models if m.get("supported")}
        changes: dict[str, Any] = {"available_models": models, "models_checked_at": utc_now().isoformat()}
        current = (record.get("model") or "").strip()
        if default_entry and (not current or current not in supported_ids):
            changes["model"] = default_entry["id"]
        await _update_user_record(client_id, user_id, changes)
        return models


async def list_claude_models(client_id: str, user_id: str, force_refresh: bool = False) -> list[dict[str, Any]]:
    record = await _get_user_record(client_id, user_id)
    if not record:
        raise ValueError("Claude is not connected for your account")
    cached, checked_at = record.get("available_models"), record.get("models_checked_at")
    # Lists cached by an earlier version have no "description" (and no
    # Default/credits-required entries) - rediscover once instead of serving them.
    if isinstance(cached, list) and not all(isinstance(e, dict) and "description" in e for e in cached):
        force_refresh = True
    if not force_refresh and isinstance(cached, list) and cached and isinstance(checked_at, str):
        try:
            age = (utc_now() - datetime.fromisoformat(checked_at)).total_seconds()
        except ValueError:
            age = None
        # A list with inconclusive probes is re-checked soon, not after 6h.
        partial = any(isinstance(e, dict) and e.get("supported") is None for e in cached)
        ttl = CLAUDE_MODELS_PARTIAL_RECHECK_SECONDS if partial else CLAUDE_MODELS_CACHE_TTL_SECONDS
        if age is not None and 0 <= age < ttl:
            return cached
    return await refresh_claude_models(client_id, user_id)
