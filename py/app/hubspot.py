# SPDX-License-Identifier: AGPL-3.0-only
import os
import json
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status

from .core import (
    HUBSPOT_AUTHORIZE_URL,
    HUBSPOT_MCP_URL,
    HUBSPOT_TOKEN_URL,
    debug_log,
    pkce_challenge,
    urlsafe_token,
    utc_now,
)
from .schemas import ChatRequest
from .supabase_admin import (
    get_connector_token,
    upsert_connector_token,
    upsert_data_source_connected,
)

HUBSPOT_SOURCE_TYPE = "hubspot_crm"
HUBSPOT_OAUTH_SESSIONS: dict[str, dict[str, Any]] = {}
HUBSPOT_MCP_TOKENS: dict[str, dict[str, Any]] = {}


def hubspot_configured() -> bool:
    return bool(
        os.getenv("HUBSPOT_MCP_CLIENT_ID")
        and os.getenv("HUBSPOT_MCP_CLIENT_SECRET")
        and hubspot_scopes()
    )


def hubspot_scopes() -> str:
    return os.getenv(
        "HUBSPOT_MCP_SCOPES",
        "crm.objects.contacts.read crm.objects.companies.read crm.objects.deals.read",
    ).strip()


def hubspot_redirect_uri() -> str:
    base_url = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base_url}/py/connectors/hubspot/oauth/callback"


def hubspot_web_redirect_url(status_value: str, message: str | None = None) -> str:
    base_url = os.getenv("WEB_BASE_URL", "http://localhost:3000").rstrip("/")
    params = {"hubspot": status_value}
    if message:
        params["message"] = message
    return f"{base_url}/?{urlencode(params)}"


def start_hubspot_oauth_session(
    user_id: str,
    client_id: str,
    redirect_uri: str,
    oauth_context: str | None = None,
) -> str:
    state = f"onb.{urlsafe_token()}" if oauth_context == "onboarding" else urlsafe_token()
    verifier = urlsafe_token(64)
    HUBSPOT_OAUTH_SESSIONS[state] = {
        "user_id": user_id,
        "client_id": client_id,
        "oauth_context": oauth_context or "app",
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
        "expires_at": utc_now() + timedelta(minutes=10),
    }
    return state


