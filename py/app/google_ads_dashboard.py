# SPDX-License-Identifier: AGPL-3.0-only
"""
Google Ads dashboard builder — assembles a structured dashboard payload
by querying the Google Ads REST API with GAQL queries.

Follows the same pattern as ga4_dashboard.py.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import httpx

from .core import logger, utc_now
from .google_ads import get_google_ads_access_token
from .supabase_admin import supabase_rest_url, supabase_service_headers, supabase_admin_configured

# ── Config ───────────────────────────────────────────────────────────────────

_API_VERSION = os.getenv("GOOGLE_ADS_API_VERSION", "v24")
_DEV_TOKEN = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
_LOGIN_CID = os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "")

_BASE = "https://googleads.googleapis.com"

_DAYS_TO_PRESET: dict[int, str] = {
    7: "LAST_7_DAYS",
    14: "LAST_14_DAYS",
    30: "LAST_30_DAYS",
    90: "LAST_90_DAYS",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _preset(days: int) -> str:
    return _DAYS_TO_PRESET.get(days, "LAST_30_DAYS")


def _headers(token: str) -> dict[str, str]:
    h: dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "developer-token": _DEV_TOKEN,
        "Content-Type": "application/json",
    }
    if _LOGIN_CID:
        h["login-customer-id"] = _LOGIN_CID.replace("-", "")
    return h


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


def _format_percent(value: float) -> str:
    """Format a fraction (0..1) as a percentage string."""
    return f"{value * 100:.1f}%"


def _micros(value: float) -> float:
    """Convert micros to currency units."""
    return value / 1_000_000


def _get(row: dict[str, Any], path: str, default: Any = None) -> Any:
    """Safely traverse a dotted path inside a GAQL result row."""
    parts = path.split(".")
    current: Any = row
    for part in parts:
        if not isinstance(current, dict):
            return default
        current = current.get(part, default)
    return current


def _float(row: dict[str, Any], path: str) -> float:
    try:
        return float(_get(row, path) or 0)
    except (TypeError, ValueError):
        return 0.0


def _str(row: dict[str, Any], path: str) -> str:
    return str(_get(row, path) or "")


# ── API calls ────────────────────────────────────────────────────────────────

async def _list_customers(client: httpx.AsyncClient, token: str) -> list[str]:
    url = f"{_BASE}/{_API_VERSION}/customers:listAccessibleCustomers"
    r = await client.get(url, headers={
        "Authorization": f"Bearer {token}",
        "developer-token": _DEV_TOKEN,
    })
    r.raise_for_status()
    return [name.split("/")[-1] for name in r.json().get("resourceNames", [])]


async def _get_customer_name(client: httpx.AsyncClient, token: str, cid: str) -> str:
    """Fetch the descriptive name for a customer ID. Returns the ID on failure."""
    try:
        rows = await _search(
            client, token, cid,
            "SELECT customer.descriptive_name, customer.id FROM customer",
        )
        if rows:
            return _str(rows[0], "customer.descriptiveName") or cid
    except httpx.HTTPError:
        pass
    return cid


async def _search_as_manager(
    client: httpx.AsyncClient, token: str,
    manager_cid: str, query: str,
) -> list[dict[str, Any]]:
    """Run a GAQL search with login-customer-id set to the manager CID."""
    url = f"{_BASE}/{_API_VERSION}/customers/{manager_cid}/googleAds:searchStream"
    headers = _headers(token)
    headers["login-customer-id"] = manager_cid.replace("-", "")
    r = await client.post(url, headers=headers, json={"query": query})
    r.raise_for_status()
    batches = r.json()
    results: list[dict[str, Any]] = []
    if isinstance(batches, list):
        for batch in batches:
            results.extend(batch.get("results", []))
    elif isinstance(batches, dict):
        results.extend(batches.get("results", []))
    return results


def _classify_google_ads_error(exc: httpx.HTTPStatusError) -> tuple[str, str]:
    """Turn a raw Google Ads API HTTP error into (reason_code, human_message).

    Google Ads wraps its real failure reason as a JSON array of
    `{"error": {"code", "status", "details": [...]}}` objects in the response
    body - the plain HTTP status alone (almost always 400/403) can't tell
    "disabled account" apart from "permission denied" apart from "bad
    query". Confirmed live on 2026-09-23 against this codebase's real
    connected account: both a `CUSTOMER_NOT_ENABLED` authorization error and
    a `REQUESTED_METRICS_FOR_MANAGER` query error come back as HTTP 403/400
    with this exact nested shape.
    """
    status_code = exc.response.status_code if exc.response is not None else None
    body = exc.response.text if exc.response is not None else str(exc)
    message = body[:300]
    error_code = None
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        error = parsed[0].get("error") or {}
        message = error.get("message") or message
        for detail in error.get("details") or []:
            if not isinstance(detail, dict):
                continue
            for sub_error in detail.get("errors") or []:
                if not isinstance(sub_error, dict):
                    continue
                code_obj = sub_error.get("errorCode") or {}
                if code_obj:
                    error_code = next(iter(code_obj.values()), None)
                if sub_error.get("message"):
                    message = sub_error["message"]
                if error_code:
                    break
            if error_code:
                break
    if error_code == "CUSTOMER_NOT_ENABLED":
        return "disabled", message
    if error_code == "REQUESTED_METRICS_FOR_MANAGER":
        return "manager_requested", message
    if error_code:
        return "api_error", message
    if status_code == 401:
        return "invalid_oauth", message
    if status_code == 403:
        return "permission_denied", message
    return "api_error", message


_IDENTITY_QUERY = "SELECT customer.id, customer.descriptive_name, customer.manager, customer.status FROM customer"


async def _list_child_accounts(
    client: httpx.AsyncClient, token: str, manager_cid: str,
) -> list[dict[str, Any]]:
    """List enabled, non-manager child accounts under a manager (MCC) account."""
    query = (
        "SELECT customer_client.id, customer_client.descriptive_name,"
        " customer_client.manager, customer_client.status"
        " FROM customer_client"
    )
    try:
        rows = await _search_as_manager(client, token, manager_cid, query)
        children: list[dict[str, Any]] = []
        for row in rows:
            raw_id = _get(row, "customerClient.id")
            is_mgr = _get(row, "customerClient.manager")
            status = _str(row, "customerClient.status")
            name = _str(row, "customerClient.descriptiveName")
            if is_mgr:
                continue
            if raw_id is None:
                continue
            child_id = str(int(raw_id)) if isinstance(raw_id, (int, float)) else str(raw_id)
            if child_id == manager_cid:
                continue
            if status and status != "ENABLED":
                continue
            child_name = name or child_id
            children.append({"id": child_id, "name": child_name, "accessible": True, "manager": False})
        return children
    except Exception as exc:
        logger.warning("_list_child_accounts failed for %s: %s", manager_cid, exc)
        return []


async def _probe_customers(
    client: httpx.AsyncClient, token: str, customer_ids: list[str],
) -> tuple[list[dict[str, Any]], list[str], dict[str, str]]:
    """Probe customer IDs; expand managers to their child accounts.

    A manager (MCC) account with zero linked children is recorded in the
    diagnostics list (so it still shows up for transparency/the account
    picker) but is deliberately NEVER added to metric_candidates - querying
    metrics on a manager account always fails with
    REQUESTED_METRICS_FOR_MANAGER, confirmed live on 2026-09-23. The
    previous version of this function added a childless manager as a
    candidate anyway, which made build_google_ads_dashboard() burn a real
    query attempt on a call that can never succeed, and masked the accurate
    "manager account with no ad accounts linked" message behind a generic
    "may not have active campaigns" fallback.

    Returns (accounts_info, candidate_ids, child_to_manager_map)."""
    info: list[dict[str, Any]] = []
    metric_candidates: list[str] = []
    child_to_manager: dict[str, str] = {}  # child_cid → manager_cid
    seen: set[str] = set()

    for cid in customer_ids:
        if cid in seen:
            continue
        seen.add(cid)
        try:
            rows = await _search(client, token, cid, _IDENTITY_QUERY)
            name = _str(rows[0], "customer.descriptiveName") or cid if rows else cid
            is_manager = bool(_get(rows[0], "customer.manager")) if rows else False
            status = _str(rows[0], "customer.status") if rows else ""

            if is_manager:
                children = await _list_child_accounts(client, token, cid)
                if children:
                    logger.info("Expanded manager %s (%s) to %d child accounts", cid, name, len(children))
                    for child in children:
                        if child["id"] not in seen:
                            seen.add(child["id"])
                            info.append(child)
                            metric_candidates.append(child["id"])
                            child_to_manager[child["id"]] = cid
                else:
                    logger.info("Manager %s (%s) has no linked child accounts", cid, name)
                    info.append({
                        "id": cid, "name": name, "accessible": True,
                        "manager": True, "has_children": False,
                    })
            else:
                enabled = status in ("", "ENABLED")
                info.append({
                    "id": cid, "name": name, "accessible": True,
                    "manager": False, "status": status,
                })
                if enabled:
                    metric_candidates.append(cid)
                else:
                    info[-1]["reason_code"] = "disabled"
                    info[-1]["reason"] = f"Customer {cid} status is {status}, not ENABLED."
        except httpx.HTTPStatusError as exc:
            reason_code, message = _classify_google_ads_error(exc)
            logger.info("Customer %s not accessible directly (%s): %s", cid, reason_code, message)
            # Retry with global LOGIN_CID as manager - a customer that fails
            # a bare self-authenticated call can still be reachable through
            # a manager hierarchy.
            if _LOGIN_CID and _LOGIN_CID != cid:
                try:
                    rows = await _search(client, token, cid, _IDENTITY_QUERY, login_cid=_LOGIN_CID)
                    name = _str(rows[0], "customer.descriptiveName") or cid if rows else cid
                    is_manager = bool(_get(rows[0], "customer.manager")) if rows else False
                    status = _str(rows[0], "customer.status") if rows else ""
                    enabled = status in ("", "ENABLED")
                    info.append({
                        "id": cid, "name": name, "accessible": True,
                        "manager": is_manager, "status": status,
                    })
                    if not is_manager and enabled:
                        metric_candidates.append(cid)
                        child_to_manager[cid] = _LOGIN_CID
                    logger.info("Customer %s accessible via LOGIN_CID %s", cid, _LOGIN_CID)
                    continue
                except httpx.HTTPStatusError as exc2:
                    reason_code, message = _classify_google_ads_error(exc2)
                    logger.info("Customer %s still not accessible via LOGIN_CID: %s", cid, message)
            info.append({
                "id": cid, "name": cid, "accessible": False,
                "reason_code": reason_code, "reason": message,
            })

    return info, metric_candidates, child_to_manager


async def _search(
    client: httpx.AsyncClient,
    token: str,
    cid: str,
    query: str,
    login_cid: str | None = None,
) -> list[dict[str, Any]]:
    url = f"{_BASE}/{_API_VERSION}/customers/{cid}/googleAds:searchStream"
    headers = _headers(token)
    if login_cid:
        headers["login-customer-id"] = login_cid.replace("-", "")
    r = await client.post(
        url,
        headers=headers,
        json={"query": query},
    )
    r.raise_for_status()
    # searchStream returns an array of batches; merge all results
    batches = r.json()
    results: list[dict[str, Any]] = []
    if isinstance(batches, list):
        for batch in batches:
            results.extend(batch.get("results", []))
    elif isinstance(batches, dict):
        results.extend(batches.get("results", []))
    return results


# ── Canonical customer resolution ───────────────────────────────────────────
# Single source of truth for "which Google Ads customer ID should this
# client's data come from" - both the Dashboard and the MCP/Ask AI tool path
# must resolve the exact same customer, and neither should trust
# `customers[0]` from listAccessibleCustomers blindly (that list mixes
# manager accounts, disabled accounts, and real client accounts with no
# indication of which is which - confirmed live on 2026-09-23 this codebase's
# real connected account: `customers[0]` was a disabled account, and the
# other entry was a manager account with zero linked children).

_RESOLUTION_CACHE_TTL_SECONDS = 600.0
_resolution_cache: dict[str, tuple[float, dict[str, Any]]] = {}


async def _verify_candidate(
    client: httpx.AsyncClient, token: str, candidate_cid: str, login_cid: str | None,
) -> dict[str, Any] | None:
    """Lightweight real-API check that a candidate customer is actually
    queryable right now (accessible, not a manager, status ENABLED) before
    it's ever handed back as "the" resolved customer. Returns the customer's
    identity dict on success, None on any failure (caller decides how to
    report that)."""
    try:
        rows = await _search(client, token, candidate_cid, _IDENTITY_QUERY, login_cid=login_cid)
    except httpx.HTTPStatusError as exc:
        _, message = _classify_google_ads_error(exc)
        logger.info("Verification failed for candidate %s: %s", candidate_cid, message)
        return None
    if not rows:
        return None
    if bool(_get(rows[0], "customer.manager")):
        return None
    status = _str(rows[0], "customer.status")
    if status not in ("", "ENABLED"):
        return None
    return {
        "id": candidate_cid,
        "name": _str(rows[0], "customer.descriptiveName") or candidate_cid,
        "status": status or "ENABLED",
    }


async def _resolve_google_ads_customer_uncached(
    client_id: str, customer_id: str | None,
) -> dict[str, Any]:
    token = await get_google_ads_access_token(client_id)
    if not token:
        return {
            "ok": False, "reason_code": "not_connected",
            "reason": "Google Ads is not connected for this client.",
            "customer_id": None, "login_customer_id": None, "name": None, "customers": [],
        }

    async with httpx.AsyncClient(timeout=30) as client:
        if customer_id:
            cid = str(customer_id).replace("-", "").strip()
            login_cid = _LOGIN_CID if _LOGIN_CID and _LOGIN_CID != cid else None
            verified = await _verify_candidate(client, token, cid, login_cid)
            if verified:
                return {
                    "ok": True, "reason_code": None, "reason": None,
                    "customer_id": verified["id"], "login_customer_id": login_cid,
                    "name": verified["name"], "customers": [],
                }
            try:
                rows = await _search(client, token, cid, _IDENTITY_QUERY, login_cid=login_cid)
                is_manager = bool(_get(rows[0], "customer.manager")) if rows else False
                name = _str(rows[0], "customer.descriptiveName") or cid if rows else cid
            except httpx.HTTPStatusError as exc:
                reason_code, message = _classify_google_ads_error(exc)
                return {
                    "ok": False, "reason_code": reason_code,
                    "reason": f"Google Ads customer {cid} could not be accessed: {message}",
                    "customer_id": None, "login_customer_id": None, "name": None, "customers": [],
                }
            if is_manager:
                return {
                    "ok": False, "reason_code": "manager_requested",
                    "reason": f"\"{name}\" is a manager (MCC) account; metrics cannot be queried on it directly.",
                    "customer_id": None, "login_customer_id": None, "name": None, "customers": [],
                }
            return {
                "ok": False, "reason_code": "disabled",
                "reason": f"Google Ads customer {cid} ({name}) is not enabled for reporting.",
                "customer_id": None, "login_customer_id": None, "name": None, "customers": [],
            }

        try:
            customer_ids = await _list_customers(client, token)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:400] if exc.response is not None else str(exc)
            logger.warning("Google Ads listAccessibleCustomers failed: %s", detail)
            return {
                "ok": False, "reason_code": "api_error",
                "reason": "Could not connect to your Google Ads account. Try disconnecting and reconnecting Google Ads from the Integrations page.",
                "customer_id": None, "login_customer_id": None, "name": None, "customers": [],
            }
        except httpx.HTTPError as exc:
            logger.warning("Google Ads network error listing customers: %s", exc)
            return {
                "ok": False, "reason_code": "network_error",
                "reason": "Could not reach the Google Ads API. Please check your internet connection and try again.",
                "customer_id": None, "login_customer_id": None, "name": None, "customers": [],
            }

        if not customer_ids:
            return {
                "ok": False, "reason_code": "no_accounts",
                "reason": "No Google Ads accounts found. Make sure your Google account has access to at least one Google Ads account.",
                "customer_id": None, "login_customer_id": None, "name": None, "customers": [],
            }

        customers_info, metric_candidates, child_to_manager = await _probe_customers(client, token, customer_ids)

        reverify_failures: list[str] = []
        for candidate_cid in metric_candidates:
            login_cid = child_to_manager.get(candidate_cid)
            verified = await _verify_candidate(client, token, candidate_cid, login_cid)
            if verified:
                return {
                    "ok": True, "reason_code": None, "reason": None,
                    "customer_id": verified["id"], "login_customer_id": login_cid,
                    "name": verified["name"], "customers": customers_info,
                }
            reverify_failures.append(candidate_cid)

        # Nothing usable - build an honest, evidence-based reason combining
        # every real finding rather than a single generic guess.
        childless_managers = [c["name"] for c in customers_info if c.get("manager") and c.get("has_children") is False]
        disabled = [c for c in customers_info if c.get("accessible") is False]
        parts: list[str] = []
        if childless_managers:
            names = ", ".join(f'"{n}"' for n in childless_managers)
            verb = "is" if len(childless_managers) == 1 else "are"
            parts.append(
                f"{names} {verb} a manager (MCC) account with no ad accounts linked to it. "
                "Create or link an ad account inside Google Ads to start seeing data here."
            )
        if disabled:
            detail = "; ".join(f"{c['id']}: {c.get('reason') or 'not accessible'}" for c in disabled)
            parts.append(f"The following Google Ads account(s) could not be accessed: {detail}")
        if reverify_failures:
            parts.append(
                f"The following account(s) could not be verified for reporting: {', '.join(reverify_failures)}"
            )
        if not parts:
            parts.append(
                "Could not verify a usable Google Ads customer for this connection. "
                "The accounts found may not have active campaigns or permissions may be restricted."
            )
        if childless_managers and not disabled and not reverify_failures:
            reason_code = "manager_no_children"
        elif disabled and not childless_managers and not reverify_failures:
            reason_code = "disabled"
        else:
            reason_code = "no_usable_customer"
        return {
            "ok": False, "reason_code": reason_code, "reason": " ".join(parts),
            "customer_id": None, "login_customer_id": None, "name": None, "customers": customers_info,
        }


async def resolve_google_ads_customer(
    client_id: str, customer_id: str | None = None, force_refresh: bool = False,
) -> dict[str, Any]:
    """Canonical, client-scoped Google Ads customer resolver.

    Returns a structured dict - never a bare customer ID - so callers can
    always tell "resolved" apart from "why not" without guessing:
      ok=True:  {"ok": True, "customer_id", "login_customer_id", "name", "customers"}
      ok=False: {"ok": False, "reason_code", "reason", "customers"}

    Both google_ads_dashboard.py's own dashboard builder and
    mcp_orchestrator.py's Ask AI tool-call path call this SAME function so
    they can never disagree about which customer a client's data comes from.
    Cached briefly (10 minutes) per (client_id, customer_id) since resolution
    costs several real Google Ads API round trips and Ask AI may call it on
    every single question.
    """
    cache_key = f"{client_id}:{customer_id or 'auto'}"
    if not force_refresh:
        cached = _resolution_cache.get(cache_key)
        if cached and (time.monotonic() - cached[0]) < _RESOLUTION_CACHE_TTL_SECONDS:
            return cached[1]
    result = await _resolve_google_ads_customer_uncached(client_id, customer_id)
    # A transient network failure says nothing about the account, so it is
    # returned but NOT remembered - otherwise one DNS blip would be replayed
    # for the whole cache TTL (to the dashboard and Ask AI alike) without any
    # further attempt to reach Google. Successes and definite account-state
    # errors are cached exactly as before.
    if result.get("reason_code") != "network_error":
        _resolution_cache[cache_key] = (time.monotonic(), result)
    return result


# ── GAQL queries ─────────────────────────────────────────────────────────────

def _account_summary_query(preset: str) -> str:
    return (
        "SELECT metrics.impressions, metrics.clicks, metrics.ctr,"
        " metrics.average_cpc, metrics.cost_micros,"
        " metrics.conversions, metrics.conversions_value"
        f" FROM customer WHERE segments.date DURING {preset}"
    )


def _campaign_perf_query(preset: str) -> str:
    return (
        "SELECT campaign.id, campaign.name, campaign.status,"
        " metrics.impressions, metrics.clicks, metrics.ctr,"
        " metrics.cost_micros, metrics.conversions, metrics.conversions_value"
        f" FROM campaign WHERE segments.date DURING {preset}"
        " AND campaign.status = 'ENABLED'"
        " ORDER BY metrics.cost_micros DESC"
        " LIMIT 10"
    )


def _daily_trend_query(preset: str) -> str:
    return (
        "SELECT segments.date, metrics.impressions, metrics.clicks,"
        " metrics.cost_micros, metrics.conversions"
        f" FROM customer WHERE segments.date DURING {preset}"
    )


def _device_query(preset: str) -> str:
    return (
        "SELECT segments.device, metrics.impressions, metrics.clicks,"
        " metrics.cost_micros, metrics.conversions"
        f" FROM campaign WHERE segments.date DURING {preset}"
    )


def _keyword_query(preset: str) -> str:
    return (
        "SELECT ad_group_criterion.keyword.text,"
        " ad_group_criterion.keyword.match_type,"
        " metrics.impressions, metrics.clicks, metrics.ctr,"
        " metrics.cost_micros, metrics.conversions"
        f" FROM keyword_view WHERE segments.date DURING {preset}"
        " AND campaign.status = 'ENABLED'"
        " AND ad_group.status = 'ENABLED'"
        " AND ad_group_criterion.status = 'ENABLED'"
        " ORDER BY metrics.clicks DESC LIMIT 15"
    )


def _click_type_query(preset: str) -> str:
    return (
        "SELECT segments.click_type,"
        " metrics.clicks, metrics.impressions, metrics.ctr,"
        " metrics.cost_micros"
        f" FROM campaign WHERE segments.date DURING {preset}"
        " AND campaign.status = 'ENABLED'"
    )


def _conversion_actions_query(preset: str) -> str:
    return (
        "SELECT conversion_action.category, conversion_action.name,"
        " metrics.conversions"
        f" FROM conversion_action WHERE segments.date DURING {preset}"
        " ORDER BY metrics.conversions DESC LIMIT 10"
    )


# ── Aggregation helpers ──────────────────────────────────────────────────────

def _aggregate_account(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Sum account-level metrics across all returned rows."""
    totals: dict[str, float] = {
        "impressions": 0,
        "clicks": 0,
        "cost_micros": 0,
        "conversions": 0,
        "conversions_value": 0,
        "ctr_sum": 0,
        "cpc_sum": 0,
    }
    for row in rows:
        totals["impressions"] += _float(row, "metrics.impressions")
        totals["clicks"] += _float(row, "metrics.clicks")
        totals["cost_micros"] += _float(row, "metrics.costMicros")
        totals["conversions"] += _float(row, "metrics.conversions")
        totals["conversions_value"] += _float(row, "metrics.conversionsValue")

    # Derive CTR and CPC from aggregated totals
    impressions = totals["impressions"]
    clicks = totals["clicks"]
    cost = totals["cost_micros"]
    totals["ctr"] = (clicks / impressions) if impressions > 0 else 0.0
    totals["avg_cpc"] = (cost / clicks) if clicks > 0 else 0.0  # still in micros
    return totals


