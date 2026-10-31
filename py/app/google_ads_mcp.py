# SPDX-License-Identifier: AGPL-3.0-only
"""
Google Ads MCP handler — integrated into the py FastAPI service.
Mounted at POST /mcp/google-ads
Accepts Google OAuth bearer tokens and exposes 15 analytics tools.
Cost values returned by Google Ads API are in micros (divide by 1,000,000 for actual currency).
"""
import json
import os
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

router = APIRouter()

_API = os.getenv("GOOGLE_ADS_API_VERSION", "v24")
_DEV_TOKEN = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
_LOGIN_CID = os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "")


# ── Google Ads API client ─────────────────────────────────────────────────────

def _headers(token: str) -> dict[str, str]:
    h = {
        "Authorization": f"Bearer {token}",
        "developer-token": _DEV_TOKEN,
        "Content-Type": "application/json",
    }
    if _LOGIN_CID:
        h["login-customer-id"] = _LOGIN_CID.replace("-", "")
    return h


def _date_clause(date_range: str | None) -> str:
    r = (date_range or "LAST_30_DAYS").strip()
    if r.upper().startswith("BETWEEN"):
        return f"segments.date {r}"
    return f"segments.date DURING {r}"


async def _list_customers(token: str) -> list[str]:
    url = f"https://googleads.googleapis.com/{_API}/customers:listAccessibleCustomers"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, headers={
            "Authorization": f"Bearer {token}",
            "developer-token": _DEV_TOKEN,
        })
        r.raise_for_status()
        return [x.split("/")[-1] for x in r.json().get("resourceNames", [])]


async def _resolve_cid(token: str, customer_id: str | None) -> str:
    cid = str(customer_id or "").replace("-", "").strip()
    if cid:
        return cid
    customers = await _list_customers(token)
    if not customers:
        raise ValueError("No accessible Google Ads accounts found for this token")
    return customers[0]


async def _search(token: str, cid: str, query: str, page_size: int = 100) -> Any:
    url = f"https://googleads.googleapis.com/{_API}/customers/{cid}/googleAds:searchStream"
    async with httpx.AsyncClient(timeout=45) as c:
        r = await c.post(url, headers=_headers(token), json={"query": query})
        r.raise_for_status()
        # searchStream returns an array of batches; merge results
        batches = r.json()
        results: list[dict[str, Any]] = []
        if isinstance(batches, list):
            for batch in batches:
                results.extend(batch.get("results", []))
        elif isinstance(batches, dict):
            results.extend(batches.get("results", []))
        return {"results": results}


async def _field_metadata(token: str, resource_type: str) -> Any:
    url = f"https://googleads.googleapis.com/{_API}/googleAdsFields/{resource_type}"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, headers={
            "Authorization": f"Bearer {token}",
            "developer-token": _DEV_TOKEN,
        })
        r.raise_for_status()
        return r.json()


# ── GAQL query builders ───────────────────────────────────────────────────────

def _account_summary_query(dr: str) -> str:
    return (
        "SELECT metrics.impressions, metrics.clicks, metrics.ctr, metrics.average_cpc,"
        " metrics.cost_micros, metrics.conversions, metrics.conversions_value,"
        " metrics.cost_per_conversion, metrics.all_conversions"
        " FROM customer"
        f" WHERE {_date_clause(dr)}"
    )


def _campaign_perf_query(dr: str, status: str | None, limit: int) -> str:
    sc = "" if (not status or status == "ALL") else f" AND campaign.status = '{status}'"
    return (
        "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type,"
        " campaign_budget.amount_micros,"
        " metrics.impressions, metrics.clicks, metrics.ctr, metrics.average_cpc,"
        " metrics.cost_micros, metrics.conversions, metrics.conversions_value,"
        " metrics.cost_per_conversion, metrics.search_impression_share"
        " FROM campaign"
        f" WHERE {_date_clause(dr)}{sc}"
        " ORDER BY metrics.cost_micros DESC"
        f" LIMIT {limit}"
    )


