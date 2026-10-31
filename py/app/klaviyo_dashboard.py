# SPDX-License-Identifier: AGPL-3.0-only
"""
Klaviyo dashboard builder — assembles a structured dashboard payload
by calling the Klaviyo MCP server tools.

The Klaviyo OAuth token is scoped to the MCP server, not the REST API,
so we route all data fetches through MCP tool calls.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx

from .core import KLAVIYO_MCP_URL, logger, utc_now
from .klaviyo import build_klaviyo_tool_arguments, get_klaviyo_access_token, parse_mcp_json_response


# ── Helpers ──────────────────────────────────────────────────────────────────

def _format_number(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(int(round(value)))


def _extract_text(content: list[dict[str, Any]] | None) -> str:
    """Pull the text out of an MCP tool result content array."""
    if not content:
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("text"):
            parts.append(item["text"])
    return "\n".join(parts)


def _try_parse_json(text: str) -> Any:
    """Try to parse a JSON string, return the parsed value or None."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


# ── MCP session helpers ──────────────────────────────────────────────────────

async def _mcp_session(
    client: httpx.AsyncClient,
    token: str,
    server_url: str,
) -> dict[str, str]:
    """Initialize an MCP session and return headers with session id."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    init_resp = await client.post(
        server_url,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": "initialize",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "braidscope-dashboard", "version": "0.1.0"},
            },
        },
    )
    init_resp.raise_for_status()
    session_id = init_resp.headers.get("mcp-session-id")
    if session_id:
        headers["Mcp-Session-Id"] = session_id
        await client.post(
            server_url,
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
    return headers


async def _mcp_list_tools(
    client: httpx.AsyncClient,
    server_url: str,
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    resp = await client.post(
        server_url,
        headers=headers,
        json={"jsonrpc": "2.0", "id": "tools-list", "method": "tools/list", "params": {}},
    )
    resp.raise_for_status()
    payload = parse_mcp_json_response(resp)
    return payload.get("result", {}).get("tools", [])


async def _mcp_call_tool(
    client: httpx.AsyncClient,
    server_url: str,
    headers: dict[str, str],
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resp = await client.post(
        server_url,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": f"call-{tool_name}",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments or {}},
        },
    )
    resp.raise_for_status()
    return parse_mcp_json_response(resp)


def _find_tool(tools: list[dict[str, Any]], *candidates: str) -> str | None:
    names = {str(t.get("name", "")) for t in tools}
    for c in candidates:
        if c in names:
            return c
    # Fallback: partial match on first candidate keyword
    keyword = candidates[0].split("_")[-1] if candidates else ""
    for name in names:
        if keyword and keyword in name:
            return name
    return None


# ── Main builder ─────────────────────────────────────────────────────────────
#
# Caching is handled entirely by the generic Redis-backed
# get_or_refresh_dashboard_cache() wrapper in dashboard_cache.py, which
# main.py's /py/dashboards/klaviyo endpoint already applies around this
# function. The bespoke Supabase-based cache helpers previously defined here
# (_get_org_id/_get_cached_klaviyo/_save_klaviyo_cache) referenced
# supabase_rest_url/supabase_service_headers/supabase_admin_configured
# without ever importing them, were never actually called except for one
# crashing line below, and duplicated caching logic that already exists at
# the endpoint layer - removed rather than fixed, to avoid two competing
# cache mechanisms for the same dashboard.


async def build_klaviyo_dashboard(
    client_id: str, days: int = 30, force_refresh: bool = False,
) -> dict[str, Any]:
    """Build a Klaviyo dashboard payload via MCP tool calls.

    `force_refresh` is accepted (not used internally) purely to match the
    call signature main.py's /py/dashboards/klaviyo endpoint already uses -
    confirmed live on 2026-09-23 that calling this function without it
    raised `TypeError: build_klaviyo_dashboard() got an unexpected keyword
    argument 'force_refresh'` on every single request, before any Klaviyo
    API call was even attempted. Freshness is controlled entirely by the
    caller's get_or_refresh_dashboard_cache() wrapper.
    """
    token = await get_klaviyo_access_token(client_id)
    if not token:
        return {"ok": False, "reason": "Klaviyo is not connected for this client."}

    server_url = os.getenv("KLAVIYO_MCP_URL", KLAVIYO_MCP_URL)

    async with httpx.AsyncClient(timeout=45) as client:
        try:
            headers = await _mcp_session(client, token, server_url)
            tools = await _mcp_list_tools(client, server_url, headers)
        except httpx.HTTPError as exc:
            logger.warning("Klaviyo MCP session init failed: %s", exc)
            return {"ok": False, "reason": "Could not connect to the Klaviyo MCP server."}

        tool_names = [t.get("name", "") for t in tools]
        logger.info("Klaviyo MCP tools available for dashboard: %s", tool_names)

        # Try calling account details tool
        account_tool = _find_tool(
            tools,
            "get_account_details", "klaviyo_get_account_details",
            "account_details", "get_account",
        )

        # Try calling lists tool
        lists_tool = _find_tool(
            tools,
            "get_lists", "klaviyo_get_lists", "list_lists", "lists",
        )

        # Try calling campaigns tool
        campaigns_tool = _find_tool(
            tools,
            "get_campaigns", "klaviyo_get_campaigns", "list_campaigns", "campaigns",
        )

        # Try calling flows tool
        flows_tool = _find_tool(
            tools,
            "get_flows", "klaviyo_get_flows", "list_flows", "flows",
        )

        # Try calling profiles/segments tool
        profiles_tool = _find_tool(
            tools,
            "get_profiles", "klaviyo_get_profiles", "list_profiles", "profiles",
            "get_segments", "klaviyo_get_segments",
        )

        # Every real Klaviyo MCP tool requires a top-level "model" field, and
        # get_campaigns additionally requires a "filter" (its schema has no
        # "no filter" option) - confirmed live on 2026-09-23 against the
        # real tool schemas. build_klaviyo_tool_arguments() adds "model";
        # the campaigns filter defaults to email campaigns, the same
        # channel this dashboard's own campaign cards assume.
        #
        # These 5 calls used to run sequentially, each a real round trip to
        # Klaviyo's API - confirmed live on 2026-09-23 that this routinely
        # took longer than the frontend's fetch timeout, surfacing as
        # "Klaviyo dashboard request timed out" even once the underlying
        # calls succeeded. Running them concurrently (like
        # google_ads_dashboard.py already does) cuts total wall time from
        # the sum of all 5 calls to roughly the slowest single one.
        async def _fetch_account() -> dict[str, Any]:
            if not account_tool:
                return {}
            result = await _mcp_call_tool(
                client, server_url, headers, account_tool,
                build_klaviyo_tool_arguments(account_tool),
            )
            text = _extract_text(result.get("result", {}).get("content"))
            parsed = _try_parse_json(text)
            if isinstance(parsed, dict):
                return parsed
            return {"raw": text} if text else {}

        async def _fetch_lists() -> list[dict[str, Any]]:
            if not lists_tool:
                return []
            result = await _mcp_call_tool(
                client, server_url, headers, lists_tool,
                build_klaviyo_tool_arguments(lists_tool),
            )
            text = _extract_text(result.get("result", {}).get("content"))
            parsed = _try_parse_json(text)
            out: list[dict[str, Any]] = []
            if isinstance(parsed, list):
                out = [{"name": item.get("name", ""), "id": item.get("id", ""), "created": item.get("created", "")} for item in parsed]
            elif isinstance(parsed, dict) and parsed.get("data"):
                for item in parsed["data"]:
                    attrs = item.get("attributes", item)
                    out.append({"name": attrs.get("name", ""), "id": item.get("id", ""), "created": attrs.get("created", "")})
            return out

        async def _fetch_campaigns() -> list[dict[str, Any]]:
            if not campaigns_tool:
                return []
            result = await _mcp_call_tool(
                client, server_url, headers, campaigns_tool,
                build_klaviyo_tool_arguments(
                    campaigns_tool, {"filter": "equals(messages.channel,'email')"}
                ),
            )
            text = _extract_text(result.get("result", {}).get("content"))
            parsed = _try_parse_json(text)
            out: list[dict[str, Any]] = []
            if isinstance(parsed, list):
                out = [{"name": c.get("name", ""), "status": c.get("status", ""), "send_time": c.get("send_time", ""), "channel": c.get("channel", "email")} for c in parsed]
            elif isinstance(parsed, dict) and parsed.get("data"):
                for item in parsed["data"]:
                    attrs = item.get("attributes", item)
                    out.append({"name": attrs.get("name", ""), "status": attrs.get("status", ""), "send_time": attrs.get("send_time", ""), "channel": "email"})
            return out

        async def _fetch_flows() -> list[dict[str, Any]]:
            if not flows_tool:
                return []
            result = await _mcp_call_tool(
                client, server_url, headers, flows_tool,
                build_klaviyo_tool_arguments(flows_tool),
            )
            text = _extract_text(result.get("result", {}).get("content"))
            parsed = _try_parse_json(text)
            out: list[dict[str, Any]] = []
            if isinstance(parsed, list):
                out = [{"name": f.get("name", ""), "status": f.get("status", ""), "trigger_type": f.get("trigger_type", "")} for f in parsed]
            elif isinstance(parsed, dict) and parsed.get("data"):
                for item in parsed["data"]:
                    attrs = item.get("attributes", item)
                    out.append({"name": attrs.get("name", ""), "status": attrs.get("status", ""), "trigger_type": attrs.get("trigger_type", "")})
            return out

        async def _fetch_profiles_count() -> int:
            if not profiles_tool:
                return 0
            result = await _mcp_call_tool(
                client, server_url, headers, profiles_tool,
                build_klaviyo_tool_arguments(profiles_tool),
            )
            text = _extract_text(result.get("result", {}).get("content"))
            parsed = _try_parse_json(text)
            if isinstance(parsed, dict):
                return parsed.get("total", parsed.get("count", 0))
            if isinstance(parsed, list):
                return len(parsed)
            return 0

        try:
            account_data, lists, campaigns, flows, profiles_count = await asyncio.gather(
                _fetch_account(), _fetch_lists(), _fetch_campaigns(), _fetch_flows(), _fetch_profiles_count(),
            )

        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:400] if exc.response is not None else str(exc)
            logger.warning("Klaviyo MCP tool call failed: %s", detail)
            return {
                "ok": False,
                "reason": f"Klaviyo MCP tool call failed. {detail}",
                "date_range": {"days": days},
            }
        except httpx.HTTPError as exc:
            logger.warning("Klaviyo MCP network error: %s", exc)
            return {
                "ok": False,
                "reason": "Could not reach the Klaviyo MCP server.",
                "date_range": {"days": days},
            }

    active_flows = [f for f in flows if f.get("status") == "live"]
    draft_flows = [f for f in flows if f.get("status") == "draft"]
    has_data = bool(lists) or bool(campaigns) or bool(flows) or profiles_count > 0

    result = {
        "ok": True,
        "has_data": has_data,
        "empty_reason": (
            "Klaviyo is connected, but no data was returned."
            if not has_data else ""
        ),
        "generated_at": utc_now().isoformat(),
        "date_range": {"days": days},
        "cards": [
            {"label": "Profiles", "value": _format_number(profiles_count) if profiles_count > 0 else "N/A", "tone": "blue", "detail": "total subscriber profiles"},
            {"label": "Lists", "value": _format_number(len(lists)), "tone": "green", "detail": "audience lists"},
            {"label": "Campaigns", "value": _format_number(len(campaigns)), "tone": "violet", "detail": "recent email campaigns"},
            {"label": "Active Flows", "value": _format_number(len(active_flows)), "tone": "amber", "detail": "live automations"},
            {"label": "Draft Flows", "value": _format_number(len(draft_flows)), "tone": "rose", "detail": "draft automations"},
            {"label": "Total Flows", "value": _format_number(len(flows)), "tone": "emerald", "detail": "all flow automations"},
        ],
        "lists": lists,
        "campaigns": campaigns,
        "flows": flows,
        "available_tools": [t.get("name") for t in tools],
    }
    return result