def _build_campaigns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    campaigns: list[dict[str, Any]] = []
    for row in rows:
        cost = round(_micros(_float(row, "metrics.costMicros")), 2)
        conversions = round(_float(row, "metrics.conversions"), 2)
        cost_per_conv = round(cost / conversions, 2) if conversions > 0 else 0.0
        campaigns.append({
            "name": _str(row, "campaign.name"),
            "status": _str(row, "campaign.status"),
            "impressions": int(_float(row, "metrics.impressions")),
            "clicks": int(_float(row, "metrics.clicks")),
            "ctr": round(_float(row, "metrics.ctr") * 100, 2),
            "cost": cost,
            "conversions": conversions,
            "value": round(_float(row, "metrics.conversionsValue"), 2),
            "costPerConversion": cost_per_conv,
        })
    return campaigns


def _build_trend(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Aggregate by date since daily rows may repeat per segment
    by_date: dict[str, dict[str, float]] = {}
    for row in rows:
        d = _str(row, "segments.date")
        if not d:
            continue
        entry = by_date.setdefault(d, {"impressions": 0, "clicks": 0, "cost": 0, "conversions": 0})
        entry["impressions"] += _float(row, "metrics.impressions")
        entry["clicks"] += _float(row, "metrics.clicks")
        entry["cost"] += _micros(_float(row, "metrics.costMicros"))
        entry["conversions"] += _float(row, "metrics.conversions")

    result = []
    for date, vals in sorted(by_date.items()):
        imp = vals["impressions"]
        clk = vals["clicks"]
        cost = vals["cost"]
        cpm = (cost / imp * 1000) if imp > 0 else 0.0
        cpc = (cost / clk) if clk > 0 else 0.0
        cpl = (cost / vals["conversions"]) if vals["conversions"] > 0 else 0.0
        result.append({
            "date": date,
            "impressions": int(imp),
            "clicks": int(clk),
            "cost": round(cost, 2),
            "conversions": round(vals["conversions"], 2),
            "cpm": round(cpm, 2),
            "cpc": round(cpc, 2),
            "cpl": round(cpl, 2),
        })
    return result


def _build_devices(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Aggregate by device since rows may repeat per campaign
    by_device: dict[str, dict[str, float]] = {}
    for row in rows:
        device = _str(row, "segments.device") or "UNKNOWN"
        entry = by_device.setdefault(device, {"impressions": 0, "clicks": 0, "cost": 0, "conversions": 0})
        entry["impressions"] += _float(row, "metrics.impressions")
        entry["clicks"] += _float(row, "metrics.clicks")
        entry["cost"] += _micros(_float(row, "metrics.costMicros"))
        entry["conversions"] += _float(row, "metrics.conversions")

    return [
        {
            "device": device,
            "impressions": int(vals["impressions"]),
            "clicks": int(vals["clicks"]),
            "cost": round(vals["cost"], 2),
            "conversions": round(vals["conversions"], 2),
        }
        for device, vals in sorted(by_device.items(), key=lambda kv: kv[1]["cost"], reverse=True)
    ]



CACHE_HOURS = 12


async def _get_org_id_gads(client_id: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
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
        logger.info("org_id lookup failed: %s", exc)
    return client_id

async def _get_cached_gads(org_id: str, customer_id: str, days: int) -> dict | None:
    if not supabase_admin_configured():
        return None
    try:
        import httpx as _httpx
        cutoff = (utc_now().replace(tzinfo=None) if utc_now().tzinfo else utc_now()).isoformat()
        from datetime import timedelta
        cutoff = (utc_now() - timedelta(hours=CACHE_HOURS)).isoformat()
        async with _httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{supabase_rest_url()}/analytics_google_ads_cache",
                params={
                    "select": "data_json,synced_at",
                    "org_id": f"eq.{org_id}",
                    "customer_id": f"eq.{customer_id}",
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
            return cached
    except Exception as exc:
        logger.info("Google Ads cache read failed (non-fatal): %s", exc)
    return None


async def _save_gads_cache(org_id: str, customer_id: str, days: int, data: dict) -> None:
    if not supabase_admin_configured():
        return
    try:
        import httpx as _httpx
        payload = {
            "org_id": org_id,
            "customer_id": customer_id,
            "days": days,
            "data_json": data,
            "synced_at": utc_now().isoformat(),
        }
        async with _httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{supabase_rest_url()}/analytics_google_ads_cache",
                params={"on_conflict": "org_id,customer_id,days"},
                headers=supabase_service_headers({"Prefer": "resolution=merge-duplicates"}),
                json=payload,
            )
            resp.raise_for_status()
        logger.info("Google Ads cache saved for org %s customer %s", org_id, customer_id)
    except Exception as exc:
        logger.info("Google Ads cache write failed (non-fatal): %s", exc)


async def _upsert_gads_daily_rows(org_id: str, customer_id: str, trend: list) -> None:
    if not supabase_admin_configured() or not trend:
        return
    try:
        import httpx as _httpx
        rows = []
        for row in trend:
            rows.append({
                "org_id": org_id,
                "account_id": customer_id,
                "date": row["date"],
                "clicks": int(row.get("clicks") or 0),
                "impressions": int(row.get("impressions") or 0),
                "spend": float(row.get("cost") or 0),
                "conversions": float(row.get("conversions") or 0),
                "cpc": float(row.get("cpc") or 0),
                "cpm": float(row.get("cpm") or 0),
                "cpa": float(row.get("cpl") or 0),
                "revenue": 0.0,
                "synced_at": utc_now().isoformat(),
            })
        async with _httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{supabase_rest_url()}/analytics_google_ads",
                params={"on_conflict": "org_id,account_id,date"},
                headers=supabase_service_headers({"Prefer": "resolution=merge-duplicates"}),
                json=rows,
            )
            resp.raise_for_status()
        logger.info("Google Ads upserted %d daily rows for org %s", len(rows), org_id)
    except Exception as exc:
        logger.info("Google Ads daily row upsert failed (non-fatal): %s", exc)

def _build_click_types(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_type: dict[str, dict[str, float]] = {}
    for row in rows:
        ct = _str(row, "segments.clickType") or "UNKNOWN"
        entry = by_type.setdefault(ct, {"clicks": 0, "impressions": 0, "cost": 0})
        entry["clicks"] += _float(row, "metrics.clicks")
        entry["impressions"] += _float(row, "metrics.impressions")
        entry["cost"] += _micros(_float(row, "metrics.costMicros"))
    return [
        {
            "clickType": ct.replace("_", " ").title(),
            "clicks": int(v["clicks"]),
            "impressions": int(v["impressions"]),
            "cost": round(v["cost"], 2),
            "ctr": round((v["clicks"] / v["impressions"] * 100) if v["impressions"] > 0 else 0, 2),
        }
        for ct, v in sorted(by_type.items(), key=lambda x: x[1]["clicks"], reverse=True)
        if v["clicks"] > 0
    ]


# ── Main builder ─────────────────────────────────────────────────────────────

async def build_google_ads_dashboard(
    client_id: str, days: int = 30, customer_id: str | None = None, force_refresh: bool = False,
) -> dict[str, Any]:
    """Build a Google Ads dashboard payload for the given client."""
    token = await get_google_ads_access_token(client_id)
    if not token:
        return {
            "ok": False,
            "reason": "Google Ads is not connected for this client.",
            "customer_id": None,
            "customers": [],
            "date_range": None,
        }

    date_preset = _preset(days)
    org_id = await _get_org_id_gads(client_id)

    # ── Check cache first ──
    if not force_refresh and customer_id is None:
        cached = await _get_cached_gads(org_id, "auto", days)
        if cached:
            logger.info("Google Ads serving cached dashboard for org %s", org_id)
            return cached

    resolution = await resolve_google_ads_customer(client_id, customer_id=customer_id, force_refresh=force_refresh)
    if not resolution["ok"]:
        return {
            "ok": False,
            "reason": resolution["reason"],
            "reason_code": resolution["reason_code"],
            "customer_id": None,
            "customers": resolution.get("customers", []),
            "date_range": {"preset": date_preset, "days": days},
        }

    cid = resolution["customer_id"]
    login_cid = resolution.get("login_customer_id")
    customers_info = resolution.get("customers") or [
        {"id": cid, "name": resolution.get("name") or cid, "accessible": True, "manager": False},
    ]

    async with httpx.AsyncClient(timeout=30) as client:
        logger.info(
            "Google Ads dashboard using resolved customer %s (login_cid=%s) for client %s (preset=%s)",
            cid, login_cid or "self", client_id, date_preset,
        )
        try:
            account_rows, campaign_rows, trend_rows, device_rows, conversion_rows, keyword_rows, click_type_rows = await asyncio.gather(
                _search(client, token, cid, _account_summary_query(date_preset), login_cid=login_cid),
                _search(client, token, cid, _campaign_perf_query(date_preset), login_cid=login_cid),
                _search(client, token, cid, _daily_trend_query(date_preset), login_cid=login_cid),
                _search(client, token, cid, _device_query(date_preset), login_cid=login_cid),
                _search(client, token, cid, _conversion_actions_query(date_preset), login_cid=login_cid),
                _search(client, token, cid, _keyword_query(date_preset), login_cid=login_cid),
                _search(client, token, cid, _click_type_query(date_preset), login_cid=login_cid),
            )
        except httpx.HTTPStatusError as exc:
            reason_code, message = _classify_google_ads_error(exc)
            logger.warning("Google Ads resolved customer %s failed on report queries: %s", cid, message)
            return {
                "ok": False,
                "reason": f"Google Ads customer {cid} was verified but reporting failed: {message}",
                "reason_code": reason_code,
                "customer_id": None,
                "customers": customers_info,
                "date_range": {"preset": date_preset, "days": days},
            }

    # Aggregate account-level metrics
    acct = _aggregate_account(account_rows)
    impressions = acct["impressions"]
    clicks = acct["clicks"]
    ctr = acct["ctr"]
    avg_cpc = _micros(acct["avg_cpc"])
    cost = _micros(acct["cost_micros"])
    conversions = acct["conversions"]

    # Build sub-sections
    campaigns = _build_campaigns(campaign_rows)
    trend = _build_trend(trend_rows)
    devices = _build_devices(device_rows)

    has_data = impressions > 0 or clicks > 0 or bool(campaigns) or bool(trend)

    # Cost per conversion (account level)
    cost_per_conversion = round(cost / conversions, 2) if conversions > 0 else 0.0

    # Impressions by month from trend data
    impressions_by_month: dict[str, int] = {}
    for t in trend:
        month = t["date"][:7] if len(t["date"]) >= 7 else t["date"]
        impressions_by_month[month] = impressions_by_month.get(month, 0) + t["impressions"]
    impressions_monthly = [
        {"month": m, "impressions": v}
        for m, v in sorted(impressions_by_month.items())
    ]

    # Performance funnel: Impressions → Clicks → Conversions
    funnel = [
        {"stage": "Impressions", "value": int(impressions)},
        {"stage": "Clicks", "value": int(clicks)},
        {"stage": "Conversions", "value": int(conversions)},
    ]

    result = {
        "ok": True,
        "has_data": has_data,
        "empty_reason": (
            "Google Ads is connected, but no data was returned for the selected date range."
            if not has_data else ""
        ),
        "generated_at": utc_now().isoformat(),
        "customer_id": cid,
        "customers": customers_info,
        "date_range": {"preset": date_preset, "days": days},
        "cards": [
            {
                "label": "Impressions",
                "value": _format_number(impressions),
                "tone": "blue",
                "detail": "total ad impressions",
            },
            {
                "label": "Clicks",
                "value": _format_number(clicks),
                "tone": "green",
                "detail": "total clicks",
            },
            {
                "label": "CTR",
                "value": _format_percent(ctr),
                "tone": "violet",
                "detail": "click-through rate",
            },
            {
                "label": "Avg CPC",
                "value": _format_money(avg_cpc),
                "tone": "amber",
                "detail": "average cost per click",
            },
            {
                "label": "Cost",
                "value": _format_money(cost),
                "tone": "rose",
                "detail": "total ad spend",
            },
            {
                "label": "Conversions",
                "value": _format_number(conversions),
                "tone": "emerald",
                "detail": "total conversions",
            },
            {
                "label": "Cost / Conv.",
                "value": _format_money(cost_per_conversion),
                "tone": "amber",
                "detail": "cost per conversion",
            },
        ],
        "trend": trend,
        "campaigns": campaigns,
        "devices": devices,
        "impressions_monthly": impressions_monthly,
        "funnel": funnel,
        "cost_per_conversion": cost_per_conversion,
        "conversion_actions": [
            {
                "category": _str(row, "conversionAction.category"),
                "name": _str(row, "conversionAction.name"),
                "conversions": round(_float(row, "metrics.conversions"), 0),
            }
            for row in conversion_rows
            if _float(row, "metrics.conversions") > 0
        ],
        "keywords": [
            {
                "keyword": _str(row, "adGroupCriterion.keyword.text"),
                "matchType": _str(row, "adGroupCriterion.keyword.matchType"),
                "impressions": int(_float(row, "metrics.impressions")),
                "clicks": int(_float(row, "metrics.clicks")),
                "ctr": round(_float(row, "metrics.ctr") * 100, 2),
                "cost": round(_micros(_float(row, "metrics.costMicros")), 2),
                "conversions": round(_float(row, "metrics.conversions"), 2),
            }
            for row in keyword_rows
            if _str(row, "adGroupCriterion.keyword.text")
        ],
        "click_types": _build_click_types(click_type_rows),
    }

    # ── Save to Supabase cache + daily rows (background) ──
    cache_key = customer_id or "auto"
    asyncio.ensure_future(_save_gads_cache(org_id, cache_key, days, result))
    asyncio.ensure_future(_upsert_gads_daily_rows(org_id, cid or cache_key, trend))

    return result
