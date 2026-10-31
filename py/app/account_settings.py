# SPDX-License-Identifier: AGPL-3.0-only
"""Server-side account settings operations."""
from datetime import datetime, timezone
import os
from typing import Any
import httpx
from fastapi import HTTPException, status
from .core import logger
from .supabase_admin import get_membership, notify_account_security, role_has_permission, supabase_admin_configured, supabase_rest_url, supabase_service_headers
from .client_access import assert_client_role, get_effective_client_role

def _auth_url():
    base = os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL")
    if not base: raise RuntimeError("SUPABASE_URL is not configured")
    return f"{base.rstrip('/')}/auth/v1"

async def _rows(table: str, params: dict[str, str]) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{supabase_rest_url()}/{table}", params=params, headers=supabase_service_headers())
        response.raise_for_status()
        return response.json()

async def assert_org_admin(organization_id: str, user_id: str):
    if not supabase_admin_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Account settings service is not configured")
    rows = await _rows("memberships", {"select":"role", "organization_id":f"eq.{organization_id}", "user_id":f"eq.{user_id}", "limit":"1"})
    if not rows or rows[0].get("role") not in {"owner", "admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only workspace owners and admins can manage the team")

async def invite_member(organization_id: str, email: str, role: str, invited_by: str):
    if role not in {"admin", "editor", "viewer"}:
        raise HTTPException(status_code=422, detail="Invalid workspace role")
    await assert_org_admin(organization_id, invited_by)
    clean_email = email.lower().strip()
    if not clean_email:
        raise HTTPException(status_code=422, detail="Email is required")

    # Existing accounts cannot receive Supabase's invite email again. Add them
    # directly to the workspace and notify them in-app instead.
    async with httpx.AsyncClient(timeout=20) as client:
        users_response = await client.get(f"{_auth_url()}/admin/users", params={"page": 1, "per_page": 1000}, headers=supabase_service_headers())
        users_response.raise_for_status()
        users_payload = users_response.json()
        users = users_payload.get("users", users_payload if isinstance(users_payload, list) else [])
        existing = next((user for user in users if (user.get("email") or "").lower() == clean_email), None)

        if existing:
            user_id = existing.get("id")
            membership = await _rows("memberships", {"select":"user_id", "organization_id":f"eq.{organization_id}", "user_id":f"eq.{user_id}", "limit":"1"})
            if membership:
                raise HTTPException(status_code=409, detail="This user is already a member of the workspace")
            response = await client.post(
                f"{supabase_rest_url()}/memberships",
                json={"organization_id": organization_id, "user_id": user_id, "role": role},
                headers=supabase_service_headers({"Prefer": "return=minimal"}),
            )
            response.raise_for_status()
            response = await client.post(
                f"{supabase_rest_url()}/user_notifications",
                json={"user_id": user_id, "organization_id": organization_id, "event_type": "team_invite", "title": "You joined a workspace", "body": f"You were added to this workspace as {role}.", "link": "/settings"},
                headers=supabase_service_headers({"Prefer": "return=minimal"}),
            )
            response.raise_for_status()
            return {"ok": True, "email": clean_email, "role": role, "existing_user": True}

    web_url = (os.getenv("WEB_APP_URL") or os.getenv("APP_PUBLIC_URL") or "").rstrip("/")
    payload = {"email": clean_email, "data": {"invited_organization_id": organization_id, "invited_role": role}}
    if web_url:
        payload["redirect_to"] = f"{web_url}/auth/invite"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{_auth_url()}/invite", json=payload, headers=supabase_service_headers())
    if not response.is_success:
        try:
            detail = response.json().get("msg") or response.json().get("message")
        except ValueError:
            detail = response.text
        raise HTTPException(status_code=400, detail=detail or "Could not send invitation")
    user = response.json()
    user_id = user.get("id") or user.get("user", {}).get("id")
    if not user_id:
        raise HTTPException(status_code=502, detail="Invitation service did not return a user")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(f"{supabase_rest_url()}/memberships", params={"on_conflict":"organization_id,user_id"}, json={"organization_id":organization_id,"user_id":user_id,"role":role}, headers=supabase_service_headers({"Prefer":"resolution=merge-duplicates"}))
        response.raise_for_status()
        response = await client.post(f"{supabase_rest_url()}/workspace_invitations", params={"on_conflict":"organization_id,email"}, json={"organization_id":organization_id,"email":clean_email,"role":role,"invited_by":invited_by,"invited_user_id":user_id,"status":"pending"}, headers=supabase_service_headers({"Prefer":"resolution=merge-duplicates"}))
        response.raise_for_status()
    return {"ok": True, "email": clean_email, "role": role, "existing_user": False}
