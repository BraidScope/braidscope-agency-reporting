# SPDX-License-Identifier: AGPL-3.0-only
"""
HubSpot CRM dashboard builder — assembles a structured dashboard payload
by querying the HubSpot REST API v3.

Follows the same pattern as google_ads_dashboard.py / meta_ads_dashboard.py.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .core import logger, utc_now
from .supabase_admin import supabase_rest_url, supabase_service_headers, supabase_admin_configured
from .hubspot import get_hubspot_access_token

# -- Config -------------------------------------------------------------------

_BASE = "https://api.hubapi.com"


# -- Helpers ------------------------------------------------------------------

def _format_number(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(int(round(value)))


def _format_money(value: float) -> str:
    if value >= 1_000:
        return f"${value / 1_000:.1f}k"
    return f"${value:,.2f}"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# -- API calls ----------------------------------------------------------------

async def _get_total(
    client: httpx.AsyncClient,
    token: str,
    object_type: str,
) -> int:
    """Fetch the total count for an object type (contacts, companies, etc.)."""
    url = f"{_BASE}/crm/v3/objects/{object_type}"
    response = await client.get(
        url,
        headers=_headers(token),
        params={"limit": 1, "archived": "false"},
    )
    response.raise_for_status()
    return int(response.json().get("total", 0))


async def _get_deals(
    client: httpx.AsyncClient,
    token: str,
) -> list[dict[str, Any]]:
    """Fetch deals with key properties."""
    url = f"{_BASE}/crm/v3/objects/deals"
    response = await client.get(
        url,
        headers=_headers(token),
        params={
            "limit": 100,
            "properties": "dealname,amount,dealstage,pipeline,closedate,createdate",
            "archived": "false",
        },
    )
    response.raise_for_status()
    return response.json().get("results", [])


async def _search_recent_contacts(
    client: httpx.AsyncClient,
    token: str,
    since_ms: int,
) -> list[dict[str, Any]]:
    """Search for contacts created since *since_ms* (epoch milliseconds)."""
    url = f"{_BASE}/crm/v3/objects/contacts/search"
    body = {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "createdate",
                        "operator": "GTE",
                        "value": str(since_ms),
                    }
                ]
            }
        ],
        "sorts": [
            {"propertyName": "createdate", "direction": "ASCENDING"}
        ],
        "limit": 100,
        "properties": ["createdate"],
    }
    response = await client.post(url, headers=_headers(token), json=body)
    response.raise_for_status()
    return response.json().get("results", [])


# -- Aggregation helpers ------------------------------------------------------

def _parse_deals(
    raw_deals: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],   # open deals list
    list[dict[str, Any]],   # won deals list
    list[dict[str, Any]],   # all deals (for top-deals table)
    list[dict[str, Any]],   # stages breakdown
]:
    open_deals: list[dict[str, Any]] = []
    won_deals: list[dict[str, Any]] = []
    all_deals: list[dict[str, Any]] = []
    stage_map: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0, "value": 0.0})

    for deal in raw_deals:
        props = deal.get("properties") or {}
        stage = (props.get("dealstage") or "unknown").lower()
        amount = _safe_float(props.get("amount"))
        name = props.get("dealname") or "(unnamed)"
        pipeline = props.get("pipeline") or ""
        closedate = props.get("closedate") or ""

        deal_row: dict[str, Any] = {
            "name": name,
            "stage": stage,
            "amount": amount,
            "pipeline": pipeline,
            "closedate": closedate,
        }
        all_deals.append(deal_row)

        # Aggregate by stage
        stage_map[stage]["count"] += 1
        stage_map[stage]["value"] += amount

        if stage == "closedwon":
            won_deals.append(deal_row)
        elif stage != "closedlost":
            open_deals.append(deal_row)

    stages = sorted(
        [
            {"stage": s, "count": int(v["count"]), "value": round(v["value"], 2)}
            for s, v in stage_map.items()
        ],
        key=lambda x: x["value"],
        reverse=True,
    )

    return open_deals, won_deals, all_deals, stages


def _build_contact_trend(
    results: list[dict[str, Any]],
    days: int,
) -> list[dict[str, Any]]:
    """Group recently-created contacts by date to produce a daily trend line."""
    by_date: dict[str, int] = defaultdict(int)

    for contact in results:
        props = contact.get("properties") or {}
        createdate = props.get("createdate") or ""
        if not createdate:
            continue
        # createdate format: 2024-06-01T12:34:56.789Z — take the date part
        date_str = createdate[:10]
        by_date[date_str] += 1

    # Ensure every day in the range has an entry (fill gaps with 0)
    today = datetime.now(timezone.utc).date()
    trend: list[dict[str, Any]] = []
    for i in range(days):
        d = today - timedelta(days=days - 1 - i)
        ds = d.isoformat()
        trend.append({"date": ds, "contacts": by_date.get(ds, 0)})

    return trend


# -- Main builder -------------------------------------------------------------


CACHE_HOURS = 12


async def _get_org_id(client_id: str) -> str:
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{supabase_rest_url()}/clients",
                params={"select": "organization_id", "id": f"eq.{client_id}", "limit": "1"},
                headers=supabase_service_headers())
            rows = r.json() if r.status_code == 200 else []
            if rows and rows[0].get("organization_id"):
                return rows[0]["organization_id"]
    except Exception as e:
        logger.info("org_id lookup failed: %s", e)
    return client_id


async def _get_cached_hubspot(org_id: str, days: int) -> dict | None:
    if not supabase_admin_configured():
        return None
    try:
        from datetime import timedelta
        import httpx as _httpx
        cutoff = (utc_now() - timedelta(hours=CACHE_HOURS)).isoformat()
        async with _httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{supabase_rest_url()}/analytics_hubspot_cache",
                params={"select": "data_json", "org_id": f"eq.{org_id}",
                        "days": f"eq.{days}", "synced_at": f"gte.{cutoff}",
                        "limit": "1", "order": "synced_at.desc"},
                headers=supabase_service_headers())
            rows = r.json() if r.status_code == 200 else []
        if rows:
            cached = rows[0]["data_json"]
            cached["_from_cache"] = True
            return cached
    except Exception as e:
        logger.info("HubSpot cache read failed: %s", e)
    return None


async def _save_hubspot_cache(org_id: str, days: int, data: dict) -> None:
    if not supabase_admin_configured():
        return
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=15) as c:
            await c.post(f"{supabase_rest_url()}/analytics_hubspot_cache",
                params={"on_conflict": "org_id,days"},
                headers=supabase_service_headers({"Prefer": "resolution=merge-duplicates"}),
                json={"org_id": org_id, "days": days, "data_json": data,
                      "synced_at": utc_now().isoformat()})
        logger.info("HubSpot cache saved for org %s", org_id)
    except Exception as e:
        logger.info("HubSpot cache write failed: %s", e)

async def build_hubspot_dashboard(client_id: str, days: int = 30, force_refresh: bool = False) -> dict[str, Any]:
    """Build a HubSpot CRM dashboard payload for the given client.

    `force_refresh` must stay in this signature: main.py's
    /py/dashboards/hubspot endpoint always calls this with force_refresh=
    (the query param's value, default False) through
    get_or_refresh_dashboard_cache's factory closure - the exact same
    "accept it even if unused" contract build_klaviyo_dashboard documents
    (see its own docstring). Before this fix, every single request to that
    endpoint raised `TypeError: build_hubspot_dashboard() got an unexpected
    keyword argument 'force_refresh'` - the dashboard could never load real
    data at all, regardless of connection state.
    """
    token = await get_hubspot_access_token(client_id)
    if not token:
        return {
            "ok": False,
            "reason": "HubSpot is not connected for this client.",
        }

    org_id = await _get_org_id(client_id)

    # ── Check cache first ──
    if not force_refresh:
        cached = await _get_cached_hubspot(org_id, days)
        if cached:
            logger.info("HubSpot serving cached dashboard for org %s", org_id)
            return cached

    since_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000
    )

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            contact_total, company_total, raw_deals, recent_contacts = await asyncio.gather(
                _get_total(client, token, "contacts"),
                _get_total(client, token, "companies"),
                _get_deals(client, token),
                _search_recent_contacts(client, token, since_ms),
            )
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:400] if exc.response is not None else str(exc)
            logger.warning("HubSpot dashboard API error: %s", detail)
            return {
                "ok": False,
                "reason": f"HubSpot API request failed. {detail}",
                "date_range": {"days": days},
            }
        except httpx.HTTPError as exc:
            logger.warning("HubSpot dashboard network error: %s", exc)
            return {
                "ok": False,
                "reason": "Could not reach the HubSpot API. Check internet/DNS on the backend and try again.",
                "date_range": {"days": days},
            }

    # -- Parse deals -----------------------------------------------------------
    open_deals, won_deals, all_deals, stages = _parse_deals(raw_deals)

    open_deal_count = len(open_deals)
    pipeline_value = round(sum(d["amount"] for d in open_deals), 2)
    won_deal_count = len(won_deals)
    won_revenue = round(sum(d["amount"] for d in won_deals), 2)

    # -- Contact trend ---------------------------------------------------------
    trend = _build_contact_trend(recent_contacts, days)

    # -- Top deals table (sorted by amount descending) -------------------------
    top_deals = sorted(all_deals, key=lambda d: d["amount"], reverse=True)

    has_data = (
        contact_total > 0
        or company_total > 0
        or bool(raw_deals)
        or bool(recent_contacts)
    )

    result = {
        "ok": True,
        "has_data": has_data,
        "empty_reason": (
            "HubSpot is connected, but no data was returned for the selected date range."
            if not has_data else ""
        ),
        "generated_at": utc_now().isoformat(),
        "date_range": {"days": days},
        "cards": [
            {
                "label": "Contacts",
                "value": _format_number(contact_total),
                "tone": "blue",
                "detail": "total contacts",
            },
            {
                "label": "Companies",
                "value": _format_number(company_total),
                "tone": "green",
                "detail": "total companies",
            },
            {
                "label": "Open Deals",
                "value": _format_number(open_deal_count),
                "tone": "violet",
                "detail": "non-closed deals",
            },
            {
                "label": "Pipeline Value",
                "value": _format_money(pipeline_value),
                "tone": "amber",
                "detail": "total value of open deals",
            },
            {
                "label": "Won Deals",
                "value": _format_number(won_deal_count),
                "tone": "emerald",
                "detail": "deals closed-won",
            },
            {
                "label": "Won Revenue",
                "value": _format_money(won_revenue),
                "tone": "rose",
                "detail": "revenue from won deals",
            },
        ],
        "trend": trend,
        "deals": top_deals,
        "stages": stages,
    }

    asyncio.ensure_future(_save_hubspot_cache(org_id, days, result))
    return result
