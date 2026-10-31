# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from .core import logger, utc_now
from .meta_ads import get_meta_ads_access_token
from .supabase_admin import supabase_rest_url, supabase_service_headers, supabase_admin_configured


def _api_version() -> str:
    return os.getenv("META_GRAPH_API_VERSION", "v20.0")


def _base_url() -> str:
    return f"https://graph.facebook.com/{_api_version()}"


_INSIGHT_FIELDS = ",".join([
    "spend", "reach", "impressions", "frequency",
    "clicks", "inline_link_clicks", "ctr", "cpc", "cpm",
    "actions", "action_values", "purchase_roas",
])

_DAYS_TO_PRESET = {
    7: "last_7d",
    14: "last_14d",
    30: "last_30d",
    90: "last_90d",
}


# ---------------------------------------------------------------------------
# Formatting helpers (mirrors ga4_dashboard.py)
# ---------------------------------------------------------------------------

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
    """Format a value that is *already* in percent units (e.g. 2.35 -> '2.35%')."""
    return f"{value:.2f}%"


# ---------------------------------------------------------------------------
# Helpers for parsing Meta insight rows
# ---------------------------------------------------------------------------

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _find_action(actions: list[dict[str, Any]] | None, action_type: str) -> float:
    """Search the ``actions`` array for a specific ``action_type`` and return its value."""
    if not actions:
        return 0.0
    for action in actions:
        if action.get("action_type") == action_type:
            return _safe_float(action.get("value"))
    return 0.0


def _find_action_value(action_values: list[dict[str, Any]] | None, action_type: str) -> float:
    """Search the ``action_values`` array for a specific ``action_type``."""
    if not action_values:
        return 0.0
    for av in action_values:
        if av.get("action_type") == action_type:
            return _safe_float(av.get("value"))
    return 0.0


def _find_roas(row: dict[str, Any]) -> float:
    """Extract purchase ROAS from ``purchase_roas`` or ``website_purchase_roas``."""
    for field in ("purchase_roas", "website_purchase_roas"):
        entries = row.get(field)
        if isinstance(entries, list):
            for entry in entries:
                val = _safe_float(entry.get("value"))
                if val:
                    return val
    return 0.0


def _date_preset(days: int) -> str:
    return _DAYS_TO_PRESET.get(days, "last_30d")


# ---------------------------------------------------------------------------
# Graph API callers
# ---------------------------------------------------------------------------

async def _graph_get(
    client: httpx.AsyncClient,
    path: str,
    token: str,
    params: dict[str, Any] | None = None,
) -> Any:
    query = dict(params or {})
    query["access_token"] = token
    response = await client.get(f"{_base_url()}/{path.lstrip('/')}", params=query)
    response.raise_for_status()
    return response.json()


async def _list_ad_accounts(
    client: httpx.AsyncClient, token: str,
) -> list[dict[str, Any]]:
    data = await _graph_get(
        client,
        "me/adaccounts",
        token,
        {"fields": "id,account_id,name,account_status,currency"},
    )
    return data.get("data") or []


async def _get_insights(
    client: httpx.AsyncClient,
    token: str,
    account_id: str,
    preset: str,
    *,
    level: str = "account",
    limit: int | None = None,
    extra_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "fields": _INSIGHT_FIELDS,
        "date_preset": preset,
        "level": level,
    }
    if limit is not None:
        params["limit"] = limit
    if extra_params:
        params.update(extra_params)
    data = await _graph_get(client, f"{account_id}/insights", token, params)
    return data.get("data") or []


# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------

