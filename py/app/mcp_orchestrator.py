# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import httpx
from openai import OpenAI

from .core import KLAVIYO_MCP_URL, debug_log
from .google_ads import get_google_ads_access_token, google_ads_mcp_url
from .ga4 import get_ga4_access_token, ga4_mcp_url
from .ga4_dashboard import build_ga4_dashboard
from .google_ads_dashboard import resolve_google_ads_customer
from .meta_ads import get_meta_ads_access_token, meta_ads_mcp_url
from .hubspot import get_hubspot_access_token, hubspot_mcp_url
from .klaviyo import (
    build_klaviyo_metric_aggregate_arguments,
    build_klaviyo_tool_arguments,
    get_klaviyo_access_token,
)
from .schemas import ChatRequest

TokenGetter = Callable[[str], Awaitable[str | None]]
UrlGetter = Callable[[], str]


@dataclass(frozen=True)
class McpPlatform:
    label: str
    display_name: str
    aliases: tuple[str, ...]
    hints: tuple[str, ...]
    get_token: TokenGetter
    get_url: UrlGetter


PLATFORMS = {
    "klaviyo": McpPlatform(
        label="klaviyo",
        display_name="Klaviyo",
        aliases=("klaviyo",),
        hints=(
            "campaign",
            "campaigns",
            "flow",
            "flows",
            "metric",
            "metrics",
            "email performance",
            "lists",
            "segments",
            "subscribers",
            # Confirmed live on 2026-09-23: "How many orders were placed in
            # the last 30 days?" - an ordinary question about Klaviyo's own
            # most common metric ("Placed Order") - matched none of the
            # hints above, so with multiple data platforms connected,
            # neither the hint-based fallback nor (given the same hints are
            # shown to it) the AI platform planner had any signal this was a
            # Klaviyo question, and it returned null - "no relevant tool
            # matched" - instead of ever attempting the call.
            "order",
            "orders",
            "placed order",
            "purchase",
            "purchases",
            "revenue",
            "sales",
            "opens",
            "opened",
            "clicks",
            "clicked",
            "unsubscribe",
            "unsubscribes",
            "deliverability",
            "bounce",
            "bounces",
            "sms",
            "profiles",
            # Confirmed live on 2026-09-23: "What's our email open rate
            # this month?" - a very ordinary Klaviyo engagement question -
            # matched none of the hints above either, so with multiple
            # platforms connected, platform selection returned null before
            # ever reaching tool selection (where the new
            # choose_klaviyo_report_call deterministic route now lives).
            "email",
            "open rate",
            "opened",
            "opens",
            "click rate",
            "clicked",
            "click-through rate",
            "conversion rate",
        ),
        get_token=get_klaviyo_access_token,
        get_url=lambda: os.getenv("KLAVIYO_MCP_URL", KLAVIYO_MCP_URL),
    ),
    "hubspot_crm": McpPlatform(
        label="hubspot_crm",
        display_name="HubSpot",
        aliases=("hubspot", "hubspot crm", "crm"),
        hints=(
            "contact",
            "contacts",
            "company",
            "companies",
            "deal",
            "deals",
            "ticket",
            "tickets",
            "crm",
        ),
        get_token=get_hubspot_access_token,
        get_url=hubspot_mcp_url,
    ),
    "meta_ads": McpPlatform(
        label="meta_ads",
        display_name="Meta Ads",
        aliases=("meta ads", "facebook ads", "instagram ads", "meta", "facebook"),
        hints=(
            "campaign",
            "campaigns",
            "ad set",
            "adset",
            "ads",
            "creative",
            "creatives",
            "reach",
            "impressions",
            "clicks",
            "ctr",
            "cpc",
            "cpm",
            "spend",
            "roas",
            "conversions",
            "purchases",
            "placement",
            "platform",
            "audience",
            "demographic",
            "facebook",
            "instagram",
        ),
        get_token=get_meta_ads_access_token,
        get_url=meta_ads_mcp_url,
    ),    "google_ads": McpPlatform(
        label="google_ads",
        display_name="Google Ads",
        aliases=("google ads", "google", "adwords"),
        hints=(
            "google ads",
            "campaign",
            "ad group",
            "adgroup",
            "keyword",
            "impression",
            "impressions",
            "clicks",
            "ctr",
            "cpc",
            "conversions",
            "roas",
            "spend",
            "budget",
            "ads",
            "advertising",
            "pmax",
            "performance max",
            "search ads",
        ),
        get_token=get_google_ads_access_token,
        get_url=google_ads_mcp_url,
    ),
    "google_analytics_4": McpPlatform(
        label="google_analytics_4",
        display_name="Google Analytics 4",
        aliases=("google analytics", "google analytics 4", "ga4", "analytics", "ga"),
        hints=(
            "ga4",
            "google analytics",
            "analytics",
            "website traffic",
            "traffic",
            "sessions",
            "session",
            "users",
            "active users",
            "new users",
            "pageview",
            "pageviews",
            "page views",
            "screen views",
            "top pages",
            # Confirmed live on 2026-09-24: "What are our top 5 pages by
            # views this month?" - an entirely ordinary GA4 question -
            # matched none of the hints above. "top pages" requires that
            # exact substring, so inserting a number ("top 5 pages") breaks
            # it, and bare "pages"/"views" were never hints at all. With
            # more than one platform connected this falls through to the AI
            # platform planner first, but that call itself can fail (rate
            # limit, provider outage, the same kind of transient network
            # blip logged elsewhere in this file) and silently drops back to
            # this same keyword heuristic - which must not go silent just
            # because a number was inserted into an otherwise obvious
            # phrasing.
            "pages",
            "page",
            "views",
            "view",
            "landing page",
            "bounce rate",
            "engagement",
            "engaged sessions",
            "engagement rate",
            "channel",
            "source",
            "medium",
            "referral",
            "organic",
            "direct",
            "acquisition",
            "events",
            "event",
            "conversions",
            "realtime",
            "real time",
            "device",
            "country",
            "geo",
            "visitors",
            "audience",
        ),
        get_token=get_ga4_access_token,
        get_url=ga4_mcp_url,
    ),
}

READ_ONLY_WORDS = (
    "get",
    "list",
    "search",
    "read",
    "fetch",
    "retrieve",
    "query",
    "metadata",
    "details",
)

READ_ONLY_PREFIXES = (
    "get",
    "list",
    "search",
    "read",
    "fetch",
    "retrieve",
    "query",
)

METRIC_MEASUREMENTS = ("count",)

DESTRUCTIVE_WORDS = (
    "create",
    "update",
    "delete",
    "archive",
    "upsert",
    "write",
    "send",
    "publish",
    "pause",
    "resume",
    "cancel",
    "remove",
)


def tool_action_words(name: str) -> set[str]:
    normalized = name.lower().replace("-", "_").replace("/", "_")
    return {part for part in normalized.split("_") if part}


def schema_has_write_inputs(tool: dict[str, Any]) -> bool:
    schema = get_tool_schema(tool)
    schema_text = json.dumps(schema, default=str).lower()
    return any(
        field.lower() in schema_text
        for field in (
            "createrequest",
            "updaterequest",
            "delete",
            "archive",
            "properties_to_update",
        )
    )


