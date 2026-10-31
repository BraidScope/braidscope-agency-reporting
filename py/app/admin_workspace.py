# SPDX-License-Identifier: AGPL-3.0-only
"""Admin Workspace aggregation endpoints.

This is deliberately a view over existing client/organization membership
tables. It does not create admin-owned client state.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

import httpx
from fastapi import HTTPException, status

from .supabase_admin import supabase_admin_configured, supabase_rest_url, supabase_service_headers


def _role_meets(role: str | None, minimum: str) -> bool:
    rank = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
    return rank.get(role or "", -1) >= rank.get(minimum, 0)


async def _get_rows(client: httpx.AsyncClient, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
    response = await client.get(f"{supabase_rest_url()}/{table}", params=params, headers=supabase_service_headers())
    response.raise_for_status()
    return response.json()


async def list_admin_clients(user_id: str) -> dict[str, Any]:
    if not supabase_admin_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Admin workspace service is not configured")

    async with httpx.AsyncClient(timeout=20) as client:
        memberships_task = _get_rows(
            client,
            "memberships",
            {"select": "organization_id,role", "user_id": f"eq.{user_id}"},
        )
        shared_rows_task = _get_rows(
            client,
            "client_memberships",
            {"select": "client_id,role,created_at", "user_id": f"eq.{user_id}"},
        )
        memberships, shared_rows = await asyncio.gather(memberships_task, shared_rows_task)

        org_ids = [row["organization_id"] for row in memberships if row.get("organization_id")]
        org_role_by_id = {row["organization_id"]: row.get("role") for row in memberships if row.get("organization_id")}

        # The owned-client queries and the shared-client query depend only on
        # the membership rows above, not on each other - run them together.
        shared_ids = [row["client_id"] for row in shared_rows if row.get("client_id")]

        async def _no_rows() -> list[dict[str, Any]]:
            return []

        if org_ids:
            in_orgs = ",".join(org_ids)
            owned_clients_task = _get_rows(
                client,
                "clients",
                {
                    "select": "id,organization_id,name,logo_url,currency,industry,created_at,branding_override",
                    "organization_id": f"in.({in_orgs})",
                    "order": "created_at.asc",
                },
            )
            owner_orgs_task = _get_rows(
                client,
                "organizations",
                {
                    "select": "id,name,logo_url,accent_color,branding_config",
                    "id": f"in.({in_orgs})",
                },
            )
        else:
            owned_clients_task = _no_rows()
            owner_orgs_task = _no_rows()
        if shared_ids:
            in_clients = ",".join(shared_ids)
            shared_clients_task = _get_rows(
                client,
                "clients",
                {
                    "select": "id,organization_id,name,logo_url,currency,industry,created_at,branding_override",
                    "id": f"in.({in_clients})",
                    "order": "created_at.asc",
                },
            )
        else:
            shared_clients_task = _no_rows()
        owned_clients, owner_orgs, shared_clients = await asyncio.gather(
            owned_clients_task, owner_orgs_task, shared_clients_task,
        )

        shared_org_ids = sorted({row["organization_id"] for row in shared_clients if row.get("organization_id")})
        shared_role_by_client = {row["client_id"]: row.get("role") for row in shared_rows if row.get("client_id")}

        # Deduplicate: if the viewer is an org member, that owned relationship
        # wins over any stale direct client_memberships row.
        accessible: dict[str, dict[str, Any]] = {}
        for row in owned_clients:
            role = org_role_by_id.get(row.get("organization_id"))
            if not _role_meets(role, "viewer"):
                continue
            accessible[row["id"]] = {
                **row,
                "access": "owner",
                "role": role,
                "ownerOrg": None,  # filled from org_by_id once organizations are loaded
            }

        for row in shared_clients:
            if row["id"] in accessible:
                continue
            role = shared_role_by_client.get(row["id"])
            if not _role_meets(role, "viewer"):
                continue
            accessible[row["id"]] = {
                **row,
                "access": "shared",
                "role": role,
                "ownerOrg": None,  # filled from org_by_id once organizations are loaded
            }

        client_ids = list(accessible)
        page_counts: Counter[str] = Counter()
        report_counts: Counter[str] = Counter()
        connector_counts: Counter[str] = Counter()
        last_activity: dict[str, str] = {}

        # Shared clients' organizations and the usage rows both depend only on
        # the client rows fetched above (which decide the accessible ids), not
        # on each other - fetch them together.
        async def _shared_orgs() -> list[dict[str, Any]]:
            if not shared_org_ids:
                return []
            return await _get_rows(
                client,
                "organizations",
                {
                    "select": "id,name,logo_url,accent_color,branding_config",
                    "id": f"in.({','.join(shared_org_ids)})",
                },
            )

        async def _usage() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            if not client_ids:
                return [], []
            return await _fetch_usage_rows(client, ",".join(client_ids))

        shared_orgs, (pages, sources) = await asyncio.gather(_shared_orgs(), _usage())
        org_by_id = {row["id"]: row for row in [*owner_orgs, *shared_orgs] if row.get("id")}
        for row in accessible.values():
            row["ownerOrg"] = org_by_id.get(row.get("organization_id"))

        if client_ids:
            for row in pages:
                client_id = row.get("client_id")
                if not client_id:
                    continue
                if row.get("type") == "report":
                    report_counts[client_id] += 1
                else:
                    page_counts[client_id] += 1
                created_at = row.get("created_at")
                if created_at and created_at > last_activity.get(client_id, ""):
                    last_activity[client_id] = created_at
            for row in sources:
                client_id = row.get("client_id")
                if not client_id:
                    continue
                connector_counts[client_id] += 1
                activity = row.get("last_sync_at") or row.get("created_at")
                if activity and activity > last_activity.get(client_id, ""):
                    last_activity[client_id] = activity

        clients = []
        for row in accessible.values():
            client_id = row["id"]
            clients.append({
                **row,
                "counts": {
                    "dashboards": page_counts[client_id],
                    "reports": report_counts[client_id],
                    "integrations": connector_counts[client_id],
                },
                "lastActivityAt": last_activity.get(client_id) or row.get("created_at"),
            })

    clients.sort(key=lambda item: ((item.get("name") or "").lower(), item.get("id") or ""))
    return {"clients": clients}


async def _fetch_usage_rows(client: httpx.AsyncClient, in_clients: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pages_response, sources_response = await asyncio.gather(
        client.get(
            f"{supabase_rest_url()}/pages",
            params={"select": "client_id,type,created_at", "client_id": f"in.({in_clients})"},
            headers=supabase_service_headers(),
        ),
        client.get(
            f"{supabase_rest_url()}/data_sources",
            params={"select": "client_id,status,last_sync_at,created_at", "client_id": f"in.({in_clients})", "status": "eq.connected"},
            headers=supabase_service_headers(),
        ),
    )
    pages_response.raise_for_status()
    sources_response.raise_for_status()
    return pages_response.json(), sources_response.json()
