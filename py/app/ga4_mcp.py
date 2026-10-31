# SPDX-License-Identifier: AGPL-3.0-only
"""
Google Analytics 4 MCP handler — integrated into the py FastAPI service.
Mounted at POST /mcp/ga4
Accepts Google OAuth bearer tokens (scope analytics.readonly) and exposes
read-only reporting tools backed by the official Google Analytics APIs:
  - Data API v1beta   (analyticsdata.googleapis.com)   — reports & realtime
  - Admin API v1beta  (analyticsadmin.googleapis.com)   — property discovery
All tools are read-only; the GA4 APIs used here only return reporting data.
"""
import json
import os
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

router = APIRouter()

_DATA_API = "https://analyticsdata.googleapis.com/v1beta"
_ADMIN_API = "https://analyticsadmin.googleapis.com/v1beta"
_DEFAULT_PROPERTY = os.getenv("GA4_PROPERTY_ID", "")


# ── Google Analytics API client ───────────────────────────────────────────────

def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# GA4 relative date keywords accepted by the Data API (NdaysAgo / today / yesterday).
def _date_range(date_range: str | None) -> dict[str, str]:
    presets = {
        "LAST_7_DAYS": "7daysAgo",
        "LAST_14_DAYS": "14daysAgo",
        "LAST_28_DAYS": "28daysAgo",
        "LAST_30_DAYS": "30daysAgo",
        "LAST_90_DAYS": "90daysAgo",
        "LAST_180_DAYS": "180daysAgo",
        "LAST_365_DAYS": "365daysAgo",
        "YESTERDAY": "yesterday",
        "TODAY": "today",
    }
    raw = (date_range or "LAST_30_DAYS").strip()
    key = raw.upper()
    if key.startswith("CUSTOM:"):
        _, start, end = raw.split(":", 2)
        return {"startDate": start or "30daysAgo", "endDate": end or "today"}
    if ":" in raw and raw[:4].isdigit():
        start, end = raw.split(":", 1)
        return {"startDate": start, "endDate": end}
    start = presets.get(key, "30daysAgo")
    end = "today"
    if key == "YESTERDAY":
        end = "yesterday"
    return {"startDate": start, "endDate": end}


async def _list_properties(token: str) -> list[dict[str, str]]:
    url = f"{_ADMIN_API}/accountSummaries?pageSize=200"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        data = r.json()
    properties: list[dict[str, str]] = []
    for account in data.get("accountSummaries", []):
        account_name = account.get("displayName", "")
        for prop in account.get("propertySummaries", []):
            resource = prop.get("property", "")  # e.g. "properties/123456789"
            properties.append(
                {
                    "property_id": resource.split("/")[-1],
                    "property": resource,
                    "display_name": prop.get("displayName", ""),
                    "account": account_name,
                }
            )
    return properties


async def _resolve_property(token: str, property_id: str | None) -> str:
    pid = str(property_id or _DEFAULT_PROPERTY or "").replace("properties/", "").strip()
    if pid:
        return pid
    properties = await _list_properties(token)
    if not properties:
        raise ValueError("No accessible Google Analytics 4 properties found for this token")
    return properties[0]["property_id"]


async def _run_report(token: str, pid: str, body: dict[str, Any]) -> Any:
    url = f"{_DATA_API}/properties/{pid}:runReport"
    async with httpx.AsyncClient(timeout=45) as c:
        r = await c.post(url, headers=_headers(token), json=body)
        r.raise_for_status()
        return r.json()


async def _run_realtime_report(token: str, pid: str, body: dict[str, Any]) -> Any:
    url = f"{_DATA_API}/properties/{pid}:runRealtimeReport"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, headers=_headers(token), json=body)
        r.raise_for_status()
        return r.json()


async def _get_metadata(token: str, pid: str) -> Any:
    url = f"{_DATA_API}/properties/{pid}/metadata"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, headers=_headers(token))
        r.raise_for_status()
        return r.json()


