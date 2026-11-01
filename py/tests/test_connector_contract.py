# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only contract for every connector.

Braidscope reads from platforms and never writes back. These tests make
that a property of the code, checked on every PR:

1. Every non-GET outbound call in a connector module is listed in its policy
   (app/connectors/policy.py) with a purpose, or targets Braidscope's own
   database/storage.
2. No GraphQL document in a connector contains a mutation.
3. The OAuth scopes each connector asks for are the ones the policy records.
4. Every tool our own MCP servers expose has a read-only name, and the
   orchestrator refuses to execute a tool with a write-like name.
5. Any module that mentions a platform's API host is covered by a policy.
6. Packages under app/connectors/ use the guarded client, not httpx directly.

A failure here means: either the call is a write (remove it), or it is a
read that needs an Endpoint entry in the policy, in the same PR.
"""
from __future__ import annotations

import ast
import pathlib
import re
import unittest

from app.connectors.policy import (
    INTERNAL_URL_MARKERS,
    POLICIES,
    ReadOnlyViolation,
    assert_read_only_graphql,
    check_request,
)

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
WRITE_METHODS = ("post", "put", "patch", "delete", "request")
MUTATION = re.compile(r"\bmutation\b\s*[A-Za-z_{(]", re.IGNORECASE)

# Platform API hosts. A module that names one of these must belong to a policy.
PLATFORM_HOST_MARKERS = {
    "shopify": ("myshopify.com", "/admin/api/"),
    "hubspot_crm": ("hubapi.com", "mcp.hubspot.com"),
    "klaviyo": ("klaviyo.com",),
    "google_analytics_4": ("analyticsdata.googleapis.com", "analyticsadmin.googleapis.com"),
    "google_ads": ("googleads.googleapis.com",),
    "meta_ads": ("graph.facebook.com",),
}
# Modules that legitimately mention platform hosts without calling them
# (orchestration, chat wiring, settings). Add with a reason.
HOST_MENTION_EXEMPT = {
    "chat_service",      # builds the MCP tool list for the AI provider
    "mcp_orchestrator",  # calls tools on the MCP servers above through their policies
    "core",              # KLAVIYO_MCP_URL constant
    "main",              # OAuth callback routes delegate to connector modules
}


def _module_source(name: str) -> tuple[str, ast.AST]:
    path = APP / f"{name}.py"
    src = path.read_text(encoding="utf-8")
    return src, ast.parse(src)


def _resolve_url_expr(src: str, tree: ast.AST, call: ast.Call) -> str:
    """Source text of the URL argument. A bare name is resolved to its last
    assignment above the call inside the same function."""
    arg = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "url"), None)
    if arg is None:
        return "<no url>"
    text = ast.get_source_segment(src, arg) or ""
    if isinstance(arg, ast.Name):
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= call.lineno <= (node.end_lineno or 0):
                assigns = [
                    n for n in ast.walk(node)
                    if isinstance(n, ast.Assign) and n.lineno < call.lineno
                    and any(isinstance(t, ast.Name) and t.id == arg.id for t in n.targets)
                ]
                if assigns:
                    text = f"{arg.id} = {ast.get_source_segment(src, assigns[-1].value)}"
                break
    return text


def _non_get_calls(name: str):
    src, tree = _module_source(name)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in WRITE_METHODS:
            continue
        receiver = ast.get_source_segment(src, node.func.value) or ""
        if receiver in ("router", "app"):
            continue  # FastAPI route decorators, not outbound calls
        yield node.lineno, node.func.attr.upper(), _resolve_url_expr(src, tree, node)


class NonGetCallsAreListed(unittest.TestCase):
    def test_every_non_get_call_is_a_listed_read_or_internal(self):
        problems = []
        for policy in POLICIES.values():
            for module in policy.modules:
                for lineno, method, url_expr in _non_get_calls(module):
                    if any(marker in url_expr for marker in INTERNAL_URL_MARKERS):
                        continue
                    try:
                        check_request(policy, method, url_expr)
                    except ReadOnlyViolation:
                        problems.append(f"app/{module}.py:{lineno} {method} {url_expr}")
        self.assertEqual(problems, [], "Non-GET calls not covered by app/connectors/policy.py:\n" + "\n".join(problems))

    def test_policy_endpoints_are_all_used(self):
        # An unused entry is a stale allowance; remove it with the code.
        for policy in POLICIES.values():
            if not policy.modules:
                continue  # package connector: enforced at runtime by the guarded client
            exprs = [f"{m} {u}" for module in policy.modules for _, m, u in _non_get_calls(module)]
            for endpoint in policy.non_get:
                hits = [e for e in exprs if e.startswith(endpoint.method) and re.search(endpoint.url_pattern, e)]
                self.assertTrue(hits, f"{policy.id}: policy endpoint {endpoint.url_pattern!r} matches no call")


class NoMutations(unittest.TestCase):
    def test_no_graphql_mutation_in_connector_modules(self):
        for policy in POLICIES.values():
            for module in policy.modules:
                src, tree = _module_source(module)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str) and MUTATION.search(node.value):
                        self.fail(f"app/{module}.py:{node.lineno} contains a GraphQL mutation")

    def test_guard_rejects_mutations_and_accepts_queries(self):
        assert_read_only_graphql("query Orders($first: Int) { orders(first: $first) { edges { node { id } } } }")
        with self.assertRaises(ReadOnlyViolation):
            assert_read_only_graphql("mutation { orderUpdate(input: {id: \"1\"}) { order { id } } }")
        with self.assertRaises(ReadOnlyViolation):
            check_request(POLICIES["shopify"], "POST", "https://x.myshopify.com/admin/api/2026-01/graphql.json", b'{"query":"mutation M { a }"}')


class ScopesMatchPolicy(unittest.TestCase):
    def test_scopes_are_the_recorded_read_scopes(self):
        from app import ga4, google_ads, hubspot, meta_ads, shopify

        self.assertEqual(tuple(shopify.SHOPIFY_SCOPES), POLICIES["shopify"].scopes)
        self.assertTrue(all(s.startswith("read_") for s in shopify.SHOPIFY_SCOPES))
        self.assertEqual((ga4.GA4_SCOPES,), POLICIES["google_analytics_4"].scopes)
        self.assertEqual(tuple(hubspot.hubspot_scopes().split()), POLICIES["hubspot_crm"].scopes)
        self.assertTrue(all(s.endswith(".read") for s in hubspot.hubspot_scopes().split()))
        self.assertEqual(tuple(meta_ads.META_ADS_SCOPES.split(",")), POLICIES["meta_ads"].scopes)
        self.assertEqual((google_ads.GOOGLE_ADS_SCOPES,), POLICIES["google_ads"].scopes)
        for policy in POLICIES.values():
            if not all(re.search(r"read|readonly|\.read$", s) for s in policy.scopes):
                self.assertTrue(policy.scope_note, f"{policy.id}: a scope that is not read-only by name needs scope_note")


class OwnMcpToolsAreReadOnly(unittest.TestCase):
    def test_every_own_mcp_tool_name_is_read_only(self):
        from app.ga4_mcp import GA4_MCP_TOOLS
        from app.google_ads_mcp import GOOGLE_ADS_MCP_TOOLS
        from app.mcp_orchestrator import tool_is_read_only, tool_name_is_read_only
        from app.meta_ads_mcp import META_ADS_MCP_TOOLS

        for tool in [*GA4_MCP_TOOLS, *GOOGLE_ADS_MCP_TOOLS, *META_ADS_MCP_TOOLS]:
            self.assertTrue(tool_name_is_read_only(tool["name"]), tool["name"])
            self.assertTrue(tool_is_read_only(tool), tool["name"])

    def test_write_like_names_are_refused(self):
        from app.mcp_orchestrator import tool_name_is_read_only

        for name in ("create_contact", "hubspot-update-deal", "delete_campaign", "send_campaign", "campaigns/publish", "archive_flow"):
            self.assertFalse(tool_name_is_read_only(name), name)
        for name in ("get_campaigns", "search_crm_objects", "list_properties", "run_report"):
            self.assertTrue(tool_name_is_read_only(name), name)


class CoverageAndIsolation(unittest.TestCase):
    def test_modules_that_name_a_platform_host_have_a_policy(self):
        covered = {m for p in POLICIES.values() for m in p.modules}
        for path in sorted(APP.glob("*.py")):
            name = path.stem
            if name in covered or name in HOST_MENTION_EXEMPT:
                continue
            text = path.read_text(encoding="utf-8")
            for connector, markers in PLATFORM_HOST_MARKERS.items():
                if any(marker in text for marker in markers):
                    self.fail(f"app/{name}.py mentions {connector}'s API but is not in that policy's modules (or HOST_MENTION_EXEMPT)")

    def test_connector_packages_use_the_guarded_client(self):
        for package in (APP / "connectors").iterdir():
            if not package.is_dir() or package.name.startswith("_"):
                continue
            for path in package.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    for n in names:
                        self.assertNotIn(n.split(".")[0], ("httpx", "requests", "urllib", "aiohttp"),
                                         f"{path}: import the client from app.connectors.http instead")