def parse_mcp_response(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        return response.json()

    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line.removeprefix("data:").strip()
        if payload and payload != "[DONE]":
            return json.loads(payload)
    return {}


def collect_mcp_text_values(value: Any, texts: list[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"text", "content"} and isinstance(nested, str):
                texts.append(nested)
            else:
                collect_mcp_text_values(nested, texts)
    elif isinstance(value, list):
        for item in value:
            collect_mcp_text_values(item, texts)


def parse_json_text(text: str) -> Any | None:
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def extract_klaviyo_items(result: dict[str, Any], item_type: str) -> list[dict[str, Any]]:
    texts: list[str] = []
    collect_mcp_text_values(result, texts)
    items: list[dict[str, Any]] = []
    for text in texts:
        parsed = parse_json_text(text)
        if not isinstance(parsed, dict):
            continue
        data = parsed.get("data")
        if not isinstance(data, list):
            continue
        for item in data:
            if isinstance(item, dict) and item.get("type") == item_type:
                items.append(item)
    return items


MCP_POST_RETRY_ATTEMPTS = 2
MCP_POST_RETRY_BACKOFF_SECONDS = 0.5


async def _mcp_post_with_retry(
    client: httpx.AsyncClient,
    server_url: str,
    *,
    headers: dict[str, str],
    json: dict[str, Any],
) -> httpx.Response:
    """Retries only genuinely transient transport-level failures (DNS/
    connect/read timeouts) - never HTTP error responses (4xx/5xx), which are
    real failures a retry can't fix. Confirmed live on 2026-09-23: real Ask
    AI requests to Klaviyo's MCP server (mcp.klaviyo.com, reached over the
    public internet - unlike GA4/Google Ads, which proxy through this same
    machine's own localhost:8000 and rarely see this) intermittently failed
    with `httpcore.ConnectError: [Errno 11001] getaddrinfo failed` - a
    transient DNS resolution hiccup, not a real routing/understanding
    failure. That exception propagated out of the whole pre-fetch phase and
    was caught by main.py's generic degrade-gracefully handler, which shows
    the same "No relevant read-only MCP tool matched... try naming the data
    source" message used for "nothing matched" - actively misleading here,
    since the platform and tool WERE already correctly resolved before the
    network call failed.
    """
    last_exc: Exception | None = None
    for attempt in range(MCP_POST_RETRY_ATTEMPTS + 1):
        try:
            return await client.post(server_url, headers=headers, json=json)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ReadError) as exc:
            last_exc = exc
            if attempt < MCP_POST_RETRY_ATTEMPTS:
                debug_log(
                    "    MCP POST to %s failed (%s), retrying (%d/%d)",
                    server_url, type(exc).__name__, attempt + 1, MCP_POST_RETRY_ATTEMPTS,
                )
                await asyncio.sleep(MCP_POST_RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
    assert last_exc is not None
    raise last_exc


async def initialize_mcp_session(
    client: httpx.AsyncClient,
    server_url: str,
    token: str,
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    response = await _mcp_post_with_retry(
        client,
        server_url,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": "initialize",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "braidscope", "version": "0.1.0"},
            },
        },
    )
    response.raise_for_status()
    session_id = response.headers.get("mcp-session-id")
    if session_id:
        headers["Mcp-Session-Id"] = session_id
        await _mcp_post_with_retry(
            client,
            server_url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        )
    return headers


async def list_mcp_tools(platform: McpPlatform, client_id: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    token = await platform.get_token(client_id)
    if not token:
        return [], {}

    server_url = platform.get_url()
    # Confirmed live on 2026-09-24: this makes 3 SEQUENTIAL round trips
    # (initialize, notifications/initialized, tools/list) through
    # _mcp_post_with_retry, which itself retries each one up to
    # MCP_POST_RETRY_ATTEMPTS more times - a 30s-per-attempt timeout gives a
    # worst case of up to 30s x 3 attempts x 3 calls = 270s for a genuinely
    # degraded remote MCP server (mcp.klaviyo.com, reached over the public
    # internet), which alone exceeds the frontend's 170s Ask AI request
    # timeout (web/src/lib/chat.js) before Codex/the model even starts. A
    # real, healthy call completes in low single-digit seconds, so 10s per
    # attempt keeps ample headroom while capping the degraded-connection
    # worst case near 90s.
    async with httpx.AsyncClient(timeout=10) as client:
        headers = await initialize_mcp_session(client, server_url, token)
        response = await _mcp_post_with_retry(
            client,
            server_url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": "tools-list",
                "method": "tools/list",
                "params": {},
            },
        )
        response.raise_for_status()
        payload = parse_mcp_response(response)
        tools = payload.get("result", {}).get("tools", [])
    debug_log(
        "MCP orchestrator listed platform=%s tools=%s",
        platform.label,
        [tool.get("name") for tool in tools],
    )
    return tools, headers


def connected_platforms(tool_labels: list[str]) -> list[McpPlatform]:
    return [platform for label, platform in PLATFORMS.items() if label in tool_labels]


def fallback_platform(question: str, platforms: list[McpPlatform]) -> McpPlatform | None:
    text = question.lower()
    scores: dict[str, int] = {}
    for platform in platforms:
        score = 0
        score += sum(3 for alias in platform.aliases if alias in text)
        score += sum(1 for hint in platform.hints if hint in text)
        if score:
            scores[platform.label] = score
    if not scores:
        # A connected-data question can omit the platform name. In that case
        # use the sole connected platform, but never route greetings or
        # general conversation into a live MCP call.
        data_terms = {
            "data", "source", "sources", "metric", "metrics", "report",
            "performance", "spend", "cost", "revenue", "sales", "traffic",
            "users", "sessions", "campaign", "campaigns", "ads", "advertising",
            "conversion", "conversions", "orders", "customers", "contacts",
            "deals", "analytics", "dashboard", "wasted", "waste",
        }
        if len(platforms) == 1 and set(tokenize(text)) & data_terms:
            return platforms[0]
        return None
    return PLATFORMS[max(scores, key=scores.get)]


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        stripped = re.sub(r"^json\s*", "", stripped, flags=re.IGNORECASE).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end >= start:
        stripped = stripped[start:end + 1]
    return json.loads(stripped)


def complete_planner_json(
    *,
    ai_config: dict[str, str] | None,
    instructions: str,
    user_input: str,
    max_tokens: int,
) -> dict[str, Any] | None:
    if not ai_config or ai_config.get("provider") == "anthropic":
        return None

    client_kwargs: dict[str, Any] = {
        "api_key": ai_config.get("api_key") or os.getenv("OPENAI_API_KEY"),
        "timeout": float(os.getenv("OPENAI_CHAT_TIMEOUT_SECONDS", "90")),
    }
    if ai_config.get("base_url"):
        client_kwargs["base_url"] = ai_config["base_url"]
    if not client_kwargs["api_key"]:
        return None

    client = OpenAI(**client_kwargs)
    response = client.chat.completions.create(
        model=ai_config.get("model") or os.getenv("OPENAI_CHAT_MODEL", "gpt-5"),
        messages=[
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_input},
        ],
        temperature=0,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    text = response.choices[0].message.content or ""
    return parse_json_object(text)


def choose_platform_with_ai(
    question: str,
    platforms: list[McpPlatform],
    ai_config: dict[str, str] | None = None,
) -> McpPlatform | None:
    if not platforms:
        return None
    fallback = fallback_platform(question, platforms)
    if len(platforms) == 1 and fallback:
        # Fast path: the hint/alias/data-term heuristic already found this
        # platform relevant, so skip the extra AI call.
        return fallback
    # Confirmed live on 2026-09-23: with exactly one connected platform
    # (GA4), questions like "Show me our top 10 most viewed pages this
    # month" and "Draft the monthly summary" contain none of GA4's hint
    # words or the generic data_terms set, so fallback_platform() returned
    # None and this used to short-circuit straight back to None - the user
    # got "No relevant read-only MCP tool matched" even though the question
    # was clearly about their connected GA4 data. A keyword miss does not
    # mean "not relevant" when there's a real platform to weigh it against,
    # so ask the AI planner instead of trusting the heuristic's silence.

    options = [
        {
            "label": platform.label,
            "name": platform.display_name,
            "aliases": platform.aliases,
            "hints": platform.hints,
        }
        for platform in platforms
    ]
    try:
        data = complete_planner_json(
            ai_config=ai_config,
            instructions=(
                "Select the single best connected data platform for this user query. "
                "Return only compact JSON: {\"platform\":\"label\"}. "
                "If no platform is clearly relevant, choose null."
            ),
            user_input=f"Query: {question}\nConnected platforms JSON: {json.dumps(options)}",
            max_tokens=120,
        )
        if not data:
            return fallback
        label = data.get("platform")
        if label in PLATFORMS and PLATFORMS[label] in platforms:
            return PLATFORMS[label]
    except Exception as exc:
        debug_log("MCP platform AI planner fallback due to %s", type(exc).__name__)
    return fallback


def tool_is_read_only(tool: dict[str, Any]) -> bool:
    name = str(tool.get("name", "")).lower()
    description = str(tool.get("description", "")).lower()
    action_words = tool_action_words(name)

    if schema_has_write_inputs(tool):
        return False
    if action_words & set(DESTRUCTIVE_WORDS):
        return False
    if action_words & set(READ_ONLY_PREFIXES):
        return True

    combined = f"{name} {description}"
    if any(word in name for word in DESTRUCTIVE_WORDS):
        return False
    return any(word in combined for word in READ_ONLY_WORDS)


def compact_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": tool.get("name"),
        "description": tool.get("description", ""),
        "inputSchema": tool.get("inputSchema") or tool.get("input_schema") or {},
    }


