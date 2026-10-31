# SPDX-License-Identifier: AGPL-3.0-only
import os
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from .core import urlsafe_token, utc_now
from .supabase_admin import (
    get_connector_token,
    upsert_connector_token,
    upsert_data_source_connected,
)

META_ADS_SOURCE_TYPE = "meta_ads"
META_ADS_SCOPES = "ads_read,business_management"
META_ADS_OAUTH_SESSIONS: dict[str, dict[str, Any]] = {}
META_ADS_MCP_TOKENS: dict[str, dict[str, Any]] = {}


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _api_version() -> str:
    return os.getenv("META_GRAPH_API_VERSION", "v20.0")


def _client_id() -> str:
    return _env_first("META_ADS_MCP_CLIENT_ID", "META_ADS_CLIENT_ID", "FACEBOOK_APP_ID")


def _client_secret() -> str:
    return _env_first("META_ADS_MCP_CLIENT_SECRET", "META_ADS_CLIENT_SECRET", "FACEBOOK_APP_SECRET")


def meta_ads_configured() -> bool:
    return bool(_client_id() and _client_secret() and meta_ads_mcp_url())


def meta_ads_redirect_uri() -> str:
    base_url = _env_first(
        "APP_BASE_URL",
        "BACKEND_PUBLIC_URL",
        "API_PUBLIC_URL",
        "APP_PUBLIC_URL",
        default="http://localhost:8000",
    ).rstrip("/")
    return f"{base_url}/py/connectors/meta-ads/oauth/callback"


def meta_ads_web_redirect_url(status_value: str, message: str | None = None) -> str:
    base_url = _env_first(
        "WEB_BASE_URL",
        "WEB_APP_URL",
        "APP_PUBLIC_URL",
        default="http://localhost:3000",
    ).rstrip("/")
    params = {"meta_ads": status_value}
    if message:
        params["message"] = message
    return f"{base_url}/?{urlencode(params)}"


def start_meta_ads_oauth_session(
    user_id: str,
    client_id: str,
    redirect_uri: str,
    oauth_context: str | None = None,
) -> str:
    state = f"onb.{urlsafe_token()}" if oauth_context == "onboarding" else urlsafe_token()
    META_ADS_OAUTH_SESSIONS[state] = {
        "user_id": user_id,
        "client_id": client_id,
        "oauth_context": oauth_context or "app",
        "redirect_uri": redirect_uri,
        "expires_at": utc_now() + timedelta(minutes=10),
    }
    return state


def meta_ads_authorization_url(redirect_uri: str, state: str) -> str:
    params = {
        "client_id": _client_id(),
        "redirect_uri": redirect_uri,
        "state": state,
        "response_type": "code",
        "scope": META_ADS_SCOPES,
    }
    return f"https://www.facebook.com/{_api_version()}/dialog/oauth?{urlencode(params)}"


async def _exchange_code(session: dict[str, Any], code: str) -> dict[str, Any]:
    url = f"https://graph.facebook.com/{_api_version()}/oauth/access_token"
    params = {
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "redirect_uri": session["redirect_uri"],
        "code": code,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


async def _exchange_long_lived(short_lived_token: str) -> dict[str, Any] | None:
    url = f"https://graph.facebook.com/{_api_version()}/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "fb_exchange_token": short_lived_token,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError):
        return None


async def complete_meta_ads_oauth(state: str, code: str) -> dict[str, Any]:
    session = META_ADS_OAUTH_SESSIONS.pop(state, None)
    if not session or session["expires_at"] <= utc_now():
        raise ValueError("OAuth session expired. Try connecting again.")

    token = await _exchange_code(session, code)
    long_lived = await _exchange_long_lived(token.get("access_token", ""))
    if long_lived and long_lived.get("access_token"):
        token.update(long_lived)

    expires_in = int(token.get("expires_in") or 3600)
    token["expires_at"] = (utc_now() + timedelta(seconds=expires_in)).isoformat()
    META_ADS_MCP_TOKENS[session["client_id"]] = token
    await upsert_connector_token(
        session["client_id"],
        META_ADS_SOURCE_TYPE,
        token,
        user_id=session.get("user_id"),
    )
    await upsert_data_source_connected(session["client_id"], META_ADS_SOURCE_TYPE, user_id=session.get("user_id"))
    return session


async def get_meta_ads_access_token(client_id: str) -> str | None:
    token = META_ADS_MCP_TOKENS.get(client_id)
    if not token:
        token = await get_connector_token(client_id, META_ADS_SOURCE_TYPE)
    if not token:
        return os.getenv("META_ADS_MCP_ACCESS_TOKEN")

    expires_at = token.get("expires_at")
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at)
        except ValueError:
            expiry = None
        if expiry and expiry <= utc_now() + timedelta(seconds=60):
            return None
    return token.get("access_token")


async def get_meta_ads_connection_status(client_id: str) -> dict[str, Any]:
    if META_ADS_MCP_TOKENS.get(client_id):
        return {"connected": True, "storage": "memory"}
    if await get_connector_token(client_id, META_ADS_SOURCE_TYPE):
        return {"connected": True, "storage": "database"}
    if os.getenv("META_ADS_MCP_ACCESS_TOKEN"):
        return {"connected": True, "storage": "env"}
    return {"connected": False, "storage": "none"}


def meta_ads_mcp_url() -> str:
    return os.getenv("META_ADS_MCP_URL", "")
