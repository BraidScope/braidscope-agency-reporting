# SPDX-License-Identifier: AGPL-3.0-only
"""
Meta Ads MCP handler — integrated into the py FastAPI service.
Mounted at POST /mcp/meta-ads
Accepts Meta OAuth bearer tokens and exposes read-only Marketing API tools.
"""
import json
import os
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

router = APIRouter()

_API = os.getenv("META_GRAPH_API_VERSION", "v20.0")
_BASE = f"https://graph.facebook.com/{_API}"


def _token(req: Request) -> str:
    return req.headers.get("Authorization", "").removeprefix("Bearer ").strip()


def _clean_account_id(ad_account_id: str | None) -> str:
    value = str(ad_account_id or "").strip()
    if not value:
        raise ValueError("ad_account_id is required. Call list_ad_accounts first if you do not know it.")
    return value if value.startswith("act_") else f"act_{value}"


def _limit(value: Any, default: int = 50) -> int:
    try:
        return max(1, min(int(value or default), 500))
    except (TypeError, ValueError):
        return default


def _time_params(args: dict[str, Any]) -> dict[str, str]:
    if args.get("date_preset"):
        return {"date_preset": str(args["date_preset"])}
    since = args.get("since")
    until = args.get("until")
    if since and until:
        return {"time_range": json.dumps({"since": str(since), "until": str(until)})}
    return {"date_preset": "last_30d"}


def _breakdowns(args: dict[str, Any]) -> str | None:
    breakdown = args.get("breakdown") or args.get("breakdowns")
    if not breakdown:
        return None
    if isinstance(breakdown, list):
        return ",".join(str(item) for item in breakdown if item)
    return str(breakdown)


async def _graph_get(token: str, path: str, params: dict[str, Any] | None = None) -> Any:
    query = dict(params or {})
    query["access_token"] = token
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.get(f"{_BASE}/{path.lstrip('/')}", params=query)
        response.raise_for_status()
        return response.json()


async def _paged_graph_get(token: str, path: str, params: dict[str, Any] | None = None, limit: int = 50) -> dict[str, Any]:
    query = dict(params or {})
    query.setdefault("limit", min(limit, 100))
    query["access_token"] = token
    rows: list[Any] = []
    next_url = f"{_BASE}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=45) as client:
        while next_url and len(rows) < limit:
            response = await client.get(next_url, params=query if "access_token" in query else None)
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data")
            if isinstance(data, list):
                rows.extend(data)
            else:
                return payload
            next_url = payload.get("paging", {}).get("next")
            query = {}
    return {"data": rows[:limit]}


def _ok(req_id: Any, data: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {"content": [{"type": "text", "text": json.dumps(data, default=str)}]},
    }


def _err(req_id: Any, msg: str) -> dict[str, Any]:
    return _ok(req_id, {"error": msg})


_DATE_PRESET = {
    "type": "string",
    "enum": [
        "today", "yesterday", "this_month", "last_month", "this_quarter",
        "maximum", "last_3d", "last_7d", "last_14d", "last_28d", "last_30d",
        "last_90d", "this_week_mon_today", "last_week_mon_sun",
        "this_year", "last_year",
    ],
    "default": "last_30d",
    "description": "Meta Ads date preset. Omit when using since/until.",
}
_AD_ACCOUNT_ID = {"type": "string", "description": "Meta ad account ID, with or without act_ prefix."}
_LIMIT = {"type": "integer", "default": 50, "description": "Maximum rows to return."}

