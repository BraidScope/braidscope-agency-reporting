# SPDX-License-Identifier: AGPL-3.0-only
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException, status

from .core import logger
from .email_service import email_configured, send_notification_email

# Email eligibility is now an explicit, independent flag
# (create_user_notification's `email_eligible` param) rather than being
# inferred from `priority` — `priority` is purely visual emphasis (does this
# render as a "high" row in the bell), while `email_eligible` is "does this
# category/event's policy allow sending an email at all when the user's own
# preference is on." This split is what lets Branding be email-eligible
# without also rendering with integration-health's "high" visual treatment.

# Display names for notification copy — kept here (not per-connector modules)
# since the notification helper below is the one place that needs them.
SOURCE_DISPLAY_NAMES = {
    "google_ads": "Google Ads",
    "google_analytics_4": "Google Analytics",
    "meta_ads": "Meta Ads",
    "hubspot_crm": "HubSpot",
    "klaviyo": "Klaviyo",
    "shopify": "Shopify",
    "openrouter_ai": "OpenRouter",
    "openai_ai": "OpenAI",
    "anthropic_ai": "Anthropic",
}
AI_PROVIDER_SOURCE_TYPES = {"openrouter_ai", "openai_ai", "anthropic_ai"}

ROLE_PERMISSION_MATRIX: dict[str, set[str]] = {
    "owner": {
        "dashboards.view", "dashboards.edit", "dashboards.manage", "dashboards.delete",
        "reports.view", "reports.edit", "reports.manage", "reports.delete",
        "integrations.view", "integrations.edit", "integrations.manage", "integrations.delete",
        "branding.view", "branding.edit", "branding.manage", "branding.delete",
        "settings.view", "settings.edit", "settings.manage", "settings.delete",
        "team.view", "team.edit", "team.manage", "team.delete",
        "billing.view", "billing.edit", "billing.manage", "billing.delete",
    },
    "admin": {
        "dashboards.view", "dashboards.edit", "dashboards.manage",
        "reports.view", "reports.edit", "reports.manage",
        "integrations.view", "integrations.edit", "integrations.manage",
        "branding.view", "branding.edit", "branding.manage",
        "settings.view", "settings.edit", "settings.manage",
        "team.view", "team.edit", "team.manage",
    },
    "editor": {
        "dashboards.view", "dashboards.edit",
        "reports.view", "reports.edit",
        "integrations.view", "integrations.edit",
        "branding.view", "branding.edit",
    },
    "viewer": {
        "dashboards.view",
        "reports.view",
        "integrations.view",
    },
}

NOTIFICATION_PERMISSION_MAP: dict[str, tuple[str, str]] = {
    "integration_connected": ("integrations", "integrations.view"),
    "integration_disconnected": ("integrations", "integrations.view"),
    "integration_reconnected": ("integrations", "integrations.view"),
    "integration_sync_failed": ("integrations", "integrations.view"),
    "integration_deleted": ("integrations", "integrations.delete"),
    "integration_auth_expired": ("integrations", "integrations.view"),
    "integration_sync_restored": ("integrations", "integrations.view"),
    "llm_provider_connected": ("integrations", "integrations.view"),
    "llm_provider_disconnected": ("integrations", "integrations.view"),
    "branding_updated": ("branding", "branding.view"),
    "branding_published": ("branding", "branding.view"),
    "report_generated": ("reports", "reports.view"),
    "report_delivered": ("reports", "reports.view"),
    "team_member_invited": ("team", "team.view"),
    "team_role_changed": ("team", "team.view"),
    "team_member_removed": ("team", "team.view"),
    "membership_role_changed": ("team", "team.view"),
    "reminder_no_data_sources": ("integrations", "integrations.view"),
    "reminder_branding_incomplete": ("branding", "branding.view"),
}


def source_display_name(source_type: str) -> str:
    return SOURCE_DISPLAY_NAMES.get(source_type, source_type)