def _parse_overview(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {
            "reach": 0, "impressions": 0, "clicks": 0,
            "ctr": 0, "cpm": 0, "spend": 0,
        }
    row = rows[0]
    return {
        "reach": _safe_float(row.get("reach")),
        "impressions": _safe_float(row.get("impressions")),
        "clicks": _safe_float(row.get("inline_link_clicks") or row.get("clicks")),
        "ctr": _safe_float(row.get("ctr")),
        "cpm": _safe_float(row.get("cpm")),
        "spend": _safe_float(row.get("spend")),
    }


def _parse_campaign_row(row: dict[str, Any]) -> dict[str, Any]:
    actions = row.get("actions") or []
    return {
        "name": row.get("campaign_name") or row.get("adset_name") or "(unknown)",
        "status": "ACTIVE",
        "spend": _safe_float(row.get("spend")),
        "reach": _safe_float(row.get("reach")),
        "impressions": _safe_float(row.get("impressions")),
        "clicks": _safe_float(row.get("inline_link_clicks") or row.get("clicks")),
        "ctr": _safe_float(row.get("ctr")),
        "cpm": _safe_float(row.get("cpm")),
        "purchases": _find_action(actions, "purchase"),
        "roas": _find_roas(row),
    }


def _parse_trend_row(row: dict[str, Any]) -> dict[str, Any]:
    impressions = _safe_float(row.get("impressions"))
    clicks = _safe_float(row.get("inline_link_clicks") or row.get("clicks"))
    spend = _safe_float(row.get("spend"))
    reach = _safe_float(row.get("reach"))
    return {
        "date": row.get("date_start") or "",
        "spend": spend,
        "impressions": impressions,
        "clicks": clicks,
        "reach": reach,
        "cpm": _safe_float(row.get("cpm")),
        "ctr": _safe_float(row.get("ctr")),
        "roas": _find_roas(row),
        "frequency": _safe_float(row.get("frequency")),
        "purchases": _find_action(row.get("actions") or [], "purchase"),
    }


def _parse_platform_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "platform": row.get("publisher_platform") or "unknown",
        "spend": _safe_float(row.get("spend")),
        "impressions": _safe_float(row.get("impressions")),
        "clicks": _safe_float(row.get("inline_link_clicks") or row.get("clicks")),
        "reach": _safe_float(row.get("reach")),
    }


# ---------------------------------------------------------------------------
# Select the first active ad account
# ---------------------------------------------------------------------------

def _select_account(accounts: list[dict[str, Any]]) -> dict[str, Any] | None:
    # Prefer first account with status=1 (ACTIVE)
    for acc in accounts:
        if acc.get("account_status") == 1:
            return acc
    return accounts[0] if accounts else None


def _clean_account_id(account: dict[str, Any]) -> str:
    raw = account.get("id") or ""
    return raw if raw.startswith("act_") else f"act_{raw}"


# ---------------------------------------------------------------------------
# Main dashboard builder
# ---------------------------------------------------------------------------


CACHE_HOURS = 12


async def _get_org_id(client_id: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{supabase_rest_url()}/clients",
                params={"select": "organization_id", "id": f"eq.{client_id}", "limit": "1"},
                headers=supabase_service_headers(),
            )
            rows = resp.json() if resp.status_code == 200 else []
            if rows and rows[0].get("organization_id"):
                return rows[0]["organization_id"]
    except Exception as exc:
        logger.info("org_id lookup failed: %s", exc)
    return client_id


async def _get_cached_meta(org_id: str, account_id: str, days: int) -> dict | None:
    if not supabase_admin_configured():
        return None
    try:
        from datetime import timedelta
        cutoff = (utc_now() - timedelta(hours=CACHE_HOURS)).isoformat()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{supabase_rest_url()}/analytics_meta_cache",
                params={
                    "select": "data_json,synced_at",
                    "org_id": f"eq.{org_id}",
                    "account_id": f"eq.{account_id}",
                    "days": f"eq.{days}",
                    "synced_at": f"gte.{cutoff}",
                    "limit": "1",
                    "order": "synced_at.desc",
                },
                headers=supabase_service_headers(),
            )
            rows = resp.json() if resp.status_code == 200 else []
        if rows:
            cached = rows[0]["data_json"]
            cached["_from_cache"] = True
            return cached
    except Exception as exc:
        logger.info("Meta cache read failed: %s", exc)
    return None


async def _save_meta_cache(org_id: str, account_id: str, days: int, data: dict) -> None:
    if not supabase_admin_configured():
        return
    try:
        payload = {
            "org_id": org_id,
            "account_id": account_id,
            "days": days,
            "data_json": data,
            "synced_at": utc_now().isoformat(),
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{supabase_rest_url()}/analytics_meta_cache",
                params={"on_conflict": "org_id,account_id,days"},
                headers=supabase_service_headers({"Prefer": "resolution=merge-duplicates"}),
                json=payload,
            )
            resp.raise_for_status()
        logger.info("Meta cache saved for org %s", org_id)
    except Exception as exc:
        logger.info("Meta cache write failed: %s", exc)