def _ad_group_perf_query(dr: str, campaign_id: str | None, limit: int) -> str:
    cc = f" AND campaign.id = {campaign_id}" if campaign_id else ""
    return (
        "SELECT ad_group.id, ad_group.name, ad_group.status,"
        " campaign.id, campaign.name,"
        " metrics.impressions, metrics.clicks, metrics.ctr, metrics.average_cpc,"
        " metrics.cost_micros, metrics.conversions, metrics.cost_per_conversion"
        " FROM ad_group"
        f" WHERE {_date_clause(dr)}{cc}"
        " ORDER BY metrics.cost_micros DESC"
        f" LIMIT {limit}"
    )


def _keyword_perf_query(dr: str, campaign_id: str | None, limit: int) -> str:
    cc = f" AND campaign.id = {campaign_id}" if campaign_id else ""
    return (
        "SELECT ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type,"
        " ad_group_criterion.quality_info.quality_score, ad_group_criterion.status,"
        " ad_group.name, campaign.name,"
        " metrics.impressions, metrics.clicks, metrics.ctr, metrics.average_cpc,"
        " metrics.cost_micros, metrics.conversions"
        " FROM keyword_view"
        f" WHERE {_date_clause(dr)}{cc}"
        " ORDER BY metrics.impressions DESC"
        f" LIMIT {limit}"
    )


def _search_terms_query(dr: str, campaign_id: str | None, limit: int) -> str:
    cc = f" AND campaign.id = {campaign_id}" if campaign_id else ""
    return (
        "SELECT search_term_view.search_term, search_term_view.status,"
        " campaign.name, ad_group.name,"
        " metrics.impressions, metrics.clicks, metrics.ctr,"
        " metrics.cost_micros, metrics.conversions"
        " FROM search_term_view"
        f" WHERE {_date_clause(dr)}{cc}"
        " ORDER BY metrics.impressions DESC"
        f" LIMIT {limit}"
    )


def _conversion_query(dr: str, limit: int) -> str:
    return (
        "SELECT campaign.name, conversion_action.name, conversion_action.category,"
        " metrics.conversions, metrics.conversions_value,"
        " metrics.cost_per_conversion, metrics.all_conversions"
        " FROM campaign"
        f" WHERE {_date_clause(dr)}"
        " ORDER BY metrics.conversions DESC"
        f" LIMIT {limit}"
    )


def _geo_query(dr: str, limit: int) -> str:
    return (
        "SELECT geographic_view.country_criterion_id, geographic_view.location_type,"
        " campaign.name,"
        " metrics.impressions, metrics.clicks, metrics.ctr,"
        " metrics.cost_micros, metrics.conversions"
        " FROM geographic_view"
        f" WHERE {_date_clause(dr)}"
        " ORDER BY metrics.impressions DESC"
        f" LIMIT {limit}"
    )


def _device_query(dr: str) -> str:
    return (
        "SELECT segments.device,"
        " metrics.impressions, metrics.clicks, metrics.ctr, metrics.average_cpc,"
        " metrics.cost_micros, metrics.conversions, metrics.cost_per_conversion"
        " FROM campaign"
        f" WHERE {_date_clause(dr)}"
        " ORDER BY metrics.cost_micros DESC"
    )


def _ad_perf_query(dr: str, campaign_id: str | None, limit: int) -> str:
    cc = f" AND campaign.id = {campaign_id}" if campaign_id else ""
    return (
        "SELECT ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad.status,"
        " ad_group_ad.ad.final_urls, ad_group_ad.ad.display_url,"
        " ad_group.name, campaign.name,"
        " metrics.impressions, metrics.clicks, metrics.ctr,"
        " metrics.cost_micros, metrics.conversions"
        " FROM ad_group_ad"
        f" WHERE {_date_clause(dr)} AND ad_group_ad.status != 'REMOVED'{cc}"
        " ORDER BY metrics.impressions DESC"
        f" LIMIT {limit}"
    )