def supabase_admin_configured() -> bool:
    return bool(
        (os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL"))
        and os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )


def supabase_rest_url() -> str:
    supabase_url = os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL")
    if not supabase_url:
        raise RuntimeError("SUPABASE_URL is not configured")
    return f"{supabase_url.rstrip('/')}/rest/v1"


def supabase_service_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not service_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not configured")
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _auth_url() -> str:
    supabase_url = os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL")
    if not supabase_url:
        raise RuntimeError("SUPABASE_URL is not configured")
    return f"{supabase_url.rstrip('/')}/auth/v1"


async def get_user_email(user_id: str) -> str | None:
    """Looks up a user's email via the Auth admin API — service-role only,
    never exposed to the browser. Used solely to address a notification
    email; never returned from any endpoint."""
    if not supabase_admin_configured() or not user_id:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{_auth_url()}/admin/users/{user_id}", headers=supabase_service_headers())
        if response.status_code != 200:
            return None
        data = response.json()
    return data.get("email")


async def get_user_avatar_url(user_id: str) -> str | None:
    """Looks up the Auth profile photo for server-rendered team member rows."""
    if not supabase_admin_configured() or not user_id:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{_auth_url()}/admin/users/{user_id}", headers=supabase_service_headers())
        if response.status_code != 200:
            return None
        data = response.json()
    return (data.get("user_metadata") or {}).get("avatar_url") or None


async def find_user_id_by_email(email: str) -> str | None:
    """Reverse of get_user_email - does an existing account own this email?
    Same admin-API-list-and-filter shape as the existing org invite flow in
    account_settings.py:invite_member (Supabase's admin API has no reliable
    server-side email filter across versions, so this mirrors that already-
    proven approach rather than introducing a second one)."""
    if not supabase_admin_configured() or not email:
        return None
    clean_email = email.lower().strip()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{_auth_url()}/admin/users", params={"page": 1, "per_page": 1000}, headers=supabase_service_headers())
        if response.status_code != 200:
            return None
        payload = response.json()
        users = payload.get("users", payload if isinstance(payload, list) else [])
    match = next((u for u in users if (u.get("email") or "").lower() == clean_email), None)
    return match.get("id") if match else None


async def get_client_workspace(client_id: str) -> dict[str, Any] | None:
    """organization_id + display name for a client, or None if it doesn't exist."""
    if not supabase_admin_configured():
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{supabase_rest_url()}/clients",
            params={"select": "id,name,organization_id", "id": f"eq.{client_id}", "limit": "1"},
            headers=supabase_service_headers(),
        )
        response.raise_for_status()
        rows = response.json()
    return rows[0] if rows else None


async def get_org_admin_user_ids(organization_id: str) -> list[str]:
    """Owner + admin user ids for an organization — the recipients for
    integration-health and other workspace-risk notifications, as opposed to
    self-service success confirmations which notify only the acting user."""
    if not supabase_admin_configured() or not organization_id:
        return []
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{supabase_rest_url()}/memberships",
            params={
                "select": "user_id",
                "organization_id": f"eq.{organization_id}",
                "role": "in.(owner,admin)",
            },
            headers=supabase_service_headers(),
        )
        response.raise_for_status()
        rows = response.json()
    return [row["user_id"] for row in rows if row.get("user_id")]


async def get_client_notification_candidates(client_id: str) -> list[dict[str, Any]]:
    if not supabase_admin_configured() or not client_id:
        return []
    workspace = await get_client_workspace(client_id)
    if not workspace:
        return []

    # Do not call client_team_members here. That RPC intentionally checks
    # auth.uid() for browser access, while this service-role request has no
    # end-user auth.uid(). Resolve both membership sources explicitly and
    # deduplicate users who may appear in both during a role transition.
    async with httpx.AsyncClient(timeout=10) as client:
        owner_response = await client.get(
            f"{supabase_rest_url()}/memberships",
            params={
                "select": "user_id,role,created_at",
                "organization_id": f"eq.{workspace['organization_id']}",
            },
            headers=supabase_service_headers(),
        )
        owner_response.raise_for_status()
        shared_response = await client.get(
            f"{supabase_rest_url()}/client_memberships",
            params={
                "select": "user_id,role,created_at",
                "client_id": f"eq.{client_id}",
            },
            headers=supabase_service_headers(),
        )
        shared_response.raise_for_status()

    candidates: dict[str, dict[str, Any]] = {}
    for row in owner_response.json():
        user_id = row.get("user_id")
        if user_id:
            candidates[user_id] = {**row, "access": "owner"}
    for row in shared_response.json():
        user_id = row.get("user_id")
        if user_id and user_id not in candidates:
            candidates[user_id] = {**row, "access": "shared"}
    return list(candidates.values())


async def get_client_actor_role(client_id: str, user_id: str) -> str | None:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{supabase_rest_url()}/rpc/effective_client_role",
            json={"p_client_id": client_id, "p_user_id": user_id},
            headers=supabase_service_headers(),
        )
        response.raise_for_status()
        return response.json() or None


