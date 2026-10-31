# SPDX-License-Identifier: AGPL-3.0-only
from typing import Any

from .openrouter import decrypt_api_key, encrypt_api_key
from .supabase_admin import (
    delete_connector_token,
    get_connector_token,
    mark_data_source_disconnected,
    supabase_admin_configured,
    upsert_connector_token,
    upsert_data_source_connected,
)

OPENAI_SOURCE_TYPE = "openai_ai"
ANTHROPIC_SOURCE_TYPE = "anthropic_ai"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-20250514"

BYOK_MEMORY: dict[str, dict[str, dict[str, Any]]] = {}


def normalize_openai_model(model: str | None) -> str:
    candidate = (model or "").strip()
    return candidate or DEFAULT_OPENAI_MODEL


def normalize_anthropic_model(model: str | None) -> str:
    candidate = (model or "").strip()
    return candidate or DEFAULT_ANTHROPIC_MODEL


def normalize_byok_model(provider: str, model: str | None) -> str:
    if provider == "anthropic":
        return normalize_anthropic_model(model)
    return normalize_openai_model(model)


def source_type_for_provider(provider: str) -> str:
    if provider == "openai":
        return OPENAI_SOURCE_TYPE
    if provider == "anthropic":
        return ANTHROPIC_SOURCE_TYPE
    raise ValueError("Unsupported BYOK provider")


async def save_byok_key(
    client_id: str,
    provider: str,
    api_key: str,
    model: str | None,
    user_id: str | None = None,
) -> dict[str, str]:
    if not supabase_admin_configured():
        raise ValueError(
            "AI provider storage is not configured. Set SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY on the backend."
        )

    clean_key = api_key.strip()
    if not clean_key:
        raise ValueError("API key is required")

    source_type = source_type_for_provider(provider)
    normalized_model = normalize_byok_model(provider, model)
    memory_token = {
        "api_key": clean_key,
        "model": normalized_model,
        "provider": provider,
    }
    stored = {
        "api_key_encrypted": encrypt_api_key(clean_key),
        "model": normalized_model,
        "provider": provider,
    }

    BYOK_MEMORY.setdefault(client_id, {})[source_type] = memory_token
    await upsert_connector_token(client_id, source_type, stored, user_id=user_id)
    await upsert_data_source_connected(client_id, source_type, user_id=user_id)
    return {"provider": provider, "model": normalized_model}


async def get_byok_connection_status(client_id: str, provider: str) -> dict[str, Any]:
    source_type = source_type_for_provider(provider)
    token = BYOK_MEMORY.get(client_id, {}).get(source_type) or await get_connector_token(
        client_id,
        source_type,
    )
    if not token:
        return {
            "connected": False,
            "storage": "none",
            "model": normalize_byok_model(provider, None),
        }
    return {
        "connected": True,
        "storage": "memory" if source_type in BYOK_MEMORY.get(client_id, {}) else "database",
        "model": normalize_byok_model(provider, token.get("model")),
    }


async def get_byok_chat_config(client_id: str, provider: str) -> dict[str, str] | None:
    source_type = source_type_for_provider(provider)
    token = BYOK_MEMORY.get(client_id, {}).get(source_type) or await get_connector_token(
        client_id,
        source_type,
    )
    if not token or not (token.get("api_key") or token.get("api_key_encrypted")):
        return None

    api_key = token.get("api_key")
    if not api_key:
        api_key = decrypt_api_key(token["api_key_encrypted"])

    config = {
        "provider": provider,
        "api_key": api_key,
        "model": normalize_byok_model(provider, token.get("model")),
    }
    if provider == "anthropic":
        config["base_url"] = "https://api.anthropic.com"
    return config


async def disconnect_byok_provider(client_id: str, provider: str, user_id: str | None = None) -> None:
    source_type = source_type_for_provider(provider)
    BYOK_MEMORY.get(client_id, {}).pop(source_type, None)
    await delete_connector_token(client_id, source_type)
    await mark_data_source_disconnected(client_id, source_type, user_id=user_id)