def _shopping_query(dr: str, limit: int) -> str:
    return (
        "SELECT segments.product_title, segments.product_type_l1, segments.product_brand,"
        " campaign.name, campaign.advertising_channel_type,"
        " metrics.impressions, metrics.clicks, metrics.cost_micros,"
        " metrics.conversions, metrics.conversions_value"
        " FROM shopping_performance_view"
        f" WHERE {_date_clause(dr)}"
        " ORDER BY metrics.impressions DESC"
        f" LIMIT {limit}"
    )


def _audience_query(dr: str, limit: int) -> str:
    return (
        "SELECT ad_group_criterion.criterion_id, ad_group_criterion.type,"
        " ad_group.name, campaign.name,"
        " metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions"
        " FROM ad_group_audience_view"
        f" WHERE {_date_clause(dr)}"
        " ORDER BY metrics.impressions DESC"
        f" LIMIT {limit}"
    )


def _budget_query(dr: str) -> str:
    return (
        "SELECT campaign_budget.id, campaign_budget.name, campaign_budget.amount_micros,"
        " campaign_budget.delivery_method, campaign_budget.total_amount_micros,"
        " campaign.id, campaign.name, campaign.status,"
        " metrics.cost_micros"
        " FROM campaign"
        f" WHERE {_date_clause(dr)}"
        " ORDER BY campaign_budget.amount_micros DESC"
    )


def _pmax_query(dr: str, limit: int) -> str:
    return (
        "SELECT campaign.id, campaign.name, campaign.status,"
        " asset_group.id, asset_group.name, asset_group.status,"
        " metrics.impressions, metrics.clicks, metrics.cost_micros,"
        " metrics.conversions, metrics.conversions_value"
        " FROM asset_group"
        f" WHERE {_date_clause(dr)}"
        " ORDER BY metrics.impressions DESC"
        f" LIMIT {limit}"
    )


# ── Tool definitions ──────────────────────────────────────────────────────────

_DR = {
    "type": "string",
    "enum": [
        "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS",
        "LAST_MONTH", "THIS_MONTH", "LAST_WEEK_SUN_SAT",
        "THIS_WEEK_SUN_TODAY", "LAST_YEAR", "THIS_YEAR",
    ],
    "default": "LAST_30_DAYS",
    "description": "Date range for the report",
}
_CID = {
    "type": "string",
    "description": "Google Ads customer ID (digits only, no dashes). Omit to auto-use the first accessible account.",
}
_LIM = {"type": "integer", "default": 50, "description": "Maximum rows to return"}