def role_has_permission(role: str | None, permission: str | None) -> bool:
    if not permission:
        return True
    return permission in ROLE_PERMISSION_MATRIX.get(role or "", set())


async def notification_recipients_for_client(client_id: str, event_type: str) -> tuple[list[str], str | None, str | None, dict[str, str]]:
    module, required_permission = NOTIFICATION_PERMISSION_MAP.get(event_type, ("general", None))
    candidates = await get_client_notification_candidates(client_id)
    role_by_user: dict[str, str] = {}
    allowed: list[str] = []
    for candidate in candidates:
        user_id = candidate.get("user_id")
        role = candidate.get("role")
        if user_id and role_has_permission(role, required_permission):
            allowed.append(user_id)
            role_by_user[user_id] = role
        elif user_id:
            logger.info(
                "Notification recipient excluded: event=%s client_id=%s user_id=%s role=%s required_permission=%s",
                event_type,
                client_id,
                user_id,
                role,
                required_permission,
            )
    logger.info(
        "Notification recipients resolved: event=%s client_id=%s candidates=%s eligible=%s required_permission=%s",
        event_type,
        client_id,
        len(candidates),
        len(allowed),
        required_permission,
    )
    return allowed, module, required_permission, role_by_user


async def get_membership(user_id: str, organization_id: str) -> dict[str, Any] | None:
    """This user's own membership row for an organization, or None if they
    aren't a member — the server-side check behind every agency-level action
    that (unlike a client-scoped one) has no clients-table row to bounce a
    client_id off of."""
    if not supabase_admin_configured() or not user_id or not organization_id:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{supabase_rest_url()}/memberships",
            params={
                "select": "role",
                "user_id": f"eq.{user_id}",
                "organization_id": f"eq.{organization_id}",
                "limit": "1",
            },
            headers=supabase_service_headers(),
        )
        response.raise_for_status()
        rows = response.json()
    return rows[0] if rows else None


def _category_prefs_for(
    prefs: dict[str, Any] | None,
    client_id: str | None,
    category: str,
    notification_scope: str = "client",
) -> dict[str, Any]:
    """Reads the effective category prefs for a (client_id, category) pair
    out of a membership's notification_prefs JSONB.

    Shape: { global: {categories:{...}}, clients: { <clientId>: {categories:{...}} } }.
    A legacy flat { categories: {...} } blob (pre-client-scoping) is treated
    as `global` and used as the fallback for any client that doesn't have
    its own entry yet, so existing saved choices are never silently reset —
    same normalize-on-read technique as App.jsx's onboarding-map fix."""
    prefs = prefs or {}
    if "categories" in prefs and "global" not in prefs and "clients" not in prefs:
        prefs = {"global": {"categories": prefs.get("categories") or {}}}
    if notification_scope == "agency":
        agency_categories = (prefs.get("agency") or {}).get("categories") or {}
        return agency_categories.get(category) or {}
    global_categories = (prefs.get("global") or {}).get("categories") or {}
    bucket_categories = global_categories
    if client_id:
        client_bucket = (prefs.get("clients") or {}).get(client_id)
        if client_bucket:
            bucket_categories = client_bucket.get("categories") or {}
    return bucket_categories.get(category) or {}


