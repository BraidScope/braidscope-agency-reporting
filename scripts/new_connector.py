#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Scaffold a connector package.

    python scripts/new_connector.py linkedin_ads "LinkedIn Ads"

Creates py/app/connectors/<id>/ (policy entry, router, fetch, dashboard,
tests) that the registry picks up with no edit to main.py. Fill in the
TODOs, then run: cd py && python -m pytest tests/test_connector_contract.py
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    cid, label = sys.argv[1], sys.argv[2]
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,30}", cid):
        print("id must be snake_case, letters/digits/underscore, like linkedin_ads")
        return 2
    package = ROOT / "py" / "app" / "connectors" / cid
    if package.exists():
        print(f"{package} already exists")
        return 1
    package.mkdir(parents=True)
    upper = cid.upper()

    (package / "__init__.py").write_text(f'''"""{label} connector. Read-only: see docs/connectors.md."""
from fastapi import APIRouter, Depends

from app.auth import AuthUser, verify_bearer_token
from app.connectors.policy import POLICIES, ConnectorPolicy, Endpoint

from .dashboard import build_dashboard

POLICY = ConnectorPolicy(
    id="{cid}",
    label="{label}",
    modules=(),  # package modules are checked through the guarded client
    hosts=(r"^api\\.example\\.com$",),  # TODO: the platform's API host(s)
    scopes=("r_ads_reporting",),  # TODO: read-only scopes
    scope_note="TODO: say why these scopes only read, or that the platform has no read-only scope and the code only calls read endpoints.",
    non_get=(
        Endpoint("POST", r"example\\.com/oauth/v2/accessToken", "OAuth code exchange"),  # TODO
    ),
)
POLICIES[POLICY.id] = POLICY

router = APIRouter(prefix="/py/connectors/{cid}", tags=["{cid}"])


@router.get("/dashboard")
async def dashboard(client_id: str, days: int = 30, user: AuthUser = Depends(verify_bearer_token)):
    # TODO: verify the user's role on client_id (see app.client_access.assert_client_access)
    return await build_dashboard(client_id, days)
''')

    (package / "fetch.py").write_text(f'''"""Talks to the {label} API. Reads only."""
from __future__ import annotations

import os
from typing import Any

from app.connectors.http import connector_client

SOURCE_TYPE = "{cid}"
API = os.getenv("{upper}_API_BASE", "https://api.example.com")  # TODO


async def fetch_daily_metrics(token: str, days: int) -> list[dict[str, Any]]:
    """Returns one row per day: {{"date": "YYYY-MM-DD", "spend": float, ...}}."""
    async with connector_client(SOURCE_TYPE, timeout=30) as client:
        response = await client.get(f"{{API}}/TODO", headers={{"Authorization": f"Bearer {{token}}"}}, params={{"days": days}})
        response.raise_for_status()
        payload = response.json()
    return payload.get("elements", [])  # TODO: normalise
''')

    (package / "dashboard.py").write_text(f'''"""Turns fetched rows into the dashboard payload the web app renders."""
from __future__ import annotations

from typing import Any

from .fetch import fetch_daily_metrics


async def build_dashboard(client_id: str, days: int) -> dict[str, Any]:
    token = ""  # TODO: load and decrypt the stored token for client_id
    if not token:
        return {{"ok": False, "reason": "{label} is not connected for this client."}}
    rows = await fetch_daily_metrics(token, days)
    return {{
        "ok": True,
        "cards": [{{"label": "Spend", "value": sum(float(r.get("spend", 0)) for r in rows)}}],
        "trends": rows,
        "table": {{"title": "Campaigns", "columns": [], "rows": []}},
    }}
''')

    tests = ROOT / "py" / "tests" / f"test_connector_{cid}.py"
    tests.write_text(f'''"""Fixture-based tests for the {label} connector. No network."""
import unittest

from app.connectors.{cid} import POLICY


class {label.replace(" ", "")}PolicyTests(unittest.TestCase):
    def test_policy_is_registered_and_read_only(self):
        self.assertEqual(POLICY.id, "{cid}")
        self.assertTrue(POLICY.scopes)
''')

    print(f"created {package.relative_to(ROOT)} and {tests.relative_to(ROOT)}")
    print("next: fill the TODOs, add a web catalog entry in web/src/lib/data.js, run the contract test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
