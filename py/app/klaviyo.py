# SPDX-License-Identifier: AGPL-3.0-only
import json
import os
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status

from .core import (
    KLAVIYO_MCP_URL,
    KLAVIYO_OAUTH_ISSUER,
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

KLAVIYO_OAUTH_SESSIONS: dict[str, dict[str, Any]] = {}
KLAVIYO_MCP_TOKENS: dict[str, dict[str, Any]] = {}


def has_klaviyo_memory_token(client_id: str) -> bool:
    return client_id in KLAVIYO_MCP_TOKENS


async def has_klaviyo_stored_token(client_id: str) -> bool:
    return bool(KLAVIYO_MCP_TOKENS.get(client_id) or await get_connector_token(client_id, "klaviyo"))


async def get_klaviyo_connection_status(client_id: str) -> dict[str, Any]:
    if KLAVIYO_MCP_TOKENS.get(client_id):
        return {"connected": True, "storage": "memory"}
    if await get_connector_token(client_id, "klaviyo"):
        return {"connected": True, "storage": "database"}
    if os.getenv("KLAVIYO_MCP_ACCESS_TOKEN"):
        return {"connected": True, "storage": "env"}
    return {"connected": False, "storage": "none"}


def klaviyo_redirect_uri() -> str:
    base_url = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base_url}/py/connectors/klaviyo/oauth/callback"


def web_redirect_url(status_value: str, message: str | None = None) -> str:
    base_url = os.getenv("WEB_BASE_URL", "http://localhost:3000").rstrip("/")
    params = {"klaviyo": status_value}
    if message:
        params["message"] = message
    return f"{base_url}/?{urlencode(params)}"