# ── Report builders ───────────────────────────────────────────────────────────

def _dims(*names: str) -> list[dict[str, str]]:
    return [{"name": n} for n in names]


def _mets(*names: str) -> list[dict[str, str]]:
    return [{"name": n} for n in names]


def _report_body(
    dr: str,
    dimensions: list[dict[str, str]],
    metrics: list[dict[str, str]],
    *,
    limit: int | None = None,
    order_metric: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "dateRanges": [_date_range(dr)],
        "dimensions": dimensions,
        "metrics": metrics,
    }
    if limit:
        body["limit"] = limit
    if order_metric:
        body["orderBys"] = [{"metric": {"metricName": order_metric}, "desc": True}]
    return body


# ── Tool definitions ──────────────────────────────────────────────────────────

_DR = {
    "type": "string",
    "enum": [
        "LAST_7_DAYS", "LAST_14_DAYS", "LAST_28_DAYS", "LAST_30_DAYS",
        "LAST_90_DAYS", "LAST_180_DAYS", "LAST_365_DAYS", "YESTERDAY", "TODAY",
    ],
    "default": "LAST_30_DAYS",
    "description": "Date range for the report",
}
_PID = {
    "type": "string",
    "description": "GA4 property ID (digits only, no 'properties/' prefix). Omit to auto-use the first accessible property.",
}
_LIM = {"type": "integer", "default": 25, "description": "Maximum rows to return"}
_START_DATE = {
    "type": "string",
    "description": "Optional custom start date in YYYY-MM-DD format. If provided, overrides date_range.",
}
_END_DATE = {
    "type": "string",
    "description": "Optional custom end date in YYYY-MM-DD format. If provided, overrides date_range.",
}


def _period_props(*, limit: bool = False) -> dict[str, Any]:
    props: dict[str, Any] = {
        "property_id": _PID,
        "date_range": _DR,
        "start_date": _START_DATE,
        "end_date": _END_DATE,
    }
    if limit:
        props["limit"] = _LIM
    return props

