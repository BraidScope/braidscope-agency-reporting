# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP client for connectors, read-only by construction.

    from app.connectors.http import connector_client

    async with connector_client("hubspot_crm", timeout=30) as client:
        response = await client.get(url, headers=...)

Every request is checked against the connector's policy before it leaves:
the host must be one the policy names (or Braidscope's own Supabase), a
non-GET method must be a listed read, and a GraphQL body must not contain a
mutation. A violation raises ReadOnlyViolation instead of sending anything.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

import httpx

from .policy import POLICIES, ReadOnlyViolation, check_request


def _own_hosts() -> set[str]:
    hosts = set()
    for name in ("SUPABASE_URL", "APP_BASE_URL"):
        value = os.getenv(name, "")
        if value:
            hosts.add(urlparse(value).hostname or "")
    hosts.discard("")
    return hosts


def _host_allowed(policy_hosts: tuple[str, ...], host: str) -> bool:
    if host in _own_hosts() or host in ("localhost", "127.0.0.1", "py", "caddy"):
        return True
    return any(re.search(pattern, host) for pattern in policy_hosts)


def connector_client(connector_id: str, **kwargs) -> httpx.AsyncClient:
    policy = POLICIES[connector_id]

    async def guard(request: httpx.Request) -> None:
        host = request.url.host or ""
        if policy.hosts and not _host_allowed(policy.hosts, host):
            raise ReadOnlyViolation(f"{policy.id}: host {host} is not in the connector policy")
        if host in _own_hosts():
            return  # Braidscope's own database and storage: the app's writes
        check_request(policy, request.method, str(request.url), request.content)

    hooks = dict(kwargs.pop("event_hooks", {}) or {})
    hooks["request"] = list(hooks.get("request", [])) + [guard]
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)
