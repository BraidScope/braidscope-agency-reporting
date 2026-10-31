# SPDX-License-Identifier: AGPL-3.0-only
"""Shared Client Workspace access: per-client roles independent of org
membership. See TEAM_ACCESS_ARCHITECTURE_PLAN.md for the full design.

effective_client_role/has_client_access are real Postgres functions (the
migration 20260919_003_client_sharing.sql) - this module is a thin Python
mirror of that SAME resolution logic for backend endpoints that use the
service role (and therefore bypass RLS), so there is exactly one place the
"who can access this client, and at what role" rule is defined conceptually,
even though it's expressed twice (SQL for RLS, Python for endpoints that
can't rely on RLS alone).
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Request, status

from .auth import verify_bearer_token
from .core import logger
from .schemas import AuthUser
from .supabase_admin import (
    find_user_id_by_email,
    get_user_avatar_url,
    get_client_workspace,
    get_user_email,
    supabase_admin_configured,
    supabase_rest_url,
    supabase_service_headers,
)
from .email_service import email_configured, send_client_invitation_email

_ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
ASSIGNABLE_CLIENT_ROLES = {"owner", "admin", "editor", "viewer"}


def role_meets_bar(role: str | None, min_role: str) -> bool:
    if not role:
        return False
    return _ROLE_RANK.get(role, -1) >= _ROLE_RANK.get(min_role, 0)


async def _rows(table: str, params: dict[str, str]) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{supabase_rest_url()}/{table}", params=params, headers=supabase_service_headers())
        response.raise_for_status()
        return response.json()


async def get_effective_client_role(client_id: str, user_id: str) -> str | None:
    if not supabase_admin_configured() or not client_id or not user_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{supabase_rest_url()}/rpc/effective_client_role",
                json={"p_client_id": client_id, "p_user_id": user_id},
                headers=supabase_service_headers(),
            )
            response.raise_for_status()
            role = response.json()
        return role or None
    except httpx.HTTPStatusError as exc:
        # Keep client authorization and notification visibility working during
        # deployments where the database function has not reached PostgREST's
        # schema cache yet. Only fall back for a missing RPC; other database
        # errors must remain visible to the caller.
        if exc.response.status_code != 404:
            raise
        workspace = await get_client_workspace(client_id)
        if not workspace:
            return None
        owner_rows = await _rows(
            "memberships",
            {
                "select": "role",
                "organization_id": f"eq.{workspace['organization_id']}",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )
        if owner_rows:
            return owner_rows[0].get("role")
        shared_rows = await _rows(
            "client_memberships",
            {
                "select": "role",
                "client_id": f"eq.{client_id}",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )
        return shared_rows[0].get("role") if shared_rows else None


async def assert_client_role(client_id: str, user_id: str, min_role: str = "viewer") -> str:
    role = await get_effective_client_role(client_id, user_id)
    if not role_meets_bar(role, min_role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have permission to do this on this client")
    return role


def require_client_role(min_role: str = "viewer"):
    """FastAPI dependency factory - `Depends(require_client_role("editor"))`
    on any client-scoped route replaces a hand-written assert_client_role
    call at the top of the handler. Reads client_id from whichever the route
    declares it as (path param or query param), so it drops into existing
    endpoint signatures without changing them. This is what most of
    main.py's connector/dashboard endpoints were missing - they verified
    WHO the caller is (verify_bearer_token) but never checked their role on
    the specific client_id being acted on before performing a service-role
    read/write that bypasses RLS entirely."""
    async def _dependency(request: Request, user: AuthUser = Depends(verify_bearer_token)) -> str:
        client_id = request.path_params.get("client_id") or request.query_params.get("client_id")
        if not client_id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="client_id is required")
        return await assert_client_role(client_id, user.sub, min_role)
    return _dependency


async def _org_name(organization_id: str) -> str:
    rows = await _rows("organizations", {"select": "name", "id": f"eq.{organization_id}", "limit": "1"})
    return (rows[0].get("name") if rows else None) or "This agency"


async def create_client_invitation(client_id: str, email: str, role: str, invited_by: str) -> dict[str, Any]:
    if role not in ASSIGNABLE_CLIENT_ROLES:
        raise HTTPException(status_code=422, detail="Invalid role")
    await assert_client_role(client_id, invited_by, "admin")

    clean_email = email.lower().strip()
    if not clean_email or "@" not in clean_email:
        raise HTTPException(status_code=422, detail="Enter a valid email address")

    workspace = await get_client_workspace(client_id)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    existing_user_id = await find_user_id_by_email(clean_email)
    if existing_user_id:
        existing_role = await get_effective_client_role(client_id, existing_user_id)
        if existing_role:
            raise HTTPException(status_code=409, detail="This person already has access to this client")

    active = await _active_pending_invitation(client_id, clean_email)
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "An invitation is already pending for this email.",
                "code": "invitation_pending",
                "invitation_id": active["id"],
            },
        )

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{supabase_rest_url()}/client_invitations",
            params={"select": "id,token"},
            json={
                "client_id": client_id,
                "organization_id": workspace["organization_id"],
                "email": clean_email,
                "role": role,
                "invited_by": invited_by,
            },
            headers=supabase_service_headers({"Prefer": "return=representation"}),
        )
        response.raise_for_status()
        created = response.json()[0]

    email_sent = await _send_invitation_email(created["id"], created["token"], client_id, workspace, clean_email, role)
    if not email_sent:
        # A row that was never delivered must not sit as "pending" - that is
        # what used to block every retry with "already pending". Cancel it so
        # the next attempt starts clean; nothing was lost (no email left).
        await _set_invitation_status(created["id"], "cancelled")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="The invitation could not be sent. Please try again.")

    return {"ok": True, "email": clean_email, "role": role, "client_name": workspace.get("name"), "email_sent": True, "invitation_id": created["id"]}


def _redact_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


async def _set_invitation_status(invitation_id: str, new_status: str, extra: dict[str, Any] | None = None) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.patch(
            f"{supabase_rest_url()}/client_invitations",
            params={"id": f"eq.{invitation_id}"},
            json={"status": new_status, **(extra or {})},
            headers=supabase_service_headers({"Prefer": "return=minimal"}),
        )
        response.raise_for_status()


async def _active_pending_invitation(client_id: str, email: str) -> dict[str, Any] | None:
    """The one genuinely live invitation for CLIENT + EMAIL, if any: status
    pending AND not past expires_at. Stale pending rows are marked expired
    here so they neither block a new invite nor show as pending."""
    rows = await _rows("client_invitations", {
        "client_id": f"eq.{client_id}",
        "email": f"eq.{email}",
        "status": "eq.pending",
        "select": "id,expires_at",
    })
    active = None
    for row in rows:
        if _is_expired(row["expires_at"]):
            await _set_invitation_status(row["id"], "expired")
        elif active is None:
            active = row
    return active


async def _send_invitation_email(invitation_id: str, token: str, client_id: str, workspace: dict[str, Any], email: str, role: str) -> bool:
    org_name = await _org_name(workspace["organization_id"])
    accept_url = f"{_web_app_url()}/accept-invitation?token={token}"
    sent = await send_client_invitation_email(email, org_name, workspace.get("name") or "this client", role, accept_url)
    # "sent" means the provider accepted the request (2xx), not inbox delivery.
    logger.info(
        "Client invitation email %s: client=%s invitation=%s to=%s configured=%s",
        "accepted_by_provider" if sent else "FAILED",
        client_id, invitation_id, _redact_email(email), email_configured(),
    )
    return sent


_RESEND_COOLDOWN_SECONDS = 60
_last_resend: dict[str, float] = {}


async def resend_invitation(invitation_id: str, actor_user_id: str) -> dict[str, Any]:
    rows = await _rows("client_invitations", {
        "id": f"eq.{invitation_id}",
        "select": "id,client_id,email,role,status,token,expires_at",
        "limit": "1",
    })
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    invitation = rows[0]
    client_id = invitation["client_id"]
    await assert_client_role(client_id, actor_user_id, "admin")

    if invitation["status"] in ("accepted", "cancelled"):
        raise HTTPException(status_code=409, detail="This invitation is no longer active. Send a new invitation instead.")

    now = time.monotonic()
    if now - _last_resend.get(invitation_id, -_RESEND_COOLDOWN_SECONDS) < _RESEND_COOLDOWN_SECONDS:
        raise HTTPException(status_code=429, detail="This invitation was just sent. Please wait a minute before resending.")
    _last_resend[invitation_id] = now

    workspace = await get_client_workspace(client_id)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    # Expired invitations are reissued on the same row/token (fresh 7 days)
    # so there is still only one invitation per client + email.
    if invitation["status"] == "expired" or _is_expired(invitation["expires_at"]):
        await _set_invitation_status(
            invitation_id, "pending",
            {"expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()},
        )

    if not await _send_invitation_email(invitation_id, invitation["token"], client_id, workspace, invitation["email"], invitation["role"]):
        _last_resend.pop(invitation_id, None)  # a failed send must stay retryable
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="The invitation could not be sent. Please try again.")
    return {"ok": True, "email": invitation["email"], "role": invitation["role"], "client_name": workspace.get("name"), "email_sent": True, "invitation_id": invitation_id}


async def get_client_team(client_id: str, actor_user_id: str) -> list[dict[str, Any]]:
    await assert_client_role(client_id, actor_user_id, "viewer")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{supabase_rest_url()}/rpc/client_team_members",
            json={"p_client_id": client_id},
            headers=supabase_service_headers(),
        )
        response.raise_for_status()
        members = response.json()
    for member in members:
        if "avatar_url" not in member:
            member["avatar_url"] = await get_user_avatar_url(member.get("user_id"))
    return members


async def remove_client_member(client_id: str, target_user_id: str, actor_user_id: str) -> None:
    await assert_client_role(client_id, actor_user_id, "admin")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.delete(
            f"{supabase_rest_url()}/client_memberships",
            params={"client_id": f"eq.{client_id}", "user_id": f"eq.{target_user_id}"},
            headers=supabase_service_headers({"Prefer": "return=minimal"}),
        )
        response.raise_for_status()


async def change_client_member_role(client_id: str, target_user_id: str, role: str, actor_user_id: str) -> None:
    if role not in ASSIGNABLE_CLIENT_ROLES:
        raise HTTPException(status_code=422, detail="Invalid role")
    await assert_client_role(client_id, actor_user_id, "admin")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.patch(
            f"{supabase_rest_url()}/client_memberships",
            params={"client_id": f"eq.{client_id}", "user_id": f"eq.{target_user_id}"},
            json={"role": role},
            headers=supabase_service_headers({"Prefer": "return=minimal"}),
        )
        response.raise_for_status()


async def list_client_invitations(client_id: str, actor_user_id: str) -> list[dict[str, Any]]:
    await assert_client_role(client_id, actor_user_id, "admin")
    return await _rows("client_invitations", {
        "client_id": f"eq.{client_id}",
        "select": "id,email,role,status,expires_at,created_at",
        "order": "created_at.desc",
    })


async def cancel_invitation(invitation_id: str, actor_user_id: str) -> None:
    rows = await _rows("client_invitations", {"id": f"eq.{invitation_id}", "select": "client_id,status", "limit": "1"})
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    await assert_client_role(rows[0]["client_id"], actor_user_id, "admin")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.patch(
            f"{supabase_rest_url()}/client_invitations",
            params={"id": f"eq.{invitation_id}"},
            json={"status": "cancelled"},
            headers=supabase_service_headers({"Prefer": "return=minimal"}),
        )
        response.raise_for_status()


def _web_app_url() -> str:
    import os
    return (os.getenv("WEB_APP_URL") or os.getenv("APP_PUBLIC_URL") or "").rstrip("/")


async def lookup_invitation(token: str) -> dict[str, Any]:
    """Public-safe (pre-login) lookup for the /accept-invitation page. Never
    exposes the raw invitation row - only what that page needs to decide
    between "log in" and "sign up"."""
    rows = await _rows("client_invitations", {
        "token": f"eq.{token}",
        "select": "id,client_id,organization_id,email,role,status,expires_at",
        "limit": "1",
    })
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    invitation = rows[0]

    status_value = invitation["status"]
    if status_value == "pending" and _is_expired(invitation["expires_at"]):
        status_value = "expired"

    workspace = await get_client_workspace(invitation["client_id"])
    org_name = await _org_name(invitation["organization_id"])
    account_exists = bool(await find_user_id_by_email(invitation["email"]))

    return {
        "email": invitation["email"],
        "role": invitation["role"],
        "status": status_value,
        "client_name": (workspace or {}).get("name") or "this client",
        "agency_name": org_name,
        "account_exists": account_exists,
    }


def _is_expired(expires_at: str) -> bool:
    expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) > expires


async def accept_invitation(token: str, user_id: str) -> dict[str, Any]:
    rows = await _rows("client_invitations", {"token": f"eq.{token}", "select": "*", "limit": "1"})
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    invitation = rows[0]

    if invitation["status"] == "accepted":
        return {"ok": True, "client_id": invitation["client_id"], "role": invitation["role"], "already_accepted": True}
    if invitation["status"] == "cancelled":
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invitation has been cancelled")
    if invitation["status"] == "expired" or _is_expired(invitation["expires_at"]):
        async with httpx.AsyncClient(timeout=15) as client:
            await client.patch(
                f"{supabase_rest_url()}/client_invitations",
                params={"id": f"eq.{invitation['id']}"},
                json={"status": "expired"},
                headers=supabase_service_headers({"Prefer": "return=minimal"}),
            )
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invitation has expired")

    accepting_email = await get_user_email(user_id)
    if (accepting_email or "").lower() != invitation["email"].lower():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This invitation was sent to a different email address")

    async with httpx.AsyncClient(timeout=15) as client:
        membership_response = await client.post(
            f"{supabase_rest_url()}/client_memberships",
            params={"on_conflict": "client_id,user_id"},
            json={
                "client_id": invitation["client_id"],
                "user_id": user_id,
                "role": invitation["role"],
                "invited_by": invitation["invited_by"],
            },
            headers=supabase_service_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
        )
        membership_response.raise_for_status()
        accept_response = await client.patch(
            f"{supabase_rest_url()}/client_invitations",
            params={"id": f"eq.{invitation['id']}"},
            json={"status": "accepted", "accepted_at": datetime.now(timezone.utc).isoformat()},
            headers=supabase_service_headers({"Prefer": "return=minimal"}),
        )
        accept_response.raise_for_status()

    return {"ok": True, "client_id": invitation["client_id"], "role": invitation["role"], "already_accepted": False}
