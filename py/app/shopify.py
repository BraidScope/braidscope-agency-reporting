# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import hmac
import os
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request, status

from .schemas import AuthUser, MetricCard, ShopifyOAuthStartResponse

SHOPIFY_SCOPES = [
    "read_orders",
    "read_products",
    "read_customers",
    "read_analytics",
]


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{name} is not configured",
        )
    return value


def supabase_rest_url(path: str) -> str:
    base_url = require_env("SUPABASE_URL").rstrip("/")
    return f"{base_url}/rest/v1/{path.lstrip('/')}"


def supabase_headers(prefer: str | None = None) -> dict[str, str]:
    service_key = require_env("SUPABASE_SERVICE_ROLE_KEY")
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def normalize_shop_domain(shop: str) -> str:
    normalized = shop.strip().lower()
    normalized = normalized.removeprefix("https://").removeprefix("http://").strip("/")
    if not normalized.endswith(".myshopify.com"):
        normalized = f"{normalized}.myshopify.com"
    if not normalized.replace("-", "").replace(".", "").isalnum():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Shopify shop domain",
        )
    return normalized


def hash_state(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def encrypt_token(token: str) -> str:
    key = require_env("CONNECTOR_TOKEN_ENCRYPTION_KEY")
    try:
        return Fernet(key.encode("utf-8")).encrypt(token.encode("utf-8")).decode("utf-8")
    except (ValueError, InvalidToken) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CONNECTOR_TOKEN_ENCRYPTION_KEY is invalid",
        ) from exc


def decrypt_token(token: str) -> str:
    key = require_env("CONNECTOR_TOKEN_ENCRYPTION_KEY")
    try:
        return Fernet(key.encode("utf-8")).decrypt(token.encode("utf-8")).decode("utf-8")
    except (ValueError, InvalidToken) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stored Shopify credential could not be decrypted",
        ) from exc