GA4_MCP_TOOLS = [
    {
        "name": "list_properties",
        "description": (
            "List all Google Analytics 4 properties accessible with the current OAuth token. "
            "Returns property IDs and display names. Call this first to discover property IDs."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_metadata",
        "description": (
            "Get the dimensions and metrics available for a GA4 property, including their API "
            "names and descriptions. Use this to know what you can request in run_report."
        ),
        "inputSchema": {"type": "object", "properties": {"property_id": _PID}},
    },
    {
        "name": "get_custom_report",
        "description": (
            "Get a fully custom GA4 report. Query any combination of dimensions and metrics. "
            "Common dimensions: date, sessionDefaultChannelGroup, sessionSource, sessionMedium, "
            "pagePath, landingPage, country, deviceCategory, eventName, browser. "
            "Common metrics: sessions, totalUsers, newUsers, activeUsers, screenPageViews, "
            "engagedSessions, engagementRate, averageSessionDuration, bounceRate, keyEvents, "
            "eventCount, totalRevenue, purchaseRevenue, ecommercePurchases."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "property_id": _PID,
                "date_range": _DR,
                "start_date": _START_DATE,
                "end_date": _END_DATE,
                "dimensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "GA4 dimension API names, e.g. ['sessionDefaultChannelGroup']",
                },
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "GA4 metric API names, e.g. ['sessions','totalUsers']",
                },
                "limit": _LIM,
                "order_by_metric": {
                    "type": "string",
                    "description": "Metric name to sort by, descending (optional).",
                },
            },
        },
    },
    {
        "name": "get_traffic_overview",
        "description": (
            "Site-wide traffic KPIs over the period: sessions, total users, new users, "
            "page views, engaged sessions, engagement rate, average session duration, bounce rate."
        ),
        "inputSchema": {"type": "object", "properties": _period_props()},
    },
    {
        "name": "get_traffic_by_day",
        "description": (
            "Daily time series of traffic: sessions, total users, new users and page views "
            "broken down by date. Useful for trend and spike analysis."
        ),
        "inputSchema": {"type": "object", "properties": _period_props()},
    },
    {
        "name": "get_traffic_sources",
        "description": (
            "Traffic by default channel group (Organic Search, Paid Search, Direct, Referral, "
            "Social, Email, etc.): sessions, total users, engaged sessions, keyEvents."
        ),
        "inputSchema": {"type": "object", "properties": _period_props(limit=True)},
    },
    {
        "name": "get_source_medium",
        "description": (
            "Traffic by session source / medium (e.g. google / organic, newsletter / email): "
            "sessions, total users, engaged sessions, keyEvents."
        ),
        "inputSchema": {"type": "object", "properties": _period_props(limit=True)},
    },
    {
        "name": "get_user_acquisition",
        "description": (
            "New user acquisition by first-user channel group: how first-time users are acquired — "
            "new users, total users, engaged sessions."
        ),
        "inputSchema": {"type": "object", "properties": _period_props(limit=True)},
    },
    {
        "name": "get_top_pages",
        "description": (
            "Most viewed pages by page path: page views, sessions, total users, "
            "average engagement time, bounce rate."
        ),
        "inputSchema": {"type": "object", "properties": _period_props(limit=True)},
    },
    {
        "name": "get_landing_pages",
        "description": (
            "Performance of landing pages (first page of a session): sessions, total users, "
            "new users, engaged sessions, bounce rate, keyEvents."
        ),
        "inputSchema": {"type": "object", "properties": _period_props(limit=True)},
    },
    {
        "name": "get_device_breakdown",
        "description": (
            "Traffic by device category (desktop, mobile, tablet): sessions, total users, "
            "engaged sessions, engagement rate, keyEvents."
        ),
        "inputSchema": {"type": "object", "properties": _period_props()},
    },
    {
        "name": "get_geo_breakdown",
        "description": (
            "Traffic by country: sessions, total users, new users, engaged sessions, keyEvents."
        ),
        "inputSchema": {"type": "object", "properties": _period_props(limit=True)},
    },
    {
        "name": "get_events_report",
        "description": (
            "Most frequent events by event name: event count, total users, event count per user. "
            "Includes automatically collected and custom events."
        ),
        "inputSchema": {"type": "object", "properties": _period_props(limit=True)},
    },
    {
        "name": "get_key_events_report",
        "description": (
            "Key events by event name: key events, total users, and event count. "
            "Use to see which key events drive results."
        ),
        "inputSchema": {"type": "object", "properties": _period_props(limit=True)},
    },
    {
        "name": "get_ecommerce_report",
        "description": (
            "Ecommerce performance by item name: items purchased, item revenue, "
            "ecommerce purchases and total purchase revenue."
        ),
        "inputSchema": {"type": "object", "properties": _period_props(limit=True)},
    },
    {
        "name": "get_campaign_performance",
        "description": (
            "Campaign performance by session campaign name: sessions, total users, engaged sessions, "
            "key events, and total revenue. Useful for marketing campaign analysis."
        ),
        "inputSchema": {"type": "object", "properties": _period_props(limit=True)},
    },
    {
        "name": "get_revenue_overview",
        "description": (
            "Revenue and purchase KPIs over the period: total revenue, purchase revenue, "
            "transactions, ecommerce purchases, and active users."
        ),
        "inputSchema": {"type": "object", "properties": _period_props()},
    },
    {
        "name": "get_realtime_active_users",
        "description": (
            "Realtime active users in the last 30 minutes, broken down by country. "
            "Reflects live activity right now."
        ),
        "inputSchema": {"type": "object", "properties": {"property_id": _PID}},
    },
]


# ── MCP protocol helpers ──────────────────────────────────────────────────────

def _token(req: Request) -> str:
    return req.headers.get("Authorization", "").removeprefix("Bearer ").strip()


