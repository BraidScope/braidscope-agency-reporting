# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import json
import os
import re
import time
from typing import Any, Iterator

import httpx
from openai import OpenAI

from .byok import get_byok_chat_config
from .claude_code import ClaudeAuthError, get_claude_chat_context, list_claude_models, run_claude_exec
from .codex import get_codex_chat_context, list_codex_models, run_codex_exec
from .core import KLAVIYO_MCP_URL, debug_log, logger
from .google_ads import get_google_ads_access_token, google_ads_mcp_url
from .ga4 import get_ga4_access_token, ga4_mcp_url
from .meta_ads import get_meta_ads_access_token, meta_ads_mcp_url
from .hubspot import get_hubspot_access_token, hubspot_mcp_url
from .klaviyo import get_klaviyo_access_token
from .mcp_orchestrator import PLATFORMS, list_mcp_tools, tool_is_read_only
from .openrouter import get_openrouter_chat_config
from .schemas import ChatRequest


# Third-party MCP servers (HubSpot, Klaviyo) expose write tools too. The
# model only ever sees the read-only subset, listed live and cached briefly.
_READ_ONLY_TOOLS_TTL_SECONDS = 600
_read_only_tools_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}


async def read_only_tool_names(label: str, client_id: str) -> list[str]:
    """Names of a remote MCP server's tools that pass tool_is_read_only.
    Empty on any failure, so the server is left out rather than opened up."""
    platform = PLATFORMS.get(label)
    if platform is None:
        return []
    key = (label, client_id)
    cached = _read_only_tools_cache.get(key)
    if cached and cached[0] > time.monotonic():
        return list(cached[1])
    try:
        tools, _ = await list_mcp_tools(platform, client_id)
    except Exception as exc:  # noqa: BLE001 - any failure means "no tools", never "all tools"
        debug_log("read-only tool listing failed label=%s error=%s", label, exc)
        return []
    names = [str(t["name"]) for t in tools if t.get("name") and tool_is_read_only(t)]
    _read_only_tools_cache[key] = (time.monotonic() + _READ_ONLY_TOOLS_TTL_SECONDS, names)
    return names