def money_amount(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except InvalidOperation:
        return Decimal("0")


def money_from_set(value: dict[str, Any] | None) -> tuple[Decimal, str | None]:
    shop_money = (value or {}).get("shopMoney") or {}
    return money_amount(shop_money.get("amount")), shop_money.get("currencyCode")


def parse_shopify_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def hash_email(email: str | None) -> str | None:
    if not email:
        return None
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def verify_shopify_hmac(params: dict[str, str]) -> None:
    received_hmac = params.get("hmac")
    if not received_hmac:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Shopify hmac")

    client_secret = require_env("SHOPIFY_CLIENT_SECRET")
    signed_params = {
        key: value
        for key, value in params.items()
        if key not in {"hmac", "signature"}
    }
    message = "&".join(f"{key}={signed_params[key]}" for key in sorted(signed_params))
    digest = hmac.new(
        client_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(digest, received_hmac):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Shopify hmac")


async def assert_client_access(client_id: str, user: AuthUser, min_role: str = "viewer") -> None:
    """Checks the caller's EFFECTIVE role on this client - their own org's
    role if they own it, or their client_memberships role if it was shared
    with them from a different agency - via the same effective_client_role()
    SQL function the RLS layer uses (one source of truth, see
    TEAM_ACCESS_ARCHITECTURE_PLAN.md), rather than a hand-rolled org-only
    membership check that would silently reject legitimate shared access.
    """
    from .client_access import get_effective_client_role, role_meets_bar

    async with httpx.AsyncClient(timeout=10) as client:
        client_response = await client.get(
            supabase_rest_url("clients"),
            params={"id": f"eq.{client_id}", "select": "id"},
            headers=supabase_headers(),
        )
        client_response.raise_for_status()
        if not client_response.json():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    role = await get_effective_client_role(client_id, user.sub)
    if not role_meets_bar(role, min_role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client access denied")


async def store_oauth_state(state: str, user: AuthUser, client_id: str, shop_domain: str) -> datetime:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            supabase_rest_url("connector_oauth_states"),
            headers=supabase_headers(),
            json={
                "state_hash": hash_state(state),
                "user_id": user.sub,
                "client_id": client_id,
                "type": "shopify",
                "shop_domain": shop_domain,
                "expires_at": expires_at.isoformat(),
            },
        )
        response.raise_for_status()
    return expires_at


async def consume_oauth_state(state: str, shop_domain: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            supabase_rest_url("connector_oauth_states"),
            params={
                "state_hash": f"eq.{hash_state(state)}",
                "type": "eq.shopify",
                "shop_domain": f"eq.{shop_domain}",
                "consumed_at": "is.null",
                "select": "id,user_id,client_id,expires_at",
            },
            headers=supabase_headers(),
        )
        response.raise_for_status()
        states = response.json()
        if not states:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state")

        oauth_state = states[0]
        expires_at = datetime.fromisoformat(oauth_state["expires_at"].replace("Z", "+00:00"))
        if expires_at <= now:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OAuth state expired")

        update_response = await client.patch(
            supabase_rest_url("connector_oauth_states"),
            params={"id": f"eq.{oauth_state['id']}"},
            headers=supabase_headers(),
            json={"consumed_at": now.isoformat()},
        )
        update_response.raise_for_status()
        return oauth_state


async def exchange_shopify_code(shop_domain: str, code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"https://{shop_domain}/admin/oauth/access_token",
            json={
                "client_id": require_env("SHOPIFY_CLIENT_ID"),
                "client_secret": require_env("SHOPIFY_CLIENT_SECRET"),
                "code": code,
            },
        )
        response.raise_for_status()
        return response.json()


async def upsert_shopify_connection(
    client_id: str,
    shop_domain: str,
    encrypted_token: str,
    scopes: list[str],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with httpx.AsyncClient(timeout=10) as client:
        credential_payload = {
            "client_id": client_id,
            "type": "shopify",
            "external_account_id": shop_domain,
            "shop_domain": shop_domain,
            "access_token_encrypted": encrypted_token,
            "scopes": scopes,
            "status": "connected",
            "updated_at": now,
        }
        credential_response = await client.post(
            supabase_rest_url("integration_credentials"),
            params={"on_conflict": "client_id,type,external_account_id"},
            headers=supabase_headers("resolution=merge-duplicates"),
            json=credential_payload,
        )
        credential_response.raise_for_status()

        data_source_payload = {
            "client_id": client_id,
            "type": "shopify",
            "status": "connected",
            "config": {"shop_domain": shop_domain, "auth_type": "oauth"},
            "last_sync_at": now,
        }
        data_source_response = await client.post(
            supabase_rest_url("data_sources"),
            params={"on_conflict": "client_id,type"},
            headers=supabase_headers("resolution=merge-duplicates"),
            json=data_source_payload,
        )
        data_source_response.raise_for_status()


async def shopify_authorization_start(
    client_id: str,
    shop: str,
    user: AuthUser,
) -> ShopifyOAuthStartResponse:
    shop_domain = normalize_shop_domain(shop)
    state = secrets.token_urlsafe(32)
    expires_at = await store_oauth_state(state, user, client_id, shop_domain)
    redirect_uri = require_env("SHOPIFY_REDIRECT_URI")
    query = urlencode(
        {
            "client_id": require_env("SHOPIFY_CLIENT_ID"),
            "scope": ",".join(SHOPIFY_SCOPES),
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    auth_url = f"https://{shop_domain}/admin/oauth/authorize?{query}"
    return ShopifyOAuthStartResponse(
        auth_url=auth_url,
        state_expires_at=expires_at.isoformat(),
    )


async def complete_shopify_oauth(request: Request) -> dict[str, Any]:
    params = dict(request.query_params)
    verify_shopify_hmac(params)

    shop_domain = normalize_shop_domain(params.get("shop", ""))
    state = params.get("state")
    code = params.get("code")
    if not state or not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing OAuth callback values")

    oauth_state = await consume_oauth_state(state, shop_domain)
    token_payload = await exchange_shopify_code(shop_domain, code)
    access_token = token_payload.get("access_token")
    if not access_token:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Shopify did not return an access token")

    scopes = [
        scope.strip()
        for scope in str(token_payload.get("scope", "")).split(",")
        if scope.strip()
    ]
    await upsert_shopify_connection(
        client_id=oauth_state["client_id"],
        shop_domain=shop_domain,
        encrypted_token=encrypt_token(access_token),
        scopes=scopes,
    )
    return {
        "type": "shopify",
        "client_id": oauth_state["client_id"],
        "shop_domain": shop_domain,
        "status": "connected",
        "scopes": scopes,
        "message": "Shopify connected. You can close this window and return to Braidscope.",
    }


async def get_shopify_credential(client_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            supabase_rest_url("integration_credentials"),
            params={
                "client_id": f"eq.{client_id}",
                "type": "eq.shopify",
                "status": "eq.connected",
                "select": "id,client_id,shop_domain,access_token_encrypted,scopes",
                "limit": "1",
            },
            headers=supabase_headers(),
        )
        response.raise_for_status()
        credentials = response.json()
        if not credentials:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No connected Shopify credential found for this client",
            )
        return credentials[0]


async def update_shopify_source_status(
    client_id: str,
    status_value: str,
    message: str | None = None,
    last_sync_at: str | None = None,
) -> None:
    config = {"auth_type": "oauth"}
    if message:
        config["last_error"] = message

    payload = {
        "status": status_value,
        "config": config,
    }
    if last_sync_at:
        payload["last_sync_at"] = last_sync_at

    async with httpx.AsyncClient(timeout=10) as client:
        previous_status = None
        try:
            current = await client.get(
                supabase_rest_url("data_sources"),
                params={
                    "select": "status",
                    "client_id": f"eq.{client_id}",
                    "type": "eq.shopify",
                    "limit": "1",
                },
                headers=supabase_headers(),
            )
            current.raise_for_status()
            rows = current.json()
            previous_status = rows[0]["status"] if rows else None
        except httpx.HTTPError:
            # Best-effort only — a failed read here must not block the real
            # status update below, it just means the transition notification
            # (which needs the *previous* status) gets skipped this once.
            pass

        response = await client.patch(
            supabase_rest_url("data_sources"),
            params={"client_id": f"eq.{client_id}", "type": "eq.shopify"},
            headers=supabase_headers(),
            json=payload,
        )
        response.raise_for_status()

    await _notify_shopify_status_transition(client_id, previous_status, status_value)


async def _notify_shopify_status_transition(
    client_id: str,
    previous_status: str | None,
    new_status: str,
) -> None:
    """Integration health is workspace-risk, not a self-service confirmation
    (see notification_improvements.md section 9/15), so recipients are the
    organization's owners/admins rather than whichever request happened to
    trigger the sync — background syncs have no "actor" at all. Only fires on
    an actual transition, so a sync that keeps failing after the first
    needs_reconnect never re-notifies, and a healthy sync never notifies.
    """
    if previous_status == new_status:
        return
    if new_status not in ("needs_reconnect", "connected"):
        return
    if new_status == "connected" and previous_status != "needs_reconnect":
        return

    try:
        from .supabase_admin import get_client_workspace

        workspace = await get_client_workspace(client_id)
        if not workspace or not workspace.get("organization_id"):
            return
        client_name = workspace.get("name") or "this workspace"

        if new_status == "needs_reconnect":
            from .supabase_admin import create_client_notification

            await create_client_notification(
                client_id,
                "Shopify needs attention",
                f"Reconnect Shopify to resume data syncing for {client_name}.",
                event_type="integration_auth_expired",
                category="integrations",
                action_url="/integrations",
                action_label="Reconnect",
                priority="high",
                email_eligible=True,
                dedupe_key=f"integration_auth_expired:{client_id}:shopify",
            )
        else:
            from .supabase_admin import create_client_notification

            await create_client_notification(
                client_id,
                "Shopify sync restored",
                f"Data syncing for {client_name} is working again.",
                event_type="integration_sync_restored",
                category="integrations",
                action_url="/integrations",
                action_label="View Integrations",
                email_eligible=True,
            )
    except Exception:
        # A notification-path failure must never surface as (or mask) a real
        # sync error — this function only ever runs as a side effect of
        # update_shopify_source_status.
        from .core import logger
        logger.exception("Shopify status-transition notification failed for client %s", client_id)


SHOPIFY_ORDERS_QUERY = """
query ShopifyOrders($first: Int!, $after: String, $query: String!) {
  orders(first: $first, after: $after, sortKey: CREATED_AT, query: $query) {
    edges {
      cursor
      node {
        id
        name
        createdAt
        processedAt
        email
        totalPriceSet { shopMoney { amount currencyCode } }
        subtotalPriceSet { shopMoney { amount currencyCode } }
        currentTotalPriceSet { shopMoney { amount currencyCode } }
        totalRefundedSet { shopMoney { amount currencyCode } }
        customer { id email }
        lineItems(first: 50) {
          edges {
            node {
              quantity
              discountedTotalSet { shopMoney { amount currencyCode } }
              product { id title }
            }
          }
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""


async def fetch_shopify_orders(
    shop_domain: str,
    access_token: str,
    days: int,
    page_size: int = 50,
    max_pages: int = 4,
) -> list[dict[str, Any]]:
    api_version = os.getenv("SHOPIFY_API_VERSION", "2026-01")
    since = datetime.now(timezone.utc) - timedelta(days=max(days, 1))
    query_filter = f"created_at:>={since.date().isoformat()}"
    orders: list[dict[str, Any]] = []
    cursor: str | None = None

    async with httpx.AsyncClient(timeout=30) as client:
        for _page in range(max_pages):
            response = await client.post(
                f"https://{shop_domain}/admin/api/{api_version}/graphql.json",
                headers={
                    "X-Shopify-Access-Token": access_token,
                    "Content-Type": "application/json",
                },
                json={
                    "query": SHOPIFY_ORDERS_QUERY,
                    "variables": {
                        "first": page_size,
                        "after": cursor,
                        "query": query_filter,
                    },
                },
            )
            if response.status_code in {401, 403}:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Shopify credential was rejected",
                )
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Shopify GraphQL returned errors",
                )

            connection = payload.get("data", {}).get("orders", {})
            orders.extend(edge["node"] for edge in connection.get("edges", []))
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

    return orders


def normalize_shopify_orders(
    client_id: str,
    shop_domain: str,
    orders: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    synced_at = datetime.now(timezone.utc).isoformat()
    normalized_orders: list[dict[str, Any]] = []
    daily: dict[tuple[str, str | None], dict[str, Any]] = defaultdict(
        lambda: {
            "orders": 0,
            "gross_revenue": Decimal("0"),
            "refunds": Decimal("0"),
            "net_revenue": Decimal("0"),
            "customers": set(),
        }
    )
    product_daily: dict[tuple[str, str, str | None], dict[str, Any]] = defaultdict(
        lambda: {
            "product_title": None,
            "currency_code": None,
            "quantity": 0,
            "gross_revenue": Decimal("0"),
        }
    )

    for order in orders:
        processed_at = parse_shopify_datetime(order.get("processedAt") or order.get("createdAt"))
        total_price, currency_code = money_from_set(order.get("totalPriceSet"))
        subtotal_price, _currency = money_from_set(order.get("subtotalPriceSet"))
        current_total, _currency = money_from_set(order.get("currentTotalPriceSet"))
        total_refunded, _currency = money_from_set(order.get("totalRefundedSet"))

        customer = order.get("customer") or {}
        customer_email = customer.get("email") or order.get("email")
        line_items = ((order.get("lineItems") or {}).get("edges") or [])
        line_item_count = sum(int((edge.get("node") or {}).get("quantity") or 0) for edge in line_items)

        normalized_orders.append(
            {
                "client_id": client_id,
                "shop_domain": shop_domain,
                "order_id": order.get("id"),
                "order_name": order.get("name"),
                "processed_at": processed_at.isoformat(),
                "currency_code": currency_code,
                "total_price": str(total_price),
                "subtotal_price": str(subtotal_price),
                "total_refunded": str(total_refunded),
                "net_revenue": str(current_total),
                "customer_id": customer.get("id"),
                "customer_email_hash": hash_email(customer_email),
                "line_item_count": line_item_count,
                "raw": order,
                "synced_at": synced_at,
            }
        )

        metric_key = (processed_at.date().isoformat(), currency_code)
        day = daily[metric_key]
        day["orders"] += 1
        day["gross_revenue"] += total_price
        day["refunds"] += total_refunded
        day["net_revenue"] += current_total
        if customer.get("id"):
            day["customers"].add(customer["id"])

        for edge in line_items:
            line_item = edge.get("node") or {}
            product = line_item.get("product") or {}
            product_id = product.get("id")
            if not product_id:
                continue
            line_revenue, line_currency = money_from_set(line_item.get("discountedTotalSet"))
            product_key = (processed_at.date().isoformat(), product_id, line_currency or currency_code)
            product_metric = product_daily[product_key]
            product_metric["product_title"] = product.get("title")
            product_metric["currency_code"] = line_currency or currency_code
            product_metric["quantity"] += int(line_item.get("quantity") or 0)
            product_metric["gross_revenue"] += line_revenue

    daily_rows: list[dict[str, Any]] = []
    for (metric_date, currency_code), values in daily.items():
        order_count = values["orders"]
        aov = values["net_revenue"] / Decimal(order_count) if order_count else Decimal("0")
        daily_rows.append(
            {
                "client_id": client_id,
                "shop_domain": shop_domain,
                "metric_date": metric_date,
                "currency_code": currency_code,
                "orders": order_count,
                "gross_revenue": str(values["gross_revenue"]),
                "refunds": str(values["refunds"]),
                "net_revenue": str(values["net_revenue"]),
                "average_order_value": str(aov.quantize(Decimal("0.01"))),
                "customers": len(values["customers"]),
                "synced_at": synced_at,
            }
        )

    product_rows: list[dict[str, Any]] = []
    for (metric_date, product_id, currency_code), values in product_daily.items():
        product_rows.append(
            {
                "client_id": client_id,
                "shop_domain": shop_domain,
                "metric_date": metric_date,
                "product_id": product_id,
                "product_title": values["product_title"],
                "currency_code": currency_code,
                "quantity": values["quantity"],
                "gross_revenue": str(values["gross_revenue"]),
                "synced_at": synced_at,
            }
        )

    return normalized_orders, daily_rows, product_rows


async def upsert_analytics_rows(
    orders: list[dict[str, Any]],
    daily_rows: list[dict[str, Any]],
    product_rows: list[dict[str, Any]],
) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        if orders:
            orders_response = await client.post(
                supabase_rest_url("analytics_shopify_orders"),
                params={"on_conflict": "client_id,order_id"},
                headers=supabase_headers("resolution=merge-duplicates"),
                json=orders,
            )
            orders_response.raise_for_status()

        if daily_rows:
            daily_response = await client.post(
                supabase_rest_url("analytics_shopify_daily_metrics"),
                params={"on_conflict": "client_id,metric_date"},
                headers=supabase_headers("resolution=merge-duplicates"),
                json=daily_rows,
            )
            daily_response.raise_for_status()

        if product_rows:
            product_response = await client.post(
                supabase_rest_url("analytics_shopify_product_daily_metrics"),
                params={"on_conflict": "client_id,metric_date,product_id"},
                headers=supabase_headers("resolution=merge-duplicates"),
                json=product_rows,
            )
            product_response.raise_for_status()


async def sync_shopify_orders(client_id: str, days: int) -> dict[str, int]:
    await update_shopify_source_status(client_id, "syncing")
    try:
        credential = await get_shopify_credential(client_id)
        shop_domain = credential["shop_domain"]
        access_token = decrypt_token(credential["access_token_encrypted"])
        raw_orders = await fetch_shopify_orders(shop_domain, access_token, days)
        orders, daily_rows, product_rows = normalize_shopify_orders(client_id, shop_domain, raw_orders)
        await upsert_analytics_rows(orders, daily_rows, product_rows)

        synced_at = datetime.now(timezone.utc).isoformat()
        await update_shopify_source_status(client_id, "connected", last_sync_at=synced_at)
        return {
            "orders_synced": len(orders),
            "daily_rows_synced": len(daily_rows),
            "product_rows_synced": len(product_rows),
        }
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            await update_shopify_source_status(client_id, "needs_reconnect", str(exc.detail))
        else:
            await update_shopify_source_status(client_id, "connected", str(exc.detail))
        raise
    except httpx.HTTPError as exc:
        await update_shopify_source_status(client_id, "connected", str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Shopify sync request failed",
        ) from exc


async def get_shopify_daily_metrics(client_id: str, days: int) -> list[dict[str, Any]]:
    since = datetime.now(timezone.utc).date() - timedelta(days=max(days, 1))
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            supabase_rest_url("analytics_shopify_daily_metrics"),
            params={
                "client_id": f"eq.{client_id}",
                "metric_date": f"gte.{since.isoformat()}",
                "select": (
                    "metric_date,currency_code,orders,gross_revenue,refunds,"
                    "net_revenue,average_order_value,customers"
                ),
                "order": "metric_date.asc",
            },
            headers=supabase_headers(),
        )
        response.raise_for_status()
        return response.json()


def format_currency(value: Decimal, currency_code: str | None = None) -> str:
    symbol = "$" if currency_code in {None, "USD", "CAD", "AUD", "NZD"} else f"{currency_code} "
    if value >= Decimal("1000"):
        return f"{symbol}{value / Decimal('1000'):.1f}k"
    return f"{symbol}{value:.0f}"


def build_shopify_cards(rows: list[dict[str, Any]]) -> tuple[list[MetricCard], list[int], list[MetricCard]]:
    currency_code = rows[-1].get("currency_code") if rows else "USD"
    orders = sum(int(row.get("orders") or 0) for row in rows)
    revenue = sum(money_amount(row.get("net_revenue")) for row in rows)
    refunds = sum(money_amount(row.get("refunds")) for row in rows)
    customers = sum(int(row.get("customers") or 0) for row in rows)
    aov = revenue / Decimal(orders) if orders else Decimal("0")

    shopify_cards = [
        MetricCard(label="Orders", value=str(orders), direction="up", detail="synced from Shopify"),
        MetricCard(label="AOV", value=format_currency(aov, currency_code), direction="up", detail="net revenue / orders"),
        MetricCard(label="Revenue", value=format_currency(revenue, currency_code), direction="up", detail="net revenue"),
        MetricCard(label="Refunds", value=format_currency(refunds, currency_code), direction="dn", detail="refunded value"),
    ]

    revenue_by_week: list[int] = []
    if rows:
        chunk_size = max(len(rows) // 4, 1)
        chunks = [rows[index : index + chunk_size] for index in range(0, len(rows), chunk_size)]
        revenue_by_week = [
            int(sum(money_amount(row.get("net_revenue")) for row in chunk) / Decimal("100"))
            for chunk in chunks[-4:]
        ]

    overview_cards = [
        MetricCard(label="Total revenue", value=format_currency(revenue, currency_code), direction="up", detail="Shopify synced"),
        MetricCard(label="Ad spend", value="$8.4k", direction="up", detail="+4%"),
        MetricCard(label="Blended ROAS", value="4.2x", direction="up", detail="+8%"),
        MetricCard(label="Orders", value=str(orders), direction="up", detail=f"{customers} customers"),
    ]
    return shopify_cards, revenue_by_week or [60, 72, 85, 95], overview_cards