def fallback_tool_call(question: str, tools: list[dict[str, Any]]) -> dict[str, Any] | None:
    read_only_tools = [tool for tool in tools if tool_is_read_only(tool)]
    if not read_only_tools:
        return None

    best_tool = select_best_semantic_tool(question, read_only_tools)
    if not best_tool:
        return None
    return {"tool_name": best_tool.get("name"), "arguments": {}}


def ga4_tool_call(question: str, tools: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose GA4's purpose-built report from explicit query intent.

    The generic planner can confuse "users in the last 30 days" with the
    realtime active-users tool because both mention users. Date-range queries
    must use a historical report; realtime is selected only when the user
    explicitly asks for live/current activity.
    """
    if not tools:
        return None

    names = {str(tool.get("name")) for tool in tools if tool.get("name")}
    lowered = question.lower()
    realtime_terms = ("right now", "currently", "currently active", "realtime", "real-time", "live users")
    if any(term in lowered for term in realtime_terms):
        if "get_realtime_active_users" in names:
            return {"tool_name": "get_realtime_active_users", "arguments": {}}
        return None

    # Confirmed live on 2026-09-23: broad terms like "sessions"/"visitors"/
    # "total users" appear in nearly every GA4 question (landing-page,
    # channel, device, campaign questions all mention "sessions"). When the
    # generic get_traffic_overview check ran first, it stole every one of
    # those questions and returned the same site-wide KPI answer regardless
    # of what was actually asked. Specific report routes must be checked
    # first, ordered most-specific first, with get_traffic_overview only as
    # the last, generic fallback.
    specific_routes: list[tuple[str, tuple[str, ...]]] = [
        ("get_landing_pages", ("landing page", "landing pages")),
        (
            "get_top_pages",
            ("top pages", "top page", "most viewed page", "most viewed pages", "popular pages", "most visited page"),
        ),
        ("get_device_breakdown", ("device", "devices", "mobile vs desktop", "desktop vs mobile", "tablet")),
        (
            "get_geo_breakdown",
            ("by country", "by countries", "geo breakdown", "geography", "which country", "countries", "by region"),
        ),
        ("get_campaign_performance", ("campaign", "campaigns")),
        (
            "get_source_medium",
            ("source/medium", "source and medium", "by source", "utm source", "utm medium"),
        ),
        ("get_user_acquisition", ("user acquisition", "how users found", "acquisition channel")),
        (
            "get_ecommerce_report",
            ("ecommerce", "e-commerce", "items purchased", "item revenue"),
        ),
        ("get_revenue_overview", ("revenue", "purchase revenue", "transactions")),
        ("get_key_events_report", ("key event", "key events", "conversion event", "conversions")),
        ("get_events_report", ("event count", "events report", "which events", "most frequent events")),
        (
            "get_traffic_by_day",
            ("by day", "daily traffic", "each day", "per day", "day by day", "trend over time"),
        ),
        (
            "get_traffic_sources",
            (
                "traffic source",
                "traffic sources",
                "traffic channel",
                "traffic channels",
                "channels",
                "channel",
                "acquisition",
            ),
        ),
    ]
    for tool_name, terms in specific_routes:
        if tool_name in names and any(term in lowered for term in terms):
            return {"tool_name": tool_name, "arguments": {}}

    if "get_traffic_overview" in names and any(
        term in lowered
        for term in (
            "how many users",
            "number of users",
            "total users",
            "active users",
            "new users",
            "sessions",
            "visitors",
            "visited the website",
            "website traffic",
            "total traffic",
            "overall traffic",
        )
    ):
        return {"tool_name": "get_traffic_overview", "arguments": {}}

    return None


def hubspot_object_search_tool_call(
    question: str,
    platform: McpPlatform,
    tools: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if platform.label != "hubspot_crm" or not wants_hubspot_object(question):
        return None

    search_tool = find_tool_by_name(tools, ("search_crm_objects",))
    if not search_tool or not tool_is_read_only(search_tool):
        return None

    return {"tool_name": str(search_tool.get("name")), "arguments": {}}


def tokenize(text: str) -> set[str]:
    cleaned = "".join(char.lower() if char.isalnum() else " " for char in text)
    words = {word for word in cleaned.split() if len(word) >= 3}
    expanded = set(words)
    for word in words:
        if word.endswith("ies") and len(word) > 4:
            expanded.add(f"{word[:-3]}y")
        if word.endswith("s") and len(word) > 3:
            expanded.add(word[:-1])
    return expanded


def get_tool_schema(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    return schema if isinstance(schema, dict) else {}


def required_schema_fields(tool: dict[str, Any]) -> list[str]:
    required = get_tool_schema(tool).get("required", [])
    return [str(field) for field in required if field]


def selected_tool_is_usable(tool: dict[str, Any], arguments: dict[str, Any]) -> bool:
    for field in required_schema_fields(tool):
        if field == "model":
            continue
        if field not in arguments or arguments[field] in (None, "", []):
            return False
    return True


def query_mentions_identifier(question: str) -> bool:
    text = question.lower()
    identifier_terms = (" id", " ids", "metric id", "flow id", "campaign id", "conversion")
    return any(term in f" {text}" for term in identifier_terms)


def query_wants_metric_performance(question: str) -> bool:
    text = question.lower()
    if "metric" in text and any(
        term in text for term in ("performance", "last 30", "last_30", "past 30", "count", "aggregate")
    ):
        return True
    # Confirmed live on 2026-09-23: "How many orders were placed in the last
    # 30 days?" - an ordinary, natural way to ask for exactly what
    # query_metric_aggregates answers - contains none of the trigger words
    # above (no literal "metric"), so this deterministic path was silently
    # never attempted for it at all, leaving the generic AI-selected planner
    # to guess both the tool AND its deeply nested body construction
    # unaided. A quantity phrase combined with a time-range phrase is a
    # reliable enough signal on its own; find_metric_for_question() already
    # safely no-ops (returns the metrics list instead of guessing) when
    # nothing in the question matches a real metric name.
    quantity_phrases = ("how many", "how much", "count of", "number of", "total number")
    time_phrases = ("last ", "past ", "this month", "this week", "this quarter", "today", "yesterday")
    if any(phrase in text for phrase in quantity_phrases) and any(phrase in text for phrase in time_phrases):
        return True
    # Confirmed live on 2026-09-24: "What's our Placed Order revenue over
    # the last 30 days?" - naming a real Klaviyo metric ("Placed Order
    # revenue") plus a time range, the single most natural way to ask this -
    # contains no quantity phrase at all ("how much"/"how many"/...), only a
    # value-asking opener ("what's our"). Without this, the deterministic
    # aggregate path never even attempted a match, and the generic planner
    # picked an unrelated bulk-job tool for Klaviyo's real 122-tool catalog
    # instead. find_candidate_metrics_for_question() already safely no-ops
    # (returns the metrics list instead of guessing) when nothing in the
    # question matches a real metric name, so broadening this trigger can't
    # make a wrong metric guess - only decide whether to try at all.
    value_phrases = (
        "what's our", "what is our", "what was our", "what were our",
        "show me our", "show our",
    )
    return any(phrase in text for phrase in value_phrases) and any(phrase in text for phrase in time_phrases)


def semantic_tool_score(question: str, tool: dict[str, Any]) -> int:
    query_terms = tokenize(question)
    tool_text = f"{tool.get('name', '')} {tool.get('description', '')}".lower()
    tool_terms = tokenize(tool_text)
    score = len(query_terms & tool_terms) * 4

    if "account" in tool_terms and "account" not in query_terms:
        score -= 12
    if "report" in tool_terms and not query_mentions_identifier(question):
        score -= 8
    if required_schema_fields(tool) and not query_mentions_identifier(question):
        score -= 4
    if any(term in tool_terms for term in ("list", "search", "get", "retrieve")):
        score += 2
    return score


def tool_matches_question(question: str, tool: dict[str, Any]) -> bool:
    query_terms = tokenize(question)
    tool_terms = tokenize(f"{tool.get('name', '')} {tool.get('description', '')}")
    requested_topics = query_terms & {
        "campaign",
        "flow",
        "metric",
        "performance",
        "contact",
        "company",
        "deal",
        "list",
        "segment",
    }
    if requested_topics:
        return bool(requested_topics & tool_terms)

    overlap = query_terms & tool_terms
    if overlap:
        return True
    return True


def select_best_semantic_tool(question: str, tools: list[dict[str, Any]]) -> dict[str, Any] | None:
    best_tool = None
    best_score = -1000
    for tool in tools:
        if required_schema_fields(tool) and not query_mentions_identifier(question):
            required = set(required_schema_fields(tool))
            if required - {"model", "limit", "channel"}:
                continue
        score = semantic_tool_score(question, tool)
        if score > best_score:
            best_score = score
            best_tool = tool
    return best_tool if best_score > -1000 else None


def find_tool_by_name(tools: list[dict[str, Any]], names: tuple[str, ...]) -> dict[str, Any] | None:
    tool_by_name = {str(tool.get("name")): tool for tool in tools if tool.get("name")}
    for name in names:
        if name in tool_by_name:
            return tool_by_name[name]
    return None


def iso_start_datetime_days_ago(days: int) -> str:
    target = datetime.now(timezone.utc) - timedelta(days=days)
    return target.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_end_datetime_today() -> str:
    return datetime.now(timezone.utc).replace(
        hour=23,
        minute=59,
        second=59,
        microsecond=0,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_date_boundary(date_text: str, is_end: bool = False) -> str:
    parsed = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if is_end:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=0)
    else:
        parsed = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_date_range_from_question(question: str) -> tuple[str, str] | None:
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", question)
    if len(dates) < 2:
        return None

    start_date, end_date = dates[0], dates[1]
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return None

    if start > end:
        start_date, end_date = end_date, start_date
    return iso_date_boundary(start_date), iso_date_boundary(end_date, is_end=True)


def metric_date_range_for_question(question: str) -> tuple[str, str]:
    explicit_range = extract_date_range_from_question(question)
    if explicit_range:
        return explicit_range
    return iso_start_datetime_days_ago(30), iso_end_datetime_today()


def metric_match_score(question: str, metric: dict[str, Any]) -> int:
    attributes = metric.get("attributes") if isinstance(metric.get("attributes"), dict) else {}
    name = str(attributes.get("name", ""))
    if not name:
        return 0
    question_lower = question.lower()
    name_lower = name.lower()
    if name_lower in question_lower:
        return 100
    return len(tokenize(question) & tokenize(name)) * 10


def find_candidate_metrics_for_question(
    question: str, metrics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """All metrics tied at the best match score, not just the first one
    found. Confirmed live on 2026-09-23: a real account had TWO metrics
    both literally named "Placed Order" - one from a since-abandoned Wix
    integration (zero activity in over a year) and one from the account's
    current, active Shopify integration (32 real orders in the last 30
    days). Both score identically (exact name match), so picking only "the
    first" is an arbitrary coin flip between a dead metric and the real one
    - confirmed live this picked the dead Wix one and answered "0 orders"
    while the account's own Klaviyo dashboard showed real recent revenue.
    """
    best_score = 0
    candidates: list[dict[str, Any]] = []
    for metric in metrics:
        score = metric_match_score(question, metric)
        if score < 10:
            continue
        if score > best_score:
            best_score = score
            candidates = [metric]
        elif score == best_score:
            candidates.append(metric)
    return candidates


def find_metric_for_question(question: str, metrics: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = find_candidate_metrics_for_question(question, metrics)
    return candidates[0] if candidates else None


def _klaviyo_aggregate_result_has_activity(result: dict[str, Any]) -> bool:
    """True if a query_metric_aggregates tool result contains at least one
    positive measurement value anywhere in its time series - used to tell a
    genuinely live metric apart from a same-named but dormant/abandoned one
    (see find_candidate_metrics_for_question's docstring for the real case
    this closes)."""
    texts: list[str] = []
    collect_mcp_text_values(result, texts)
    for text in texts:
        parsed = parse_json_text(text)
        if not isinstance(parsed, dict):
            continue
        data = parsed.get("data")
        if not isinstance(data, dict):
            continue
        attributes = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
        series = attributes.get("data") if isinstance(attributes.get("data"), list) else []
        for entry in series:
            if not isinstance(entry, dict):
                continue
            measurements = entry.get("measurements") if isinstance(entry.get("measurements"), dict) else {}
            for values in measurements.values():
                if isinstance(values, list) and any(
                    isinstance(value, (int, float)) and value > 0 for value in values
                ):
                    return True
    return False


async def select_active_klaviyo_metric(
    platform: McpPlatform,
    client_id: str,
    aggregate_tool_name: str,
    candidates: list[dict[str, Any]],
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """When multiple same-named metric candidates exist, probe each with a
    real aggregate call over the question's own date range and prefer the
    first with actual activity, instead of an arbitrary pick. Falls back to
    the first candidate if none show activity (keeps behavior deterministic
    rather than guessing further)."""
    if len(candidates) == 1:
        return candidates[0]
    for candidate in candidates:
        metric_id = candidate.get("id")
        if not metric_id:
            continue
        args = build_klaviyo_metric_aggregate_arguments(metric_id, start_date, end_date)
        try:
            result = await execute_mcp_tool(platform, client_id, "probe metric activity", aggregate_tool_name, args)
        except Exception as exc:  # noqa: BLE001 - best-effort probe, never fatal
            debug_log("    select_active_klaviyo_metric: probe failed for %s: %s", metric_id, exc)
            continue
        if _klaviyo_aggregate_result_has_activity(result):
            debug_log("    select_active_klaviyo_metric: %s (%s) has real activity, selected", metric_id, candidate.get("attributes", {}).get("name"))
            return candidate
    debug_log("    select_active_klaviyo_metric: no candidate had activity, defaulting to first")
    return candidates[0]


async def choose_klaviyo_metric_aggregate_call(
    question: str,
    platform: McpPlatform,
    client_id: str,
    tools: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if platform.label != "klaviyo" or not query_wants_metric_performance(question):
        return None

    metrics_tool = find_tool_by_name(tools, ("klaviyo_get_metrics", "get_metrics"))
    aggregate_tool = find_tool_by_name(
        tools,
        ("klaviyo_query_metric_aggregates", "query_metric_aggregates"),
    )
    if not metrics_tool:
        return None

    metrics_result = await execute_mcp_tool(
        platform,
        client_id,
        "What Klaviyo metrics are available?",
        str(metrics_tool.get("name")),
        {},
    )
    metrics = extract_klaviyo_items(metrics_result, "metric")
    candidates = find_candidate_metrics_for_question(question, metrics)
    if not candidates:
        return {
            "tool_name": str(metrics_tool.get("name")),
            "arguments": {},
            "prefetched_result": metrics_result,
            "note": "No matching metric name was found, so the metrics list was returned.",
        }

    start_date, end_date = metric_date_range_for_question(question)

    if len(candidates) > 1 and aggregate_tool:
        metric = await select_active_klaviyo_metric(
            platform, client_id, str(aggregate_tool.get("name")), candidates, start_date, end_date,
        )
    else:
        metric = candidates[0]

    metric_id = metric.get("id")
    if not metric_id or not aggregate_tool:
        return {
            "tool_name": str(metrics_tool.get("name")),
            "arguments": {},
            "prefetched_result": metrics_result,
            "note": f"Matched metric {metric_id or '(missing id)'}, but no aggregate tool is available.",
        }

    return {
        "tool_name": str(aggregate_tool.get("name")),
        "arguments": build_klaviyo_metric_aggregate_arguments(
            metric_id, start_date, end_date, measurements=list(METRIC_MEASUREMENTS),
        ),
        "resolved_metric": {
            "id": metric_id,
            "name": (metric.get("attributes") or {}).get("name"),
        },
    }


KLAVIYO_REPORT_STATISTICS_TERMS: dict[str, tuple[str, ...]] = {
    "open_rate": ("open rate", "opened rate", "opens rate"),
    "click_rate": ("click rate", "click-through rate", "clickthrough rate", "ctr", "clicked rate"),
    "click_to_open_rate": ("click to open rate", "click-to-open rate"),
    "conversion_rate": ("conversion rate",),
    "bounce_rate": ("bounce rate",),
    "unsubscribe_rate": ("unsubscribe rate", "unsub rate"),
    "delivery_rate": ("delivery rate", "deliverability"),
    "spam_complaint_rate": ("spam complaint rate", "spam rate"),
}

KLAVIYO_RANKING_TERMS = (
    "which flow", "which campaign", "top flow", "top campaign",
    "best flow", "best campaign", "most revenue", "most conversions",
    "most orders", "drove the most",
)

# Confirmed live on 2026-09-24: "How did our last email campaign perform?" -
# an entirely ordinary, generic way to ask for a campaign report - names no
# specific rate statistic (no "open rate"/"click rate"/...) and asks for no
# ranking ("which campaign"/"top campaign"/...), so it matched neither
# KLAVIYO_REPORT_STATISTICS_TERMS nor KLAVIYO_RANKING_TERMS and
# choose_klaviyo_report_call() never even attempted a match - despite
# already defaulting its "statistics" argument to ["conversions",
# "conversion_rate"] for exactly this "no specific stat named" case. The
# gate was stricter than the function's own fallback behavior needed.
KLAVIYO_GENERIC_PERFORMANCE_TERMS = ("perform", "performance", "results", "how did", "doing")

KLAVIYO_TIMEFRAME_KEYS: dict[str, str] = {
    "today": "today",
    "yesterday": "yesterday",
    "this week": "this_week",
    "this month": "this_month",
    "this year": "this_year",
    "last week": "last_week",
    "last month": "last_month",
    "last year": "last_year",
    "last 7 days": "last_7_days",
    "last 30 days": "last_30_days",
    "last 90 days": "last_90_days",
    "last 3 months": "last_3_months",
    "last 12 months": "last_12_months",
    "last 365 days": "last_365_days",
}


def klaviyo_requested_statistics(question: str) -> list[str]:
    lowered = question.lower()
    return [
        stat for stat, terms in KLAVIYO_REPORT_STATISTICS_TERMS.items()
        if any(term in lowered for term in terms)
    ]


def klaviyo_wants_ranking_by_flow_or_campaign(question: str) -> bool:
    lowered = question.lower()
    return any(term in lowered for term in KLAVIYO_RANKING_TERMS)


def klaviyo_wants_generic_performance(question: str) -> bool:
    lowered = question.lower()
    mentions_campaign_or_flow = any(term in lowered for term in ("campaign", "flow", "email"))
    return mentions_campaign_or_flow and any(term in lowered for term in KLAVIYO_GENERIC_PERFORMANCE_TERMS)


def klaviyo_report_scope(question: str) -> str:
    """"flow" or "campaign" - whichever this question is about. Defaults to
    campaign, since most bare engagement-rate questions ("open rate",
    "click rate") are naturally about campaigns rather than flows."""
    lowered = question.lower()
    mentions_flow = "flow" in lowered
    mentions_campaign = "campaign" in lowered
    if mentions_flow and not mentions_campaign:
        return "flow"
    return "campaign"


def klaviyo_timeframe_key_for_question(question: str) -> str:
    lowered = question.lower()
    for phrase, key in KLAVIYO_TIMEFRAME_KEYS.items():
        if phrase in lowered:
            return key
    return "last_30_days"


def find_metrics_by_exact_name(metrics: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    target = name.lower()
    return [
        metric for metric in metrics
        if str((metric.get("attributes") or {}).get("name", "")).lower() == target
    ]


async def choose_klaviyo_report_call(
    question: str,
    platform: McpPlatform,
    client_id: str,
    tools: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Deterministic routing for Klaviyo's campaign/flow report tools -
    engagement rates (open rate, click rate, ...) and "which flow/campaign
    drove the most revenue/conversions" ranking questions.

    Confirmed live on 2026-09-23: with Klaviyo's real 122-tool catalog
    handed to the generic AI tool-selection planner in one shot, it picked
    completely unrelated tools for these exact question shapes -
    `get_bulk_suppress_profiles_jobs` for a flow-revenue question,
    `list_agent_knowledge` for an email-open-rate question. The existing
    semantic fallback scorer (select_best_semantic_tool) doesn't fix this
    either: it systematically penalizes tools whose name contains "report"
    and tools with required schema fields (a heuristic built to avoid
    detail/report tools that need an ID the user hasn't given), which
    describes get_campaign_report/get_flow_report almost by definition -
    confirmed live these tools didn't even appear in the scorer's own top
    10 for either question. A dedicated deterministic route, mirroring
    ga4_tool_call's approach for GA4, is needed instead of trying to tune a
    generic scorer to correctly rank a 122-tool catalog it wasn't designed
    for.
    """
    if platform.label != "klaviyo":
        return None

    requested_stats = klaviyo_requested_statistics(question)
    wants_ranking = klaviyo_wants_ranking_by_flow_or_campaign(question)
    if not requested_stats and not wants_ranking and not klaviyo_wants_generic_performance(question):
        return None

    scope = klaviyo_report_scope(question)
    tool_name = "get_flow_report" if scope == "flow" else "get_campaign_report"
    report_tool = find_tool_by_name(tools, (tool_name,))
    metrics_tool = find_tool_by_name(tools, ("get_metrics",))
    aggregate_tool = find_tool_by_name(tools, ("query_metric_aggregates",))
    if not report_tool or not metrics_tool:
        return None

    # get_campaign_report/get_flow_report both require a conversion_metric_id
    # - their own schema description says to default to the "Placed Order"
    # metric when the user hasn't named one. Reuses the same duplicate-name
    # -safe resolution as the order-count path (see
    # find_candidate_metrics_for_question's docstring for the real Wix/
    # Shopify duplicate-metric case this guards against here too).
    metrics_result = await execute_mcp_tool(
        platform, client_id, question, str(metrics_tool.get("name")), {},
    )
    metrics = extract_klaviyo_items(metrics_result, "metric")
    conversion_candidates = find_metrics_by_exact_name(metrics, "Placed Order")
    conversion_metric_id = None
    if conversion_candidates:
        if len(conversion_candidates) > 1 and aggregate_tool:
            preset_start, preset_end = metric_date_range_for_question(question)
            resolved = await select_active_klaviyo_metric(
                platform, client_id, str(aggregate_tool.get("name")),
                conversion_candidates, preset_start, preset_end,
            )
        else:
            resolved = conversion_candidates[0]
        conversion_metric_id = resolved.get("id")
    elif metrics:
        conversion_metric_id = metrics[0].get("id")

    if not conversion_metric_id:
        return {
            "tool_name": str(metrics_tool.get("name")),
            "arguments": {},
            "prefetched_result": metrics_result,
            "note": "No Klaviyo metrics were found to use as the report's conversion metric.",
        }

    arguments = build_klaviyo_tool_arguments(
        tool_name,
        {
            "statistics": requested_stats or ["conversions", "conversion_rate"],
            "conversion_metric_id": conversion_metric_id,
            "timeframe": {"key": klaviyo_timeframe_key_for_question(question)},
        },
    )
    if wants_ranking:
        arguments["group_by"] = (
            ["flow_id", "flow_name"] if scope == "flow"
            else ["campaign_id", "campaign_message_name"]
        )

    return {
        "tool_name": tool_name,
        "arguments": arguments,
        "resolved_metric": {"id": conversion_metric_id, "name": "Placed Order"},
    }


def choose_tool_with_ai(
    question: str,
    platform: McpPlatform,
    tools: list[dict[str, Any]],
    ai_config: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    allowed_tools = [tool for tool in tools if tool_is_read_only(tool)]
    if not allowed_tools:
        return None

    if platform.label == "google_analytics_4":
        deterministic_ga4_call = ga4_tool_call(question, allowed_tools)
        if deterministic_ga4_call:
            return deterministic_ga4_call

    deterministic_tool_call = hubspot_object_search_tool_call(
        question,
        platform,
        allowed_tools,
    )
    if deterministic_tool_call:
        return deterministic_tool_call

    tool_summaries = [compact_tool(tool) for tool in allowed_tools]
    try:
        data = complete_planner_json(
            ai_config=ai_config,
            instructions=(
                "You are selecting a read-only MCP tool call. "
                "Use only the provided tools and their input schemas. "
                "Return only compact JSON with this shape: "
                "{\"tool_name\":\"name\",\"arguments\":{}}. "
                "Never choose create, update, delete, archive, send, publish, or write tools. "
                "If the user has not provided required IDs such as metric IDs, flow IDs, "
                "campaign IDs, or conversion metric IDs, choose a discovery/list/search tool "
                "instead of a report/detail tool that requires those IDs."
            ),
            user_input=(
                f"Platform: {platform.display_name}\n"
                f"User query: {question}\n"
                f"Available read-only tools JSON: {json.dumps(tool_summaries, default=str)}"
            ),
            max_tokens=700,
        )
        if not data:
            return fallback_tool_call(question, allowed_tools)
        selected_name = data.get("tool_name")
        arguments = data.get("arguments") or {}
        names = {tool.get("name") for tool in allowed_tools}
        if selected_name in names and isinstance(arguments, dict):
            selected_tool = next(
                tool for tool in allowed_tools if tool.get("name") == selected_name
            )
            normalized_arguments = normalize_arguments(
                platform,
                question,
                str(selected_name),
                arguments,
            )
            if selected_tool_is_usable(
                selected_tool,
                normalized_arguments,
            ) and tool_matches_question(question, selected_tool):
                return {"tool_name": selected_name, "arguments": arguments}
            debug_log(
                "MCP planner selected unusable/mismatched tool=%s required=%s args=%s",
                selected_name,
                required_schema_fields(selected_tool),
                normalized_arguments,
            )
    except Exception as exc:
        debug_log("MCP tool AI planner fallback due to %s", type(exc).__name__)
    return fallback_tool_call(question, allowed_tools)


def wants_hubspot_object(question: str) -> str | None:
    text = question.lower()
    if "compan" in text:
        return "companies"
    if "deal" in text:
        return "deals"
    if "contact" in text:
        return "contacts"
    if "ticket" in text:
        return "tickets"
    return None


def hubspot_default_properties(object_type: str, question: str) -> list[str]:
    if object_type == "contacts":
        properties = ["firstname", "lastname", "email", "company", "phone", "createdate"]
        if "last modified" in question.lower() or "modified" in question.lower():
            properties.append("lastmodifieddate")
        return properties
    if object_type == "companies":
        return ["name", "domain", "industry", "city", "createdate", "hs_lastmodifieddate"]
    if object_type == "deals":
        return ["dealname", "dealstage", "amount", "closedate", "createdate"]
    if object_type == "tickets":
        return ["subject", "content", "hs_pipeline_stage", "createdate"]
    return []


def normalize_hubspot_arguments(question: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(arguments)
    if "search_crm_objects" not in tool_name:
        return normalized

    object_type = normalized.get("objectType") or normalized.get("object_type")
    inferred_object_type = wants_hubspot_object(question)
    if inferred_object_type:
        object_type = inferred_object_type
    if object_type:
        normalized["objectType"] = str(object_type)
        normalized.pop("object_type", None)

    if normalized.get("objectType") and not normalized.get("properties"):
        normalized["properties"] = hubspot_default_properties(
            normalized["objectType"],
            question,
        )

    normalized.setdefault("limit", 10)
    return normalized


def normalize_klaviyo_arguments(question: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Only adds the one field every real Klaviyo tool actually requires
    ("model", via build_klaviyo_tool_arguments) and otherwise passes
    arguments through unchanged.

    This function used to blindly inject a top-level "channel" field for
    any tool name containing "campaign", and a top-level "limit" field for
    any tool name not containing "aggregate"/"report". Confirmed live on
    2026-09-23 against the real Klaviyo MCP server's actual schemas: neither
    field exists on get_campaigns, get_campaign_report, or get_account_details
    (get_account_details has no "limit" property at all and its call was
    rejected live with "Unexpected keyword argument: limit" once the "model"
    bug above was fixed) - these tools use entirely different real
    mechanisms instead (get_campaigns requires a `filter` string;
    get_campaign_report requires `statistics`/`conversion_metric_id`;
    pagination is `page_size`, not `limit`). Blindly adding fields a
    schema doesn't declare breaks calls outright rather than helping them,
    given Klaviyo's schemas consistently reject unknown properties. The AI
    tool-selection planner already receives each tool's full real schema
    (see compact_tool()) and is instructed to build arguments from it, so
    it - not blind field injection - is responsible for supplying
    tool-specific required fields correctly.
    """
    return build_klaviyo_tool_arguments(tool_name, arguments)


def normalize_google_ads_arguments(question: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(arguments)
    lowered_name = tool_name.lower()
    if "search" in lowered_name and not normalized.get("query"):
        normalized.setdefault("query", "SELECT campaign.id, campaign.name, campaign.status FROM campaign LIMIT 10")
    return normalized


def normalize_arguments(
    platform: McpPlatform,
    question: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if platform.label == "hubspot_crm":
        return normalize_hubspot_arguments(question, tool_name, arguments)
    if platform.label == "klaviyo":
        return normalize_klaviyo_arguments(question, tool_name, arguments)
    if platform.label == "google_ads":
        return normalize_google_ads_arguments(question, tool_name, arguments)
    return arguments


async def resolve_ga4_property_id(client_id: str) -> str | None:
    """Ask AI's GA4 tool calls previously never specified a property_id at
    all, letting ga4_mcp.py's own fallback (_resolve_property: use
    property_id if given, else whichever property Google's Admin API lists
    FIRST) silently pick one - completely arbitrary, and not necessarily the
    same property the GA4 Dashboard feature uses. Confirmed live on
    2026-09-23: a real client's connected Google account had 11 GA4
    properties under one "Vote Rev" account. Ask AI's realtime-active-users
    tool queried "Postcards for Friends" (508793074, essentially no traffic)
    and answered "0 active users", while the Dashboard - which DOES pick
    intelligently, see ga4_dashboard.py's _select_property(): it probes
    every property for real report data and picks the one that actually has
    some - was showing "All In VA" (511575139, 6 active users). Two
    different properties, two different answers, for the same client and
    the same underlying Google connection.

    Reuses build_ga4_dashboard()'s existing smart selection AND its
    Supabase cache (analytics_ga4_cache) rather than re-probing every
    property on every chat message - a cache hit here costs one cheap
    Supabase read, not N parallel Google API report calls - so Ask AI and
    the Dashboard are guaranteed to agree on which property is "the" one
    for this client.
    """
    try:
        dashboard = await build_ga4_dashboard(client_id)
    except Exception as exc:  # noqa: BLE001 - best-effort resolution, never fatal to the chat turn
        debug_log(
            "    resolve_ga4_property_id(client_id=%s): failed: %s: %s",
            client_id, type(exc).__name__, exc,
        )
        return None
    selected = dashboard.get("selected_property") if isinstance(dashboard, dict) else None
    property_id = selected.get("id") if isinstance(selected, dict) else None
    debug_log("    resolve_ga4_property_id(client_id=%s): resolved property_id=%s", client_id, property_id)
    return property_id


async def resolve_google_ads_customer_id(client_id: str) -> str | None:
    """Ask AI's Google Ads tool calls previously never specified a
    customer_id, letting google_ads_mcp.py's own fallback
    (_resolve_cid: use customer_id if given, else customers[0] from
    listAccessibleCustomers) pick one with zero regard for whether it's a
    manager account, a disabled account, or a real usable customer.
    Confirmed live on 2026-09-23: a real client's connection had exactly two
    accessible customers - a manager (MCC) account with zero linked
    children, and a separate account Google's own API reports as
    CUSTOMER_NOT_ENABLED - and listAccessibleCustomers happened to return
    the disabled one first, so every Ask AI Google Ads question hit
    CUSTOMER_NOT_ENABLED directly.

    Reuses resolve_google_ads_customer()'s canonical, verified selection
    (google_ads_dashboard.py) - the exact same function the Dashboard now
    calls - so Ask AI and the Dashboard are guaranteed to agree on which
    customer is "the" one for this client, the same guarantee
    resolve_ga4_property_id gives for GA4.

    ID-only convenience wrapper: execute_mcp_tool() below calls
    resolve_google_ads_customer() directly instead of this, because on
    failure it needs the resolver's full combined reason (not just None) to
    surface a real answer instead of letting google_ads_mcp.py's own
    fallback rediscover a less complete error on its own. This wrapper
    exists for any caller that only needs the ID.
    """
    try:
        resolution = await resolve_google_ads_customer(client_id)
    except Exception as exc:  # noqa: BLE001 - best-effort resolution, never fatal to the chat turn
        debug_log(
            "    resolve_google_ads_customer_id(client_id=%s): failed: %s: %s",
            client_id, type(exc).__name__, exc,
        )
        return None
    customer_id = resolution.get("customer_id") if resolution.get("ok") else None
    debug_log(
        "    resolve_google_ads_customer_id(client_id=%s): resolved customer_id=%s (ok=%s, reason_code=%s)",
        client_id, customer_id, resolution.get("ok"), resolution.get("reason_code"),
    )
    return customer_id


async def execute_mcp_tool(
    platform: McpPlatform,
    client_id: str,
    question: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    token = await platform.get_token(client_id)
    if not token:
        return {"error": f"{platform.display_name} MCP token is missing"}

    # GA4 only: every report tool needs to know WHICH property to query when
    # the connected Google account has more than one (see
    # resolve_ga4_property_id's docstring for the real, live-confirmed bug
    # this closes). "list_properties" is excluded deliberately - it lists
    # every property across the account, so a single resolved property_id
    # doesn't apply to it. An explicit property_id the caller already
    # supplied is never overridden.
    if (
        platform.label == "google_analytics_4"
        and tool_name != "list_properties"
        and not arguments.get("property_id")
    ):
        resolved_property_id = await resolve_ga4_property_id(client_id)
        if resolved_property_id:
            arguments = {**arguments, "property_id": resolved_property_id}

    # Google Ads only: same reasoning as GA4 above - see
    # resolve_google_ads_customer_id's docstring for the real, live-confirmed
    # bug this closes. "list_accessible_customers" is excluded deliberately -
    # it lists every customer across the account, so a single resolved
    # customer_id doesn't apply to it. An explicit customer_id the caller
    # already supplied is never overridden.
    #
    # When resolution genuinely finds no usable customer, this returns the
    # resolver's own combined, evidence-based reason (e.g. "X is a manager
    # with no ad accounts linked AND Y is not enabled") directly - instead
    # of silently proceeding with no customer_id, which would let
    # google_ads_mcp.py's own naive customers[0] fallback pick again and
    # surface a less complete error than the one already computed here.
    # Confirmed live on 2026-09-23: without this short-circuit, Ask AI's
    # error message named only the disabled account and omitted the
    # childless-manager finding that the Dashboard's identical resolution
    # already reports.
    if platform.label == "google_ads" and tool_name != "list_accessible_customers":
        if arguments.get("customer_id"):
            pass
        else:
            resolution = await resolve_google_ads_customer(client_id)
            if resolution.get("ok"):
                arguments = {**arguments, "customer_id": resolution["customer_id"]}
            else:
                return {"error": resolution.get("reason") or "No usable Google Ads customer found for this client."}

    server_url = platform.get_url()
    # Same reasoning as list_mcp_tools' timeout above: this makes 3
    # sequential round trips (initialize, notifications/initialized,
    # tools/call) through _mcp_post_with_retry, each retried up to
    # MCP_POST_RETRY_ATTEMPTS more times. The previous 45s-per-attempt value
    # gave a worst case of up to 45s x 3 attempts x 3 calls = 405s for a
    # degraded remote MCP server - on top of whatever list_mcp_tools already
    # spent resolving the tool to call, comfortably enough on its own to
    # blow past the frontend's 170s Ask AI request timeout.
    async with httpx.AsyncClient(timeout=10) as client:
        headers = await initialize_mcp_session(client, server_url, token)
        response = await _mcp_post_with_retry(
            client,
            server_url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": "tool-call",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": normalize_arguments(platform, question, tool_name, arguments),
                },
            },
        )
        response.raise_for_status()
        return parse_mcp_response(response)


async def maybe_get_orchestrated_mcp_result(
    request: ChatRequest,
    client_context: dict[str, Any],
    tools: list[dict[str, Any]],
    ai_config: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    tool_labels = [str(tool.get("server_label")) for tool in tools]
    platforms = connected_platforms(tool_labels)
    t0 = time.monotonic()
    platform = choose_platform_with_ai(request.question, platforms, ai_config)
    debug_log("    choose_platform_with_ai: %.2fs -> %s", time.monotonic() - t0, platform.label if platform else None)
    if not platform:
        return None

    t0 = time.monotonic()
    available_tools, _headers = await list_mcp_tools(platform, client_context["id"])
    debug_log("    list_mcp_tools(%s): %.2fs -> %d tools", platform.label, time.monotonic() - t0, len(available_tools))

    t0 = time.monotonic()
    metric_tool_call = await choose_klaviyo_metric_aggregate_call(
        request.question,
        platform,
        client_context["id"],
        available_tools,
    )
    report_tool_call = None
    if not metric_tool_call:
        report_tool_call = await choose_klaviyo_report_call(
            request.question,
            platform,
            client_context["id"],
            available_tools,
        )
    tool_call = metric_tool_call or report_tool_call or choose_tool_with_ai(
        request.question,
        platform,
        available_tools,
        ai_config,
    )
    debug_log("    tool selection: %.2fs -> %s", time.monotonic() - t0, (tool_call or {}).get("tool_name"))
    if not tool_call:
        return {
            "server_label": platform.label,
            "intent": "dynamic_tool_selection",
            "result": {
                "error": "No safe read-only MCP tool matched this request.",
                "available_tools": [tool.get("name") for tool in available_tools],
            },
        }

    if tool_call.get("prefetched_result"):
        return {
            "server_label": platform.label,
            "intent": "dynamic_tool_selection",
            "selected_tool": tool_call["tool_name"],
            "selected_arguments": tool_call["arguments"],
            "note": tool_call.get("note"),
            "result": tool_call["prefetched_result"],
        }

    debug_log(
        "MCP orchestrator executing platform=%s tool=%s args=%s",
        platform.label,
        tool_call["tool_name"],
        tool_call["arguments"],
    )
    t0 = time.monotonic()
    result = await execute_mcp_tool(
        platform,
        client_context["id"],
        request.question,
        tool_call["tool_name"],
        tool_call["arguments"],
    )
    elapsed = time.monotonic() - t0
    result_payload = result.get("result") if isinstance(result, dict) else None
    debug_log(
        "TOOL_CALL_COMPLETED platform=%s tool=%s duration=%.2fs success=%s result_keys=%s",
        platform.label,
        tool_call["tool_name"],
        elapsed,
        not (isinstance(result_payload, dict) and result_payload.get("error")),
        sorted(result_payload.keys()) if isinstance(result_payload, dict) else type(result_payload).__name__,
    )
    debug_log(
        "TOOL_RESULT_NORMALIZED platform=%s tool=%s payload_type=%s",
        platform.label,
        tool_call["tool_name"],
        type(result_payload).__name__,
    )
    return {
        "server_label": platform.label,
        "intent": "dynamic_tool_selection",
        "selected_tool": tool_call["tool_name"],
        "selected_arguments": tool_call["arguments"],
        "resolved_metric": tool_call.get("resolved_metric"),
        "result": result,
    }