async def create_user_notification(
    user_ids: list[str],
    organization_id: str | None,
    title: str,
    body: str | None = None,
    *,
    event_type: str,
    category: str = "general",
    client_id: str | None = None,
    action_url: str | None = None,
    action_label: str | None = None,
    priority: str = "normal",
    email_eligible: bool = False,
    metadata: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    module: str | None = None,
    required_permission: str | None = None,
    client_name: str | None = None,
    role_by_user: dict[str, str] | None = None,
    notification_scope: str = "client",
    raise_on_insert_failure: bool = False,
) -> bool:
    """The one shared path for creating persistent in-app notifications, and
    the optional email that can go alongside one.

    Only ever called from confirmed backend success points (never from a
    frontend click handler). For each recipient, in-app and email are
    decided independently from that user's notification_prefs, scoped to
    `client_id` when the event belongs to a client (see _category_prefs_for):
      - in-app row is inserted when `.inApp` is true (default true — before
        a user has an explicit preference recorded, everything behaves as
        always-on).
      - email is sent only when BOTH `email_eligible` is true (a property of
        the calling event/category, e.g. integration_auth_expired or
        branding_published — decoupled from `priority`, which is purely
        visual emphasis) AND the user's own `.email` preference is on
        (default false — email is opt-in, unlike in-app).
    Raises only when raise_on_insert_failure is true. Most producers keep the
    best-effort behavior so notification or email problems do not break the
    real action that triggered them.
    """
    if not supabase_admin_configured() or not user_ids:
        return False
    mapped_module, mapped_permission = NOTIFICATION_PERMISSION_MAP.get(event_type, (None, None))
    module = module or mapped_module
    required_permission = required_permission or mapped_permission
    unique_ids = list(dict.fromkeys(uid for uid in user_ids if uid))
    if not unique_ids:
        return False
    if client_id and required_permission:
        filtered: list[str] = []
        for uid in unique_ids:
            role = (role_by_user or {}).get(uid)
            if not role:
                try:
                    from .client_access import get_effective_client_role
                    role = await get_effective_client_role(client_id, uid)
                except Exception:
                    role = None
            if role_has_permission(role, required_permission):
                filtered.append(uid)
        unique_ids = filtered
    if not unique_ids:
        return

    email_targets: list[str] = []
    prefs_by_user: dict[str, dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if client_id:
                prefs_response = await client.get(
                    f"{supabase_rest_url()}/notification_preferences",
                    params={
                        "select": "user_id,preferences",
                        "client_id": f"eq.{client_id}",
                        "user_id": f"in.({','.join(unique_ids)})",
                    },
                    headers=supabase_service_headers(),
                )
                prefs_response.raise_for_status()
                prefs_by_user = {
                    row["user_id"]: {"categories": (row.get("preferences") or {}).get("categories") or {}}
                    for row in prefs_response.json()
                }
                global_response = await client.get(
                    f"{supabase_rest_url()}/memberships",
                    params={
                        "select": "user_id,notification_prefs",
                        "user_id": f"in.({','.join(unique_ids)})",
                    },
                    headers=supabase_service_headers(),
                )
                global_response.raise_for_status()
                for row in global_response.json():
                    uid = row.get("user_id")
                    if uid and uid not in prefs_by_user:
                        prefs_by_user[uid] = row.get("notification_prefs") or {}
            elif organization_id:
                prefs_response = await client.get(
                    f"{supabase_rest_url()}/memberships",
                    params={
                        "select": "user_id,notification_prefs",
                        "organization_id": f"eq.{organization_id}",
                        "user_id": f"in.({','.join(unique_ids)})",
                    },
                    headers=supabase_service_headers(),
                )
                prefs_response.raise_for_status()
                prefs_by_user = {
                    row["user_id"]: (row.get("notification_prefs") or {})
                    for row in prefs_response.json()
                }

    except Exception:
        # Preference storage must not prevent the persistent in-app event.
        # Missing preferences use the documented defaults: in-app on, email
        # opt-in. The insert below is intentionally attempted independently.
        logger.exception("notification preference lookup failed for event_type=%s", event_type)

    rows = []
    for uid in unique_ids:
        category_prefs = _category_prefs_for(
            prefs_by_user.get(uid),
            client_id,
            category,
            notification_scope,
        )
        in_app_enabled = category_prefs.get("inApp", True)
        email_enabled = bool(category_prefs.get("email", False))
        logger.info(
            "Notification preference evaluation: event=%s scope=%s user_id=%s category=%s prefs=%s in_app=%s email=%s",
            event_type,
            notification_scope,
            uid,
            category,
            category_prefs,
            in_app_enabled,
            email_enabled,
        )

        if in_app_enabled:
            rows.append({
                "user_id": uid,
                "organization_id": organization_id,
                "client_id": client_id,
                "scope": notification_scope,
                "category": category,
                "event_type": event_type,
                "title": title,
                "body": body,
                "link": action_url,
                "action_label": action_label,
                "priority": priority,
                "module": module,
                "required_permission": required_permission,
                "metadata": {
                    **(metadata or {}),
                    "workspace_scope": notification_scope,
                    **({"module": module} if module else {}),
                    **({"required_permission": required_permission} if required_permission else {}),
                },
                "dedupe_key": dedupe_key,
            })
        if email_eligible and email_enabled:
            email_targets.append(uid)

    inserted = not rows
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if rows:
                params: dict[str, str] = {}
                prefer = "return=minimal"
                if dedupe_key:
                    params["on_conflict"] = "user_id,organization_id,dedupe_key"
                    prefer = "return=minimal,resolution=ignore-duplicates"

                response = await client.post(
                    f"{supabase_rest_url()}/user_notifications",
                    params=params,
                    headers=supabase_service_headers({"Prefer": prefer}),
                    json=rows,
                )
                logger.info(
                    "Supabase notification POST: url=%s status=%s body=%s payload=%s",
                    f"{supabase_rest_url()}/user_notifications",
                    response.status_code,
                    response.text,
                    rows,
                )
                if response.status_code >= 400 and _is_missing_scope_column(response) and not raise_on_insert_failure:
                    # Older PostgREST instances can lag behind a direct
                    # schema migration. Metadata remains the compatibility
                    # source of truth, so retry without the optional column.
                    legacy_rows = [{key: value for key, value in row.items() if key != "scope"} for row in rows]
                    response = await client.post(
                        f"{supabase_rest_url()}/user_notifications",
                        params=params,
                        headers=supabase_service_headers({"Prefer": prefer}),
                        json=legacy_rows,
                    )
                    logger.warning(
                        "Supabase notification POST legacy retry: status=%s body=%s",
                        response.status_code,
                        response.text,
                    )
                if response.status_code >= 400:
                    logger.error(
                        "Supabase notification POST failed: url=%s status=%s body=%s payload=%s",
                        f"{supabase_rest_url()}/user_notifications",
                        response.status_code,
                        response.text,
                        rows,
                    )
                response.raise_for_status()
                inserted = True
    except Exception:
        logger.exception(
            "create_user_notification failed for event_type=%s user_ids=%s organization_id=%s payload=%s",
            event_type,
            unique_ids,
            organization_id,
            rows,
        )
        if raise_on_insert_failure:
            raise

    logger.info(
        "Notification email evaluation: event=%s client_id=%s eligible=%s configured=%s targets=%s",
        event_type,
        client_id,
        email_eligible,
        email_configured(),
        len(email_targets),
    )
    if not email_targets or not email_configured():
        return inserted
    for uid in email_targets:
        try:
            to_email = await get_user_email(uid)
            logger.info(
                "Notification email target resolved: event=%s user_id=%s email=%s preference=%s",
                event_type,
                uid,
                to_email,
                _category_prefs_for(prefs_by_user.get(uid), client_id, category, notification_scope),
            )
            if not to_email:
                logger.warning("Notification email skipped: no email for user_id=%s event_type=%s", uid, event_type)
                continue
            role_label = ((role_by_user or {}).get(uid) or "").capitalize()
            context_lines = []
            if client_name:
                context_lines.append(f"Workspace: {client_name}")
            if role_label:
                context_lines.append(f"You are an {role_label} in this workspace." if role_label[0] in "AEIOU" else f"You are a {role_label} in this workspace.")
            context_lines.append(body or title)
            sent = await send_notification_email(to_email, title, "\n\n".join(context_lines), action_url, action_label)
            logger.info(
                "Notification email result: event=%s user_id=%s sent=%s",
                event_type,
                uid,
                sent,
            )
        except Exception:
            logger.exception("notification email delivery failed for event_type=%s", event_type)
    return inserted


def _is_missing_scope_column(response: httpx.Response) -> bool:
    """Allow old PostgREST schema caches to serve legacy rows only."""
    body = response.text.lower()
    return response.status_code in {400, 404} and (
        "could not find the 'scope' column" in body
        or "column user_notifications.scope does not exist" in body
        or "schema cache" in body and "scope" in body
    )


async def create_client_notification(
    client_id: str,
    title: str,
    body: str | None = None,
    *,
    event_type: str,
    category: str,
    action_url: str | None = None,
    action_label: str | None = None,
    priority: str = "normal",
    email_eligible: bool = False,
    metadata: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> None:
    workspace = await get_client_workspace(client_id)
    if not workspace:
        return
    recipients, module, required_permission, role_by_user = await notification_recipients_for_client(client_id, event_type)
    if action_url and "client=" not in action_url:
        separator = "&" if "?" in action_url else "?"
        action_url = f"{action_url}{separator}client={client_id}"
    await create_user_notification(
        recipients,
        workspace.get("organization_id"),
        title,
        body,
        event_type=event_type,
        category=category,
        client_id=client_id,
        action_url=action_url,
        action_label=action_label,
        priority=priority,
        email_eligible=email_eligible,
        metadata=metadata,
        dedupe_key=dedupe_key,
        module=module,
        required_permission=required_permission,
        client_name=workspace.get("name") or "this workspace",
        role_by_user=role_by_user,
        notification_scope="client",
    )


async def get_connector_token(client_id: str, source_type: str) -> dict[str, Any] | None:
    if not supabase_admin_configured():
        return None

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{supabase_rest_url()}/connector_oauth_tokens",
            params={
                "select": "token_payload,oauth_client_id,expires_at,scope",
                "client_id": f"eq.{client_id}",
                "source_type": f"eq.{source_type}",
                "limit": "1",
            },
            headers=supabase_service_headers(),
        )
        response.raise_for_status()
        rows = response.json()
    if not rows:
        return None

    row = rows[0]
    token = dict(row.get("token_payload") or {})
    if row.get("oauth_client_id"):
        token["oauth_client_id"] = row["oauth_client_id"]
    if row.get("expires_at"):
        token["expires_at"] = row["expires_at"]
    if row.get("scope"):
        token["scope"] = row["scope"]
    return token


async def upsert_connector_token(
    client_id: str,
    source_type: str,
    token: dict[str, Any],
    user_id: str | None = None,
) -> None:
    if not supabase_admin_configured():
        return

    payload = {
        "client_id": client_id,
        "source_type": source_type,
        "token_payload": token,
        "oauth_client_id": token.get("oauth_client_id"),
        "expires_at": token.get("expires_at"),
        "scope": token.get("scope"),
        "last_connected_by": user_id,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{supabase_rest_url()}/connector_oauth_tokens",
            params={"on_conflict": "client_id,source_type"},
            headers=supabase_service_headers({"Prefer": "resolution=merge-duplicates"}),
            json=payload,
        )
        response.raise_for_status()


async def upsert_data_source_connected(
    client_id: str,
    source_type: str,
    user_id: str | None = None,
) -> None:
    if not supabase_admin_configured():
        return

    payload = {
        "client_id": client_id,
        "type": source_type,
        "status": "connected",
        "config": {},
        "last_sync_at": datetime.now(timezone.utc).isoformat(),
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{supabase_rest_url()}/data_sources",
            params={"on_conflict": "client_id,type"},
            headers=supabase_service_headers({"Prefer": "resolution=merge-duplicates"}),
            json=payload,
        )
        response.raise_for_status()

    if not user_id:
        return
    workspace = await get_client_workspace(client_id)
    if not workspace:
        return
    is_ai = source_type in AI_PROVIDER_SOURCE_TYPES
    name = source_display_name(source_type)
    client_name = workspace.get("name") or "this workspace"
    await create_client_notification(
        client_id,
        f"{name} connected",
        (f"{name} is ready for AI analysis on {client_name}." if is_ai
         else f"{name} is now connected to {client_name}."),
        event_type="llm_provider_connected" if is_ai else "integration_connected",
        category="integrations",
        action_url="/integrations",
        action_label="View Integrations",
        email_eligible=True,
    )


async def delete_connector_token(client_id: str, source_type: str) -> None:
    if not supabase_admin_configured():
        return

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.delete(
            f"{supabase_rest_url()}/connector_oauth_tokens",
            params={
                "client_id": f"eq.{client_id}",
                "source_type": f"eq.{source_type}",
            },
            headers=supabase_service_headers(),
        )
        response.raise_for_status()


async def mark_data_source_disconnected(
    client_id: str,
    source_type: str,
    user_id: str | None = None,
) -> None:
    if not supabase_admin_configured():
        return

    payload = {
        "status": "disabled",
        "last_sync_at": None,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.patch(
            f"{supabase_rest_url()}/data_sources",
            params={
                "client_id": f"eq.{client_id}",
                "type": f"eq.{source_type}",
            },
            headers=supabase_service_headers({"Prefer": "return=minimal"}),
            json=payload,
        )
        response.raise_for_status()

    if not user_id:
        return
    workspace = await get_client_workspace(client_id)
    if not workspace:
        return
    is_ai = source_type in AI_PROVIDER_SOURCE_TYPES
    name = source_display_name(source_type)
    client_name = workspace.get("name") or "this workspace"
    await create_client_notification(
        client_id,
        f"{name} disconnected",
        f"{name} was disconnected from {client_name}.",
        event_type="llm_provider_disconnected" if is_ai else "integration_disconnected",
        category="integrations",
        action_url="/integrations",
        action_label="View Integrations",
        email_eligible=True,
    )


def _branding_publish_dedupe_key(scope: str, entity_id: str, publish_key: str | None) -> str:
    clean_key = str(publish_key or "").strip()
    if not clean_key:
        clean_key = datetime.now(timezone.utc).isoformat()
    return f"branding_published:{scope}:{entity_id}:{clean_key[:120]}"


async def notify_branding_published(user_id: str, organization_id: str, client_id: str | None, publish_key: str | None = None) -> dict[str, Any]:
    """Replaces the old blind DB trigger (dropped in
    20260919_001_client_scoped_notifications.sql): the frontend already
    knows exactly which entity it just saved (the agency, or a specific
    client's override), so it calls this right after a confirmed save
    instead of a trigger having to guess from a raw JSONB diff. Routes
    through create_user_notification for correct client attribution,
    preference checking, dedupe, and email — none of which the trigger
    could do.
    """
    if client_id:
        workspace = await get_client_workspace(client_id)
        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        actor_role = await get_client_actor_role(client_id, user_id)
        if not role_has_permission(actor_role, "branding.edit"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to update branding for this client",
            )
        client_name = workspace.get("name") or "this client"
        title = f"{client_name}'s branding published"
        body = f"{client_name}'s branding changes are now live."
        action_url = f"/branding?client={client_id}"
        # The save endpoint is already idempotent at the branding write. Each
        # confirmed publish is a distinct user action and must be able to
        # produce its own notification/email, including repeated publishes.
        dedupe_key = _branding_publish_dedupe_key("client", client_id, publish_key)
        await create_client_notification(
            client_id,
            title,
            body,
            event_type="branding_published",
            category="branding",
            action_url=action_url,
            action_label="View Branding",
            email_eligible=True,
            dedupe_key=dedupe_key,
        )
        return {"scope": "client", "inserted": True, "dedupe_key": dedupe_key}

    membership = await get_membership(user_id, organization_id)
    logger.info(
        "Agency branding notification context: authenticated_user_id=%s organization_id=%s membership=%s",
        user_id,
        organization_id,
        membership,
    )
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this organization")
    title = "Agency branding published"
    body = "Your agency branding changes are now live."
    action_url = "/agency-branding"
    dedupe_key = _branding_publish_dedupe_key("agency", organization_id, publish_key)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{supabase_rest_url()}/memberships",
            params={
                "select": "user_id,role,notification_prefs",
                "organization_id": f"eq.{organization_id}",
            },
            headers=supabase_service_headers(),
        )
        response.raise_for_status()
        candidates = response.json()
    owner = next((row for row in candidates if row.get("role") == "owner"), None)
    if not owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization owner not found")
    role_by_user = {owner["user_id"]: "owner"}
    try:
        owner_email = await get_user_email(owner["user_id"])
    except Exception:
        owner_email = None
        logger.exception("Agency branding owner email lookup failed: user_id=%s", owner["user_id"])
    logger.info(
        "Agency branding notification owner resolved: authenticated_user_id=%s organization_id=%s organization_owner_id=%s owner_email=%s agency_notification_preferences=%s",
        user_id,
        organization_id,
        owner["user_id"],
        owner_email,
        ((owner.get("notification_prefs") or {}).get("agency") or {}),
    )
    logger.info(
        "Agency branding generated notification payload: organization_id=%s user_id=%s client_id=%s scope=%s metadata=%s category=%s event_type=%s dedupe_key=%s",
        organization_id,
        owner["user_id"],
        None,
        "agency",
        {"workspace_scope": "agency", "module": "branding", "required_permission": "branding.view"},
        "branding",
        "branding_published",
        dedupe_key,
    )
    inserted = await create_user_notification(
        list(role_by_user),
        organization_id,
        title,
        body,
        event_type="branding_published",
        category="branding",
        client_id=None,
        action_url=action_url,
        action_label="View Agency Branding",
        email_eligible=True,
        dedupe_key=dedupe_key,
        role_by_user=role_by_user,
        notification_scope="agency",
        raise_on_insert_failure=True,
    )
    if not inserted:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Agency notification was not inserted",
        )
    return {"scope": "agency", "inserted": True, "owner_id": owner["user_id"], "owner_email": owner_email, "dedupe_key": dedupe_key}


