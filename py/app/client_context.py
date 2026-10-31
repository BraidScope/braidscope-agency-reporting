# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import os
from typing import Any

import httpx
from fastapi import HTTPException, status

from .hubspot import has_hubspot_stored_token
from .klaviyo import has_klaviyo_stored_token
from .schemas import AuthUser, ChatRequest


async def load_client_context(request: ChatRequest, user: AuthUser) -> dict[str, Any]:
    if request.client_id.startswith("demo-"):
        return {
            "id": request.client_id,
            "name": request.client_name or request.client_id,
            "connected_sources": request.connected_sources,
        }

    supabase_url = os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("VITE_SUPABASE_ANON_KEY")
    if not supabase_url or not supabase_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase server configuration is missing",
        )

    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {user.access_token}",
    }
    rest_url = f"{supabase_url.rstrip('/')}/rest/v1"

    try:
        async with httpx.AsyncClient(headers=headers, timeout=10) as client:
            client_response, sources_response = await asyncio.gather(
                client.get(f"{rest_url}/clients", params={
                    "select": "id,name",
                    "id": f"eq.{request.client_id}",
                    "limit": "1",
                }),
                client.get(f"{rest_url}/data_sources", params={
                    "select": "type,status",
                    "client_id": f"eq.{request.client_id}",
                    "status": "in.(connected,syncing)",
                }),
            )
            client_response.raise_for_status()
            clients = client_response.json()
            if not clients:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Client workspace is not accessible",
                )
            sources_response.raise_for_status()
            sources = sources_response.json()
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not load client data sources",
        ) from exc

    connected_sources = [source["type"] for source in sources]
    has_klaviyo, has_hubspot = await asyncio.gather(
        has_klaviyo_stored_token(request.client_id),
        has_hubspot_stored_token(request.client_id),
    )
    if has_klaviyo and "klaviyo" not in connected_sources:
        connected_sources.append("klaviyo")
    if has_hubspot and "hubspot_crm" not in connected_sources:
        connected_sources.append("hubspot_crm")

    return {
        "id": clients[0]["id"],
        "name": clients[0]["name"],
        "connected_sources": connected_sources,
    }


async def verify_client_access(request: ChatRequest, user: AuthUser) -> None:
    """Access check only: the same `clients` lookup (under the caller's own JWT,
    so RLS decides visibility) and the same 503/403/502 outcomes as
    load_client_context, without the data_sources / stored-token lookups whose
    result the connector *status* endpoints never use."""
    if request.client_id.startswith("demo-"):
        return

    supabase_url = os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("VITE_SUPABASE_ANON_KEY")
    if not supabase_url or not supabase_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase server configuration is missing",
        )

    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {user.access_token}",
    }
    rest_url = f"{supabase_url.rstrip('/')}/rest/v1"

    try:
        async with httpx.AsyncClient(headers=headers, timeout=10) as client:
            client_response = await client.get(f"{rest_url}/clients", params={
                "select": "id,name",
                "id": f"eq.{request.client_id}",
                "limit": "1",
            })
            client_response.raise_for_status()
            clients = client_response.json()
            if not clients:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Client workspace is not accessible",
                )
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not load client data sources",
        ) from exc