def hubspot_authorization_url(redirect_uri: str, state: str) -> str:
    session = HUBSPOT_OAUTH_SESSIONS[state]
    params = {
        "client_id": os.getenv("HUBSPOT_MCP_CLIENT_ID", ""),
        "redirect_uri": redirect_uri,
        "scope": hubspot_scopes(),
        "state": state,
        "code_challenge": pkce_challenge(session["code_verifier"]),
        "code_challenge_method": "S256",
    }
    return f"{HUBSPOT_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_hubspot_code(session: dict[str, Any], code: str) -> dict[str, Any]:
    payload = {
        "grant_type": "authorization_code",
        "client_id": os.getenv("HUBSPOT_MCP_CLIENT_ID"),
        "client_secret": os.getenv("HUBSPOT_MCP_CLIENT_SECRET"),
        "redirect_uri": session["redirect_uri"],
        "code": code,
        "code_verifier": session["code_verifier"],
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            HUBSPOT_TOKEN_URL,
            data=payload,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()


async def refresh_hubspot_token(client_id: str, token: dict[str, Any]) -> dict[str, Any] | None:
    refresh_token = token.get("refresh_token")
    if not refresh_token or not os.getenv("HUBSPOT_MCP_CLIENT_ID") or not os.getenv("HUBSPOT_MCP_CLIENT_SECRET"):
        return None

    payload = {
        "grant_type": "refresh_token",
        "client_id": os.getenv("HUBSPOT_MCP_CLIENT_ID"),
        "client_secret": os.getenv("HUBSPOT_MCP_CLIENT_SECRET"),
        "refresh_token": refresh_token,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                HUBSPOT_TOKEN_URL,
                data=payload,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            refreshed = response.json()
    except (httpx.HTTPError, ValueError):
        return None

    refreshed["expires_at"] = (
        utc_now() + timedelta(seconds=int(refreshed.get("expires_in", 1800)))
    ).isoformat()
    HUBSPOT_MCP_TOKENS[client_id] = refreshed
    await upsert_connector_token(client_id, HUBSPOT_SOURCE_TYPE, refreshed)
    return refreshed


async def get_hubspot_access_token(client_id: str) -> str | None:
    token = HUBSPOT_MCP_TOKENS.get(client_id)
    if not token:
        token = await get_connector_token(client_id, HUBSPOT_SOURCE_TYPE)
    if not token:
        return os.getenv("HUBSPOT_MCP_ACCESS_TOKEN")

    expires_at = token.get("expires_at")
    if expires_at:
        expiry = datetime.fromisoformat(expires_at)
        if expiry <= utc_now() + timedelta(seconds=60):
            token = await refresh_hubspot_token(client_id, token)
            if not token:
                return None
    return token.get("access_token")


async def complete_hubspot_oauth(state: str, code: str) -> dict[str, Any]:
    session = HUBSPOT_OAUTH_SESSIONS.pop(state, None)
    if not session or session["expires_at"] <= utc_now():
        raise ValueError("OAuth session expired. Try connecting again.")

    token = await exchange_hubspot_code(session, code)
    token["expires_at"] = (
        utc_now() + timedelta(seconds=int(token.get("expires_in", 1800)))
    ).isoformat()
    HUBSPOT_MCP_TOKENS[session["client_id"]] = token
    await upsert_connector_token(
        session["client_id"],
        HUBSPOT_SOURCE_TYPE,
        token,
        user_id=session.get("user_id"),
    )
    await upsert_data_source_connected(session["client_id"], HUBSPOT_SOURCE_TYPE, user_id=session.get("user_id"))
    return session


async def has_hubspot_stored_token(client_id: str) -> bool:
    return bool(
        HUBSPOT_MCP_TOKENS.get(client_id)
        or await get_connector_token(client_id, HUBSPOT_SOURCE_TYPE)
    )


async def get_hubspot_connection_status(client_id: str) -> dict[str, Any]:
    if HUBSPOT_MCP_TOKENS.get(client_id):
        return {"connected": True, "storage": "memory"}
    if await get_connector_token(client_id, HUBSPOT_SOURCE_TYPE):
        return {"connected": True, "storage": "database"}
    if os.getenv("HUBSPOT_MCP_ACCESS_TOKEN"):
        return {"connected": True, "storage": "env"}
    return {"connected": False, "storage": "none"}


def hubspot_mcp_url() -> str:
    return os.getenv("HUBSPOT_MCP_URL", HUBSPOT_MCP_URL)


def hubspot_direct_intent_for_question(request: ChatRequest, tool_labels: list[str]) -> str | None:
    if "hubspot_crm" not in tool_labels:
        return None
    text = request.question.lower()
    if "hubspot" in text and ("contact" in text or "contacts" in text):
        return "list_contacts"
    return None


def parse_mcp_json_response(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        return response.json()

    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line.removeprefix("data:").strip()
        if payload and payload != "[DONE]":
            return json.loads(payload)
    return {}


def select_hubspot_tool_name(intent: str, available_tools: list[dict[str, Any]]) -> str | None:
    names = [str(tool.get("name", "")) for tool in available_tools if tool.get("name")]
    if intent == "list_contacts":
        candidates = ("search_crm_objects", "hubspot_search_crm_objects")
        for candidate in candidates:
            if candidate in names:
                return candidate
        for name in names:
            if "search" in name and "crm" in name:
                return name
    return None


def build_hubspot_tool_arguments(intent: str, tool_name: str) -> dict[str, Any]:
    if intent == "list_contacts" and "search_crm_objects" in tool_name:
        return {
            "objectType": "contacts",
            "limit": 10,
            "properties": ["firstname", "lastname", "email", "company", "phone", "createdate"],
        }
    return {}


async def call_hubspot_mcp_tool(client_id: str, intent: str) -> dict[str, Any]:
    token = await get_hubspot_access_token(client_id)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HubSpot MCP token is missing",
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    initialize_payload = {
        "jsonrpc": "2.0",
        "id": "initialize",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "braidscope", "version": "0.1.0"},
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        initialize_response = await client.post(
            hubspot_mcp_url(),
            headers=headers,
            json=initialize_payload,
        )
        initialize_response.raise_for_status()
        session_id = initialize_response.headers.get("mcp-session-id")
        if session_id:
            headers["Mcp-Session-Id"] = session_id
            await client.post(
                hubspot_mcp_url(),
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
            )

        tools_response = await client.post(
            hubspot_mcp_url(),
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": "tools-list",
                "method": "tools/list",
                "params": {},
            },
        )
        tools_response.raise_for_status()
        tools_payload = parse_mcp_json_response(tools_response)
        available_tools = tools_payload.get("result", {}).get("tools", [])
        available_names = [tool.get("name") for tool in available_tools if tool.get("name")]
        debug_log("HubSpot MCP available tools=%s", available_names)
        tool_name = select_hubspot_tool_name(intent, available_tools)
        if not tool_name:
            return {
                "error": f"No HubSpot MCP tool matched intent: {intent}",
                "available_tools": available_names,
            }

        tool_response = await client.post(
            hubspot_mcp_url(),
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": "tool-call",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": build_hubspot_tool_arguments(intent, tool_name),
                },
            },
        )
        tool_response.raise_for_status()
        return parse_mcp_json_response(tool_response)


async def maybe_get_direct_hubspot_result(
    request: ChatRequest,
    client_context: dict[str, Any],
    tools: list[dict[str, Any]],
) -> dict[str, Any] | None:
    tool_labels = [tool.get("server_label") for tool in tools]
    intent = hubspot_direct_intent_for_question(request, tool_labels)
    if not intent:
        return None

    debug_log(
        "Calling HubSpot MCP directly intent=%s client_id=%s",
        intent,
        client_context["id"],
    )
    result = await call_hubspot_mcp_tool(client_context["id"], intent)
    return {
        "server_label": "hubspot_crm",
        "intent": intent,
        "result": result,
    }