async def build_meta_ads_dashboard(
    client_id: str, days: int = 30, account_id: str | None = None, force_refresh: bool = False,
) -> dict[str, Any]:
    """`force_refresh` must stay in this signature: main.py's
    /py/dashboards/meta-ads endpoint always calls this with force_refresh=
    (the query param's value, default False) through
    get_or_refresh_dashboard_cache's factory closure - the exact same
    "accept it even if unused" contract build_klaviyo_dashboard documents
    (see its own docstring). Before this fix, every single request to that
    endpoint raised `TypeError: build_meta_ads_dashboard() got an
    unexpected keyword argument 'force_refresh'` - the dashboard could
    never load real data at all, regardless of connection state.
    """
    token = await get_meta_ads_access_token(client_id)
    if not token:
        return {
            "ok": False,
            "reason": "Meta Ads is not connected for this client.",
            "account_id": None,
            "accounts": [],
        }

    org_id = await _get_org_id(client_id)
    # `account_id` is reassigned below to the resolved/cleaned account id
    # once one is selected - remembered here so the cache read (before
    # resolution) and the cache write (after) use the exact same key for
    # the "no account_id requested" case, instead of the write silently
    # keying on whichever real account got auto-selected and the read never
    # matching it again.
    requested_account_id = account_id

    # ── Check cache first ──
    if not force_refresh and requested_account_id is None:
        cached = await _get_cached_meta(org_id, "auto", days)
        if cached:
            logger.info("Meta Ads serving cached dashboard for org %s", org_id)
            return cached

    preset = _date_preset(days)

    async with httpx.AsyncClient(timeout=30) as client:
        # --- Discover ad accounts ---
        try:
            accounts = await _list_ad_accounts(client, token)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:400] if exc.response is not None else str(exc)
            logger.warning("Meta Ads dashboard: failed to list ad accounts: %s", detail)
            return {
                "ok": False,
                "reason": "Could not connect to your Meta Ads account. Try disconnecting and reconnecting Meta Ads from the Integrations page.",
                "account_id": None,
                "accounts": [],
                "date_range": {"preset": preset, "days": days},
            }
        except httpx.HTTPError as exc:
            logger.warning("Meta Ads dashboard: network error listing accounts: %s", exc)
            return {
                "ok": False,
                "reason": "Could not reach the Meta Ads API. Please check your internet connection and try again.",
                "account_id": None,
                "accounts": [],
                "date_range": {"preset": preset, "days": days},
            }

        # Build accounts list for the selector
        accounts_info = [
            {
                "id": _clean_account_id(acc),
                "name": acc.get("name") or _clean_account_id(acc),
                "status": acc.get("account_status"),
            }
            for acc in accounts
        ]
        # account_status: 1=ACTIVE, 2=DISABLED, 3=UNSETTLED, 7=PENDING_RISK_REVIEW,
        # 8=PENDING_SETTLEMENT, 9=IN_GRACE_PERIOD, 100=PENDING_CLOSURE, 101=CLOSED.
        # Logged unconditionally (not just on error) because _select_account's
        # "first ACTIVE, else accounts[0]" heuristic has no way to know which
        # account actually has real recent spend when a Business Manager has
        # more than one - the same "naive first-candidate pick is wrong for a
        # real multi-account connection" failure mode already fixed for GA4
        # (resolve_ga4_property_id) and Google Ads (resolve_google_ads_customer).
        # If a real account with data goes unpicked, this line is what proves it.
        logger.info(
            "Meta Ads dashboard discovered accounts for client %s: %s",
            client_id, accounts_info,
        )

        # Select account: user-specified or auto
        account = None
        if account_id:
            for acc in accounts:
                if _clean_account_id(acc) == account_id or acc.get("account_id") == account_id.replace("act_", ""):
                    account = acc
                    break
        if not account:
            account = _select_account(accounts)
        if not account:
            return {
                "ok": False,
                "reason": "No Meta ad accounts found. Make sure your Facebook account has access to at least one ad account in Meta Business Suite.",
                "account_id": None,
                "accounts": accounts_info,
                "date_range": {"preset": preset, "days": days},
            }

        account_id = _clean_account_id(account)
        account_name = account.get("name") or account_id

        logger.info(
            "Meta Ads dashboard selected account %s (%s) for client %s",
            account_id, account_name, client_id,
        )

        # --- Fetch all 4 insight queries in parallel ---
        try:
            overview_rows, campaign_rows, trend_rows, platform_rows = await asyncio.gather(
                _get_insights(client, token, account_id, preset,
                    level="account"),
                _get_insights(client, token, account_id, preset,
                    level="campaign", limit=10,
                    extra_params={"fields": f"campaign_name,{_INSIGHT_FIELDS}"}),
                _get_insights(client, token, account_id, preset,
                    level="account", limit=days,
                    extra_params={"time_increment": "1"}),
                _get_insights(client, token, account_id, preset,
                    level="account",
                    extra_params={"breakdowns": "publisher_platform"}),
            )
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:400] if exc.response is not None else str(exc)
            logger.warning("Meta Ads dashboard insights failed for %s: %s", account_id, detail)
            return {
                "ok": False,
                "reason": "Could not load Meta Ads data for this account. The account may not have active campaigns or permissions may be restricted.",
                "account_id": account_id,
                "account_name": account_name,
                "accounts": accounts_info,
                "date_range": {"preset": preset, "days": days},
            }
        except httpx.HTTPError as exc:
            logger.warning("Meta Ads dashboard network error for %s: %s", account_id, exc)
            return {
                "ok": False,
                "reason": "Could not reach the Meta Ads API. Please check your internet connection and try again.",
                "account_id": account_id,
                "account_name": account_name,
                "accounts": accounts_info,
                "date_range": {"preset": preset, "days": days},
            }

        # A 200 response with an empty "data" array (no HTTPStatusError) is
        # indistinguishable, from here, between "genuinely zero delivery in
        # this window" and "token lacks ads_read on this specific asset" -
        # Meta's Graph API returns {"data": []} for both rather than a
        # permission error for the insights edge specifically. Logged
        # unconditionally so a real empty result is diagnosable from
        # .braidscope-debug.log without needing to reproduce it again.
        logger.info(
            "Meta Ads dashboard insights row counts for %s (preset=%s): "
            "overview=%d campaigns=%d trend=%d platforms=%d overview_sample=%s",
            account_id, preset,
            len(overview_rows), len(campaign_rows), len(trend_rows), len(platform_rows),
            overview_rows[0] if overview_rows else None,
        )

    # --- Build the payload ---
    overview = _parse_overview(overview_rows)
    campaigns = [_parse_campaign_row(r) for r in campaign_rows]
    trend = sorted(
        [_parse_trend_row(r) for r in trend_rows],
        key=lambda r: r["date"],
    )
    platforms = [_parse_platform_row(r) for r in platform_rows]

    has_data = bool(overview_rows) or bool(campaign_rows) or bool(trend_rows)

    result = {
        "ok": True,
        "has_data": has_data,
        "empty_reason": (
            "Meta Ads is connected, but no data was returned for the selected date range."
            if not has_data else ""
        ),
        "generated_at": utc_now().isoformat(),
        "account_id": account_id,
        "account_name": account_name,
        "accounts": accounts_info,
        "date_range": {"preset": preset, "days": days},
        "cards": [
            {"label": "Reach", "value": _format_number(overview["reach"]), "tone": "blue", "detail": "people reached"},
            {"label": "Impressions", "value": _format_number(overview["impressions"]), "tone": "green", "detail": "total impressions"},
            {"label": "Clicks", "value": _format_number(overview["clicks"]), "tone": "violet", "detail": "link clicks"},
            {"label": "CTR", "value": _format_percent(overview["ctr"]), "tone": "amber", "detail": "click-through rate"},
            {"label": "CPM", "value": _format_money(overview["cpm"]), "tone": "rose", "detail": "cost per 1000 impressions"},
            {"label": "Spend", "value": _format_money(overview["spend"]), "tone": "emerald", "detail": "total spend"},
        ],
        "trend": trend,
        "campaigns": campaigns,
        "platforms": platforms,
    }

    cache_account_key = requested_account_id or "auto"
    asyncio.ensure_future(_save_meta_cache(org_id, cache_account_key, days, result))
    return result
