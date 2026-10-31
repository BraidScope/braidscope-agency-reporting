# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import httpx

from .core import logger, utc_now
from .ga4 import get_ga4_access_token
from .supabase_admin import supabase_rest_url, supabase_service_headers, supabase_admin_configured

GA4_ADMIN_BASE = "https://analyticsadmin.googleapis.com/v1beta"
GA4_DATA_BASE = "https://analyticsdata.googleapis.com/v1beta"

CACHE_HOURS = 12  # serve cached data if fresher than this


async def _get_org_id(client_id: str) -> str:
    """Look up organization_id from client_id. Falls back to client_id if not found."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            from .supabase_admin import supabase_rest_url, supabase_service_headers
            resp = await client.get(
                f"{supabase_rest_url()}/clients",
                params={"select": "organization_id", "id": f"eq.{client_id}", "limit": "1"},
                headers=supabase_service_headers(),
            )
            resp.raise_for_status()
            rows = resp.json()
            if rows and rows[0].get("organization_id"):
                return rows[0]["organization_id"]
    except Exception as exc:
        logger.info("org_id lookup failed (non-fatal): %s", exc)
    return client_id


def _iso_date(days_ago: int = 0) -> str:
    return (utc_now().date() - timedelta(days=days_ago)).isoformat()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _metric(row: dict[str, Any], index: int) -> float:
    values = row.get("metricValues") or []
    if index >= len(values):
        return 0.0
    try:
        return float(values[index].get("value") or 0)
    except (TypeError, ValueError):
        return 0.0


def _dimension(row: dict[str, Any], index: int) -> str:
    values = row.get("dimensionValues") or []
    if index >= len(values):
        return ""
    return values[index].get("value") or ""


def _format_number(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(int(round(value)))


def _format_money(value: float) -> str:
    if value >= 1_000:
        return f"${value / 1_000:.1f}k"
    return f"${value:,.0f}"


def _format_percent(value: float) -> str:
    return f"{value * 100:.1f}%"


# ─────────────────────────── SUPABASE CACHE ───────────────────────────

async def _get_cached_dashboard(org_id: str, property_id: str, days: int) -> dict[str, Any] | None:
    """Return cached dashboard JSON if fresh enough, else None."""
    if not supabase_admin_configured():
        return None
    try:
        cutoff = (utc_now() - timedelta(hours=CACHE_HOURS)).isoformat()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{supabase_rest_url()}/analytics_ga4_cache",
                params={
                    "select": "data_json,synced_at",
                    "org_id": f"eq.{org_id}",
                    "property_id": f"eq.{property_id}",
                    "days": f"eq.{days}",
                    "synced_at": f"gte.{cutoff}",
                    "limit": "1",
                    "order": "synced_at.desc",
                },
                headers=supabase_service_headers(),
            )
            resp.raise_for_status()
            rows = resp.json()
        if rows:
            cached = rows[0]["data_json"]
            cached["_from_cache"] = True
            cached["_cached_at"] = rows[0]["synced_at"]
            return cached
    except Exception as exc:
        logger.info("GA4 cache read failed (non-fatal): %s", exc)
    return None


async def _save_cache(org_id: str, property_id: str, days: int, data: dict[str, Any]) -> None:
    """Upsert dashboard result into Supabase cache table."""
    if not supabase_admin_configured():
        return
    try:
        payload = {
            "org_id": org_id,
            "property_id": property_id,
            "days": days,
            "data_json": data,
            "synced_at": utc_now().isoformat(),
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{supabase_rest_url()}/analytics_ga4_cache",
                params={"on_conflict": "org_id,property_id,days"},
                headers=supabase_service_headers({"Prefer": "resolution=merge-duplicates"}),
                json=payload,
            )
            resp.raise_for_status()
        logger.info("GA4 cache saved for org %s property %s days %s", org_id, property_id, days)
    except Exception as exc:
        logger.info("GA4 cache write failed (non-fatal): %s", exc)


async def _upsert_daily_rows(org_id: str, property_id: str, trend: list[dict]) -> None:
    """Store daily GA4 rows into analytics_ga4 table."""
    if not supabase_admin_configured() or not trend:
        return
    try:
        rows = []
        for row in trend:
            sessions = int(row.get("sessions") or 0)
            key_events = int(row.get("keyEvents") or 0)
            rows.append({
                "org_id": org_id,
                "property_id": property_id,
                "date": row["date"],
                "sessions": sessions,
                "new_users": int(row.get("newUsers") or 0),
                "active_users": int(row.get("activeUsers") or 0),
                "organic_traffic": 0,  # updated separately if available
                "conversions": key_events,
                "conv_rate": float(row.get("conversionRate") or 0),
                "synced_at": utc_now().isoformat(),
            })
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{supabase_rest_url()}/analytics_ga4",
                params={"on_conflict": "org_id,property_id,date"},
                headers=supabase_service_headers({"Prefer": "resolution=merge-duplicates"}),
                json=rows,
            )
            resp.raise_for_status()
        logger.info("GA4 upserted %d daily rows for org %s", len(rows), org_id)
    except Exception as exc:
        logger.info("GA4 daily row upsert failed (non-fatal): %s", exc)


# ─────────────────────────── GA4 API HELPERS ──────────────────────────

async def _list_properties(client: httpx.AsyncClient, token: str) -> list[dict[str, str]]:
    response = await client.get(
        f"{GA4_ADMIN_BASE}/accountSummaries",
        headers=_headers(token),
    )
    response.raise_for_status()
    summaries = response.json().get("accountSummaries") or []
    properties: list[dict[str, str]] = []
    for account in summaries:
        for prop in account.get("propertySummaries") or []:
            raw_name = prop.get("property") or ""
            property_id = raw_name.rsplit("/", 1)[-1]
            if property_id:
                properties.append({
                    "id": property_id,
                    "name": prop.get("displayName") or f"Property {property_id}",
                    "account": account.get("displayName") or "Google Analytics",
                })
    return properties


async def _run_report(
    client: httpx.AsyncClient,
    token: str,
    property_id: str,
    *,
    dimensions: list[str] | None = None,
    metrics: list[str],
    start_date: str,
    end_date: str,
    limit: int = 10,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "metrics": [{"name": metric} for metric in metrics],
        "limit": max(1, min(limit, 250)),
    }
    if dimensions:
        body["dimensions"] = [{"name": dimension} for dimension in dimensions]
    response = await client.post(
        f"{GA4_DATA_BASE}/properties/{property_id}:runReport",
        headers=_headers(token),
        json=body,
    )
    response.raise_for_status()
    return response.json()


async def _probe_one_property(
    client: httpx.AsyncClient, token: str,
    prop: dict[str, str], start_date: str, end_date: str,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        **prop, "row_count": 0, "active_users": 0, "sessions": 0,
        "has_data": False, "error": "",
    }
    try:
        probe = await _run_report(
            client, token, prop["id"],
            metrics=["activeUsers", "sessions"],
            start_date=start_date, end_date=end_date, limit=1,
        )
        rows = probe.get("rows") or []
        item["row_count"] = len(rows)
        if rows:
            item["active_users"] = int(_metric(rows[0], 0))
            item["sessions"] = int(_metric(rows[0], 1))
            item["has_data"] = item["active_users"] > 0 or item["sessions"] > 0
    except Exception as exc:
        item["error"] = str(exc)
    return item


async def _probe_properties(
    client: httpx.AsyncClient, token: str,
    properties: list[dict[str, str]], start_date: str, end_date: str,
) -> list[dict[str, Any]]:
    if not properties:
        return []
    return list(await asyncio.gather(
        *(_probe_one_property(client, token, p, start_date, end_date) for p in properties)
    ))


def _select_property(
    properties: list[dict[str, str]],
    diagnostics: list[dict[str, Any]],
    property_id: str | None,
) -> dict[str, str] | None:
    if property_id:
        return next((item for item in properties if item["id"] == property_id), None)
    best = next((item for item in diagnostics if item.get("has_data")), None)
    if best:
        return {"id": best["id"], "name": best["name"], "account": best["account"]}
    first_probe = diagnostics[0] if diagnostics else None
    if first_probe:
        return {"id": first_probe["id"], "name": first_probe["name"], "account": first_probe["account"]}
    return properties[0] if properties else None


# ─────────────────────────── DATE WINDOW ─────────────────────────────

# GA4's Data API has no data before this date and rejects earlier ranges.
GA4_EARLIEST_DATE = date(2015, 8, 14)
MAX_CUSTOM_RANGE_DAYS = 365


def resolve_date_window(
    days: int,
    start: date | None = None,
    end: date | None = None,
) -> tuple[str, str, int]:
    """Return (start_iso, end_iso, span_days) for a dashboard request.

    Presets pass only ``days`` (1 = today..today, 30 = the last 30 days
    including today). A custom range passes both ``start`` and ``end``
    (inclusive); ``days`` is then ignored and the span is derived from the
    dates. Raises ValueError with a user-facing message for a bad range.
    """
    if start is None and end is None:
        return _iso_date(max(1, days) - 1), _iso_date(0), days
    if start is None or end is None:
        raise ValueError("Choose both a start and an end date for a custom range.")
    if start > end:
        raise ValueError("The start date must be on or before the end date.")
    # One day of slack past UTC "today": a user east of UTC can legitimately
    # pick their own local today, which is still tomorrow in UTC terms.
    if end > utc_now().date() + timedelta(days=1):
        raise ValueError("The end date can't be in the future.")
    if start < GA4_EARLIEST_DATE:
        raise ValueError(f"GA4 has no data before {GA4_EARLIEST_DATE.isoformat()}.")
    span = (end - start).days + 1
    if span > MAX_CUSTOM_RANGE_DAYS:
        raise ValueError(f"Custom ranges can be at most {MAX_CUSTOM_RANGE_DAYS} days.")
    return start.isoformat(), end.isoformat(), span


# ─────────────────────────── MAIN FUNCTION ────────────────────────────

async def build_ga4_dashboard(
    client_id: str,
    days: int = 30,
    property_id: str | None = None,
    force_refresh: bool = False,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    # Validate the window before any network work (raises ValueError).
    start_date, end_date, days = resolve_date_window(days, start, end)
    # The Supabase snapshot cache is keyed by (org, property, days) only, so a
    # custom window must neither read nor overwrite a preset's entry of the
    # same length. The per-day rows are date-keyed and stay safe to upsert.
    custom_range = start is not None
    token = await get_ga4_access_token(client_id)
    if not token:
        return {
            "ok": False,
            "reason": "Google Analytics 4 is not connected for this client.",
            "properties": [],
            "selected_property": None,
        }

    org_id = await _get_org_id(client_id)

    async with httpx.AsyncClient(timeout=30) as client:
        # A network/HTTP failure while discovering properties is reported the
        # same way the report stage below reports its own failures (a
        # structured ok:false), instead of escaping as an unhandled HTTP 500.
        # Only httpx errors are handled here; anything else still propagates.
        try:
            properties = await _list_properties(client, token)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:400] if exc.response is not None else str(exc)
            logger.warning("GA4 property discovery failed: %s", detail)
            return {
                "ok": False,
                "reason": f"GA4 property discovery failed. {detail}",
                "properties": [],
                "property_diagnostics": [],
                "selected_property": None,
            }
        except httpx.HTTPError as exc:
            logger.warning("GA4 property discovery network error: %s", exc)
            return {
                "ok": False,
                "reason": "Could not reach the Google Analytics Admin API.",
                "properties": [],
                "property_diagnostics": [],
                "selected_property": None,
            }
        property_diagnostics = await _probe_properties(client, token, properties, start_date, end_date) if properties else []
        selected = _select_property(properties, property_diagnostics, property_id)
        if not selected:
            return {
                "ok": False,
                "reason": "No GA4 properties were found for this Google account.",
                "properties": [],
                "property_diagnostics": [],
                "selected_property": None,
            }

        pid = selected["id"]

        # ── Check cache first (skip if force_refresh) ──
        if not force_refresh and not custom_range:
            cached = await _get_cached_dashboard(org_id, pid, days)
            if cached:
                logger.info("GA4 serving cached dashboard for org %s property %s", org_id, pid)
                # Still attach fresh property list
                cached["properties"] = properties
                cached["selected_property"] = selected
                return cached

        logger.info("GA4 fetching live data for property %s client %s", pid, client_id)

        try:
            overview, trend, channels, pages, devices, events, countries = await asyncio.gather(
                _run_report(client, token, pid,
                    metrics=["activeUsers", "newUsers", "sessions", "engagementRate",
                             "keyEvents", "totalRevenue", "purchaseRevenue", "ecommercePurchases"],
                    start_date=start_date, end_date=end_date, limit=1),
                _run_report(client, token, pid,
                    dimensions=["date"],
                    metrics=["sessions", "activeUsers", "newUsers", "keyEvents", "totalRevenue"],
                    start_date=start_date, end_date=end_date, limit=days),
                _run_report(client, token, pid,
                    dimensions=["sessionDefaultChannelGroup"],
                    metrics=["sessions", "totalRevenue", "keyEvents", "engagementRate"],
                    start_date=start_date, end_date=end_date, limit=8),
                _run_report(client, token, pid,
                    dimensions=["pageTitle", "pagePath"],
                    metrics=["screenPageViews", "activeUsers", "eventCount", "keyEvents"],
                    start_date=start_date, end_date=end_date, limit=10),
                _run_report(client, token, pid,
                    dimensions=["deviceCategory"],
                    metrics=["activeUsers", "sessions"],
                    start_date=start_date, end_date=end_date, limit=5),
                _run_report(client, token, pid,
                    dimensions=["eventName"],
                    metrics=["eventCount", "keyEvents", "totalRevenue"],
                    start_date=start_date, end_date=end_date, limit=10),
                _run_report(client, token, pid,
                    dimensions=["country", "countryId"],
                    metrics=["activeUsers", "sessions"],
                    start_date=start_date, end_date=end_date, limit=50),
            )
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:400] if exc.response is not None else str(exc)
            logger.warning("GA4 dashboard report failed for property %s: %s", pid, detail)
            return {
                "ok": False,
                "reason": f"GA4 report request failed for property {pid}. {detail}",
                "properties": properties,
                "selected_property": selected,
                "property_diagnostics": property_diagnostics,
                "date_range": {"start": start_date, "end": end_date, "days": days},
            }
        except httpx.HTTPError as exc:
            logger.warning("GA4 dashboard network error for property %s: %s", pid, exc)
            return {
                "ok": False,
                "reason": "Could not reach the Google Analytics Data API.",
                "properties": properties,
                "selected_property": selected,
                "property_diagnostics": property_diagnostics,
                "date_range": {"start": start_date, "end": end_date, "days": days},
            }

    overview_row = (overview.get("rows") or [{}])[0]
    trend_rows = sorted(trend.get("rows") or [], key=lambda row: _dimension(row, 0))
    channel_rows = sorted(channels.get("rows") or [], key=lambda row: _metric(row, 0), reverse=True)
    page_rows = sorted(pages.get("rows") or [], key=lambda row: _metric(row, 0), reverse=True)
    device_rows = sorted(devices.get("rows") or [], key=lambda row: _metric(row, 0), reverse=True)
    event_rows = sorted(events.get("rows") or [], key=lambda row: _metric(row, 0), reverse=True)
    country_rows = sorted(countries.get("rows") or [], key=lambda row: _metric(row, 0), reverse=True)

    has_data = any(bool(rows) for rows in (trend_rows, channel_rows, page_rows, device_rows, event_rows))

    organic_channel = next(
        (ch for ch in channel_rows if "organic" in (_dimension(ch, 0) or "").lower()), None,
    )

    total_sessions = _metric(overview_row, 2)
    total_key_events = _metric(overview_row, 4)
    conversion_rate = (total_key_events / total_sessions * 100) if total_sessions > 0 else 0.0

    built_trend = []
    for row in trend_rows:
        sessions_day = _metric(row, 0)
        key_events_day = _metric(row, 3)
        built_trend.append({
            "date": _dimension(row, 0),
            "sessions": sessions_day,
            "activeUsers": _metric(row, 1),
            "newUsers": _metric(row, 2),
            "keyEvents": key_events_day,
            "revenue": _metric(row, 4),
            "conversionRate": round((key_events_day / sessions_day * 100) if sessions_day > 0 else 0, 2),
        })

    result = {
        "ok": True,
        "has_data": has_data,
        "empty_reason": "GA4 is connected, but this property returned no rows for the selected date range." if not has_data else "",
        "generated_at": utc_now().isoformat(),
        "date_range": {"start": start_date, "end": end_date, "days": days},
        "properties": properties,
        "property_diagnostics": property_diagnostics,
        "selected_property": selected,
        "cards": [
            {"label": "Active users", "value": _format_number(_metric(overview_row, 0)), "tone": "blue", "detail": "users with engaged activity"},
            {"label": "New users", "value": _format_number(_metric(overview_row, 1)), "tone": "green", "detail": "first-time visitors"},
            {"label": "Sessions", "value": _format_number(_metric(overview_row, 2)), "tone": "violet", "detail": "total visits"},
            {"label": "Engagement rate", "value": _format_percent(_metric(overview_row, 3)), "tone": "amber", "detail": "quality of visits"},
            {"label": "Key events", "value": _format_number(_metric(overview_row, 4)), "tone": "rose", "detail": "conversion-like actions"},
            {"label": "Total revenue", "value": _format_money(_metric(overview_row, 5)), "tone": "emerald", "detail": "GA4 attributed revenue"},
        ],
        "trend": built_trend,
        "channels": [
            {
                "channel": _dimension(row, 0) or "(not set)",
                "sessions": int(_metric(row, 0)),
                "revenue": _metric(row, 1),
                "keyEvents": _metric(row, 2),
                "engagementRate": _format_percent(_metric(row, 3)),
            }
            for row in channel_rows
        ],
        "pages": [
            {
                "title": _dimension(row, 0) or "(untitled)",
                "path": _dimension(row, 1) or "/",
                "views": int(_metric(row, 0)),
                "activeUsers": int(_metric(row, 1)),
                "events": int(_metric(row, 2)),
                "keyEvents": int(_metric(row, 3)),
            }
            for row in page_rows
        ],
        "devices": [
            {
                "device": (_dimension(row, 0) or "unknown").title(),
                "activeUsers": int(_metric(row, 0)),
                "sessions": int(_metric(row, 1)),
            }
            for row in device_rows
        ],
        "events": [
            {
                "event": _dimension(row, 0) or "(not set)",
                "count": int(_metric(row, 0)),
                "keyEvents": int(_metric(row, 1)),
                "revenue": _metric(row, 2),
            }
            for row in event_rows
        ],
        "countries": [
            {
                "country": _dimension(row, 0) or "Unknown",
                "countryId": _dimension(row, 1) or "",
                "activeUsers": int(_metric(row, 0)),
                "sessions": int(_metric(row, 1)),
            }
            for row in country_rows
        ],
        "conversion_rate": round(conversion_rate, 2),
        "organic_sessions": int(_metric(organic_channel, 0)) if organic_channel else 0,
    }

    # ── Save to Supabase cache + daily rows (background, non-blocking) ──
    if not custom_range:
        asyncio.ensure_future(_save_cache(org_id, pid, days, result))
    asyncio.ensure_future(_upsert_daily_rows(org_id, pid, built_trend))

    return result