async def build_mcp_tools(client_id: str, connected_sources: list[str]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    connected = set(connected_sources)

    google_ads_url = google_ads_mcp_url()
    google_ads_token = await get_google_ads_access_token(client_id)
    if "google_ads" in connected and google_ads_url and google_ads_token:
        tools.append(
            {
                "type": "mcp",
                "server_label": "google_ads",
                "server_description": "Official Google Ads MCP server for read-only advertising performance analysis.",
                "server_url": google_ads_url,
                "authorization": google_ads_token,
                "allowed_tools": [
                    "customers_list_accessible_customers",
                    "metadata_get_resource_metadata",
                    "search_search",
                ],
                "require_approval": "never",
            }
        )

    meta_ads_url = meta_ads_mcp_url()
    meta_ads_token = await get_meta_ads_access_token(client_id)
    if "meta_ads" in connected and meta_ads_url and meta_ads_token:
        tools.append(
            {
                "type": "mcp",
                "server_label": "meta_ads",
                "server_description": "Braidscope read-only Meta Ads MCP server for campaign, ad set, ad, creative, and insights analysis.",
                "server_url": meta_ads_url,
                "authorization": meta_ads_token,
                "require_approval": "never",
            }
        )
    ga4_url = ga4_mcp_url()
    ga4_token = await get_ga4_access_token(client_id)
    if "google_analytics_4" in connected and ga4_url and ga4_token:
        tools.append(
            {
                "type": "mcp",
                "server_label": "google_analytics_4",
                "server_description": "Braidscope read-only Google Analytics 4 MCP server for website traffic, acquisition, engagement, events, and ecommerce analysis.",
                "server_url": ga4_url,
                "authorization": ga4_token,
                "require_approval": "never",
            }
        )

    klaviyo_url = os.getenv("KLAVIYO_MCP_URL", KLAVIYO_MCP_URL)
    klaviyo_token = await get_klaviyo_access_token(client_id)
    if "klaviyo" in connected and klaviyo_token:
        allowed = await read_only_tool_names("klaviyo", client_id)
        if allowed:
            tools.append(
                {
                    "type": "mcp",
                    "server_label": "klaviyo",
                    "server_description": "Official Klaviyo hosted MCP server for read-only campaign, flow, and metric analysis.",
                    "server_url": klaviyo_url,
                    "authorization": klaviyo_token,
                    "allowed_tools": allowed,
                    "require_approval": "never",
                }
            )

    hubspot_token = await get_hubspot_access_token(client_id)
    if "hubspot_crm" in connected and hubspot_token:
        allowed = await read_only_tool_names("hubspot_crm", client_id)
        if allowed:
            tools.append(
                {
                    "type": "mcp",
                    "server_label": "hubspot_crm",
                    "server_description": "Official HubSpot remote MCP server for read-only CRM analysis.",
                    "server_url": hubspot_mcp_url(),
                    "authorization": hubspot_token,
                    "allowed_tools": allowed,
                    "require_approval": "never",
                }
            )

    return tools


async def get_chat_configuration(
    client_id: str,
    connected_sources: list[str],
    user_id: str | None = None,
) -> dict[str, Any]:
    connected = set(connected_sources)
    # These lookups are independent of each other (each reads its own
    # provider's stored credentials, and the AI-provider lookup uses none of
    # them), so run them together instead of one after another.
    (
        klaviyo_token,
        hubspot_token,
        google_ads_token,
        meta_ads_token,
        ga4_token,
        ai_providers,
    ) = await asyncio.gather(
        get_klaviyo_access_token(client_id),
        get_hubspot_access_token(client_id),
        get_google_ads_access_token(client_id),
        get_meta_ads_access_token(client_id),
        get_ga4_access_token(client_id),
        get_available_ai_providers(client_id, user_id),
    )
    integrations = {
        "google_ads": {
            "connected": "google_ads" in connected,
            "configured": bool(google_ads_token and google_ads_mcp_url()),
        },
        "google_analytics_4": {
            "connected": "google_analytics_4" in connected,
            "configured": bool(ga4_token and ga4_mcp_url()),
        },
        "meta_ads": {
            "connected": "meta_ads" in connected,
            "configured": bool(meta_ads_token and meta_ads_mcp_url()),
        },
        "klaviyo": {
            "connected": "klaviyo" in connected,
            "configured": bool(klaviyo_token),
        },
        "hubspot_crm": {
            "connected": "hubspot_crm" in connected,
            "configured": bool(hubspot_token),
        },
    }
    active = [
        name
        for name, integration in integrations.items()
        if integration["connected"] and integration["configured"]
    ]
    missing = [
        name
        for name, integration in integrations.items()
        if integration["connected"] and not integration["configured"]
    ]
    ai_provider = ai_providers[0] if ai_providers else {}
    ai_configured = bool(ai_provider)
    return {
        "openai_configured": ai_configured,
        "ai_provider": {
            "provider": ai_provider.get("provider"),
            "model": ai_provider.get("model"),
        } if ai_provider else None,
        "ai_providers": [
            {"provider": provider["provider"], "model": provider["model"]}
            for provider in ai_providers
        ],
        "integrations": integrations,
        "active_integrations": active,
        "missing_integrations": missing,
        "ready": bool(ai_configured and active),
    }


async def _load_codex_provider(client_id: str) -> dict[str, str] | None:
    """The openai_codex entry - the existing Codex-CLI ChatGPT connection
    (py/app/codex.py), client-scoped by client_id like every other loader
    here. Has no api_key: Codex CLI authenticates through its own on-disk
    session under codex_home, not a bearer key, so this entry is only ever
    consumed by stream_codex_provider() below (see the explicit
    provider == "openai_codex" branch in stream_chat_response), never by the
    generic OpenAI()/api_key code paths the other providers use."""
    context = await get_codex_chat_context(client_id)
    if not context:
        return None
    return {"provider": "openai_codex", "model": context["model"], "codex_home": context["codex_home"]}


async def _load_claude_code_provider(client_id: str, user_id: str | None) -> dict[str, str] | None:
    """The claude_code entry - the requesting USER's own Claude Code CLI
    sign-in (py/app/claude_code.py). Unlike openai_codex (per client) this is
    per user: without a user_id, or without that user's own connection, the
    provider is simply not offered, so it can never resolve to a teammate's
    account. No api_key - consumed only by stream_claude_code_provider()."""
    context = await get_claude_chat_context(client_id, user_id)
    if not context:
        return None
    return {"provider": "claude_code", "model": context["model"], "claude_config_dir": context["config_dir"]}


async def get_available_ai_providers(client_id: str, user_id: str | None = None) -> list[dict[str, str]]:
    providers: list[dict[str, str]] = []
    provider_loaders = (
        ("openrouter", lambda: get_openrouter_chat_config(client_id)),
        ("openai_codex", lambda: _load_codex_provider(client_id)),
        # NOTE: intentionally left active, not removed - this is the
        # existing raw-API-key OpenAI path (py/app/byok.py), a distinct,
        # still-supported connection method from the Codex/ChatGPT OAuth
        # one above. A client only ever has one or the other in practice
        # (the Ask AI drawer's own "Connect with ChatGPT" button only ever
        # drives the Codex flow), so there is no real ambiguity between
        # them - but per explicit instruction this BYOK path must keep
        # working exactly as before, so nothing about it changes here.
        ("claude_code", lambda: _load_claude_code_provider(client_id, user_id)),
        ("openai", lambda: get_byok_chat_config(client_id, "openai")),
        ("anthropic", lambda: get_byok_chat_config(client_id, "anthropic")),
    )
    for provider_name, load_provider in provider_loaders:
        try:
            provider_config = await load_provider()
        except ValueError as exc:
            debug_log(
                "Skipping AI provider %s for client_id=%s: %s",
                provider_name,
                client_id,
                exc,
            )
            continue
        if provider_config:
            providers.append(provider_config)
    return providers


async def get_ai_provider_config(
    client_id: str,
    preferred_provider: str | None = None,
    model_override: str | None = None,
    user_id: str | None = None,
) -> dict[str, str]:
    providers = await get_available_ai_providers(client_id, user_id)
    provider = None
    normalized_provider = (preferred_provider or "").strip().lower()
    if normalized_provider:
        provider = next(
            (item for item in providers if item.get("provider") == normalized_provider),
            None,
        )
    if provider is None and providers:
        provider = providers[0]
    if not provider:
        return {}

    selected = dict(provider)
    if model_override and model_override.strip():
        selected["model"] = model_override.strip()
    return selected


def normalize_model_rows(provider: str, payload: dict[str, Any]) -> list[dict[str, str]]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []

    rows: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        name = (
            item.get("name")
            or item.get("display_name")
            or item.get("displayName")
            or model_id
        )
        rows.append({"id": model_id, "name": str(name)})

    return sorted(rows, key=lambda row: row["id"])


async def list_ai_provider_models(
    client_id: str,
    provider: str,
    user_id: str | None = None,
) -> list[dict[str, str]]:
    normalized_provider = provider.strip().lower()

    if normalized_provider == "claude_code":
        # Only aliases really probed against THIS user's signed-in Claude Code
        # session (claude_code.discover_claude_models) - never the Anthropic
        # API /v1/models catalog the BYOK "anthropic" branch below uses.
        if not user_id:
            raise ValueError("Claude is not connected for your account")
        models = await list_claude_models(client_id, user_id)
        return [
            {
                "id": entry["id"],
                "name": entry.get("display_name") or entry["id"],
                "description": entry.get("description") or "",
                "default": entry.get("default", False),
                "requires_credits": entry.get("supported") == "requires_credits",
            }
            for entry in models
            # Verified runnable, or visible-but-needs-usage-credits (shown
            # with a note, like Claude Code's own picker). Substituted or
            # inconclusive entries stay hidden.
            if entry.get("supported") is True or entry.get("supported") == "requires_credits"
        ]

    if normalized_provider == "openai_codex":
        # Strict separation, per explicit requirement: this list is ONLY
        # models verified live against the current authenticated Codex CLI /
        # ChatGPT-Codex runtime (list_codex_models -> discover_codex_models
        # in codex.py, built on `codex debug models` plus a real
        # `codex exec -m <id>` probe per candidate - confirmed live that
        # catalog metadata alone is not trustworthy: gpt-6-astra reports
        # supported_in_api=true and visibility="list" yet is rejected
        # outright for a real ChatGPT-account session). This never touches
        # the public OpenAI API catalog (the separate "openai" BYOK branch
        # in fetch_ai_provider_models below) and never OpenRouter's - those
        # remain fully separate code paths below, untouched.
        models = await list_codex_models(client_id)
        return [
            {
                "id": entry["id"],
                "name": entry.get("display_name") or entry["id"],
                "default": entry.get("default", False),
                "reasoning_support": entry.get("reasoning_support"),
                "tool_support": entry.get("tool_support"),
            }
            for entry in models
            # Only what the live probe actually confirmed - an entry can be
            # present-but-unverified (supported=None, discovery itself
            # failed) or present-but-rejected (supported=False, e.g.
            # gpt-6-astra); neither belongs in a selectable list.
            if entry.get("supported") is True
        ]

    ai_config = await get_ai_provider_config(client_id, preferred_provider=provider, user_id=user_id)
    if not ai_config:
        raise ValueError("Selected AI provider is not connected for this client")

    return await fetch_ai_provider_models(ai_config)


async def list_ai_provider_models_for_key(
    provider: str,
    api_key: str | None = None,
) -> list[dict[str, str]]:
    provider_name = provider.strip().lower()
    if provider_name not in {"openrouter", "openai", "anthropic"}:
        raise ValueError("Unsupported AI provider")
    if provider_name in {"openai", "anthropic"} and not (api_key or "").strip():
        raise ValueError("API key is required to load models for this provider")

    ai_config = {
        "provider": provider_name,
        "api_key": (api_key or "").strip(),
    }
    if provider_name == "openrouter":
        ai_config["base_url"] = "https://openrouter.ai/api/v1"
    elif provider_name == "anthropic":
        ai_config["base_url"] = "https://api.anthropic.com"
    return await fetch_ai_provider_models(ai_config)


async def fetch_ai_provider_models(ai_config: dict[str, str]) -> list[dict[str, str]]:
    provider_name = ai_config.get("provider")
    headers: dict[str, str] = {}
    url = ""
    params: dict[str, Any] = {}
    if provider_name == "openrouter":
        url = f"{ai_config.get('base_url', 'https://openrouter.ai/api/v1').rstrip('/')}/models"
        if ai_config.get("api_key"):
            headers["Authorization"] = f"Bearer {ai_config['api_key']}"
    elif provider_name == "openai":
        url = "https://api.openai.com/v1/models"
        headers["Authorization"] = f"Bearer {ai_config['api_key']}"
    elif provider_name == "anthropic":
        url = f"{ai_config.get('base_url', 'https://api.anthropic.com').rstrip('/')}/v1/models"
        params["limit"] = 100
        headers.update(
            {
                "x-api-key": ai_config["api_key"],
                "anthropic-version": "2023-06-01",
            }
        )
    else:
        raise ValueError("Unsupported AI provider")

    all_rows: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
            all_rows.extend(normalize_model_rows(provider_name or "", payload))

            if not payload.get("has_more"):
                break

            last_id = payload.get("last_id")
            if not isinstance(last_id, str) or not last_id:
                break
            params["after_id"] = last_id

    unique_rows = {row["id"]: row for row in all_rows}
    return sorted(unique_rows.values(), key=lambda row: row["id"])


def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# When the request is ambiguous the model answers with this marker followed by a
# JSON object instead of prose. The filter below turns that into a `question`
# SSE event so the drawer can render real option buttons.
QUESTION_MARKER = "<<<BRAIDSCOPE_QUESTION>>>"

QUESTION_INSTRUCTIONS = (
    "If the user's request is ambiguous in a way that would change your answer — an undefined "
    "threshold, an unstated date range, or more than one plausible metric — do not guess. "
    f"Instead reply with exactly the marker {QUESTION_MARKER} on the first line, then a single "
    'JSON object on the next line with this shape: {"question": "one short question", '
    '"multi": false, "options": [{"label": "short answer", "hint": "what picking this means"}]}. '
    "Give 2-4 options, always grounded in data you can actually query. Output nothing else when "
    "you ask. Ask at most once per conversation, and only when the answer genuinely changes."
)

# Explicit grounding rules for the "read the already-fetched tool result and
# answer honestly" step every provider branch below performs. Several of
# these are already enforced in code before the model ever runs (a detected
# tool error short-circuits via detected_tool_error_message, a non-answer is
# filtered via is_non_answer_provider_text) - they are restated here anyway
# so the model itself never contradicts that behavior on the rare payload
# shape those checks don't recognize.
GROUNDING_INSTRUCTIONS = (
    "The tool result you are given is the only source of truth - it has already been fetched "
    "for you, so never say you will check, look up, or need to call a tool; answer from what is "
    "already there. Use only facts present in that result. Never invent or estimate a number, "
    "campaign, contact, deal, date, or percentage that is not literally in it, and never fill a "
    "missing field from general knowledge or a prior answer. A metric that is present and equal "
    "to zero is a real, reportable value - say the number (e.g. \"0 active users\" or \"$0 spent\"), "
    "never \"no data was returned\", which means the field was absent, not zero. Do not mistake a "
    "product, plan, or campaign name that contains digits for a numeric metric. If the result only "
    "answers part of the question, answer the part it supports and say plainly what it does not "
    "cover — do not silently drop the unanswered part or paper over it with a guess. When more than "
    "one tool result is provided, keep each value attributed to the platform it came from and never "
    "blend or infer one platform's figure from another's."
)


def parse_question_payload(text: str) -> dict[str, Any] | None:
    """Pull the JSON object that follows the question marker, if it parses."""
    marker_index = text.find(QUESTION_MARKER)
    if marker_index < 0:
        return None

    payload = parse_json_text(text[marker_index + len(QUESTION_MARKER):])
    if not isinstance(payload, dict):
        return None

    question = str(payload.get("question") or "").strip()
    raw_options = payload.get("options")
    if not question or not isinstance(raw_options, list):
        return None

    options: list[dict[str, str]] = []
    for option in raw_options[:4]:
        if isinstance(option, dict):
            label = str(option.get("label") or "").strip()
            hint = str(option.get("hint") or "").strip()
        else:
            label, hint = str(option).strip(), ""
        if label:
            options.append({"label": label, "hint": hint})

    if len(options) < 2:
        return None

    return {"question": question, "multi": bool(payload.get("multi")), "options": options}


class QuestionFilter:
    """Holds back the stream just long enough to tell prose from a question.

    The marker only ever appears at the very start of a reply, so we buffer
    until we have enough characters to rule it out, then let everything through
    untouched. That keeps normal answers streaming token-by-token.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._decided = False
        self._is_question = False

    def feed(self, delta: str) -> str:
        """Return the text that is safe to stream now (may be empty)."""
        if self._decided:
            if not self._is_question:
                return delta
            # Keep accumulating so the JSON body is complete by finish().
            self._buffer += delta
            return ""

        self._buffer += delta
        stripped = self._buffer.lstrip()

        if stripped.startswith(QUESTION_MARKER):
            self._decided = True
            self._is_question = True
            return ""

        # Still consistent with the marker being typed out — keep waiting.
        if QUESTION_MARKER.startswith(stripped) and len(stripped) < len(QUESTION_MARKER):
            return ""

        self._decided = True
        self._is_question = False
        flushed, self._buffer = self._buffer, ""
        return flushed

    def finish(self) -> tuple[dict[str, Any] | None, str]:
        """Return (question payload, leftover text) once the stream ends."""
        if self._is_question:
            return parse_question_payload(self._buffer), ""
        if not self._decided:
            # Stream ended mid-marker; treat whatever arrived as plain text.
            leftover, self._buffer = self._buffer, ""
            return None, leftover
        return None, ""


def tool_event(server_label: str | None, name: str | None, status: str = "done") -> str:
    return sse_event(
        "tool",
        {
            "server": server_label or "mcp",
            "name": name or "query",
            "status": status,
        },
    )


def safe_event_payload(event: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": getattr(event, "type", "unknown")}
    for attr in ("name", "server_label", "tool_name", "error"):
        value = getattr(event, attr, None)
        if value:
            payload[attr] = str(value)
    response = getattr(event, "response", None)
    if response:
        error = getattr(response, "error", None)
        if error:
            payload["response_error"] = {
                "type": getattr(error, "type", None),
                "code": getattr(error, "code", None),
                "message": getattr(error, "message", None),
            }
    item = getattr(event, "item", None)
    if item:
        payload["item_type"] = getattr(item, "type", None)
        payload["item_name"] = getattr(item, "name", None)
        payload["item_status"] = getattr(item, "status", None)
        item_error = getattr(item, "error", None)
        if item_error:
            payload["item_error"] = str(item_error)
    return payload


def extract_response_text(response: Any) -> str:
    if not response:
        return ""

    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text)

    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                chunks.append(str(text))
    return "".join(chunks)


WRAPPER_PREVIEW_KEYS = {"jsonrpc", "id", "method"}
NESTED_RESULT_KEYS = (
    "result",
    "results",
    "data",
    "items",
    "series",
    "aggregates",
    "content",
    "structuredContent",
    "structured_content",
    "toolResult",
    "tool_result",
)
AGGREGATE_ROW_KEYS = ("data", "results", "items", "series", "aggregates")


def _looks_like_raw_json(value: Any) -> bool:
    """True for a string that is itself a JSON object/array - e.g. an MCP
    tool's "text" field, which is almost always JSON-encoded, not plain
    prose. General safety net, used everywhere a "flat scalar field" preview
    is built (summarize_aggregate_record, compact_json_preview): confirmed
    live on 2026-09-23 that skipping this check let raw, unparsed JSON reach
    the user twice, for two different GA4 tools whose shape nothing upstream
    knew how to summarize - `list_properties` ("type: text; text:
    {\"properties\": [...]}}") and an empty `get_traffic_overview` report
    ("kind: analyticsData#runReport", picked as the one remaining scalar
    field once the payload's only other key, "metadata", was filtered out
    for being a dict). A raw JSON string is never a useful "key: value"
    preview line regardless of which tool produced it, so this is checked
    generically rather than special-cased per tool/shape.
    """
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return stripped[:1] in ("{", "[") and parse_json_text(stripped) is not None


_SOURCE_DISPLAY_NAMES = {
    "google_ads": "Google Ads",
    "google_analytics_4": "Google Analytics 4",
    "klaviyo": "Klaviyo",
    "hubspot_crm": "HubSpot",
    "meta_ads": "Meta Ads",
}


def _source_display_name(server_label: str | None) -> str:
    if not server_label:
        return "This data source"
    return _SOURCE_DISPLAY_NAMES.get(server_label, server_label.replace("_", " ").title())


def _extract_google_ads_error_detail(msg: str) -> str | None:
    """Google Ads' MCP server nests a whole Google Ads API error response
    (an array of `{"error": {"code", "message", "status", "details": [...]}}`
    objects) as a JSON-encoded string inside its own `{"error": "<json>"}`
    wrapper. Confirmed live on 2026-09-23 against a real account: the raw
    message was 400+ characters of escaped nested JSON - useless to show a
    user directly - containing the actual human-readable reason
    ("The customer account can't be accessed because it is not yet enabled
    or has been deactivated.") three levels deep inside details[].errors[].
    """
    parsed = parse_json_text(msg)
    if not isinstance(parsed, list):
        return None
    messages: list[str] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        error = entry.get("error")
        if not isinstance(error, dict):
            continue
        found = False
        for detail in error.get("details") or []:
            if not isinstance(detail, dict):
                continue
            for sub_error in detail.get("errors") or []:
                if isinstance(sub_error, dict) and sub_error.get("message"):
                    messages.append(str(sub_error["message"]))
                    found = True
        if not found and error.get("message"):
            messages.append(str(error["message"]))
    return "; ".join(messages) if messages else None


def extract_mcp_tool_error_text(payload: Any) -> str:
    """This codebase's MCP tool servers (ga4_mcp.py, google_ads_mcp.py, ...)
    all wrap a genuine upstream API failure the same way - as a successful
    JSON-RPC response whose content is `{"error": "<message>"}` - not as a
    transport-level failure. That means "the tool call succeeded" (what
    execute_mcp_tool's own success/failure tracking sees) does not mean "the
    data is usable". Confirmed live on 2026-09-23: a real client's Google Ads
    account returned exactly this shape (a 403 CUSTOMER_NOT_ENABLED error)
    for get_campaign_performance. Nothing recognized it, so it fell through
    every summarizer to the meaningless "type: text" leftover, and separately
    wasted a full 90-second Codex timeout trying to interpret it as data.
    """
    if isinstance(payload, str):
        parsed = parse_json_text(payload)
        if parsed is None:
            return ""
        payload = parsed
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if error is None:
        # Klaviyo's real MCP tool errors use a JSON:API-style shape instead
        # of the {"error": ...} convention other platforms use - confirmed
        # live on 2026-09-23 for a real, malformed tool call: {"data":
        # {"errors": [{"status": 400, "code": "invalid", "title": "Invalid
        # input.", "detail": "{\"detail\":{\"error\":\"Company does not
        # exist.\"}}"}]}, "status": 400}. Without this, the real message
        # ("Company does not exist.") was buried three levels deep inside a
        # JSON-encoded string, and only the bare outer "status: 400" field
        # survived to the generic flat-field fallback.
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("errors"), list):
            messages: list[str] = []
            for entry in data["errors"]:
                if not isinstance(entry, dict):
                    continue
                message = None
                detail = entry.get("detail")
                if isinstance(detail, str):
                    parsed_detail = parse_json_text(detail)
                    if isinstance(parsed_detail, dict):
                        inner_detail = parsed_detail.get("detail")
                        if isinstance(inner_detail, dict) and inner_detail.get("error"):
                            message = str(inner_detail["error"])
                    if message is None:
                        message = detail
                if message is None:
                    message = entry.get("title")
                if message:
                    messages.append(str(message))
            if messages:
                return "; ".join(messages)
        for key in NESTED_RESULT_KEYS:
            nested = payload.get(key)
            if isinstance(nested, (dict, str)):
                nested_error = extract_mcp_tool_error_text(nested)
                if nested_error:
                    return nested_error
        return ""
    if isinstance(error, dict):
        message = error.get("message") or error.get("msg")
        return str(message) if message else ""
    if isinstance(error, str):
        return _extract_google_ads_error_detail(error) or error
    return str(error)


def detected_tool_error_message(direct_tool_result: dict[str, Any] | None) -> str | None:
    """Scan a direct MCP result for the shared `{"error": ...}` failure
    shape and, if found, build one clean, honest sentence naming the source
    and the real reason - used to short-circuit before ever calling a model,
    so a genuine upstream failure (wrong permissions, expired token, rate
    limit) never gets handed to an LLM to guess at or silently time out on.
    """
    if not direct_tool_result:
        return None
    result = direct_tool_result.get("result")
    source_name = _source_display_name(direct_tool_result.get("server_label"))

    # A bare, un-enveloped {"error": "..."} dict - what execute_mcp_tool()
    # itself returns directly for a missing token or a failed upstream
    # resolution (e.g. "no usable Google Ads customer found"), never wrapped
    # in the MCP content-array format real tool calls use. Confirmed live on
    # 2026-09-23: without this direct check, this shape fell all the way
    # through to the generic flat-field dump and surfaced as a raw
    # "error: <message>" line instead of a clean, source-attributed sentence.
    direct_error = extract_mcp_tool_error_text(result)
    if direct_error:
        return f"{source_name} couldn't complete this request: {direct_error}"

    texts: list[str] = []
    collect_text_values(result, texts)
    for text in texts:
        stripped = text.strip()
        if not stripped:
            continue
        error_text = extract_mcp_tool_error_text(stripped)
        if error_text:
            return f"{source_name} couldn't complete this request: {error_text}"
    return None


def collect_text_values(value: Any, texts: list[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"text", "content"} and isinstance(nested, str):
                texts.append(nested)
            else:
                collect_text_values(nested, texts)
    elif isinstance(value, list):
        for item in value:
            collect_text_values(item, texts)


def parse_json_text(text: str) -> Any | None:
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def first_scalar(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, "", []):
            return value
    return None


KLAVIYO_REPORT_RATE_STATS = {
    "open_rate", "click_rate", "click_to_open_rate", "conversion_rate",
    "bounce_rate", "bounced_or_failed_rate", "delivery_rate", "failed_rate",
    "spam_complaint_rate", "unsubscribe_rate",
}
KLAVIYO_REPORT_CURRENCY_STATS = {
    "average_order_value", "conversion_value", "revenue_per_recipient",
}
KLAVIYO_REPORT_PREFERRED_STAT_ORDER = (
    "recipients", "delivered", "delivery_rate",
    "opens", "opens_unique", "open_rate",
    "clicks", "clicks_unique", "click_rate", "click_to_open_rate",
    "conversions", "conversion_uniques", "conversion_rate", "conversion_value",
    "revenue_per_recipient", "average_order_value",
    "bounced", "bounce_rate", "unsubscribes", "unsubscribe_rate",
)


def _format_klaviyo_stat_value(name: str, value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if name in KLAVIYO_REPORT_RATE_STATS:
        return f"{number * 100:.1f}%"
    if name in KLAVIYO_REPORT_CURRENCY_STATS:
        return f"${number:,.2f}"
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _ordered_klaviyo_stats(statistics: dict[str, Any]) -> list[tuple[str, Any]]:
    ordered = [(key, statistics[key]) for key in KLAVIYO_REPORT_PREFERRED_STAT_ORDER if key in statistics]
    remaining = [(key, value) for key, value in statistics.items() if key not in KLAVIYO_REPORT_PREFERRED_STAT_ORDER]
    return ordered + remaining


def _klaviyo_report_row_primary_value(row: dict[str, Any]) -> float:
    statistics = row.get("statistics") if isinstance(row.get("statistics"), dict) else {}
    for key in ("conversion_value", "revenue_per_recipient", "conversions", "clicks", "opens"):
        if key in statistics:
            try:
                return float(statistics[key])
            except (TypeError, ValueError):
                continue
    return 0.0


def summarize_klaviyo_report(payload: Any) -> str:
    """get_campaign_report/get_flow_report return `{"data": {"type":
    "campaign-values-report"|"flow-values-report", "attributes":
    {"results": [{"groupings": {...}, "statistics": {...}, "flow_details"/
    "campaign_details": {...}}]}}}`. Confirmed live on 2026-09-23: nothing
    recognized this shape either, so it fell through to the generic
    flat-field fallback and surfaced the raw envelope's own "type"
    discriminator ("type: campaign-values-report" / "type:
    flow-values-report") as if it were the answer. An empty `results` list
    (confirmed live: a real, valid response for a period with no matching
    campaigns) is a genuine zero, stated honestly rather than silently
    producing nothing.
    """
    if isinstance(payload, str):
        parsed = parse_json_text(payload)
        if parsed is None:
            return ""
        payload = parsed
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data")
    if not isinstance(data, dict) or not str(data.get("type", "")).endswith("-values-report"):
        return ""
    attributes = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
    results = attributes.get("results") if isinstance(attributes.get("results"), list) else []
    if not results:
        return "Klaviyo returned no matching campaigns or flows for this report and period."

    sorted_results = sorted(
        (row for row in results if isinstance(row, dict)),
        key=_klaviyo_report_row_primary_value,
        reverse=True,
    )
    lines = []
    for row in sorted_results[:10]:
        groupings = row.get("groupings") if isinstance(row.get("groupings"), dict) else {}
        statistics = row.get("statistics") if isinstance(row.get("statistics"), dict) else {}

        name = next((str(v) for k, v in groupings.items() if k.endswith("_name") and v), None)
        if not name:
            for key, value in row.items():
                if key.endswith("_details") and isinstance(value, dict):
                    detail_attrs = value.get("attributes") if isinstance(value.get("attributes"), dict) else {}
                    if detail_attrs.get("name"):
                        name = str(detail_attrs["name"])
                        break
        if not name:
            name = next((str(v) for v in groupings.values() if v), "Result")

        parts = [
            f"{stat_name.replace('_', ' ')}: {_format_klaviyo_stat_value(stat_name, value)}"
            for stat_name, value in _ordered_klaviyo_stats(statistics)
            if value not in (None, "")
        ]
        if parts:
            lines.append(f"- {name} — " + ", ".join(parts))

    return "\n".join(lines) if lines else "Klaviyo returned no usable statistics for this report."


def summarize_klaviyo_metric_aggregate(payload: Any) -> str:
    """Klaviyo's query_metric_aggregates tool returns a single object (not
    a list) shaped `{"data": {"type": "metric-aggregate", "attributes":
    {"dates": [...], "data": [{"dimensions": [...], "measurements":
    {"<key>": [values...]}}]}}}`. Confirmed live on 2026-09-23 against a
    real account: nothing recognized this shape -
    summarize_klaviyo_collection() only handles list-shaped `data` from
    GET-style tools and returns "" immediately for a dict - so it fell all
    the way through to the generic flat-field fallback, which picked up the
    raw envelope's own "type" discriminator and surfaced "type:
    metric-aggregate" as if it were the answer, even though the real answer
    (0 "Placed Order" events every day for the requested 31-day range - a
    real, valid zero, not missing data) was two levels deeper.
    """
    if isinstance(payload, str):
        parsed = parse_json_text(payload)
        if parsed is None:
            return ""
        payload = parsed
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("type") != "metric-aggregate":
        return ""
    attributes = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
    dates = attributes.get("dates") if isinstance(attributes.get("dates"), list) else []
    series = attributes.get("data") if isinstance(attributes.get("data"), list) else []

    lines = []
    for entry in series:
        if not isinstance(entry, dict):
            continue
        dimensions = entry.get("dimensions") or []
        measurements = entry.get("measurements") if isinstance(entry.get("measurements"), dict) else {}
        label = ", ".join(str(item) for item in dimensions) if dimensions else "Total"
        for measure_name, values in measurements.items():
            if not isinstance(values, list):
                continue
            numeric_values = [value for value in values if isinstance(value, (int, float))]
            total = sum(numeric_values)
            formatted_total = int(total) if float(total).is_integer() else round(total, 2)
            lines.append(f"{label} — {measure_name}: {formatted_total}")

    if not lines:
        return "Klaviyo returned no aggregate data for this request."

    date_range = f" ({dates[0][:10]} to {dates[-1][:10]})" if dates else ""
    return f"Klaviyo metric aggregate{date_range}:\n" + "\n".join(f"- {line}" for line in lines)


def summarize_klaviyo_collection(payload: dict[str, Any]) -> str:
    data = payload.get("data")
    if not isinstance(data, list):
        return ""

    lines: list[str] = []
    for item in data[:20]:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        item_id = item.get("id")
        name = first_scalar(attributes, ("name", "title", "label")) or "(No name)"

        if item_type == "flow":
            status = first_scalar(attributes, ("status",)) or "unknown"
            trigger = first_scalar(attributes, ("triggerType", "trigger_type"))
            updated = first_scalar(attributes, ("updated", "updated_at"))
            details = [f"Status: {status}"]
            if trigger:
                details.append(f"Trigger: {trigger}")
            if updated:
                details.append(f"Updated: {updated}")
            if item_id:
                details.append(f"ID: {item_id}")
            lines.append(f"- {name} — {'; '.join(details)}")
            continue

        if item_type == "metric":
            details = []
            if item_id:
                details.append(f"ID: {item_id}")
            lines.append(f"- {name}{' — ' + '; '.join(details) if details else ''}")
            continue

        if item_type == "account":
            # Klaviyo's account resource has no flat "name"/"status" field at
            # all - its real name lives at
            # attributes.contact_information.organization_name (or
            # default_sender_name), several levels deeper than every other
            # resource type this function handles. Confirmed live on
            # 2026-09-23: without this branch, the generic fallback below
            # found no "name"/"title"/"label" field and produced
            # "(No name) - ID: <id>" for a real, named account.
            contact = attributes.get("contact_information")
            contact = contact if isinstance(contact, dict) else {}
            account_name = first_scalar(
                contact, ("organization_name", "default_sender_name")
            ) or "(unnamed account)"
            details = []
            industry = first_scalar(attributes, ("industry",))
            if industry:
                details.append(f"Industry: {industry}")
            timezone_value = first_scalar(attributes, ("timezone",))
            if timezone_value:
                details.append(f"Timezone: {timezone_value}")
            currency = first_scalar(attributes, ("preferred_currency",))
            if currency:
                details.append(f"Currency: {currency}")
            if item_id:
                details.append(f"ID: {item_id}")
            lines.append(f"- {account_name}{' — ' + '; '.join(details) if details else ''}")
            continue

        status = first_scalar(attributes, ("status",))
        details = []
        if status:
            details.append(f"Status: {status}")
        if item_id:
            details.append(f"ID: {item_id}")
        lines.append(f"- {name}{' — ' + '; '.join(details) if details else ''}")

    if not lines:
        return ""

    total = len(data)
    item_type = data[0].get("type") if isinstance(data[0], dict) else "items"
    heading = f"Here are the {item_type}s returned ({total} total):"
    return f"{heading}\n" + "\n".join(lines)


def summarize_aggregate_payload(payload: Any) -> str:
    if isinstance(payload, str):
        parsed = parse_json_text(payload)
        if parsed is None:
            return ""
        payload = parsed

    if isinstance(payload, list):
        lines = []
        for index, item in enumerate(payload[:20], start=1):
            preview = summarize_aggregate_record(item)
            if preview:
                lines.append(f"{index}. {preview}")
        return "\n".join(lines)

    if not isinstance(payload, dict):
        return ""

    for key in NESTED_RESULT_KEYS:
        nested = payload.get(key)
        if isinstance(nested, (list, dict)):
            nested_summary = summarize_aggregate_payload(nested)
            if nested_summary:
                return nested_summary

    return summarize_aggregate_record(payload)


GA4_METRIC_LABELS = {
    "sessions": "sessions",
    "totalUsers": "total users",
    "newUsers": "new users",
    "activeUsers": "active users",
    "screenPageViews": "page views",
    "engagedSessions": "engaged sessions",
    "engagementRate": "engagement rate",
    "averageSessionDuration": "avg. session duration",
    "userEngagementDuration": "engagement duration",
    "bounceRate": "bounce rate",
    "keyEvents": "key events",
    "conversions": "conversions",
    "totalRevenue": "revenue",
    "eventCount": "event count",
    "eventCountPerUser": "events per user",
    "screenPageViewsPerSession": "page views per session",
    "sessionsPerUser": "sessions per user",
}

GA4_RATE_METRICS = {"engagementRate", "bounceRate", "sessionConversionRate", "userConversionRate"}
GA4_DURATION_METRICS = {"averageSessionDuration", "userEngagementDuration"}
GA4_CURRENCY_METRICS = {"totalRevenue", "purchaseRevenue"}


def humanize_ga4_metric_name(name: str) -> str:
    return GA4_METRIC_LABELS.get(name, name)


def format_ga4_metric_value(name: str, raw_value: Any) -> str:
    try:
        number = float(raw_value)
    except (TypeError, ValueError):
        return str(raw_value)
    if name in GA4_RATE_METRICS:
        return f"{number * 100:.1f}%"
    if name in GA4_DURATION_METRICS:
        total_seconds = int(round(number))
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    if name in GA4_CURRENCY_METRICS:
        return f"${number:,.2f}"
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.2f}"


def summarize_ga4_payload(payload: Any) -> str:
    if isinstance(payload, str):
        parsed = parse_json_text(payload)
        if parsed is None:
            return ""
        payload = parsed
    if not isinstance(payload, dict):
        return ""

    metric_headers = payload.get("metricHeaders")
    rows = payload.get("rows")
    dimension_headers = payload.get("dimensionHeaders") or []
    if isinstance(metric_headers, list) and rows is None:
        return "GA4 returned no data rows for this request."
    kind = payload.get("kind")
    if (
        metric_headers is None
        and not dimension_headers
        and rows is None
        and isinstance(kind, str)
        and kind.startswith("analyticsData#")
    ):
        # A GA4 report response with none of metricHeaders/dimensionHeaders/
        # rows present at all - not merely empty rows with real headers
        # (the case above), but no headers either - confirmed live on
        # 2026-09-23: a real `get_traffic_overview` call for a low-traffic
        # property returned exactly {"metadata": {...}, "kind":
        # "analyticsData#runReport"} for the requested period. Without this
        # check, nothing recognized this shape and it fell all the way
        # through to compact_json_preview(), which picked "kind" as the one
        # remaining non-nested scalar field and surfaced
        # "kind: analyticsData#runReport" as if it were the answer.
        return "GA4 returned no data for this request."
    if not isinstance(metric_headers, list) or not isinstance(rows, list):
        for key in NESTED_RESULT_KEYS:
            nested = payload.get(key)
            if isinstance(nested, (dict, str)):
                summary = summarize_ga4_payload(nested)
                if summary:
                    return summary
        return ""

    metric_names = [
        str(item.get("name"))
        for item in metric_headers
        if isinstance(item, dict) and item.get("name")
    ]
    dimension_names = [
        str(item.get("name"))
        for item in dimension_headers
        if isinstance(item, dict) and item.get("name")
    ]
    lines = []
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        dimensions = row.get("dimensionValues") or []
        metrics = row.get("metricValues") or []
        values = []
        for index, name in enumerate(dimension_names):
            if index < len(dimensions) and isinstance(dimensions[index], dict):
                values.append(f"{name}: {dimensions[index].get('value', '')}")
        for index, name in enumerate(metric_names):
            if index < len(metrics) and isinstance(metrics[index], dict):
                raw_value = metrics[index].get("value", "")
                values.append(f"{humanize_ga4_metric_name(name)}: {format_ga4_metric_value(name, raw_value)}")
        if values:
            lines.append("- " + ", ".join(values))
    return "\n".join(lines) or "GA4 returned no data rows for this request."


def summarize_ga4_properties_list(payload: Any) -> str:
    """`list_properties` returns a completely different shape from every
    other GA4 tool - {"properties": [{"property_id", "property",
    "display_name", "account"}, ...]} - not a report with metricHeaders/
    rows at all, so summarize_ga4_payload() never matches it (returns "").
    Without a dedicated summarizer, this fell all the way through to the
    final compact_json_preview()-based fallback, which - confirmed live on
    2026-09-23 - surfaced the raw, unparsed JSON string verbatim
    ("type: text; text: {\"properties\": [...]}}") as if it were the answer.
    """
    if isinstance(payload, str):
        parsed = parse_json_text(payload)
        return summarize_ga4_properties_list(parsed) if parsed is not None else ""
    if not isinstance(payload, dict):
        return ""
    properties = payload.get("properties")
    if not isinstance(properties, list):
        return ""
    lines = []
    for entry in properties[:20]:
        if not isinstance(entry, dict):
            continue
        name = entry.get("display_name") or entry.get("property") or entry.get("property_id")
        if not name:
            continue
        property_id = entry.get("property_id")
        lines.append(f"{name} ({property_id})" if property_id else str(name))
    if not lines:
        return "No GA4 properties were returned for this account."
    return "Connected GA4 properties: " + ", ".join(lines) + "."


GOOGLE_ADS_METRIC_LABELS = {
    "impressions": "impressions",
    "clicks": "clicks",
    "ctr": "CTR",
    "costMicros": "cost",
    "averageCpc": "avg. CPC",
    "conversions": "conversions",
    "conversionsValue": "conversion value",
    "costPerConversion": "cost/conversion",
    "searchImpressionShare": "search impr. share",
    "allConversions": "all conversions",
    "allConversionsValue": "all conversion value",
}
GOOGLE_ADS_MICROS_METRIC_FIELDS = {"costMicros", "averageCpc", "costPerConversion"}
GOOGLE_ADS_RATE_METRIC_FIELDS = {"ctr", "searchImpressionShare"}
GOOGLE_ADS_CURRENCY_METRIC_FIELDS = {"conversionsValue", "allConversionsValue"}
GOOGLE_ADS_TITLE_GROUPS = (
    "campaign",
    "adGroup",
    "keywordView",
    "adGroupCriterion",
    "searchTermView",
    "conversionAction",
    "customer",
)
GOOGLE_ADS_ROW_SIGNAL_KEYS = GOOGLE_ADS_TITLE_GROUPS + ("metrics",)


def _humanize_google_ads_field(name: str) -> str:
    label = GOOGLE_ADS_METRIC_LABELS.get(name)
    if label:
        return label
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", name).replace("_", " ").strip()
    return spaced.lower() or name


def _format_google_ads_metric_value(name: str, raw_value: Any) -> str:
    try:
        number = float(raw_value)
    except (TypeError, ValueError):
        return str(raw_value)
    if name in GOOGLE_ADS_MICROS_METRIC_FIELDS:
        return f"${number / 1_000_000:,.2f}"
    if name in GOOGLE_ADS_RATE_METRIC_FIELDS:
        return f"{number * 100:.1f}%"
    if name in GOOGLE_ADS_CURRENCY_METRIC_FIELDS:
        return f"${number:,.2f}"
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.2f}"


def summarize_google_ads_row(row: Any) -> str:
    """Google Ads' searchStream API returns every field nested inside its
    resource group (`row["campaign"]["name"]`, `row["metrics"]["clicks"]`,
    ...) - never as a top-level scalar. Confirmed live on 2026-09-23: this
    shape defeated every flat-field summarizer in this file (there are no
    top-level scalar fields to find at all), which fell all the way through
    to describing the raw MCP content wrapper `{"type": "text", "text":
    "<json>"}` itself - and since "text" was correctly filtered out as raw
    JSON, only the useless "type: text" survived as the final answer.
    """
    if not isinstance(row, dict):
        return ""
    title = None
    for group in GOOGLE_ADS_TITLE_GROUPS:
        group_value = row.get(group)
        if isinstance(group_value, dict) and group_value.get("name"):
            title = str(group_value["name"])
            break
    parts = []
    metrics = row.get("metrics")
    if isinstance(metrics, dict):
        for key, value in metrics.items():
            if value in (None, "", []):
                continue
            parts.append(f"{_humanize_google_ads_field(key)}: {_format_google_ads_metric_value(key, value)}")
    if not parts:
        return ""
    prefix = f"{title} — " if title else ""
    return prefix + ", ".join(parts)


def summarize_google_ads_payload(payload: Any, *, is_google_ads: bool = False) -> str:
    """`is_google_ads` is only set by direct_tool_fallback_text once it
    already knows server_label == "google_ads" - it exists solely so a
    genuinely empty Google Ads result ({"results": []}) can be named as
    such. HubSpot's CRM v3 API uses the exact same top-level "results" key
    for its own, differently-shaped rows (see summarize_hubspot_payload
    below), so an empty list under that key alone is not proof this is a
    Google Ads payload.
    """
    if isinstance(payload, str):
        parsed = parse_json_text(payload)
        if parsed is None:
            return ""
        payload = parsed
    if not isinstance(payload, dict):
        return ""
    results = payload.get("results")
    if not isinstance(results, list):
        for key in NESTED_RESULT_KEYS:
            if key == "results":
                continue
            nested = payload.get(key)
            if isinstance(nested, (dict, str)):
                nested_summary = summarize_google_ads_payload(nested, is_google_ads=is_google_ads)
                if nested_summary:
                    return nested_summary
        return ""
    if not results:
        return "Google Ads returned no rows for this request." if is_google_ads else ""
    # Same reasoning for the non-empty case: without this check, a real
    # HubSpot response would reach this function first in the OR chain,
    # every row would fail summarize_google_ads_row (no "campaign"/"metrics"
    # groups), and it would incorrectly claim "Google Ads returned no rows"
    # for a HubSpot payload that actually had records.
    if not any(
        isinstance(row, dict) and any(key in row for key in GOOGLE_ADS_ROW_SIGNAL_KEYS)
        for row in results
    ):
        return ""
    lines = []
    for index, row in enumerate(results[:20], start=1):
        preview = summarize_google_ads_row(row)
        if preview:
            lines.append(f"{index}. {preview}")
    return "\n".join(lines) if lines else "Google Ads returned no rows for this request."


META_ADS_TITLE_FIELDS = ("ad_name", "adset_name", "campaign_name", "account_name")
META_ADS_CURRENCY_FIELDS = {"spend", "cpc", "cpm"}
META_ADS_PERCENT_FIELDS = {"ctr"}
META_ADS_FLAT_METRIC_FIELDS = (
    "spend", "reach", "impressions", "frequency", "clicks", "inline_link_clicks",
    "ctr", "cpc", "cpm", "date_start", "date_stop",
)
# Meta's Graph API reports conversions/revenue/ROAS as arrays of
# {"action_type", "value"} pairs on the insight row - never as top-level
# scalar fields - so a flat-field summarizer (or compact_json_preview's
# generic fallback, which skips nested list/dict values entirely) sees only
# spend/impressions/clicks/ctr and silently drops every purchase, lead, and
# ROAS figure the row actually contains. That is exactly the "must not
# invent a ROAS number, but must also not silently drop a real one" case the
# grounding rules are meant to prevent, so this nesting is unwrapped
# explicitly rather than left to generic handling.
META_ADS_ACTION_LIST_FIELDS = (
    ("actions", "", False),
    ("action_values", " value", True),
    ("purchase_roas", " ROAS", False),
    ("website_purchase_roas", " website ROAS", False),
)


def _format_meta_ads_metric_value(name: str, raw_value: Any) -> str:
    try:
        number = float(raw_value)
    except (TypeError, ValueError):
        return str(raw_value)
    if name in META_ADS_CURRENCY_FIELDS:
        return f"${number:,.2f}"
    if name in META_ADS_PERCENT_FIELDS:
        return f"{number:.2f}%"
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _humanize_meta_ads_action_type(action_type: str) -> str:
    return str(action_type).replace("_", " ").strip()


def summarize_meta_ads_row(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    title = first_scalar(row, META_ADS_TITLE_FIELDS)
    parts = []
    for key in META_ADS_FLAT_METRIC_FIELDS:
        value = row.get(key)
        if value in (None, "", []):
            continue
        parts.append(f"{key.replace('_', ' ')}: {_format_meta_ads_metric_value(key, value)}")
    for field, suffix, is_currency in META_ADS_ACTION_LIST_FIELDS:
        entries = row.get(field)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            action_type = entry.get("action_type")
            value = entry.get("value")
            if not action_type or value in (None, ""):
                continue
            label = f"{_humanize_meta_ads_action_type(action_type)}{suffix}"
            if is_currency:
                formatted = _format_meta_ads_metric_value("spend", value)
            elif field.endswith("roas"):
                try:
                    formatted = f"{float(value):.2f}x"
                except (TypeError, ValueError):
                    formatted = str(value)
            else:
                formatted = str(value)
            parts.append(f"{label}: {formatted}")
    if not parts:
        return ""
    prefix = f"{title} — " if title else ""
    return prefix + ", ".join(parts)


META_ADS_ROW_SIGNAL_KEYS = META_ADS_TITLE_FIELDS + META_ADS_FLAT_METRIC_FIELDS


def summarize_meta_ads_payload(payload: Any, *, is_meta_ads: bool = False) -> str:
    """`is_meta_ads` is only set by direct_tool_fallback_text once it already
    knows server_label == "meta_ads" - it exists solely so a genuinely empty
    Meta Ads insights result ({"data": []}) can be named as such. Without
    it, an empty "data" list can't be told apart from any other platform's
    unrelated, coincidentally-empty "data" list (Klaviyo's collection shape
    uses the same key), so this function would otherwise have to guess.
    """
    if isinstance(payload, str):
        parsed = parse_json_text(payload)
        if parsed is None:
            return ""
        payload = parsed
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data")
    if not isinstance(data, list):
        for key in NESTED_RESULT_KEYS:
            if key == "data":
                continue
            nested = payload.get(key)
            if isinstance(nested, (dict, str)):
                nested_summary = summarize_meta_ads_payload(nested, is_meta_ads=is_meta_ads)
                if nested_summary:
                    return nested_summary
        return ""
    if not data:
        return "Meta Ads returned no rows for this request." if is_meta_ads else ""
    # Only claim this generic-looking {"data": [...]} shape as a Meta Ads
    # insights result once at least one row actually has a Meta-Ads-specific
    # field (spend/impressions/ctr/campaign_name/...) - "data" is also one
    # of the shared NESTED_RESULT_KEYS other platforms use (Klaviyo's
    # JSON:API collections in particular), so a bare list of dicts is not
    # enough evidence on its own.
    if not any(
        isinstance(row, dict) and any(key in row for key in META_ADS_ROW_SIGNAL_KEYS)
        for row in data
    ):
        return ""
    lines = []
    for index, row in enumerate(data[:20], start=1):
        preview = summarize_meta_ads_row(row)
        if not preview and isinstance(row, dict):
            preview = compact_json_preview(row)
        if preview:
            lines.append(f"{index}. {preview}")
    return "\n".join(lines) if lines else "Meta Ads returned no rows for this request."


HUBSPOT_TITLE_PROPERTIES = (
    "dealname", "email", "name", "subject", "firstname", "domain", "company",
)


def _hubspot_record_title(record: dict[str, Any], properties: dict[str, Any]) -> str | None:
    title = first_scalar(properties, HUBSPOT_TITLE_PROPERTIES)
    if title:
        return str(title)
    first_name = properties.get("firstname")
    last_name = properties.get("lastname")
    if first_name or last_name:
        return " ".join(part for part in (first_name, last_name) if part)
    return None


def summarize_hubspot_record(record: Any) -> str:
    """HubSpot's CRM v3 API - the shape its official MCP server proxies -
    nests every actual field one level down inside "properties"
    ({"id", "properties": {...}, "createdAt", ...}), never as top-level
    scalars. Left to compact_json_preview's generic flat-field fallback,
    "properties" is a dict and is skipped entirely, so only id/createdAt/
    updatedAt/archived would ever surface - never the contact's name/email,
    the deal's amount/stage, or the company's domain the user actually
    asked about.
    """
    if not isinstance(record, dict):
        return str(record) if record not in (None, "", []) else ""
    properties = record.get("properties")
    if not isinstance(properties, dict):
        return ""
    title = _hubspot_record_title(record, properties)
    parts = []
    for key, value in properties.items():
        if value in (None, "", []) or isinstance(value, (dict, list)):
            continue
        if _looks_like_raw_json(value):
            continue
        if key in HUBSPOT_TITLE_PROPERTIES and str(value) == title:
            continue
        parts.append(f"{key}: {value}")
        if len(parts) >= 6:
            break
    if not parts and not title:
        return ""
    prefix = f"{title} — " if title else ""
    return prefix + ", ".join(parts) if parts else (title or "")


def summarize_hubspot_payload(payload: Any, *, is_hubspot: bool = False) -> str:
    """`is_hubspot` mirrors `is_meta_ads`/`is_google_ads` above: it is only
    set once direct_tool_fallback_text already knows server_label ==
    "hubspot_crm", and exists solely so a genuinely empty HubSpot result can
    be named as such without misclaiming some other, unrelated platform's
    coincidentally-empty {"results": []} (Google Ads uses the same key).
    """
    if isinstance(payload, str):
        parsed = parse_json_text(payload)
        if parsed is None:
            return ""
        payload = parsed
    if not isinstance(payload, dict):
        return ""

    # A single-record response (e.g. "get contact by id") - not a list.
    if isinstance(payload.get("properties"), dict) and "results" not in payload:
        return summarize_hubspot_record(payload)

    results = payload.get("results")
    if not isinstance(results, list):
        for key in NESTED_RESULT_KEYS:
            if key == "results":
                continue
            nested = payload.get(key)
            if isinstance(nested, (dict, str)):
                nested_summary = summarize_hubspot_payload(nested, is_hubspot=is_hubspot)
                if nested_summary:
                    return nested_summary
        return ""
    if not results:
        return "HubSpot returned no records for this request." if is_hubspot else ""
    if not is_hubspot and not any(
        isinstance(record, dict) and isinstance(record.get("properties"), dict) for record in results
    ):
        # A non-empty "results" list with no HubSpot-shaped ("properties"
        # dict) records at all and an unconfirmed server_label - most likely
        # Google Ads' own "results" list (already handled above, first in
        # the OR chain), not a HubSpot payload. Defer rather than guess.
        return ""
    lines = []
    for index, record in enumerate(results[:20], start=1):
        preview = summarize_hubspot_record(record)
        if not preview and isinstance(record, dict):
            preview = compact_json_preview(record)
        if preview:
            lines.append(f"{index}. {preview}")
    text = "\n".join(lines) if lines else "HubSpot returned no records for this request."
    paging_after = None
    paging = payload.get("paging")
    if isinstance(paging, dict):
        next_page = paging.get("next")
        if isinstance(next_page, dict):
            paging_after = next_page.get("after")
    if paging_after:
        text += "\n(More records are available; ask a narrower question to see additional results.)"
    return text


def summarize_aggregate_record(record: Any) -> str:
    if not isinstance(record, dict):
        return str(record) if record not in (None, "", []) else ""

    attributes = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    measurements = record.get("measurements") if isinstance(record.get("measurements"), dict) else {}
    statistics = record.get("statistics") if isinstance(record.get("statistics"), dict) else {}
    values = {**attributes, **measurements, **statistics}
    for key, value in record.items():
        if key in WRAPPER_PREVIEW_KEYS:
            continue
        if isinstance(value, (dict, list)):
            continue
        values.setdefault(key, value)

    interesting = []
    for key in (
        "date",
        "datetime",
        "startDate",
        "endDate",
        "count",
        "sum",
        "value",
        "total",
        "unique",
        "rate",
    ):
        value = values.get(key)
        if value not in (None, "", []):
            interesting.append(f"{key}: {value}")

    if interesting:
        return "; ".join(interesting)

    flat = []
    for key, value in values.items():
        if value in (None, "", []) or isinstance(value, (dict, list)):
            continue
        if _looks_like_raw_json(value):
            continue
        flat.append(f"{key}: {value}")
        if len(flat) >= 6:
            break
    return "; ".join(flat)


def metric_context_text(direct_tool_result: dict[str, Any]) -> str:
    selected_arguments = direct_tool_result.get("selected_arguments")
    if not isinstance(selected_arguments, dict):
        selected_arguments = {}
    resolved_metric = direct_tool_result.get("resolved_metric")
    if not isinstance(resolved_metric, dict):
        resolved_metric = {}

    lines: list[str] = []
    metric_name = resolved_metric.get("name")
    metric_id = (
        selected_arguments.get("metricId")
        or selected_arguments.get("metric_id")
        or resolved_metric.get("id")
    )
    if metric_name and metric_id:
        lines.append(f"Metric: {metric_name} ({metric_id})")
    elif metric_id:
        lines.append(f"Metric ID: {metric_id}")

    start_date = selected_arguments.get("startDate") or selected_arguments.get("start_date")
    end_date = selected_arguments.get("endDate") or selected_arguments.get("end_date")
    if start_date and end_date:
        lines.append(f"Date range: {start_date} to {end_date}")

    measurements = selected_arguments.get("measurements")
    if measurements:
        if isinstance(measurements, list):
            measurements_text = ", ".join(str(item) for item in measurements)
        else:
            measurements_text = str(measurements)
        lines.append(f"Measurements: {measurements_text}")

    return "\n".join(lines)


def is_wrapper_only_preview(preview: str) -> bool:
    stripped = preview.strip()
    if not stripped:
        return True
    parts = [part.strip().split(":", 1)[0] for part in stripped.split(";") if part.strip()]
    return bool(parts) and all(part in WRAPPER_PREVIEW_KEYS for part in parts)


def empty_aggregate_text(direct_tool_result: dict[str, Any]) -> str:
    if direct_tool_result.get("selected_tool") != "klaviyo_query_metric_aggregates":
        return ""

    lines = [
        "Klaviyo returned no aggregate metric rows for this request.",
    ]
    context = metric_context_text(direct_tool_result)
    if context:
        lines.append(context)
    lines.append(
        "Note: this endpoint uses event time, not campaign or flow send date."
    )
    return "\n".join(lines)


def aggregate_payload_has_rows(payload: Any) -> bool:
    if isinstance(payload, str):
        parsed = parse_json_text(payload)
        if parsed is None:
            return False
        payload = parsed

    if isinstance(payload, list):
        return any(item not in (None, "", [], {}) for item in payload)

    if not isinstance(payload, dict):
        return False

    if any(key in payload for key in ("measurements", "statistics", "count", "value", "total")):
        return True

    for key in AGGREGATE_ROW_KEYS:
        nested = payload.get(key)
        if aggregate_payload_has_rows(nested):
            return True

    nested_result = payload.get("result")
    if isinstance(nested_result, (dict, list)):
        return aggregate_payload_has_rows(nested_result)

    structured = payload.get("structuredContent") or payload.get("structured_content")
    if isinstance(structured, (dict, list)):
        return aggregate_payload_has_rows(structured)

    return False


def compact_json_preview(value: Any, max_items: int = 8) -> str:
    if isinstance(value, dict):
        for key in NESTED_RESULT_KEYS + ("objects",):
            nested = value.get(key)
            if isinstance(nested, (list, dict)):
                preview = compact_json_preview(nested, max_items)
                if preview and not is_wrapper_only_preview(preview):
                    return preview

        fields = []
        for key, nested in value.items():
            if key in WRAPPER_PREVIEW_KEYS:
                continue
            if isinstance(nested, (dict, list)):
                continue
            if nested in (None, "", []):
                continue
            if _looks_like_raw_json(nested):
                continue
            fields.append(f"{key}: {nested}")
            if len(fields) >= 6:
                break
        return "; ".join(fields) if fields else ""

    if isinstance(value, list):
        lines = []
        for index, item in enumerate(value[:max_items], start=1):
            preview = compact_json_preview(item, max_items=max_items)
            if preview:
                lines.append(f"{index}. {preview}")
        return "\n".join(lines)

    return str(value)


def _numeric_metric_values(payload: Any, metric_name: str) -> list[float]:
    if isinstance(payload, str):
        parsed = parse_json_text(payload)
        return _numeric_metric_values(parsed, metric_name) if parsed is not None else []
    if not isinstance(payload, dict):
        return []
    headers = payload.get("metricHeaders")
    rows = payload.get("rows")
    if isinstance(headers, list) and isinstance(rows, list):
        names = [
            str(item.get("name"))
            for item in headers
            if isinstance(item, dict) and item.get("name")
        ]
        if metric_name in names:
            index = names.index(metric_name)
            values = []
            for row in rows:
                metrics = row.get("metricValues", []) if isinstance(row, dict) else []
                if index >= len(metrics) or not isinstance(metrics[index], dict):
                    continue
                raw = metrics[index].get("value")
                try:
                    values.append(float(raw))
                except (TypeError, ValueError):
                    continue
            return values
    values: list[float] = []
    for nested in payload.values():
        values.extend(_numeric_metric_values(nested, metric_name))
    return values


def _ga4_realtime_report_is_empty(payload: Any) -> bool:
    """True only for the specific, narrow case this is meant for: a GA4
    report whose metricHeaders explicitly include "activeUsers" and whose
    "rows" key is entirely absent (not an empty list, not malformed data -
    literally missing). See direct_tool_fallback_text's realtime branch for
    why that combination, and only that combination, is safe to treat as a
    real, valid zero rather than "no data": confirmed live this is exactly
    what GA4's Data API returns for a zero-active-users realtime report.
    Deliberately does NOT fire for `rows: []` (an explicit empty list is a
    different, less certain signal) or for reports missing "activeUsers"
    from their metricHeaders entirely (a different tool/report shape this
    reasoning was never verified against).
    """
    if isinstance(payload, str):
        parsed = parse_json_text(payload)
        return _ga4_realtime_report_is_empty(parsed) if parsed is not None else False
    if not isinstance(payload, dict):
        return False
    metric_headers = payload.get("metricHeaders")
    if not isinstance(metric_headers, list):
        return False
    metric_names = {
        str(item.get("name"))
        for item in metric_headers
        if isinstance(item, dict) and item.get("name")
    }
    return "activeUsers" in metric_names and payload.get("rows") is None


def direct_tool_fallback_text(
    direct_tool_result: dict[str, Any] | None,
    question: str | None = None,
) -> str:
    if not direct_tool_result:
        return ""

    error_message = detected_tool_error_message(direct_tool_result)
    if error_message:
        return error_message

    server_label = direct_tool_result.get("server_label")
    is_meta_ads = server_label == "meta_ads"
    is_google_ads = server_label == "google_ads"
    is_hubspot = server_label == "hubspot_crm"

    texts: list[str] = []
    collect_text_values(direct_tool_result.get("result"), texts)
    parsed_summaries = []
    plain_texts = []
    parsed_payloads: list[Any] = []
    for text in texts:
        stripped = text.strip()
        if not stripped:
            continue
        parsed = parse_json_text(stripped)
        if parsed is not None:
            parsed_payloads.append(parsed)
            if isinstance(parsed, dict):
                parsed_summary = (
                    summarize_ga4_payload(parsed)
                    or summarize_ga4_properties_list(parsed)
                    or summarize_google_ads_payload(parsed, is_google_ads=is_google_ads)
                    or summarize_klaviyo_metric_aggregate(parsed)
                    or summarize_klaviyo_report(parsed)
                    or summarize_meta_ads_payload(parsed, is_meta_ads=is_meta_ads)
                    or summarize_hubspot_payload(parsed, is_hubspot=is_hubspot)
                    or summarize_klaviyo_collection(parsed)
                )
                parsed_summaries.append(parsed_summary or compact_json_preview(parsed))
            else:
                parsed_summaries.append(compact_json_preview(parsed))
        else:
            plain_texts.append(stripped)

    structured_ga4 = summarize_ga4_payload(direct_tool_result.get("result"))
    if structured_ga4 and structured_ga4 not in parsed_summaries:
        parsed_summaries.append(structured_ga4)

    structured_google_ads = summarize_google_ads_payload(direct_tool_result.get("result"), is_google_ads=is_google_ads)
    if structured_google_ads and structured_google_ads not in parsed_summaries:
        parsed_summaries.append(structured_google_ads)

    structured_klaviyo_aggregate = summarize_klaviyo_metric_aggregate(direct_tool_result.get("result"))
    if structured_klaviyo_aggregate and structured_klaviyo_aggregate not in parsed_summaries:
        parsed_summaries.append(structured_klaviyo_aggregate)

    structured_klaviyo_report = summarize_klaviyo_report(direct_tool_result.get("result"))
    if structured_klaviyo_report and structured_klaviyo_report not in parsed_summaries:
        parsed_summaries.append(structured_klaviyo_report)

    structured_meta_ads = summarize_meta_ads_payload(direct_tool_result.get("result"), is_meta_ads=is_meta_ads)
    if structured_meta_ads and structured_meta_ads not in parsed_summaries:
        parsed_summaries.append(structured_meta_ads)

    structured_hubspot = summarize_hubspot_payload(direct_tool_result.get("result"), is_hubspot=is_hubspot)
    if structured_hubspot and structured_hubspot not in parsed_summaries:
        parsed_summaries.append(structured_hubspot)

    if (
        question
        and direct_tool_result.get("selected_tool") == "get_realtime_active_users"
    ):
        active_users = [
            value
            for payload in parsed_payloads
            for value in _numeric_metric_values(payload, "activeUsers")
        ]
        if active_users:
            total = sum(active_users)
            formatted_total = str(int(total)) if total.is_integer() else str(total)
            return f"There are currently {formatted_total} active users."
        if any(_ga4_realtime_report_is_empty(payload) for payload in parsed_payloads):
            # GA4's Data API omits the "rows" key entirely (rather than
            # returning a row with value "0") when a realtime report's
            # metric total is genuinely zero - confirmed live against this
            # exact tool on 2026-09-23: a direct call to this client's real
            # GA4 MCP endpoint returned {"dimensionHeaders": [...],
            # "metricHeaders": [{"name": "activeUsers", ...}],
            # "kind": "analyticsData#runRealtimeReport"} with no "rows" key
            # at all, while the real GA4 dashboard for the same property
            # showed "Active users in last 30 minutes: 0" at the same
            # moment. This is documented GA4 API behavior specific to this
            # single-metric report - not evidence of an error, wrong
            # property, or missing permissions the way an empty result on a
            # different GA4 report (traffic-by-source, by-day, etc.) could
            # be - so it is safe to state as a real, valid zero here and
            # only here, rather than the more technical (if not literally
            # incorrect) "GA4 returned no data rows for this request."
            return "There are currently 0 active users."

    is_metric_aggregate = (
        direct_tool_result.get("selected_tool") == "klaviyo_query_metric_aggregates"
    )
    if is_metric_aggregate and not aggregate_payload_has_rows(
        direct_tool_result.get("result")
    ):
        readable_parts = [*plain_texts, empty_aggregate_text(direct_tool_result)]
        return "\n".join(item for item in readable_parts if item)[:3000]

    structured_preview = summarize_aggregate_payload(direct_tool_result.get("result"))
    has_dedicated_summary = (
        direct_tool_result.get("server_label")
        in ("google_analytics_4", "google_ads", "klaviyo", "meta_ads", "hubspot_crm")
        and parsed_summaries
    )
    if has_dedicated_summary:
        structured_preview = ""
    if is_wrapper_only_preview(structured_preview):
        structured_preview = ""
    if not structured_preview and not has_dedicated_summary:
        structured_preview = compact_json_preview(direct_tool_result.get("result"))
    if is_wrapper_only_preview(structured_preview):
        structured_preview = ""

    if not structured_preview:
        structured_preview = empty_aggregate_text(direct_tool_result)

    readable_parts = [*parsed_summaries, *plain_texts]
    if structured_preview and structured_preview not in readable_parts:
        readable_parts.append(structured_preview)

    readable_text = "\n".join(item for item in readable_parts if item)
    if readable_text:
        return readable_text[:3000]

    selected_tool = direct_tool_result.get("selected_tool")
    result = direct_tool_result.get("result")
    preview = compact_json_preview(result)
    if is_wrapper_only_preview(preview):
        preview = ""
    if not preview:
        return ""

    intro = (
        f"The MCP tool {selected_tool} returned data, but the AI formatter did not "
        "produce a final sentence. Here is the raw result summary:"
        if selected_tool
        else "The MCP tool returned data. Here is the raw result summary:"
    )
    return f"{intro}\n{preview}"


def is_non_answer_provider_text(text: str) -> bool:
    # Curly/smart quotes normalized to straight ones first: confirmed live
    # against a real Codex/ChatGPT session on 2026-09-23 that its actual
    # output uses U+2019 ("I can’t access...") rather than a plain
    # apostrophe, which silently defeated every "i'm"/"i'll"/"can't"-style
    # check below (this one and the pre-existing ones above it) when matched
    # against real provider text rather than the ASCII-quoted strings used in
    # tests/fixtures.
    normalized = " ".join(text.lower().replace("’", "'").replace("‘", "'").split())
    # Every "does this contain a real data value" guard below must scan this,
    # never `normalized` directly: "GA4" / "Google Analytics 4" are the
    # platform's own product name, not a metric value, but their "4" reads as
    # a digit to a naive `any(c.isdigit() ...)` scan - confirmed live, twice,
    # against a real Codex session (see the no-access-refusal and
    # bare-clarifying-question blocks below): both a no-access refusal and a
    # "Ready to analyze ... Google Analytics 4 data. What would you like to
    # know?" clarifying question were initially missed by their own digit
    # guards purely because of this token, on responses that had zero actual
    # returned metric values in them.
    digit_scan_basis = normalized.replace("ga4", "").replace("analytics 4", "analytics")
    if normalized in {
        "ready to help with braidscope analytics.",
        "ready to help.",
    }:
        return True
    if (
        normalized.startswith(("ready to help", "i'm ready to help", "i am ready to help"))
        and not any(character.isdigit() for character in digit_scan_basis)
        and any(
            marker in normalized
            for marker in ("analytics", "official mcp", "read-only", "read only")
        )
    ):
        return True
    # Bare "Ready." (full stop, not "ready to help"/"ready to analyze")
    # followed by a deflection to a DIFFERENT topic - confirmed live on
    # 2026-09-23, same authenticated session/pipeline as the other cases
    # here, for the question "How many users are active right now?" with
    # the GA4 tool card showing success: the model answered "Ready. Ask
    # about the connected marketing data, Klaviyo, or HubSpot." instead of
    # the number, and slipped past every existing check (no "official mcp",
    # no trailing "?", didn't start with "ready to help"/"understood,"/
    # "got it,") because it's a distinct opening shape from all of them.
    if normalized.startswith("ready.") and not any(
        character.isdigit() for character in digit_scan_basis
    ):
        return True
    # "Ready for <client>'s marketing analytics questions." - confirmed live
    # on 2026-09-23, same pipeline, for "What are my Klaviyo account
    # details?" with the klaviyo account_details tool card showing success:
    # the model answered this generic readiness line instead of the actual
    # account details the tool had already returned. The client's name
    # varies per request, so this matches the fixed shape around it (opener
    # + "questions." close) rather than one literal string.
    if (
        normalized.startswith("ready for ")
        and normalized.endswith("questions.")
        and not any(character.isdigit() for character in digit_scan_basis)
    ):
        return True
    generic_openers = (
        "i'm ready to analyze",
        "i am ready to analyze",
        # Bare "Ready to analyze ..." (no "I'm"/"I am") is the same opener
        # without the pronoun - confirmed live, same session as the other
        # cases in this function: "Ready to analyze Live Trace's Google
        # Analytics 4 data." with no trailing question, so it also missed
        # the separate bare-clarifying-question check below (nothing to
        # match, it never asks anything - it just never answers either).
        "ready to analyze",
        "i'll use official mcp data",
        "i will use official mcp data",
        "i'll use read-only official mcp data",
        "i will use read-only official mcp data",
        "i'll answer using only data",
        "i will answer using only data",
    )
    if normalized.startswith(generic_openers):
        return True
    if (
        "official mcp" in normalized
        and any(phrase in normalized for phrase in ("what would you like", "what should i check", "ask me"))
    ):
        return True
    # A bare clarifying question, with no "official mcp" framing, is a
    # narrower version of the same failure - confirmed live against the same
    # authenticated Codex session as the no-access refusals below: given the
    # exact same prompt (embedded answer: "There are currently 17 active
    # users."), one run produced "What would you like to analyze for Live
    # Trace?" instead - a different, non-deterministic refusal shape from the
    # same root cause, proving individual phrase matching alone cannot keep
    # up with a generative model's phrasing variance. The system already has
    # one sanctioned, structured way to ask the user something before
    # answering - the QUESTION_MARKER path parsed above this function, before
    # is_non_answer_provider_text() is ever reached (see stream_codex_provider
    # and the other stream_*_provider functions). Any plain-text reply that
    # reaches here and is itself just a bare question with no real value in
    # it did not use that sanctioned path, so it is never a compliant final
    # answer - guarded on "no digits" so a real answer that legitimately ends
    # by offering a follow-up ("Revenue was $12,430 - want a daily breakdown?")
    # is not discarded.
    if normalized.endswith("?") and not any(character.isdigit() for character in digit_scan_basis):
        return True
    if any(
        marker in normalized
        for marker in (
            "analyticsdata#runrealtimereport",
            "analyticsdata#runreport",
            '"metricheaders"',
            '"metricvalues"',
            "dimensionheaders",
            "dimensionvalues",
        )
    ):
        return True
    # "Got it." is the same acknowledgment-then-restate-instructions shape as
    # "Understood," below, just different wording - confirmed live on
    # 2026-09-23 (a real Ask AI request for "How many users are active right
    # now?", GA4 tool card showing success): the model answered "Got it.
    # I'll answer only from connected official data tools for the selected
    # Braidscope client, keep responses compact, and ask a structured
    # clarification when the date range or metric would change the answer."
    # instead of the actual number, even though the MCP tool call had
    # already succeeded. Grouped with "understood," in the same digit-guarded
    # startswith check rather than added as a separate one-off, since it's
    # the identical failure pattern.
    if normalized.startswith(("understood,", "understood.", "got it,", "got it.")) and not any(
        character.isdigit() for character in digit_scan_basis
    ):
        return True
    if (
        normalized.startswith(("understood,", "understood."))
        and "answer from" in normalized
        and ("ask once" in normalized or "missing date range" in normalized)
    ):
        return True
    # Codex CLI's own baked-in coding-agent persona (not something Braidscope
    # controls) can refuse to treat the normalized MCP result embedded in the
    # prompt as authoritative, even when build_final_input_text() puts the
    # exact answer in plain text right above the question - confirmed live
    # against an authenticated Codex/ChatGPT session on 2026-09-23: identical
    # "I can't access/see/determine ... data" refusals reproduced across
    # realtime and historical GA4 prompts regardless of exact wording, often
    # followed by a request to "connect" a source or asking which dashboard
    # to check, as if it needs its own tool call rather than the value
    # already given. A refusal like this must never reach the user as the
    # final answer when the backend actually has real data.
    #
    # Two factors required together (an opener phrase AND a refusal-shaped
    # context phrase), not a "no digits present" guard: a first attempt at
    # that guard was tried directly against the live samples above and
    # false-negatived on both of them, because "GA4" and "last 30 days" -
    # both completely ordinary things for a refusal to mention while
    # explaining what it can't do - already contain digits ("4", "30") that
    # have nothing to do with an actual returned metric value. The two-factor
    # form only fires when the text both opens with a real refusal phrase AND
    # separately shapes itself like one (references "this chat"/"this
    # workspace"/"the available context", claims tools aren't available,
    # asks the user which source to check, or suggests connecting one) -
    # combinations a genuine data-bearing answer does not produce.
    no_access_openers = (
        "i can't access", "i cannot access",
        "i don't have access to", "i do not have access to",
        "i can't see", "i cannot see",
        "i can't determine", "i cannot determine",
        "i can't provide", "i cannot provide",
        "i can't give", "i cannot give",
    )
    no_access_context = (
        "in this chat", "in this workspace", "from the available context",
        "tools are available", "no data returned by", "connect the",
        "which product", "which dashboard", "should i check",
    )
    if any(opener in normalized for opener in no_access_openers) and any(
        context in normalized for context in no_access_context
    ):
        return True
    if normalized.rstrip(".") in {"no data was returned", "there is no data"}:
        return True
    return False


def build_final_input_text(
    request: ChatRequest,
    direct_tool_result: dict[str, Any] | None = None,
) -> str:
    if not direct_tool_result:
        return request.question

    server_label = direct_tool_result.get("server_label", "MCP")
    normalized_result = direct_tool_fallback_text(
        direct_tool_result,
        request.question,
    )
    return (
        "The backend already called the official MCP tool for this user request. "
        "Use the normalized tool result below as the only source of facts. "
        "Return the final answer directly; do not describe your plan, tool usage, "
        "internal fields, JSON, or MCP protocol. If it says no data was returned, "
        "say that clearly and do not invent details.\n\n"
        f"User question: {request.question}\n\n"
        f"{server_label} normalized MCP result:\n"
        f"{normalized_result or 'No usable data was returned by the tool.'}"
    )


def no_tool_input_text(request: ChatRequest) -> str:
    """Prompt for a message that needed no connector call (a greeting, a
    general question, a follow-up). These providers cannot receive the live
    MCP tools, so the model is told plainly that it has no numbers and which
    sources it could be asked about, instead of the user getting a canned
    notice for saying hello."""
    sources = ", ".join(str(s) for s in (request.connected_sources or []) if s) or "none"
    return (
        "No connected-data tool was called for this message, so you have no live "
        "numbers. Answer the user directly and briefly. If they ask about their "
        "data, say which sources are connected and ask them to name the source "
        "and the metric they want. Never invent figures.\n\n"
        f"Connected sources: {sources}\n\n"
        f"User message: {request.question}"
    )


def stream_chat_completion_provider(
    request: ChatRequest,
    instructions: str,
    direct_tool_result: dict[str, Any] | None,
    ai_config: dict[str, str],
) -> Iterator[str]:
    if not direct_tool_result:
        client = OpenAI(
            api_key=ai_config["api_key"],
            base_url=ai_config.get("base_url", "https://openrouter.ai/api/v1"),
            timeout=float(os.getenv("OPENAI_CHAT_TIMEOUT_SECONDS", "90")),
        )
        response = client.chat.completions.create(
            model=ai_config["model"],
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": no_tool_input_text(request)},
            ],
            temperature=0,
            max_tokens=int(os.getenv("OPENAI_CHAT_MAX_OUTPUT_TOKENS", "1200")),
        )
        text = response.choices[0].message.content or ""
        yield sse_event("message", {"delta": text or "I could not produce an answer. Try again."})
        yield sse_event("done", {})
        return

    tool_error = detected_tool_error_message(direct_tool_result)
    if tool_error:
        yield tool_event(direct_tool_result.get("server_label"), direct_tool_result.get("intent"))
        yield sse_event("message", {"delta": tool_error})
        yield sse_event("done", {})
        return

    client = OpenAI(
        api_key=ai_config["api_key"],
        base_url=ai_config.get("base_url", "https://openrouter.ai/api/v1"),
        timeout=float(os.getenv("OPENAI_CHAT_TIMEOUT_SECONDS", "90")),
    )
    response = client.chat.completions.create(
        model=ai_config["model"],
        messages=[
            {"role": "system", "content": instructions},
            {"role": "user", "content": build_final_input_text(request, direct_tool_result)},
        ],
        temperature=0,
        max_tokens=int(os.getenv("OPENAI_CHAT_MAX_OUTPUT_TOKENS", "1200")),
    )
    yield tool_event(direct_tool_result.get("server_label"), direct_tool_result.get("intent"))

    text = response.choices[0].message.content or ""
    # This path is non-streaming, so the whole reply is available at once.
    question = parse_question_payload(text)
    if question:
        yield sse_event("question", question)
        yield sse_event("done", {})
        return

    fallback = direct_tool_fallback_text(direct_tool_result, request.question)
    # A provider can occasionally return its generic welcome sentence even though
    # the pre-fetch completed and the tool result was included in the prompt.
    # Never replace a real MCP answer with that non-answer.
    if is_non_answer_provider_text(text):
        text = ""
    yield sse_event("message", {"delta": text or fallback})
    yield sse_event("done", {})


def stream_anthropic_provider(
    request: ChatRequest,
    instructions: str,
    direct_tool_result: dict[str, Any] | None,
    ai_config: dict[str, str],
) -> Iterator[str]:
    if not direct_tool_result:
        with httpx.Client(timeout=float(os.getenv("OPENAI_CHAT_TIMEOUT_SECONDS", "90"))) as client:
            response = client.post(
                f"{ai_config.get('base_url', 'https://api.anthropic.com').rstrip('/')}/v1/messages",
                headers={
                    "x-api-key": ai_config["api_key"],
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": ai_config["model"],
                    "max_tokens": int(os.getenv("OPENAI_CHAT_MAX_OUTPUT_TOKENS", "1200")),
                    "temperature": 0,
                    "system": instructions,
                    "messages": [{"role": "user", "content": no_tool_input_text(request)}],
                },
            )
            response.raise_for_status()
            blocks = response.json().get("content") or []
        text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        yield sse_event("message", {"delta": text or "I could not produce an answer. Try again."})
        yield sse_event("done", {})
        return

    tool_error = detected_tool_error_message(direct_tool_result)
    if tool_error:
        yield tool_event(direct_tool_result.get("server_label"), direct_tool_result.get("intent"))
        yield sse_event("message", {"delta": tool_error})
        yield sse_event("done", {})
        return

    with httpx.Client(timeout=float(os.getenv("OPENAI_CHAT_TIMEOUT_SECONDS", "90"))) as client:
        response = client.post(
            f"{ai_config.get('base_url', 'https://api.anthropic.com').rstrip('/')}/v1/messages",
            headers={
                "x-api-key": ai_config["api_key"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ai_config["model"],
                "max_tokens": int(os.getenv("OPENAI_CHAT_MAX_OUTPUT_TOKENS", "1200")),
                "temperature": 0,
                "system": instructions,
                "messages": [
                    {
                        "role": "user",
                        "content": build_final_input_text(request, direct_tool_result),
                    }
                ],
            },
        )
        response.raise_for_status()
        payload = response.json()

    yield tool_event(direct_tool_result.get("server_label"), direct_tool_result.get("intent"))

    chunks = [
        item.get("text", "")
        for item in payload.get("content", [])
        if item.get("type") == "text"
    ]
    text = "".join(chunks)
    question = parse_question_payload(text)
    if question:
        yield sse_event("question", question)
        yield sse_event("done", {})
        return

    fallback = direct_tool_fallback_text(direct_tool_result, request.question)
    if is_non_answer_provider_text(text):
        text = ""
    yield sse_event("message", {"delta": text or fallback})
    yield sse_event("done", {})


def stream_codex_provider(
    request: ChatRequest,
    instructions: str,
    direct_tool_result: dict[str, Any] | None,
    ai_config: dict[str, str],
) -> Iterator[str]:
    """The Codex-CLI execution bridge - the one new piece this feature adds.

    Mirrors stream_chat_completion_provider/stream_anthropic_provider above
    exactly: those two providers ALSO can't receive Braidscope's live MCP
    tools directly (that's a Responses-API-only mechanism, the default path
    further down in stream_chat_response), so they only ever answer once
    Braidscope has already pre-fetched a direct_tool_result and embedded it
    in the prompt - same reasoning applies to Codex CLI, which has no
    knowledge of Google Ads/Klaviyo/HubSpot MCP servers at all. Reusing that
    exact same gate keeps this provider's behavior predictable and prevents
    Codex from trying to "answer anyway" using its own unrelated sandboxed
    tools against an empty throwaway working directory.
    """
    if not direct_tool_result:
        yield sse_event(
            "message",
            {
                "delta": (
                    "No relevant read-only MCP tool matched that request for the connected sources. "
                    "Try naming the data source or asking about a metric available in the connected account."
                )
            },
        )
        yield sse_event("done", {})
        return

    codex_home = ai_config.get("codex_home")
    if not codex_home:
        yield sse_event(
            "message",
            {"delta": "ChatGPT/Codex is not connected for this client. Connect it from Integrations first."},
        )
        yield sse_event("done", {})
        return

    # Confirmed live on 2026-09-23: a real Google Ads permission error
    # (403 CUSTOMER_NOT_ENABLED) reached this point disguised as a
    # "successful" tool call, and Codex burned the full 90-second exec
    # timeout trying to make sense of an error payload as if it were data -
    # the user saw "Codex took too long to respond" for a failure that had
    # nothing to do with Codex. A detected tool-level error is answered
    # immediately and honestly instead of ever being handed to the model.
    tool_error = detected_tool_error_message(direct_tool_result)
    if tool_error:
        yield tool_event(direct_tool_result.get("server_label"), direct_tool_result.get("intent"))
        yield sse_event("message", {"delta": tool_error})
        yield sse_event("done", {})
        return

    prompt = f"{instructions}\n\n{build_final_input_text(request, direct_tool_result)}"
    debug_log(
        "MODEL_CONTINUATION_STARTED provider=openai_codex model=%s tool=%s",
        ai_config.get("model"),
        direct_tool_result.get("selected_tool"),
    )

    # Emitted BEFORE the (potentially slow, up to CODEX_EXEC_TIMEOUT_SECONDS)
    # subprocess call, not after. Codex CLI is a real agentic coding tool,
    # not a quick chat-completion API call - a cold run can genuinely take
    # tens of seconds. Without an early byte on the SSE stream, the drawer
    # shows a bare "Working..." with zero feedback for that whole window;
    # this at least surfaces the trace row immediately so it's visible that
    # the MCP data was fetched and Codex has started, not stuck.
    yield tool_event(direct_tool_result.get("server_label"), direct_tool_result.get("intent"))

    t0 = time.monotonic()
    debug_log("  run_codex_exec starting (codex_home=%s, model=%s, prompt_len=%d)", codex_home, ai_config.get("model"), len(prompt))
    try:
        exec_result = run_codex_exec(codex_home, prompt, model=ai_config.get("model"))
    except ValueError as exc:
        debug_log("  run_codex_exec FAILED after %.2fs: %s", time.monotonic() - t0, exc)
        yield sse_event("message", {"delta": f"Codex could not answer this: {exc}"})
        yield sse_event("done", {})
        return
    text = exec_result.text
    debug_log(
        "  run_codex_exec succeeded after %.2fs (output_len=%d, requested_model=%s, actual_model=%s)",
        time.monotonic() - t0, len(text), ai_config.get("model"), exec_result.actual_model,
    )
    # The stored connector label (ai_config["model"], e.g. "gpt-4.1-mini") is
    # a setting picked before any model ran - it is never passed to the CLI
    # (see run_codex_exec's docstring for why forcing it breaks every call).
    # Surface what Codex actually used as a separate, optional wire event so
    # the real model is observable rather than silently hidden behind that
    # stored label; unrecognized SSE event types are already ignored by the
    # frontend (web/src/lib/chat.js), so this is additive and cannot break
    # existing rendering.
    if exec_result.actual_model:
        yield sse_event(
            "debug",
            {"requested_model": ai_config.get("model"), "actual_model": exec_result.actual_model},
        )

    question = parse_question_payload(text)
    if question:
        yield sse_event("question", question)
        yield sse_event("done", {})
        return

    provider_non_answer = is_non_answer_provider_text(text)
    fallback = direct_tool_fallback_text(direct_tool_result, request.question)
    debug_log(
        "FINAL_RESPONSE_GENERATED provider=openai_codex model=%s tool=%s provider_non_answer=%s fallback_available=%s",
        ai_config.get("model"),
        direct_tool_result.get("selected_tool"),
        provider_non_answer,
        bool(fallback),
    )
    if provider_non_answer:
        text = ""
    yield sse_event("message", {"delta": text or fallback})
    yield sse_event("done", {})


def stream_claude_code_provider(
    request: ChatRequest,
    instructions: str,
    direct_tool_result: dict[str, Any] | None,
    ai_config: dict[str, str],
) -> Iterator[str]:
    """Claude Code CLI execution bridge. Same contract as stream_codex_provider:
    Braidscope has already pre-fetched the MCP data and embedded it as text, so
    every Claude Code tool/MCP/skill is disabled and the model can only answer
    from that text. Instructions go in as the SYSTEM prompt (replacing Claude
    Code's default coding-agent persona), the question over stdin."""
    if not direct_tool_result:
        yield sse_event(
            "message",
            {
                "delta": (
                    "No relevant read-only MCP tool matched that request for the connected sources. "
                    "Try naming the data source or asking about a metric available in the connected account."
                )
            },
        )
        yield sse_event("done", {})
        return

    config_dir = ai_config.get("claude_config_dir")
    if not config_dir:
        yield sse_event("message", {"delta": "Claude is not connected for your account. Connect it from Integrations first."})
        yield sse_event("done", {})
        return

    tool_error = detected_tool_error_message(direct_tool_result)
    if tool_error:
        yield tool_event(direct_tool_result.get("server_label"), direct_tool_result.get("intent"))
        yield sse_event("message", {"delta": tool_error})
        yield sse_event("done", {})
        return

    debug_log(
        "MODEL_CONTINUATION_STARTED provider=claude_code model=%s tool=%s",
        ai_config.get("model"),
        direct_tool_result.get("selected_tool"),
    )
    yield tool_event(direct_tool_result.get("server_label"), direct_tool_result.get("intent"))

    t0 = time.monotonic()
    try:
        exec_result = run_claude_exec(
            config_dir,
            build_final_input_text(request, direct_tool_result),
            system_prompt=instructions,
            model=ai_config.get("model"),
        )
    except ClaudeAuthError as exc:
        debug_log("  run_claude_exec auth failure after %.2fs", time.monotonic() - t0)
        yield sse_event("message", {"delta": str(exc)})
        yield sse_event("done", {})
        return
    except ValueError as exc:
        debug_log("  run_claude_exec FAILED after %.2fs: %s", time.monotonic() - t0, exc)
        yield sse_event("message", {"delta": f"Claude could not answer this: {exc}"})
        yield sse_event("done", {})
        return
    text = exec_result.text
    debug_log(
        "  run_claude_exec succeeded after %.2fs (output_len=%d, requested_model=%s, actual_model=%s)",
        time.monotonic() - t0, len(text), ai_config.get("model"), exec_result.actual_model,
    )
    if exec_result.actual_model:
        yield sse_event(
            "debug",
            {"requested_model": ai_config.get("model"), "actual_model": exec_result.actual_model},
        )

    question = parse_question_payload(text)
    if question:
        yield sse_event("question", question)
        yield sse_event("done", {})
        return

    provider_non_answer = is_non_answer_provider_text(text)
    fallback = direct_tool_fallback_text(direct_tool_result, request.question)
    debug_log(
        "FINAL_RESPONSE_GENERATED provider=claude_code model=%s tool=%s provider_non_answer=%s fallback_available=%s",
        ai_config.get("model"),
        direct_tool_result.get("selected_tool"),
        provider_non_answer,
        bool(fallback),
    )
    if provider_non_answer:
        text = ""
    yield sse_event("message", {"delta": text or fallback})
    yield sse_event("done", {})


def stream_chat_response(
    request: ChatRequest,
    client_context: dict[str, Any],
    tools: list[dict[str, Any]],
    direct_tool_result: dict[str, Any] | None = None,
    ai_config: dict[str, str] | None = None,
) -> Iterator[str]:
    ai_config = ai_config or {
        "provider": "openai",
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "model": os.getenv("OPENAI_CHAT_MODEL", "gpt-5"),
    }
    client_kwargs: dict[str, Any] = {
        "api_key": ai_config.get("api_key") or os.getenv("OPENAI_API_KEY"),
        "timeout": float(os.getenv("OPENAI_CHAT_TIMEOUT_SECONDS", "90")),
    }
    if ai_config.get("base_url"):
        client_kwargs["base_url"] = ai_config["base_url"]
    # NOT constructed here anymore - see right before its one actual use
    # below. This used to run unconditionally, for EVERY provider, before
    # any of the openrouter/anthropic/openai_codex branches even got a
    # chance to run - none of those three ever use this client at all, they
    # each build their own. For openai_codex specifically there is no
    # api_key at all by design (Codex CLI authenticates through its own
    # on-disk session, not a bearer key), so OpenAI(api_key="") raised
    # `OpenAIError: Missing credentials...` immediately, on the very first
    # iteration of this generator, before the provider branch below was
    # ever reached - the exact, confirmed cause of Ask AI silently hanging
    # for the Codex provider (verified directly: OpenAI(api_key="") raises
    # synchronously) rather than answering or showing a real error.
    source_names = ", ".join(client_context["connected_sources"]) or "none listed"
    google_ads_customer_id = os.getenv("GOOGLE_ADS_CUSTOMER_ID")
    tool_labels = [tool.get("server_label") for tool in tools]
    has_klaviyo = "klaviyo" in tool_labels
    saw_mcp_call = False
    saw_text = False
    debug_log(
        "AI chat start client_id=%s sources=%s mcp_tools=%s openai_model=%s",
        client_context["id"],
        client_context["connected_sources"],
        tool_labels,
        ai_config.get("model", os.getenv("OPENAI_CHAT_MODEL", "gpt-5")),
    )
    if direct_tool_result:
        debug_log(
            "Using direct MCP result server=%s intent=%s",
            direct_tool_result.get("server_label"),
            direct_tool_result.get("intent"),
        )
    for tool in tools:
        debug_log(
            "MCP tool configured label=%s url=%s authorization_present=%s allowed_tools=%s",
            tool.get("server_label"),
            tool.get("server_url"),
            bool(tool.get("authorization")),
            tool.get("allowed_tools"),
        )
    instructions = (
        "You are Braidscope's marketing analytics assistant. "
        "Answer only from data returned by the available official MCP tools. "
        "Format answers for a narrow chat drawer: use a short intro, compact bullets, "
        "and avoid dumping long raw URLs unless the user asks for links. "
        "Do not invent metrics, campaign names, causes, or recommendations. "
        "If a tool cannot access the requested account or data, explain that clearly. "
        "If the user asks about Klaviyo account details, campaigns, flows, metrics, or reports, "
        "you must call the Klaviyo MCP tool before answering. "
        "If the user asks about HubSpot contacts, companies, deals, tickets, activities, or CRM records, "
        "you must call the HubSpot MCP tool before answering. "
        "Use the Klaviyo MCP account, campaign, flow, metric, and report tools when available. "
        "Never say a Klaviyo endpoint is unavailable unless the MCP tool call itself fails. "
        "Use read-only analysis. Never ask a tool to create, update, pause, delete, or modify anything. "
        f"The selected Braidscope client is {client_context['name']}. "
        f"The server verified these connected sources: {source_names}. "
        f"The configured Google Ads customer ID is {google_ads_customer_id or 'not configured'}. "
        "Do not use or mention data from a different client or account. "
        + GROUNDING_INSTRUCTIONS
        + " "
        + QUESTION_INSTRUCTIONS
    )

    try:
        if ai_config.get("provider") == "openrouter":
            yield from stream_chat_completion_provider(
                request,
                instructions,
                direct_tool_result,
                ai_config,
            )
            return
        if ai_config.get("provider") == "anthropic":
            yield from stream_anthropic_provider(
                request,
                instructions,
                direct_tool_result,
                ai_config,
            )
            return
        if ai_config.get("provider") == "claude_code":
            yield from stream_claude_code_provider(
                request,
                instructions,
                direct_tool_result,
                ai_config,
            )
            return
        if ai_config.get("provider") == "openai_codex":
            yield from stream_codex_provider(
                request,
                instructions,
                direct_tool_result,
                ai_config,
            )
            return

        client = OpenAI(**client_kwargs)
        input_text = build_final_input_text(request, direct_tool_result)
        response_tools = tools
        if direct_tool_result:
            response_tools = []
        response_options: dict[str, Any] = {
            "model": ai_config.get("model", os.getenv("OPENAI_CHAT_MODEL", "gpt-5")),
            "instructions": instructions,
            "input": input_text,
            "tools": response_tools,
            "stream": True,
            "store": False,
            "max_output_tokens": int(os.getenv("OPENAI_CHAT_MAX_OUTPUT_TOKENS", "1200")),
        }
        if direct_tool_result:
            yield tool_event(
                direct_tool_result.get("server_label"),
                direct_tool_result.get("intent"),
            )

        question_filter = QuestionFilter()
        announced_tools: set[tuple[str, str]] = set()
        stream = client.responses.create(**response_options)
        for event in stream:
            event_type = getattr(event, "type", "unknown")
            if event_type != "response.output_text.delta":
                debug_log("OpenAI stream event %s", safe_event_payload(event))
            if event_type.startswith("response.mcp_call") or (
                event_type == "response.output_item.added"
                and getattr(getattr(event, "item", None), "type", None) == "mcp_call"
            ):
                saw_mcp_call = True
                # Surface each MCP call once so the drawer can show real work.
                item = getattr(event, "item", None)
                server = getattr(event, "server_label", None) or getattr(item, "server_label", None)
                name = getattr(event, "name", None) or getattr(item, "name", None)
                key = (str(server or ""), str(name or ""))
                if key not in announced_tools:
                    announced_tools.add(key)
                    failed = event_type.endswith(".failed")
                    yield tool_event(server, name, "failed" if failed else "done")
            if event.type == "response.output_text.delta":
                saw_text = True
                visible = question_filter.feed(event.delta)
                if visible:
                    yield sse_event("message", {"delta": visible})
            elif event.type == "response.output_text.done":
                text = getattr(event, "text", "")
                if text and not saw_text:
                    saw_text = True
                    visible = question_filter.feed(text)
                    if visible:
                        yield sse_event("message", {"delta": visible})
            elif event.type == "response.completed":
                text = extract_response_text(getattr(event, "response", None))
                if text and not saw_text:
                    saw_text = True
                    visible = question_filter.feed(text)
                    if visible:
                        yield sse_event("message", {"delta": visible})
            elif event.type == "response.failed":
                error = getattr(event.response, "error", None)
                message = getattr(error, "message", None) or "OpenAI response failed"
                debug_log("OpenAI response failed: %s", message)
                yield sse_event("error", {"message": message})
                return

        question, leftover = question_filter.finish()
        if question:
            yield sse_event("question", question)
            yield sse_event("done", {})
            return
        if leftover:
            yield sse_event("message", {"delta": leftover})

        if not saw_text:
            fallback_text = direct_tool_fallback_text(
                direct_tool_result,
                request.question,
            )
            if fallback_text:
                yield sse_event("message", {"delta": fallback_text})
                saw_text = True
        if not saw_text:
            yield sse_event(
                "message",
                {
                    "delta": (
                        "I could not produce a final text answer from the connected MCP tool. "
                        "Please retry with a narrower question, like listing HubSpot contacts or Klaviyo account details."
                    )
                },
            )
        if has_klaviyo and not saw_mcp_call:
            debug_log(
                "WARNING Klaviyo MCP was configured but no mcp_call occurred for client_id=%s",
                client_context["id"],
            )
        yield sse_event("done", {})
    except Exception as exc:
        logger.exception("AI chat exception")
        yield sse_event("error", {"message": str(exc)})