async def remove_member(organization_id: str, user_id: str, removed_by: str):
    await assert_org_admin(organization_id, removed_by)
    if user_id == removed_by:
        raise HTTPException(status_code=422, detail="You cannot remove yourself from the workspace")

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.delete(
            f"{supabase_rest_url()}/memberships",
            params={"organization_id": f"eq.{organization_id}", "user_id": f"eq.{user_id}"},
            headers=supabase_service_headers({"Prefer": "return=minimal"}),
        )
        response.raise_for_status()
        for table in ("workspace_invitations", "user_notifications"):
            params = {"organization_id": f"eq.{organization_id}"}
            params["invited_user_id" if table == "workspace_invitations" else "user_id"] = f"eq.{user_id}"
            response = await client.delete(f"{supabase_rest_url()}/{table}", params=params, headers=supabase_service_headers({"Prefer": "return=minimal"}))
            if response.status_code not in {200, 204}:
                response.raise_for_status()

        remaining = await _rows("memberships", {"select":"organization_id", "user_id":f"eq.{user_id}", "limit":"1"})
        account_deleted = False
        if not remaining:
            response = await client.delete(f"{_auth_url()}/admin/users/{user_id}", headers=supabase_service_headers())
            response.raise_for_status()
            account_deleted = True

    return {"ok": True, "account_deleted": account_deleted}
async def record_session_activity(user_id, session_id, organization_id, user_agent, ip_address, client_session_id=None):
    if not supabase_admin_configured():
        return {"recorded": False, "reason": "Supabase admin service is not configured"}
    stable_session_id = client_session_id or session_id
    if not stable_session_id:
        return {"recorded": False, "reason": "Authenticated token did not contain a session id"}
    existing = await _rows("user_session_activity",{"select":"id","user_id":f"eq.{user_id}","session_id":f"eq.{stable_session_id}","limit":"1"})
    payload={"user_id":user_id,"session_id":stable_session_id,"organization_id":organization_id,"user_agent":user_agent[:500],"ip_address":ip_address,"last_seen_at":datetime.now(timezone.utc).isoformat()}
    async with httpx.AsyncClient(timeout=15) as client:
        response=await client.post(f"{supabase_rest_url()}/user_session_activity",params={"on_conflict":"user_id,session_id"},json=payload,headers=supabase_service_headers({"Prefer":"resolution=merge-duplicates"}))
        response.raise_for_status()
    if not existing:
        await notify_account_security(user_id, organization_id, "new_session_created", body=f"New sign-in activity was recorded from {user_agent[:120] or 'an unknown device'}.", dedupe_key=f"new_session_created:{stable_session_id}")
    return {"recorded": True}

async def recent_sessions(user_id, session_id, client_session_id=None):
    if not supabase_admin_configured(): return []
    rows=await _rows("user_session_activity",{"select":"id,session_id,user_agent,ip_address,last_seen_at,created_at","user_id":f"eq.{user_id}","order":"last_seen_at.desc","limit":"12"})
    current_session_id = client_session_id or session_id
    return [{**row,"current":bool(current_session_id and row.get("session_id")==current_session_id)} for row in rows]