async def register_klaviyo_oauth_client(redirect_uri: str) -> str:
    payload = {
        "client_name": "Braidscope",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(f"{KLAVIYO_OAUTH_ISSUER}/register", json=payload)
        response.raise_for_status()
        data = response.json()
    return data["client_id"]


async def exchange_klaviyo_code(session: dict[str, Any], code: str) -> dict[str, Any]:
    payload = {
        "grant_type": "authorization_code",
        "client_id": session["oauth_client_id"],
        "code": code,
        "redirect_uri": session["redirect_uri"],
        "code_verifier": session["code_verifier"],
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{KLAVIYO_OAUTH_ISSUER}/token",
            data=payload,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()


async def refresh_klaviyo_token(client_id: str, token: dict[str, Any]) -> dict[str, Any] | None:
    refresh_token = token.get("refresh_token")
    oauth_client_id = token.get("oauth_client_id")
    if not refresh_token or not oauth_client_id:
        return None

    payload = {
        "grant_type": "refresh_token",
        "client_id": oauth_client_id,
        "refresh_token": refresh_token,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{KLAVIYO_OAUTH_ISSUER}/token",
                data=payload,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            refreshed = response.json()
    except (httpx.HTTPError, ValueError):
        return None

    refreshed["oauth_client_id"] = oauth_client_id
    refreshed["expires_at"] = (
        utc_now() + timedelta(seconds=int(refreshed.get("expires_in", 3600)))
    ).isoformat()
    KLAVIYO_MCP_TOKENS[client_id] = refreshed
    await upsert_connector_token(client_id, "klaviyo", refreshed)
    return refreshed


async def get_klaviyo_access_token(client_id: str) -> str | None:
    token = KLAVIYO_MCP_TOKENS.get(client_id)
    if not token:
        token = await get_connector_token(client_id, "klaviyo")
    if not token:
        return os.getenv("KLAVIYO_MCP_ACCESS_TOKEN")

    expires_at = token.get("expires_at")
    if expires_at:
        expiry = datetime.fromisoformat(expires_at)
        if expiry <= utc_now() + timedelta(seconds=60):
            token = await refresh_klaviyo_token(client_id, token)
            if not token:
                return None
    return token.get("access_token")


def start_klaviyo_oauth_session(
    user_id: str,
    client_id: str,
    oauth_client_id: str,
    redirect_uri: str,
    oauth_context: str | None = None,
) -> str:
    state = f"onb.{urlsafe_token()}" if oauth_context == "onboarding" else urlsafe_token()
    verifier = urlsafe_token(64)
    KLAVIYO_OAUTH_SESSIONS[state] = {
        "user_id": user_id,
        "client_id": client_id,
        "oauth_client_id": oauth_client_id,
        "oauth_context": oauth_context or "app",
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
        "expires_at": utc_now() + timedelta(minutes=10),
    }
    return state


def klaviyo_authorization_url(oauth_client_id: str, redirect_uri: str, state: str) -> str:
    session = KLAVIYO_OAUTH_SESSIONS[state]
    params = {
        "response_type": "code",
        "client_id": oauth_client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": pkce_challenge(session["code_verifier"]),
        "code_challenge_method": "S256",
        "resource": os.getenv("KLAVIYO_MCP_URL", KLAVIYO_MCP_URL),
    }
    return f"{KLAVIYO_OAUTH_ISSUER}/authorize?{urlencode(params)}"


async def complete_klaviyo_oauth(state: str, code: str) -> dict[str, Any]:
    session = KLAVIYO_OAUTH_SESSIONS.pop(state, None)
    if not session or session["expires_at"] <= utc_now():
        raise ValueError("OAuth session expired. Try connecting again.")

    token = await exchange_klaviyo_code(session, code)
    token["oauth_client_id"] = session["oauth_client_id"]
    token["expires_at"] = (
        utc_now() + timedelta(seconds=int(token.get("expires_in", 3600)))
    ).isoformat()
    KLAVIYO_MCP_TOKENS[session["client_id"]] = token
    await upsert_connector_token(
        session["client_id"],
        "klaviyo",
        token,
        user_id=session.get("user_id"),
    )
    await upsert_data_source_connected(session["client_id"], "klaviyo", user_id=session.get("user_id"))
    return session


def klaviyo_direct_intent_for_question(request: ChatRequest, tool_labels: list[str]) -> str | None:
    if "klaviyo" not in tool_labels:
        return None
    text = request.question.lower()
    account_keywords = (
        "account details",
        "account detail",
        "account info",
        "account information",
        "klaviyo account",
    )
    if any(keyword in text for keyword in account_keywords):
        return "account_details"
    return None


def select_klaviyo_tool_name(intent: str, available_tools: list[dict[str, Any]]) -> str | None:
    names = [str(tool.get("name", "")) for tool in available_tools if tool.get("name")]
    if intent == "account_details":
        candidates = (
            "get_account_details",
            "klaviyo_get_account_details",
            "account_details",
            "get_account",
        )
        for candidate in candidates:
            if candidate in names:
                return candidate
        for name in names:
            if "account" in name and "detail" in name:
                return name
    return None


KLAVIYO_TOOL_MODEL_LABEL = "braidscope-ai"


def build_klaviyo_tool_arguments(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Every one of Klaviyo's real MCP tools requires a top-level "model"
    field ("the name and version of the LLM currently using the tool") -
    confirmed live on 2026-09-23 against the real connected Klaviyo MCP
    server's actual tool schemas (get_account_details, query_metric_aggregates,
    and others all list "model" in their top-level `required`). None of the
    122 real tool names are prefixed "klaviyo_" - that prefix never appears
    anywhere in the real tool list - so the old `tool_name.startswith(...)`
    check never matched a single real tool, and every Klaviyo call failed
    with "1 validation error ... model: Missing required keyword only
    argument" exactly as seen live in Ask AI.
    """
    tool_arguments = dict(arguments or {})
    tool_arguments.setdefault("model", KLAVIYO_TOOL_MODEL_LABEL)
    return tool_arguments


def build_klaviyo_metric_aggregate_arguments(
    metric_id: str,
    start_date: str,
    end_date: str,
    measurements: list[str] | None = None,
) -> dict[str, Any]:
    """Klaviyo's real query_metric_aggregates tool requires a fully nested,
    JSON:API-style body - `body.data.attributes.{metric_id, measurements,
    filter, ...}` - never flat top-level fields, and its schema declares
    `additionalProperties: false` at every level, so any extra flat field
    (the old code sent `metricId`/`measurements`/`startDate`/`endDate` at
    the top level) causes the whole call to be rejected. The date range must
    be encoded as filter-function strings
    (`greater-or-equal(datetime,...)`/`less-than(datetime,...)`), not
    separate start/end fields. Confirmed live on 2026-09-23 against the real
    tool schema.
    """
    start = start_date.rstrip("Z")
    end = end_date.rstrip("Z")
    return build_klaviyo_tool_arguments(
        "query_metric_aggregates",
        {
            "body": {
                "data": {
                    "type": "metric-aggregate",
                    "attributes": {
                        "metric_id": metric_id,
                        "measurements": list(measurements or ["count"]),
                        "filter": [
                            f"greater-or-equal(datetime,{start})",
                            f"less-than(datetime,{end})",
                        ],
                    },
                }
            }
        },
    )


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


async def call_klaviyo_mcp_tool(
    client_id: str,
    intent: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    token = await get_klaviyo_access_token(client_id)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Klaviyo MCP token is missing",
        )

    server_url = os.getenv("KLAVIYO_MCP_URL", KLAVIYO_MCP_URL)
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
    # Confirmed live on 2026-09-24: this function makes 4 SEQUENTIAL,
    # uncached round trips (initialize, notifications/initialized,
    # tools/list, tools/call) to Klaviyo's hosted MCP server over the public
    # internet, each independently subject to this client's timeout - with
    # no retry wrapper (unlike mcp_orchestrator.py's _mcp_post_with_retry).
    # A real, healthy call completes each step in well under a second (the
    # actual tools/call step itself has been observed completing in ~4s);
    # a 30s-per-request timeout meant a degraded connection where most
    # attempts genuinely take the full timeout to fail could push a single
    # "Show me my Klaviyo account details" request to ~250s total - past
    # the frontend's 170s Ask AI request timeout (web/src/lib/chat.js) every
    # time, regardless of how many retries or fallback layers exist upstream.
    # 10s is still generous headroom above real observed latency while
    # keeping the worst case for all 4 steps combined under 40s.
    async with httpx.AsyncClient(timeout=10) as client:
        initialize_response = await client.post(
            server_url,
            headers=headers,
            json=initialize_payload,
        )
        initialize_response.raise_for_status()
        session_id = initialize_response.headers.get("mcp-session-id")
        if session_id:
            headers["Mcp-Session-Id"] = session_id
            await client.post(
                server_url,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
            )

        tools_response = await client.post(
            server_url,
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
        debug_log("Klaviyo MCP available tools=%s", available_names)
        tool_name = select_klaviyo_tool_name(intent, available_tools)
        if not tool_name:
            return {
                "error": f"No Klaviyo MCP tool matched intent: {intent}",
                "available_tools": available_names,
            }

        call_payload = {
            "jsonrpc": "2.0",
            "id": "tool-call",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": build_klaviyo_tool_arguments(tool_name, arguments),
            },
        }
        tool_response = await client.post(
            server_url,
            headers=headers,
            json=call_payload,
        )
        tool_response.raise_for_status()
        return parse_mcp_json_response(tool_response)


async def maybe_get_direct_klaviyo_result(
    request: ChatRequest,
    client_context: dict[str, Any],
    tools: list[dict[str, Any]],
) -> dict[str, Any] | None:
    tool_labels = [tool.get("server_label") for tool in tools]
    intent = klaviyo_direct_intent_for_question(request, tool_labels)
    if not intent:
        return None

    debug_log(
        "Calling Klaviyo MCP directly intent=%s client_id=%s",
        intent,
        client_context["id"],
    )
    result = await call_klaviyo_mcp_tool(client_context["id"], intent)
    return {
        "server_label": "klaviyo",
        "intent": intent,
        "result": result,
    }