async def notify_role_changed(actor_user_id: str, organization_id: str, target_user_id: str, role: str) -> None:
    """Replaces the old memberships_notify_role_changed DB trigger, for the
    same reason notify_branding_published replaced the branding trigger:
    routing through create_user_notification is what makes email actually
    possible (a trigger can only ever INSERT a row - it can't call Resend).
    Notifies the member whose role changed, not the admin who changed it.
    """
    actor_membership = await get_membership(actor_user_id, organization_id)
    if not actor_membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this organization")
    target_membership = await get_membership(target_user_id, organization_id)
    if not target_membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found in this organization")

    await create_user_notification(
        [target_user_id],
        organization_id,
        "Your role was changed",
        f"Your role is now {role}.",
        event_type="membership_role_changed",
        category="account_security",
        client_id=None,
        action_url="/settings",
        action_label="View Settings",
        email_eligible=True,
    )


ACCOUNT_SECURITY_EVENTS = {
    "password_changed": ("Password changed", "Your Braidscope password was changed."),
    "mfa_enabled": ("Two-factor authentication enabled", "Two-factor authentication is now enabled for your account."),
    "mfa_disabled": ("Two-factor authentication disabled", "Two-factor authentication was disabled for your account."),
    "new_session_created": ("New session created", "A new browser session was created for your account."),
    "sessions_revoked": ("Other sessions revoked", "All other active sessions were signed out."),
    "profile_updated": ("Profile updated", "Your account profile was updated."),
    "security_settings_changed": ("Security settings changed", "Your account security settings were updated."),
}


