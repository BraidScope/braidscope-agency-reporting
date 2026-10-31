# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx

from .core import logger, utc_now
from .shopify import (
    decrypt_token,
    fetch_shopify_orders,
    format_currency,
    get_shopify_credential,
    get_shopify_daily_metrics,
    money_amount,
    normalize_shopify_orders,
    supabase_headers,
    supabase_rest_url,
    upsert_analytics_rows,
)

CACHE_HOURS = 12


async def _get_org_id(client_id: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{supabase_rest_url('clients')}",
                params={"select": "organization_id", "id": f"eq.{client_id}", "limit": "1"},
                headers=supabase_headers(),
            )
            resp.raise_for_status()
            rows = resp.json()
            if rows and rows[0].get("organization_id"):
                return rows[0]["organization_id"]
    except Exception as exc:
        logger.info("org_id lookup failed: %s", exc)
    return client_id


async def _get_top_products(client_id: str, days: int) -> list[dict]:
    since = (utc_now().date() - timedelta(days=max(days, 1))).isoformat()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                supabase_rest_url("analytics_shopify_product_daily_metrics"),
                params={
                    "client_id": f"eq.{client_id}",
                    "metric_date": f"gte.{since}",
                    "select": "product_id,product_title,quantity,gross_revenue,currency_code",
                    "order": "gross_revenue.desc",
                    "limit": "20",
                },
                headers=supabase_headers(),
            )
            resp.raise_for_status()
            rows = resp.json()

        # Aggregate by product
        by_product: dict[str, dict] = {}
        for row in rows:
            pid = row.get("product_id") or "unknown"
            if pid not in by_product:
                by_product[pid] = {
                    "product": row.get("product_title") or "Unknown",
                    "quantity": 0,
                    "revenue": Decimal("0"),
                    "currency": row.get("currency_code"),
                }
            by_product[pid]["quantity"] += int(row.get("quantity") or 0)
            by_product[pid]["revenue"] += money_amount(row.get("gross_revenue"))

        sorted_products = sorted(by_product.values(), key=lambda x: x["revenue"], reverse=True)
        currency = sorted_products[0]["currency"] if sorted_products else "USD"
        return [
            {
                "product": p["product"],
                "quantity": p["quantity"],
                "revenue": float(p["revenue"]),
                "revenueFormatted": format_currency(p["revenue"], currency),
            }
            for p in sorted_products[:10]
        ]
    except Exception as exc:
        logger.info("Top products fetch failed: %s", exc)
        return []


async def build_shopify_dashboard(
    client_id: str,
    days: int = 30,
    force_refresh: bool = False,
) -> dict[str, Any]:

    # Check if Shopify is connected
    try:
        credential = await get_shopify_credential(client_id)
        shop_domain = credential["shop_domain"]
    except Exception:
        return {
            "ok": False,
            "reason": "Shopify is not connected for this client.",
            "shop_domain": None,
        }

    # Sync latest orders if force_refresh or no data
    daily_rows = await get_shopify_daily_metrics(client_id, days)

    if force_refresh or not daily_rows:
        try:
            access_token = decrypt_token(credential["access_token_encrypted"])
            raw_orders = await fetch_shopify_orders(shop_domain, access_token, days)
            orders, d_rows, p_rows = normalize_shopify_orders(client_id, shop_domain, raw_orders)
            await upsert_analytics_rows(orders, d_rows, p_rows)
            daily_rows = await get_shopify_daily_metrics(client_id, days)
        except Exception as exc:
            logger.warning("Shopify sync failed: %s", exc)
            if not daily_rows:
                return {
                    "ok": False,
                    "reason": f"Could not fetch Shopify data: {exc}",
                    "shop_domain": shop_domain,
                }

    # Build metrics
    currency_code = daily_rows[-1].get("currency_code") if daily_rows else "USD"
    total_orders = sum(int(r.get("orders") or 0) for r in daily_rows)
    total_revenue = sum(money_amount(r.get("net_revenue")) for r in daily_rows)
    total_refunds = sum(money_amount(r.get("refunds")) for r in daily_rows)
    total_gross = sum(money_amount(r.get("gross_revenue")) for r in daily_rows)
    total_customers = sum(int(r.get("customers") or 0) for r in daily_rows)
    aov = total_revenue / Decimal(total_orders) if total_orders else Decimal("0")

    # Trend data
    trend = [
        {
            "date": r["metric_date"],
            "orders": int(r.get("orders") or 0),
            "revenue": float(money_amount(r.get("net_revenue"))),
            "aov": float(money_amount(r.get("average_order_value"))),
            "customers": int(r.get("customers") or 0),
        }
        for r in daily_rows
    ]

    top_products = await _get_top_products(client_id, days)

    return {
        "ok": True,
        "has_data": bool(daily_rows),
        "shop_domain": shop_domain,
        "date_range": {"days": days},
        "generated_at": utc_now().isoformat(),
        "cards": [
            {"label": "Total revenue", "value": format_currency(total_revenue, currency_code), "tone": "emerald", "detail": "net revenue"},
            {"label": "Orders", "value": str(total_orders), "tone": "blue", "detail": "total orders"},
            {"label": "Avg order value", "value": format_currency(aov, currency_code), "tone": "violet", "detail": "net revenue / orders"},
            {"label": "Refunds", "value": format_currency(total_refunds, currency_code), "tone": "rose", "detail": "refunded value"},
            {"label": "Customers", "value": str(total_customers), "tone": "amber", "detail": "unique customers"},
            {"label": "Gross revenue", "value": format_currency(total_gross, currency_code), "tone": "green", "detail": "before refunds"},
        ],
        "trend": trend,
        "top_products": top_products,
        "currency_code": currency_code,
    }