META_ADS_MCP_TOOLS = [
    {
        "name": "list_ad_accounts",
        "description": "List Meta ad accounts accessible to the connected user, including account name, currency, timezone and status.",
        "inputSchema": {"type": "object", "properties": {"limit": _LIMIT}},
    },
    {
        "name": "get_ad_account_details",
        "description": "Get details for a Meta ad account: name, currency, timezone, status, spend cap, balance and business.",
        "inputSchema": {"type": "object", "properties": {"ad_account_id": _AD_ACCOUNT_ID}, "required": ["ad_account_id"]},
    },
    {
        "name": "list_campaigns",
        "description": "List campaigns in a Meta ad account with objective, status, buying type, budget fields and timestamps.",
        "inputSchema": {"type": "object", "properties": {"ad_account_id": _AD_ACCOUNT_ID, "limit": _LIMIT}, "required": ["ad_account_id"]},
    },
    {
        "name": "list_adsets",
        "description": "List ad sets with campaign, optimization goal, billing event, budget, bid strategy, status and schedule.",
        "inputSchema": {"type": "object", "properties": {"ad_account_id": _AD_ACCOUNT_ID, "campaign_id": {"type": "string"}, "limit": _LIMIT}, "required": ["ad_account_id"]},
    },
    {
        "name": "list_ads",
        "description": "List ads with campaign, ad set, creative id, status and timestamps.",
        "inputSchema": {"type": "object", "properties": {"ad_account_id": _AD_ACCOUNT_ID, "campaign_id": {"type": "string"}, "adset_id": {"type": "string"}, "limit": _LIMIT}, "required": ["ad_account_id"]},
    },
    {
        "name": "get_insights",
        "description": "Generic Meta Ads insights report for account/campaign/adset/ad level with spend, reach, impressions, clicks, CTR, CPC, CPM, actions, action values, ROAS and optional breakdown.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ad_account_id": _AD_ACCOUNT_ID,
                "level": {"type": "string", "enum": ["account", "campaign", "adset", "ad"], "default": "campaign"},
                "date_preset": _DATE_PRESET,
                "since": {"type": "string", "description": "YYYY-MM-DD start date. Use with until."},
                "until": {"type": "string", "description": "YYYY-MM-DD end date. Use with since."},
                "breakdown": {"type": "string", "description": "Optional breakdown e.g. age, gender, country, publisher_platform, platform_position, impression_device."},
                "limit": _LIMIT,
            },
            "required": ["ad_account_id"],
        },
    },
    {
        "name": "get_campaign_performance",
        "description": "Campaign-level performance: spend, reach, impressions, frequency, clicks, CTR, CPC, CPM, actions, purchases, ROAS and conversion values.",
        "inputSchema": {"type": "object", "properties": {"ad_account_id": _AD_ACCOUNT_ID, "date_preset": _DATE_PRESET, "since": {"type": "string"}, "until": {"type": "string"}, "limit": _LIMIT}, "required": ["ad_account_id"]},
    },
    {
        "name": "get_adset_performance",
        "description": "Ad-set-level performance: spend, reach, impressions, frequency, clicks, CTR, CPC, CPM, actions, purchases, ROAS and conversion values.",
        "inputSchema": {"type": "object", "properties": {"ad_account_id": _AD_ACCOUNT_ID, "date_preset": _DATE_PRESET, "since": {"type": "string"}, "until": {"type": "string"}, "limit": _LIMIT}, "required": ["ad_account_id"]},
    },
    {
        "name": "get_ad_performance",
        "description": "Ad-level performance for creative testing: spend, reach, impressions, frequency, clicks, CTR, CPC, CPM, actions, purchases, ROAS and conversion values.",
        "inputSchema": {"type": "object", "properties": {"ad_account_id": _AD_ACCOUNT_ID, "date_preset": _DATE_PRESET, "since": {"type": "string"}, "until": {"type": "string"}, "limit": _LIMIT}, "required": ["ad_account_id"]},
    },
    {
        "name": "get_demographic_breakdown",
        "description": "Performance breakdown by age and gender for Meta Ads.",
        "inputSchema": {"type": "object", "properties": {"ad_account_id": _AD_ACCOUNT_ID, "level": {"type": "string", "enum": ["account", "campaign", "adset", "ad"], "default": "campaign"}, "date_preset": _DATE_PRESET, "since": {"type": "string"}, "until": {"type": "string"}, "limit": _LIMIT}, "required": ["ad_account_id"]},
    },
    {
        "name": "get_platform_breakdown",
        "description": "Performance breakdown by publisher platform and placement/platform position: Facebook, Instagram, Audience Network, Messenger.",
        "inputSchema": {"type": "object", "properties": {"ad_account_id": _AD_ACCOUNT_ID, "level": {"type": "string", "enum": ["account", "campaign", "adset", "ad"], "default": "campaign"}, "date_preset": _DATE_PRESET, "since": {"type": "string"}, "until": {"type": "string"}, "limit": _LIMIT}, "required": ["ad_account_id"]},
    },
    {
        "name": "get_device_breakdown",
        "description": "Performance breakdown by impression device for Meta Ads.",
        "inputSchema": {"type": "object", "properties": {"ad_account_id": _AD_ACCOUNT_ID, "level": {"type": "string", "enum": ["account", "campaign", "adset", "ad"], "default": "campaign"}, "date_preset": _DATE_PRESET, "since": {"type": "string"}, "until": {"type": "string"}, "limit": _LIMIT}, "required": ["ad_account_id"]},
    },
    {
        "name": "get_creatives",
        "description": "List ad creatives in an account with names, object story spec, asset feed spec, thumbnails and effective object story id.",
        "inputSchema": {"type": "object", "properties": {"ad_account_id": _AD_ACCOUNT_ID, "limit": _LIMIT}, "required": ["ad_account_id"]},
    },
    {
        "name": "get_action_breakdown",
        "description": "Insights grouped by action_type to inspect purchases, leads, add-to-cart, landing page views and other conversion actions.",
        "inputSchema": {"type": "object", "properties": {"ad_account_id": _AD_ACCOUNT_ID, "level": {"type": "string", "enum": ["account", "campaign", "adset", "ad"], "default": "campaign"}, "date_preset": _DATE_PRESET, "since": {"type": "string"}, "until": {"type": "string"}, "limit": _LIMIT}, "required": ["ad_account_id"]},
    },
]


