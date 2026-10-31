# SPDX-License-Identifier: AGPL-3.0-only
import re
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from .core import pkce_challenge, urlsafe_token, utc_now
from .supabase_admin import (
    delete_connector_token,
    get_connector_token,
    mark_data_source_disconnected,
    supabase_admin_configured,
    upsert_connector_token,
    upsert_data_source_connected,
)

OPENROUTER_SOURCE_TYPE = "openrouter_ai"
OPENROUTER_AUTH_URL = "https://openrouter.ai/auth"
OPENROUTER_KEY_EXCHANGE_URL = "https://openrouter.ai/api/v1/auth/keys"
DEFAULT_OPENROUTER_MODEL = "moonshotai/kimi-k2.6:free"
MODEL_SLUG_RE = re.compile(r"^[a-zA-Z0-9._:-]+/[a-zA-Z0-9._:+-]+$")

OPENROUTER_OAUTH_SESSIONS: dict[str, dict[str, Any]] = {}
OPENROUTER_KEYS: dict[str, dict[str, Any]] = {}


def connector_cipher() -> Fernet:
    import os

    key = os.getenv("CONNECTOR_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise ValueError("CONNECTOR_TOKEN_ENCRYPTION_KEY is required for OpenRouter OAuth")
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, InvalidToken) as exc:
        raise ValueError("CONNECTOR_TOKEN_ENCRYPTION_KEY is invalid") from exc


def encrypt_api_key(api_key: str) -> str:
    return connector_cipher().encrypt(api_key.encode("utf-8")).decode("utf-8")


def decrypt_api_key(encrypted: str) -> str:
    try:
        return connector_cipher().decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Stored OpenRouter API key could not be decrypted") from exc


def normalize_openrouter_model(model: str | None) -> str:
    candidate = (model or "").strip() or DEFAULT_OPENROUTER_MODEL
    if not MODEL_SLUG_RE.match(candidate):
        return DEFAULT_OPENROUTER_MODEL
    return candidate


def openrouter_callback_uri() -> str:
    import os

    base_url = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base_url}/py/connectors/openrouter/oauth/callback"


def openrouter_web_redirect_url(status_value: str, message: str | None = None) -> str:
    import os

    base_url = os.getenv("WEB_BASE_URL", "http://localhost:3000").rstrip("/")
    params = {"openrouter": status_value}
    if message:
        params["message"] = message
    return f"{base_url}/?{urlencode(params)}"


def start_openrouter_oauth_session(
    user_id: str,
    client_id: str,
    model: str | None = None,
    oauth_context: str | None = None,
) -> tuple[str, str]:
    state = f"onb.{urlsafe_token()}" if oauth_context == "onboarding" else urlsafe_token()
    verifier = urlsafe_token(64)
    OPENROUTER_OAUTH_SESSIONS[state] = {
        "user_id": user_id,
        "client_id": client_id,
        "code_verifier": verifier,
        "model": normalize_openrouter_model(model),
        "oauth_context": oauth_context or "app",
        "expires_at": utc_now() + timedelta(minutes=10),
    }
    return state, verifier


def openrouter_authorization_url(state: str) -> str:
    session = OPENROUTER_OAUTH_SESSIONS[state]
    callback_url = f"{openrouter_callback_uri()}?state={state}"
    params = {
        "callback_url": callback_url,
        "code_challenge": pkce_challenge(session["code_verifier"]),
        "code_challenge_method": "S256",
    }
    return f"{OPENROUTER_AUTH_URL}?{urlencode(params)}"


async def exchange_openrouter_code(session: dict[str, Any], code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            OPENROUTER_KEY_EXCHANGE_URL,
            json={
                "code": code,
                "code_verifier": session["code_verifier"],
                "code_challenge_method": "S256",
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()


async def complete_openrouter_oauth(state: str, code: str) -> dict[str, Any]:
    session = OPENROUTER_OAUTH_SESSIONS.pop(state, None)
    if not session or session["expires_at"] <= utc_now():
        raise ValueError("OpenRouter OAuth session expired. Try connecting again.")
    if not supabase_admin_configured():
        raise ValueError(
            "OpenRouter token storage is not configured. Set SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY on the backend."
        )

    token_payload = await exchange_openrouter_code(session, code)
    api_key = token_payload.get("key")
    if not api_key:
        raise ValueError("OpenRouter did not return an API key.")

    memory_token = {
        "api_key": api_key,
        "model": session["model"],
        "provider": "openrouter",
        "created_at": utc_now().isoformat(),
    }
    stored = {
        "api_key_encrypted": encrypt_api_key(api_key),
        "model": session["model"],
        "provider": "openrouter",
        "created_at": memory_token["created_at"],
    }
    OPENROUTER_KEYS[session["client_id"]] = memory_token
    await upsert_connector_token(
        session["client_id"],
        OPENROUTER_SOURCE_TYPE,
        stored,
        user_id=session.get("user_id"),
    )
    await upsert_data_source_connected(session["client_id"], OPENROUTER_SOURCE_TYPE, user_id=session.get("user_id"))
    return session


async def get_openrouter_connection_status(client_id: str) -> dict[str, Any]:
    token = OPENROUTER_KEYS.get(client_id) or await get_connector_token(
        client_id,
        OPENROUTER_SOURCE_TYPE,
    )
    if token:
        return {
            "connected": True,
            "storage": "memory" if client_id in OPENROUTER_KEYS else "database",
            "model": normalize_openrouter_model(token.get("model")),
        }
    return {"connected": False, "storage": "none", "model": DEFAULT_OPENROUTER_MODEL}


async def get_openrouter_chat_config(client_id: str) -> dict[str, str] | None:
    import os

    token = OPENROUTER_KEYS.get(client_id) or await get_connector_token(
        client_id,
        OPENROUTER_SOURCE_TYPE,
    )
    if token and (token.get("api_key") or token.get("api_key_encrypted")):
        api_key = token.get("api_key")
        if not api_key:
            api_key = decrypt_api_key(token["api_key_encrypted"])
        return {
            "provider": "openrouter",
            "api_key": api_key,
            "base_url": os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            "model": normalize_openrouter_model(token.get("model")),
        }

    return None


async def disconnect_openrouter(client_id: str, user_id: str | None = None) -> None:
    OPENROUTER_KEYS.pop(client_id, None)
    await delete_connector_token(client_id, OPENROUTER_SOURCE_TYPE)
    await mark_data_source_disconnected(client_id, OPENROUTER_SOURCE_TYPE, user_id=user_id)
