# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only policy for every connector.

Braidscope reads data from platforms and never sends anything back. Most
reads are plain GET requests. A few platforms need a POST for a read: the
OAuth token exchange, a GraphQL or GAQL query, a search endpoint, or MCP
JSON-RPC. Each of those is listed here, per connector, with the reason. The
contract test fails on any non-GET call that is not listed, and the runtime
client (http.py) refuses it.

Adding a connector: add a ConnectorPolicy below. Adding a non-GET call to an
existing connector: add an Endpoint with its purpose, in the same PR as the
code, so the reviewer sees both.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


class ReadOnlyViolation(RuntimeError):
    """A connector tried an outbound call the policy does not allow."""


@dataclass(frozen=True)
class Endpoint:
    method: str
    # Regex. At runtime it is matched against the full URL; in the contract
    # test against the URL expression in the source, so patterns usually
    # name both the constant and the host (CONST_NAME|host/path).
    url_pattern: str
    purpose: str


@dataclass(frozen=True)
class ConnectorPolicy:
    id: str
    label: str
    # Modules under app/ that talk to this platform. The contract test scans them.
    modules: tuple[str, ...]
    # Hosts this connector may call (regex). Empty means not enforced yet.
    hosts: tuple[str, ...]
    # OAuth scopes requested. Read-only by name, or explained in scope_note.
    scopes: tuple[str, ...]
    scope_note: str = ""
    # Non-GET calls that are reads.
    non_get: tuple[Endpoint, ...] = ()


# URL expressions that point at Braidscope's own database or storage, never
# at the platform. These are the app's writes (cache rows, credentials) and
# are allowed in every connector.
INTERNAL_URL_MARKERS = ("supabase_rest_url", "supabase_storage_url", "_storage_url(")

MCP_JSONRPC = "MCP JSON-RPC (initialize, tools/list, tools/call); only tools that pass tool_is_read_only are called"

POLICIES: dict[str, ConnectorPolicy] = {
    "shopify": ConnectorPolicy(
        id="shopify",
        label="Shopify",
        modules=("shopify", "shopify_dashboard"),
        hosts=(r"\.myshopify\.com$",),
        scopes=("read_orders", "read_products", "read_customers", "read_analytics"),
        non_get=(
            Endpoint("POST", r"admin/oauth/access_token", "OAuth code exchange"),
            Endpoint("POST", r"graphql\.json", "GraphQL query; assert_read_only_graphql rejects mutations"),
        ),
    ),
    "hubspot_crm": ConnectorPolicy(
        id="hubspot_crm",
        label="HubSpot",
        modules=("hubspot", "hubspot_dashboard"),
        hosts=(r"^api\.hubapi\.com$", r"^mcp\.hubspot\.com$", r"^app\.hubspot\.com$"),
        scopes=("crm.objects.contacts.read", "crm.objects.companies.read", "crm.objects.deals.read"),
        non_get=(
            Endpoint("POST", r"HUBSPOT_TOKEN_URL|api\.hubapi\.com/oauth/v1/token", "OAuth code exchange and refresh"),
            Endpoint("POST", r"hubspot_mcp_url\(\)|mcp\.hubspot\.com", MCP_JSONRPC),
            Endpoint("POST", r"crm/v3/objects/\w+/search", "CRM search is a POST with a filter body; it reads"),
        ),
    ),
    "klaviyo": ConnectorPolicy(
        id="klaviyo",
        label="Klaviyo",
        modules=("klaviyo", "klaviyo_dashboard"),
        hosts=(r"\.klaviyo\.com$",),
        scopes=(),
        scope_note="Klaviyo's hosted MCP server issues its own read scopes during dynamic client registration.",
        non_get=(
            Endpoint("POST", r"KLAVIYO_OAUTH_ISSUER\}/register|klaviyo\.com/oauth/register", "OAuth dynamic client registration"),
            Endpoint("POST", r"KLAVIYO_OAUTH_ISSUER\}/token|klaviyo\.com/oauth/token", "OAuth code exchange and refresh"),
            Endpoint("POST", r"server_url|KLAVIYO_MCP_URL|mcp\.klaviyo\.com", MCP_JSONRPC),
        ),
    ),
    "google_analytics_4": ConnectorPolicy(
        id="google_analytics_4",
        label="Google Analytics 4",
        modules=("ga4", "ga4_dashboard", "ga4_mcp"),
        hosts=(r"\.googleapis\.com$", r"^accounts\.google\.com$", r"^oauth2\.googleapis\.com$"),
        scopes=("https://www.googleapis.com/auth/analytics.readonly",),
        non_get=(
            Endpoint("POST", r"GA4_TOKEN_URL|oauth2\.googleapis\.com/token", "OAuth code exchange and refresh"),
            Endpoint("POST", r":runReport|:runRealtimeReport", "Analytics Data API reports are POST queries"),
        ),
    ),
    "google_ads": ConnectorPolicy(
        id="google_ads",
        label="Google Ads",
        modules=("google_ads", "google_ads_dashboard", "google_ads_mcp"),
        hosts=(r"\.googleapis\.com$", r"^accounts\.google\.com$"),
        scopes=("https://www.googleapis.com/auth/adwords",),
        scope_note=(
            "Google Ads has no read-only OAuth scope. Read-only is enforced by code: "
            "the only Google Ads API call is googleAds:searchStream (GAQL SELECT)."
        ),
        non_get=(
            Endpoint("POST", r"GOOGLE_ADS_TOKEN_URL|oauth2\.googleapis\.com/token", "OAuth code exchange and refresh"),
            Endpoint("POST", r"googleAds:searchStream", "GAQL search; the API has no write through this endpoint"),
        ),
    ),
    "meta_ads": ConnectorPolicy(
        id="meta_ads",
        label="Meta Ads",
        modules=("meta_ads", "meta_ads_dashboard", "meta_ads_mcp"),
        hosts=(r"^graph\.facebook\.com$", r"\.facebook\.com$"),
        scopes=("ads_read", "business_management"),
        scope_note=(
            "business_management is needed to list the ad accounts under a Business. "
            "The connector only calls GET endpoints on the Graph API."
        ),
        non_get=(),
    ),
}

_MUTATION = re.compile(r"\bmutation\b\s*[A-Za-z_{(]", re.IGNORECASE)


def assert_read_only_graphql(query: str) -> None:
    """Rejects a GraphQL document that contains a mutation operation."""
    if _MUTATION.search(query or ""):
        raise ReadOnlyViolation("GraphQL mutations are not allowed: connectors only read")


def is_internal_url(url: str) -> bool:
    return any(marker in url for marker in INTERNAL_URL_MARKERS)


def check_request(policy: ConnectorPolicy, method: str, url: str, body: bytes | str | None = None) -> None:
    """Raises ReadOnlyViolation unless the call is allowed by the policy.

    Used at runtime by http.connector_client and by the contract test on
    source expressions. GET/HEAD/OPTIONS are reads; any other method must be
    listed in policy.non_get. A body that carries a GraphQL document must not
    contain a mutation.
    """
    method = method.upper()
    if method not in ("GET", "HEAD", "OPTIONS"):
        if not any(ep.method == method and re.search(ep.url_pattern, url) for ep in policy.non_get):
            raise ReadOnlyViolation(
                f"{policy.id}: {method} {url} is not in the read-only policy. "
                "If this call only reads, add an Endpoint to app/connectors/policy.py with its purpose."
            )
    if body:
        text = body.decode("utf-8", "ignore") if isinstance(body, bytes) else str(body)
        if '"query"' in text:
            assert_read_only_graphql(text)
