# SPDX-License-Identifier: AGPL-3.0-only
"""Connector framework.

A connector brings a platform's numbers into Braidscope. It never writes to
the platform. Three things make that true and keep it true:

- policy.py   declares, per connector, the OAuth scopes and the few non-GET
              calls that are still reads (token exchange, a query POST, MCP
              JSON-RPC). Anything else is a violation.
- http.py     gives new connectors an HTTP client that enforces that policy
              at runtime.
- tests/test_connector_contract.py checks every connector module against
              the policy in CI, so a write call cannot merge unnoticed.

New connectors live in a subpackage here (app/connectors/<id>/) and are
picked up by registry.py. See docs/connectors.md.
"""