GOOGLE_ADS_MCP_TOOLS = [
    {
        "name": "list_accessible_customers",
        "description": (
            "List all Google Ads customer account IDs accessible with the current OAuth token. "
            "Call this first to discover available customer IDs before running any report."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search",
        "description": (
            "Execute any raw GAQL (Google Ads Query Language) query. "
            "Queryable resources: campaign, ad_group, keyword_view, ad_group_ad, "
            "search_term_view, geographic_view, shopping_performance_view, "
            "ad_group_audience_view, campaign_budget, conversion_action, customer, asset_group."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Full GAQL query. Example: "
                        "SELECT campaign.name, metrics.clicks FROM campaign "
                        "WHERE segments.date DURING LAST_30_DAYS ORDER BY metrics.clicks DESC LIMIT 10"
                    ),
                },
                "customer_id": _CID,
                "page_size": {"type": "integer", "default": 100, "description": "Max results (up to 10000)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_resource_metadata",
        "description": (
            "Get available fields, metrics, segments and their data types for a Google Ads resource. "
            "Use this to know what you can SELECT or filter in GAQL queries."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "resource_type": {
                    "type": "string",
                    "description": "Resource name e.g. campaign, ad_group, keyword_view, ad_group_ad",
                },
                "customer_id": _CID,
            },
            "required": ["resource_type"],
        },
    },
    {
        "name": "get_account_summary",
        "description": (
            "Account-level KPI summary: total impressions, clicks, CTR, avg CPC, "
            "total cost (micros), conversions, conversion value, cost per conversion."
        ),
        "inputSchema": {"type": "object", "properties": {"customer_id": _CID, "date_range": _DR}},
    },
    {
        "name": "get_campaign_performance",
        "description": (
            "Performance per campaign: impressions, clicks, CTR, avg CPC, cost (micros), "
            "conversions, conversion value, cost per conversion, search impression share, "
            "budget amount, campaign type and status."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_id": _CID,
                "date_range": _DR,
                "campaign_status": {
                    "type": "string",
                    "enum": ["ALL", "ENABLED", "PAUSED", "REMOVED"],
                    "default": "ENABLED",
                },
                "limit": _LIM,
            },
        },
    },
    {
        "name": "get_ad_group_performance",
        "description": (
            "Performance per ad group: impressions, clicks, CTR, avg CPC, cost, "
            "conversions, cost per conversion — with parent campaign name."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_id": _CID,
                "date_range": _DR,
                "campaign_id": {"type": "string", "description": "Filter by campaign ID (optional)"},
                "limit": _LIM,
            },
        },
    },
    {
        "name": "get_keyword_performance",
        "description": (
            "Keyword-level performance: keyword text, match type, quality score, status, "
            "impressions, clicks, CTR, avg CPC, cost, conversions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_id": _CID,
                "date_range": _DR,
                "campaign_id": {"type": "string", "description": "Filter by campaign ID (optional)"},
                "limit": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "get_search_terms_report",
        "description": (
            "Actual search queries users typed that triggered ads: search term, status, "
            "campaign, ad group, impressions, clicks, CTR, cost, conversions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_id": _CID,
                "date_range": _DR,
                "campaign_id": {"type": "string", "description": "Filter by campaign ID (optional)"},
                "limit": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "get_conversion_report",
        "description": (
            "Conversion data by campaign and conversion action: action name, category, "
            "total conversions, conversion value, cost per conversion, all conversions."
        ),
        "inputSchema": {"type": "object", "properties": {"customer_id": _CID, "date_range": _DR, "limit": _LIM}},
    },
    {
        "name": "get_geographic_performance",
        "description": (
            "Performance by country/region: country criterion ID, location type, "
            "campaign, impressions, clicks, CTR, cost, conversions."
        ),
        "inputSchema": {"type": "object", "properties": {"customer_id": _CID, "date_range": _DR, "limit": _LIM}},
    },
    {
        "name": "get_device_performance",
        "description": (
            "Performance by device type (MOBILE, DESKTOP, TABLET, CONNECTED_TV): "
            "impressions, clicks, CTR, avg CPC, cost, conversions, cost per conversion."
        ),
        "inputSchema": {"type": "object", "properties": {"customer_id": _CID, "date_range": _DR}},
    },
    {
        "name": "get_ad_performance",
        "description": (
            "Individual ad performance: ad ID, type, status, display URL, final URLs, "
            "ad group, campaign, impressions, clicks, CTR, cost, conversions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_id": _CID,
                "date_range": _DR,
                "campaign_id": {"type": "string", "description": "Filter by campaign ID (optional)"},
                "limit": _LIM,
            },
        },
    },
    {
        "name": "get_shopping_performance",
        "description": (
            "Shopping and Performance Max product data: product title, brand, product type, "
            "campaign, impressions, clicks, cost, conversions, conversion value."
        ),
        "inputSchema": {"type": "object", "properties": {"customer_id": _CID, "date_range": _DR, "limit": _LIM}},
    },
    {
        "name": "get_audience_performance",
        "description": (
            "Performance by audience segment: criterion ID, type, ad group, campaign, "
            "impressions, clicks, cost, conversions."
        ),
        "inputSchema": {"type": "object", "properties": {"customer_id": _CID, "date_range": _DR, "limit": _LIM}},
    },
    {
        "name": "get_budget_report",
        "description": (
            "Campaign budget details: budget name, amount (micros), delivery method, "
            "total amount, campaign name/status, spend for the period."
        ),
        "inputSchema": {"type": "object", "properties": {"customer_id": _CID, "date_range": _DR}},
    },
    {
        "name": "get_pmax_performance",
        "description": (
            "Performance Max asset group performance: asset group name, status, "
            "impressions, clicks, cost, conversions, conversion value."
        ),
        "inputSchema": {"type": "object", "properties": {"customer_id": _CID, "date_range": _DR, "limit": _LIM}},
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


def _err(req_id: Any, msg: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {"content": [{"type": "text", "text": json.dumps({"error": msg})}]},
    }


async def _dispatch(req_id: Any, name: str, args: dict, token: str) -> dict:
    try:
        dr = args.get("date_range", "LAST_30_DAYS")
        lim = int(args.get("limit") or 50)

        if name == "list_accessible_customers":
            return _ok(req_id, {"customers": await _list_customers(token)})

        cid = await _resolve_cid(token, args.get("customer_id"))

        if name == "search":
            data = await _search(token, cid, args["query"], int(args.get("page_size") or 100))
            return _ok(req_id, data)

        if name == "get_resource_metadata":
            return _ok(req_id, await _field_metadata(token, args["resource_type"]))

        if name == "get_account_summary":
            return _ok(req_id, await _search(token, cid, _account_summary_query(dr)))

        if name == "get_campaign_performance":
            return _ok(req_id, await _search(token, cid, _campaign_perf_query(dr, args.get("campaign_status", "ENABLED"), lim)))

        if name == "get_ad_group_performance":
            return _ok(req_id, await _search(token, cid, _ad_group_perf_query(dr, args.get("campaign_id"), lim)))

        if name == "get_keyword_performance":
            return _ok(req_id, await _search(token, cid, _keyword_perf_query(dr, args.get("campaign_id"), int(args.get("limit") or 100))))

        if name == "get_search_terms_report":
            return _ok(req_id, await _search(token, cid, _search_terms_query(dr, args.get("campaign_id"), int(args.get("limit") or 100))))

        if name == "get_conversion_report":
            return _ok(req_id, await _search(token, cid, _conversion_query(dr, lim)))

        if name == "get_geographic_performance":
            return _ok(req_id, await _search(token, cid, _geo_query(dr, lim)))

        if name == "get_device_performance":
            return _ok(req_id, await _search(token, cid, _device_query(dr)))

        if name == "get_ad_performance":
            return _ok(req_id, await _search(token, cid, _ad_perf_query(dr, args.get("campaign_id"), lim)))

        if name == "get_shopping_performance":
            return _ok(req_id, await _search(token, cid, _shopping_query(dr, lim)))

        if name == "get_audience_performance":
            return _ok(req_id, await _search(token, cid, _audience_query(dr, lim)))

        if name == "get_budget_report":
            return _ok(req_id, await _search(token, cid, _budget_query(dr)))

        if name == "get_pmax_performance":
            return _ok(req_id, await _search(token, cid, _pmax_query(dr, lim)))

        return _err(req_id, f"Unknown tool: {name}")

    except httpx.HTTPStatusError as exc:
        return _err(req_id, exc.response.text)
    except httpx.HTTPError as exc:
        return _err(req_id, str(exc))
    except ValueError as exc:
        return _err(req_id, str(exc))


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/mcp/google-ads")
async def google_ads_mcp_handler(request: Request) -> Any:
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
                "serverInfo": {"name": "google-ads-mcp", "version": "1.0.0"},
            },
        }

    if method == "notifications/initialized":
        return Response(status_code=200)

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": GOOGLE_ADS_MCP_TOOLS}}

    if method == "tools/call":
        params = body.get("params", {})
        return await _dispatch(req_id, params.get("name", ""), params.get("arguments", {}), token)

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}}