async def _filter_notifications_for_current_access(
    user_id: str,
    rows: list[dict[str, Any]],
    known_roles: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    allowed: list[dict[str, Any]] = []
    # known_roles: roles the caller has ALREADY resolved for this same user in
    # this same request (the route's own access check). They are used instead
    # of looking the identical role up a second time; any client not listed
    # is looked up exactly as before. Never stored beyond this call.
    role_cache: dict[str, str | None] = dict(known_roles or {})
    for row in rows:
        row_client_id = row.get("client_id")
        required_permission = row.get("required_permission")
        if not row_client_id or not required_permission:
            allowed.append(row)
            continue
        if row_client_id not in role_cache:
            role_cache[row_client_id] = await get_effective_client_role(row_client_id, user_id)
        if role_has_permission(role_cache[row_client_id], required_permission):
            allowed.append(row)
    return allowed

async def list_notifications(
    user_id,
    organization_id=None,
    client_id=None,
    category=None,
    unread_only=False,
    before=None,
    limit=25,
    scope="client",
    known_roles=None,
):
    if not supabase_admin_configured(): return []
    safe_limit = max(1, min(int(limit or 25), 100))
    # Scope column is authoritative. Metadata is only a compatibility fallback
    # for older databases where selecting/filtering the scope column fails.
    candidate_limit = max(safe_limit, 1000)
    params={
        "select":"id,title,body,link,action_label,category,priority,event_type,module,required_permission,client_id,scope,metadata,created_at,read_at",
        "user_id":f"eq.{user_id}",
        "order":"created_at.desc",
        "limit":str(candidate_limit),
    }
    # Client notifications belong to the client's owning organization, which
    # can differ from the viewer's organization for shared-client users.
    # The route already verifies client access, so an additional viewer-org
    # filter would hide valid notifications from shared admins/editors.
    if organization_id:
        params["organization_id"] = f"eq.{organization_id}"
    params["scope"] = f"eq.{scope}"
    if scope == "agency":
        params["client_id"] = "is.null"
        params["category"] = "in.(branding,account_security)"
    elif not client_id:
        params["client_id"] = "is.null"
    # Active client's own rows PLUS legitimately global rows (client_id is
    # null - e.g. account_security events) - never a strict equality, or
    # global notifications would disappear the moment any client is selected.
    if client_id:
        params["or"] = f"(client_id.eq.{client_id},client_id.is.null)"
    if category and category != "all": params["category"]=f"eq.{category}"
    if unread_only: params["read_at"]="is.null"
    if before: params["created_at"]=f"lt.{before}"
    try:
        rows = await _rows("user_notifications", params)
    except httpx.HTTPStatusError as error:
        if "scope" not in error.response.text.lower():
            raise
        logger.warning("Notification scope column unavailable; using legacy metadata filtering: %s", error.response.text)
        params["select"] = params["select"].replace(",scope", "")
        params.pop("scope", None)
        rows = await _rows("user_notifications", params)
    rows = _filter_notification_scope(rows, scope)
    rows = await _filter_notifications_for_current_access(user_id, rows, known_roles)
    return rows[:safe_limit]

async def count_unread_notifications(user_id, organization_id=None, client_id=None, scope="client"):
    if not supabase_admin_configured():
        return 0
    params = {"select": "id,scope,metadata", "user_id": f"eq.{user_id}", "read_at": "is.null", "scope": f"eq.{scope}"}
    if organization_id:
        params["organization_id"] = f"eq.{organization_id}"
    if scope == "agency":
        params["client_id"] = "is.null"
        params["category"] = "in.(branding,account_security)"
    elif client_id:
        params["or"] = f"(client_id.eq.{client_id},client_id.is.null)"
    else:
        params["client_id"] = "is.null"
    try:
        rows = await _rows("user_notifications", params)
    except httpx.HTTPStatusError as error:
        if "scope" not in error.response.text.lower():
            raise
        logger.warning("Notification scope column unavailable for unread count; using legacy metadata filtering: %s", error.response.text)
        params["select"] = "id,metadata"
        params.pop("scope", None)
        rows = await _rows("user_notifications", params)
    rows = _filter_notification_scope(rows, scope)
    return len(rows)


def _filter_notification_scope(rows: list[dict[str, Any]], requested_scope: str) -> list[dict[str, Any]]:
    """Use the scope column as authority; metadata is legacy-only."""
    scoped_rows = [row for row in rows if row.get("scope") is not None]
    if scoped_rows:
        return [row for row in rows if row.get("scope") == requested_scope]
    return [
        row for row in rows
        if (
            (row.get("metadata") or {}).get("workspace_scope") == "agency"
            if requested_scope == "agency"
            else (row.get("metadata") or {}).get("workspace_scope") != "agency"
        )
    ]

async def mark_notifications_read(user_id, ids=None):
    if not supabase_admin_configured(): return
    params={"user_id":f"eq.{user_id}","read_at":"is.null"}
    if ids: params["id"]=f"in.({','.join(ids)})"
    async with httpx.AsyncClient(timeout=15) as client:
        response=await client.patch(f"{supabase_rest_url()}/user_notifications",params=params,json={"read_at":datetime.now(timezone.utc).isoformat()},headers=supabase_service_headers())
        response.raise_for_status()

async def create_account_security_notification(user_id: str, organization_id: str | None, event_type: str):
    if organization_id and not await get_membership(user_id, organization_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this organization")
    await notify_account_security(user_id, organization_id, event_type)
    return {"ok": True}

def _default_agency_notification_prefs():
    return {
        "categories": {
            "branding": {"inApp": True, "email": False},
            "account_security": {"inApp": True, "email": False},
        }
    }

async def get_agency_notification_preferences(organization_id: str, user_id: str):
    membership = await get_membership(user_id, organization_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this organization")
    rows = await _rows(
        "memberships",
        {
            "select": "notification_prefs",
            "organization_id": f"eq.{organization_id}",
            "user_id": f"eq.{user_id}",
            "limit": "1",
        },
    )
    saved = ((rows[0].get("notification_prefs") if rows else None) or {}).get("agency") or {}
    prefs = _default_agency_notification_prefs()
    for category, values in (saved.get("categories") or {}).items():
        if category in prefs["categories"] and isinstance(values, dict):
            prefs["categories"][category] = {
                "inApp": values.get("inApp", True) is not False,
                "email": bool(values.get("email", False)),
            }
    return {"prefs": prefs, "can_edit": membership.get("role") == "owner"}

async def save_agency_notification_preferences(organization_id: str, user_id: str, prefs: dict):
    membership = await get_membership(user_id, organization_id)
    if not membership or membership.get("role") != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the organization owner can update agency notification preferences")
    normalized = _default_agency_notification_prefs()
    for category in normalized["categories"]:
        values = (prefs.get("categories") or {}).get(category) or {}
        normalized["categories"][category] = {
            "inApp": values.get("inApp", True) is not False,
            "email": bool(values.get("email", False)),
        }
    rows = await _rows(
        "memberships",
        {"select": "notification_prefs", "organization_id": f"eq.{organization_id}", "user_id": f"eq.{user_id}", "limit": "1"},
    )
    current = (rows[0].get("notification_prefs") if rows else None) or {}
    current["agency"] = normalized
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.patch(
            f"{supabase_rest_url()}/memberships",
            params={"organization_id": f"eq.{organization_id}", "user_id": f"eq.{user_id}"},
            json={"notification_prefs": current},
            headers=supabase_service_headers({"Prefer": "return=minimal"}),
        )
        response.raise_for_status()
    return {"ok": True, "prefs": normalized, "can_edit": True}

def _default_notification_prefs():
    categories = {}
    for category in ("integrations", "branding", "reminders", "account_security", "reports"):
        categories[category] = {"inApp": True, "email": False}
    categories["account_security"]["inApp"] = True
    return {"categories": categories}

def _merge_notification_prefs(saved):
    base = _default_notification_prefs()
    saved_categories = (saved or {}).get("categories") or {}
    for key, value in saved_categories.items():
        if key not in base["categories"] or not isinstance(value, dict):
            continue
        base["categories"][key] = {
            "inApp": True if key == "account_security" else value.get("inApp", True) is not False,
            "email": bool(value.get("email", False)),
        }
    return base

async def get_client_notification_preferences(client_id: str, user_id: str):
    await assert_client_role(client_id, user_id, "viewer")
    rows = await _rows(
        "notification_preferences",
        {
            "select": "preferences",
            "client_id": f"eq.{client_id}",
            "user_id": f"eq.{user_id}",
            "limit": "1",
        },
    )
    role = await assert_client_role(client_id, user_id, "viewer")
    return {
        "prefs": _merge_notification_prefs(rows[0].get("preferences") if rows else None),
        "can_edit": role in {"owner", "admin"},
        "role": role,
    }

async def save_client_notification_preferences(client_id: str, user_id: str, prefs: dict):
    role = await assert_client_role(client_id, user_id, "admin")
    if role not in {"owner", "admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have permission to update notification preferences for this client.")
    normalized = _merge_notification_prefs(prefs)
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{supabase_rest_url()}/notification_preferences",
            params={"on_conflict": "client_id,user_id"},
            json={
                "client_id": client_id,
                "user_id": user_id,
                "preferences": normalized,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            headers=supabase_service_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
        )
        response.raise_for_status()
    return {"ok": True, "prefs": normalized, "can_edit": True}