async def notify_account_security(
    user_id: str,
    organization_id: str | None,
    event_type: str,
    *,
    body: str | None = None,
    dedupe_key: str | None = None,
) -> None:
    title, default_body = ACCOUNT_SECURITY_EVENTS.get(event_type, ACCOUNT_SECURITY_EVENTS["security_settings_changed"])
    await create_user_notification(
        [user_id],
        organization_id,
        title,
        body or default_body,
        event_type=event_type,
        category="account_security",
        client_id=None,
        action_url="/settings",
        action_label="Review security",
        email_eligible=True,
        dedupe_key=dedupe_key,
        notification_scope="agency",
    )


async def check_client_reminders(user_id: str, organization_id: str, client_id: str) -> None:
    if client_id.startswith("demo-"):
        return
    """Minimal, no-scheduler reminders: checked opportunistically (e.g. when
    a client's Integrations/Branding data loads), not on a cron. Each
    reminder uses a STABLE per-client dedupe_key, so the DB's own unique
    index guarantees it is only ever inserted once — this is what satisfies
    "never repeat the same reminder every login" and "resolved-state
    suppression" without any scheduling infrastructure: once fired, a
    reminder never fires again for that client even if the condition
    recurs, which is the correct conservative behavior until real periodic
    re-reminding is built.
    """
    membership = await get_membership(user_id, organization_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this organization")
    workspace = await get_client_workspace(client_id)
    if not workspace or workspace.get("organization_id") != organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client does not belong to this organization")
    client_name = workspace.get("name") or "this client"

    async with httpx.AsyncClient(timeout=10) as client:
        sources_response = await client.get(
            f"{supabase_rest_url()}/data_sources",
            params={"select": "id", "client_id": f"eq.{client_id}", "status": "eq.connected", "limit": "1"},
            headers=supabase_service_headers(),
        )
        sources_response.raise_for_status()
        has_connected_source = bool(sources_response.json())

        # Client branding overrides live on the client row itself
        # (clients.branding_override), not inside the agency's
        # organizations.branding_config.overrides blob - that path was
        # retired by 20260920_001_client_branding_ownership.sql (the old
        # column is left in place but is dead/stale going forward). Reading
        # the old location here always found `enabled: false` for any client
        # that customized its branding after that migration, producing a
        # permanent, incorrect "still using agency default branding"
        # reminder. See BRANDING_ARCHITECTURE_IMPLEMENTATION.md analysis.
        client_response = await client.get(
            f"{supabase_rest_url()}/clients",
            params={"select": "branding_override", "id": f"eq.{client_id}", "limit": "1"},
            headers=supabase_service_headers(),
        )
        client_response.raise_for_status()
        client_rows = client_response.json()

    branding_override = (client_rows[0].get("branding_override") if client_rows else None) or {}
    has_custom_branding = bool(branding_override.get("enabled"))

    if not has_connected_source:
        await create_user_notification(
            [user_id],
            organization_id,
            "Connect your first data source",
            f"{client_name} doesn't have any connected data sources yet.",
            event_type="reminder_no_data_sources",
            category="reminders",
            client_id=client_id,
            action_url="/integrations",
            action_label="Connect a source",
            email_eligible=True,
            dedupe_key=f"reminder_no_data_sources:{client_id}",
        )
    if not has_custom_branding:
        await create_user_notification(
            [user_id],
            organization_id,
            "Complete your client branding",
            f"{client_name} is still using your agency's default branding.",
            event_type="reminder_branding_incomplete",
            category="reminders",
            client_id=client_id,
            action_url="/branding",
            action_label="Customize branding",
            email_eligible=True,
            dedupe_key=f"reminder_branding_incomplete:{client_id}",
        )
