# SPDX-License-Identifier: AGPL-3.0-only
import os
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from .core import (
    GOOGLE_ADS_AUTHORIZE_URL,
    GOOGLE_ADS_TOKEN_URL,
    pkce_challenge,
    urlsafe_token,
    utc_now,
)
from .supabase_admin import (
    get_connector_token,
    upsert_connector_token,
    upsert_data_source_connected,
)

GOOGLE_ADS_SOURCE_TYPE = "google_ads"
GOOGLE_ADS_SCOPES = "https://www.googleapis.com/auth/adwords"
GOOGLE_ADS_OAUTH_SESSIONS: dict[str, dict[str, Any]] = {}
GOOGLE_ADS_MCP_TOKENS: dict[str, dict[str, Any]] = {}
def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _google_ads_client_id() -> str:
    return _env_first("GOOGLE_ADS_MCP_CLIENT_ID", "GOOGLE_ADS_MCP_OAUTH_CLIENT_ID")


def _google_ads_client_secret() -> str:
    return _env_first("GOOGLE_ADS_MCP_CLIENT_SECRET", "GOOGLE_ADS_MCP_OAUTH_CLIENT_SECRET")


def google_ads_configured() -> bool:
    return bool(
        _google_ads_client_id()
        and _google_ads_client_secret()
        and os.getenv("GOOGLE_ADS_MCP_URL")
    )


def google_ads_redirect_uri() -> str:
    base_url = _env_first("APP_BASE_URL", "BACKEND_PUBLIC_URL", "API_PUBLIC_URL", "APP_PUBLIC_URL", default="http://localhost:8000").rstrip("/")
    return f"{base_url}/py/connectors/google-ads/oauth/callback"


def google_ads_web_redirect_url(status_value: str, message: str | None = None) -> str:
    base_url = _env_first("WEB_BASE_URL", "WEB_APP_URL", "APP_PUBLIC_URL", default="http://localhost:3000").rstrip("/")
    params = {"google_ads": status_value}
    if message:
        params["message"] = message
    return f"{base_url}/?{urlencode(params)}"


def start_google_ads_oauth_session(
    user_id: str,
    client_id: str,
    redirect_uri: str,
    oauth_context: str | None = None,
) -> str:
    state = f"onb.{urlsafe_token()}" if oauth_context == "onboarding" else urlsafe_token()
    verifier = urlsafe_token(64)
    GOOGLE_ADS_OAUTH_SESSIONS[state] = {
        "user_id": user_id,
        "client_id": client_id,
        "oauth_context": oauth_context or "app",
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
        "expires_at": utc_now() + timedelta(minutes=10),
    }
    return state


def google_ads_authorization_url(redirect_uri: str, state: str) -> str:
    session = GOOGLE_ADS_OAUTH_SESSIONS[state]
    params = {
        "client_id": _google_ads_client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_ADS_SCOPES,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": pkce_challenge(session["code_verifier"]),
        "code_challenge_method": "S256",
    }
    return f"{GOOGLE_ADS_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_google_ads_code(session: dict[str, Any], code: str) -> dict[str, Any]:
    payload = {
        "grant_type": "authorization_code",
        "client_id": _google_ads_client_id(),
        "client_secret": _google_ads_client_secret(),
        "redirect_uri": session["redirect_uri"],
        "code": code,
        "code_verifier": session["code_verifier"],
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            GOOGLE_ADS_TOKEN_URL,
            data=payload,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()


async def refresh_google_ads_token(client_id: str, token: dict[str, Any]) -> dict[str, Any] | None:
    refresh_token = token.get("refresh_token")
    if (
        not refresh_token
        or not _google_ads_client_id()
        or not _google_ads_client_secret()
    ):
        return None

    payload = {
        "grant_type": "refresh_token",
        "client_id": _google_ads_client_id(),
        "client_secret": _google_ads_client_secret(),
        "refresh_token": refresh_token,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                GOOGLE_ADS_TOKEN_URL,
                data=payload,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            refreshed = response.json()
    except (httpx.HTTPError, ValueError):
        return None

    # Google does not always return a new refresh_token; keep the existing one
    if not refreshed.get("refresh_token"):
        refreshed["refresh_token"] = refresh_token

    refreshed["expires_at"] = (
        utc_now() + timedelta(seconds=int(refreshed.get("expires_in", 3600)))
    ).isoformat()
    GOOGLE_ADS_MCP_TOKENS[client_id] = refreshed
    await upsert_connector_token(client_id, GOOGLE_ADS_SOURCE_TYPE, refreshed)
    return refreshed


async def get_google_ads_access_token(client_id: str) -> str | None:
    token = GOOGLE_ADS_MCP_TOKENS.get(client_id)
    if not token:
        token = await get_connector_token(client_id, GOOGLE_ADS_SOURCE_TYPE)
    if not token:
        return os.getenv("GOOGLE_ADS_MCP_ACCESS_TOKEN")

    expires_at = token.get("expires_at")
    if expires_at:
        expiry = datetime.fromisoformat(expires_at)
        if expiry <= utc_now() + timedelta(seconds=60):
            token = await refresh_google_ads_token(client_id, token)
            if not token:
                return None
    return token.get("access_token")


async def complete_google_ads_oauth(state: str, code: str) -> dict[str, Any]:
    session = GOOGLE_ADS_OAUTH_SESSIONS.pop(state, None)
    if not session or session["expires_at"] <= utc_now():
        raise ValueError("OAuth session expired. Try connecting again.")

    token = await exchange_google_ads_code(session, code)
    token["expires_at"] = (
        utc_now() + timedelta(seconds=int(token.get("expires_in", 3600)))
    ).isoformat()
    GOOGLE_ADS_MCP_TOKENS[session["client_id"]] = token
    await upsert_connector_token(
        session["client_id"],
        GOOGLE_ADS_SOURCE_TYPE,
        token,
        user_id=session.get("user_id"),
    )
    await upsert_data_source_connected(session["client_id"], GOOGLE_ADS_SOURCE_TYPE, user_id=session.get("user_id"))
    return session


async def has_google_ads_stored_token(client_id: str) -> bool:
    return bool(
        GOOGLE_ADS_MCP_TOKENS.get(client_id)
        or await get_connector_token(client_id, GOOGLE_ADS_SOURCE_TYPE)
    )


async def get_google_ads_connection_status(client_id: str) -> dict[str, Any]:
    if GOOGLE_ADS_MCP_TOKENS.get(client_id):
        return {"connected": True, "storage": "memory"}
    if await get_connector_token(client_id, GOOGLE_ADS_SOURCE_TYPE):
        return {"connected": True, "storage": "database"}
    if os.getenv("GOOGLE_ADS_MCP_ACCESS_TOKEN"):
        return {"connected": True, "storage": "env"}
    return {"connected": False, "storage": "none"}


def google_ads_mcp_url() -> str:
    return os.getenv("GOOGLE_ADS_MCP_URL", "")