def _ok(req_id: Any, data: Any) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {"content": [{"type": "text", "text": json.dumps(data)}]},
    }


def _err(req_id: Any, msg: str, *, status_code: int | None = None) -> dict:
    payload: dict[str, Any] = {"error": msg}
    if status_code:
        payload["status_code"] = status_code
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
    }


def _google_error(exc: httpx.HTTPStatusError) -> str:
    try:
        payload = exc.response.json()
    except ValueError:
        return exc.response.text or str(exc)
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = error.get("message") or str(error)
        details = error.get("details") or []
        if details:
            violations = []
            for detail in details:
                for violation in detail.get("fieldViolations", []) if isinstance(detail, dict) else []:
                    field = violation.get("field")
                    description = violation.get("description")
                    if field or description:
                        violations.append(f"{field}: {description}".strip(": "))
            if violations:
                return f"{message} ({'; '.join(violations[:3])})"
        return message
    return str(payload)


async def _dispatch(req_id: Any, name: str, args: dict, token: str) -> dict:
    try:
        dr = args.get("date_range", "LAST_30_DAYS")
        start_date = args.get("start_date") or args.get("startDate")
        end_date = args.get("end_date") or args.get("endDate")
        if start_date or end_date:
            dr = f"CUSTOM:{start_date or '30daysAgo'}:{end_date or 'today'}"
        lim = max(1, min(int(args.get("limit") or 25), 250))

        if name == "list_properties":
            return _ok(req_id, {"properties": await _list_properties(token)})

        pid = await _resolve_property(token, args.get("property_id"))

        if name == "get_metadata":
            return _ok(req_id, await _get_metadata(token, pid))

        if name == "get_realtime_active_users":
            body = {"dimensions": _dims("country"), "metrics": _mets("activeUsers")}
            return _ok(req_id, await _run_realtime_report(token, pid, body))

        if name == "get_campaign_performance":
            body = _report_body(
                dr,
                _dims("sessionCampaignName"),
                _mets("sessions", "totalUsers", "engagedSessions", "keyEvents", "totalRevenue"),
                limit=lim,
                order_metric="sessions",
            )
            return _ok(req_id, await _run_report(token, pid, body))

        if name == "get_revenue_overview":
            body = _report_body(
                dr,
                [],
                _mets("totalRevenue", "purchaseRevenue", "transactions", "ecommercePurchases", "activeUsers"),
            )
            return _ok(req_id, await _run_report(token, pid, body))

        if name == "get_custom_report":
            dimensions = [{"name": d} for d in (args.get("dimensions") or ["date"])]
            metrics = [{"name": m} for m in (args.get("metrics") or ["sessions", "totalUsers"])]
            body = {
                "dateRanges": [_date_range(dr)],
                "dimensions": dimensions,
                "metrics": metrics,
                "limit": max(1, min(int(args.get("limit") or 50), 250)),
            }
            order_metric = args.get("order_by_metric")
            if order_metric:
                body["orderBys"] = [{"metric": {"metricName": order_metric}, "desc": True}]
            return _ok(req_id, await _run_report(token, pid, body))

        if name == "get_traffic_overview":
            body = _report_body(
                dr,
                [],
                _mets(
                    "sessions", "totalUsers", "newUsers", "screenPageViews",
                    "engagedSessions", "engagementRate", "averageSessionDuration", "bounceRate",
                ),
            )
            return _ok(req_id, await _run_report(token, pid, body))

        if name == "get_traffic_by_day":
            body = _report_body(
                dr,
                _dims("date"),
                _mets("sessions", "totalUsers", "newUsers", "screenPageViews"),
            )
            body["orderBys"] = [{"dimension": {"dimensionName": "date"}}]
            return _ok(req_id, await _run_report(token, pid, body))

        if name == "get_traffic_sources":
            body = _report_body(
                dr,
                _dims("sessionDefaultChannelGroup"),
                _mets("sessions", "totalUsers", "engagedSessions", "keyEvents"),
                limit=lim,
                order_metric="sessions",
            )
            return _ok(req_id, await _run_report(token, pid, body))

        if name == "get_source_medium":
            body = _report_body(
                dr,
                _dims("sessionSourceMedium"),
                _mets("sessions", "totalUsers", "engagedSessions", "keyEvents"),
                limit=lim,
                order_metric="sessions",
            )
            return _ok(req_id, await _run_report(token, pid, body))

        if name == "get_user_acquisition":
            body = _report_body(
                dr,
                _dims("firstUserDefaultChannelGroup"),
                _mets("newUsers", "totalUsers", "engagedSessions"),
                limit=lim,
                order_metric="newUsers",
            )
            return _ok(req_id, await _run_report(token, pid, body))

        if name == "get_top_pages":
            body = _report_body(
                dr,
                _dims("pagePath"),
                _mets("screenPageViews", "sessions", "totalUsers", "userEngagementDuration", "bounceRate"),
                limit=lim,
                order_metric="screenPageViews",
            )
            return _ok(req_id, await _run_report(token, pid, body))

        if name == "get_landing_pages":
            body = _report_body(
                dr,
                _dims("landingPage"),
                _mets("sessions", "totalUsers", "newUsers", "engagedSessions", "bounceRate", "keyEvents"),
                limit=lim,
                order_metric="sessions",
            )
            return _ok(req_id, await _run_report(token, pid, body))

        if name == "get_device_breakdown":
            body = _report_body(
                dr,
                _dims("deviceCategory"),
                _mets("sessions", "totalUsers", "engagedSessions", "engagementRate", "keyEvents"),
                order_metric="sessions",
            )
            return _ok(req_id, await _run_report(token, pid, body))

        if name == "get_geo_breakdown":
            body = _report_body(
                dr,
                _dims("country"),
                _mets("sessions", "totalUsers", "newUsers", "engagedSessions", "keyEvents"),
                limit=lim,
                order_metric="sessions",
            )
            return _ok(req_id, await _run_report(token, pid, body))

        if name == "get_events_report":
            body = _report_body(
                dr,
                _dims("eventName"),
                _mets("eventCount", "totalUsers", "eventCountPerUser"),
                limit=lim,
                order_metric="eventCount",
            )
            return _ok(req_id, await _run_report(token, pid, body))

        if name == "get_key_events_report":
            body = _report_body(
                dr,
                _dims("eventName"),
                _mets("keyEvents", "totalUsers", "eventCount"),
                limit=lim,
                order_metric="keyEvents",
            )
            return _ok(req_id, await _run_report(token, pid, body))

        if name == "get_ecommerce_report":
            body = _report_body(
                dr,
                _dims("itemName"),
                _mets("itemsPurchased", "itemRevenue", "ecommercePurchases", "purchaseRevenue"),
                limit=lim,
                order_metric="itemRevenue",
            )
            return _ok(req_id, await _run_report(token, pid, body))

        return _err(req_id, f"Unknown tool: {name}")

    except httpx.HTTPStatusError as exc:
        return _err(req_id, _google_error(exc), status_code=exc.response.status_code)
    except httpx.HTTPError as exc:
        return _err(req_id, str(exc))
    except ValueError as exc:
        return _err(req_id, str(exc))


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/mcp/ga4")
async def ga4_mcp_handler(request: Request) -> Any:
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
                "serverInfo": {"name": "ga4-mcp", "version": "1.0.0"},
            },
        }

    if method == "notifications/initialized":
        return Response(status_code=200)

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": GA4_MCP_TOOLS}}

    if method == "tools/call":
        if not token:
            return _err(req_id, "GA4 access token is missing. Reconnect Google Analytics 4 and try again.")
        params = body.get("params", {})
        return await _dispatch(req_id, params.get("name", ""), params.get("arguments", {}), token)

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}}