_INSIGHT_FIELDS = ",".join([
    "account_id", "account_name", "campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name",
    "date_start", "date_stop", "spend", "reach", "impressions", "frequency", "clicks", "inline_link_clicks",
    "ctr", "cpc", "cpm", "actions", "action_values", "purchase_roas", "website_purchase_roas",
])


async def _insights(token: str, account_id: str, args: dict[str, Any], level: str | None = None, breakdowns: str | None = None) -> Any:
    params: dict[str, Any] = {
        "fields": _INSIGHT_FIELDS,
        "level": level or args.get("level") or "campaign",
        "limit": _limit(args.get("limit")),
    }
    params.update(_time_params(args))
    selected_breakdowns = breakdowns or _breakdowns(args)
    if selected_breakdowns:
        params["breakdowns"] = selected_breakdowns
    return await _paged_graph_get(token, f"{_clean_account_id(account_id)}/insights", params, _limit(args.get("limit")))


async def _dispatch(req_id: Any, name: str, args: dict[str, Any], token: str) -> dict[str, Any]:
    try:
        limit = _limit(args.get("limit"))
        if name == "list_ad_accounts":
            fields = "id,account_id,name,account_status,currency,timezone_name,business{name},amount_spent,balance"
            return _ok(req_id, await _paged_graph_get(token, "me/adaccounts", {"fields": fields}, limit))

        account_id = args.get("ad_account_id")

        if name == "get_ad_account_details":
            fields = "id,account_id,name,account_status,currency,timezone_name,business{name},amount_spent,balance,spend_cap,disable_reason"
            return _ok(req_id, await _graph_get(token, _clean_account_id(account_id), {"fields": fields}))

        if name == "list_campaigns":
            fields = "id,name,status,effective_status,objective,buying_type,daily_budget,lifetime_budget,budget_remaining,start_time,stop_time,created_time,updated_time"
            return _ok(req_id, await _paged_graph_get(token, f"{_clean_account_id(account_id)}/campaigns", {"fields": fields}, limit))

        if name == "list_adsets":
            fields = "id,name,campaign_id,campaign{name},status,effective_status,optimization_goal,billing_event,bid_strategy,daily_budget,lifetime_budget,budget_remaining,start_time,end_time,created_time,updated_time"
            path = f"{args['campaign_id']}/adsets" if args.get("campaign_id") else f"{_clean_account_id(account_id)}/adsets"
            return _ok(req_id, await _paged_graph_get(token, path, {"fields": fields}, limit))

        if name == "list_ads":
            fields = "id,name,campaign_id,campaign{name},adset_id,adset{name},creative{id,name},status,effective_status,created_time,updated_time"
            if args.get("adset_id"):
                path = f"{args['adset_id']}/ads"
            elif args.get("campaign_id"):
                path = f"{args['campaign_id']}/ads"
            else:
                path = f"{_clean_account_id(account_id)}/ads"
            return _ok(req_id, await _paged_graph_get(token, path, {"fields": fields}, limit))

        if name == "get_insights":
            return _ok(req_id, await _insights(token, account_id, args))
        if name == "get_campaign_performance":
            return _ok(req_id, await _insights(token, account_id, args, level="campaign"))
        if name == "get_adset_performance":
            return _ok(req_id, await _insights(token, account_id, args, level="adset"))
        if name == "get_ad_performance":
            return _ok(req_id, await _insights(token, account_id, args, level="ad"))
        if name == "get_demographic_breakdown":
            return _ok(req_id, await _insights(token, account_id, args, breakdowns="age,gender"))
        if name == "get_platform_breakdown":
            return _ok(req_id, await _insights(token, account_id, args, breakdowns="publisher_platform,platform_position"))
        if name == "get_device_breakdown":
            return _ok(req_id, await _insights(token, account_id, args, breakdowns="impression_device"))
        if name == "get_action_breakdown":
            return _ok(req_id, await _insights(token, account_id, args, breakdowns="action_type"))

        if name == "get_creatives":
            fields = "id,name,title,body,object_type,thumbnail_url,effective_object_story_id,object_story_spec,asset_feed_spec,call_to_action_type"
            return _ok(req_id, await _paged_graph_get(token, f"{_clean_account_id(account_id)}/adcreatives", {"fields": fields}, limit))

        return _err(req_id, f"Unknown tool: {name}")
    except httpx.HTTPStatusError as exc:
        return _err(req_id, exc.response.text)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return _err(req_id, str(exc))


@router.post("/mcp/meta-ads")
async def meta_ads_mcp_handler(request: Request) -> Any:
    body = await request.json()
    method = body.get("method")
    req_id = body.get("id")
    token = _token(request)

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "meta-ads-mcp", "version": "1.0.0"},
            },
        }

    if method == "notifications/initialized":
        return Response(status_code=200)

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": META_ADS_MCP_TOOLS}}

    if method == "tools/call":
        params = body.get("params", {})
        return await _dispatch(req_id, params.get("name", ""), params.get("arguments", {}), token)

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}}
