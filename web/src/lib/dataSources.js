// SPDX-License-Identifier: AGPL-3.0-only
import { SOURCES } from "./data";
import { supabase, isSupabaseConfigured } from "./supabase";
import { getSession } from "./auth";

const DEMO_CONNECTED = new Set(SOURCES.filter((source) => source.connected).map((source) => source.type));

export const OAUTH_RESULT_CHANNEL = "braidscope:oauth-result";

// Every OAuth-start function below ends by sending the browser to the
// provider's authorization page. Onboarding needs that to happen in a new
// tab (so the onboarding modal/state in the original tab survives), while
// every existing call site (the Integrations page) needs the current,
// same-tab redirect left exactly as it was. `newTab` defaults to false so
// nothing changes unless a caller opts in.
export function navigateForOAuth(url, { newTab = false } = {}) {
  if (newTab) {
    window.open(url, "_blank", "noopener,noreferrer");
    return;
  }
  window.location.assign(url);
}

function oauthContextForTab(newTab) {
  return newTab ? "onboarding" : "app";
}

export function mergeSourceCatalog(records = [], demoMode = false) {
  const byType = new Map(records.map((record) => [record.type, record]));

  return SOURCES.map((source) => {
    const record = byType.get(source.type);
    const status = record?.status || (demoMode && DEMO_CONNECTED.has(source.type) ? "connected" : "disabled");

    return {
      ...source,
      recordId: record?.id || null,
      status,
      connected: status === "connected" || status === "syncing",
      lastSyncAt: record?.last_sync_at || null,
      config: record?.config || {},
    };
  });
}

export function getSourceSummary(sources = []) {
  const connected = sources.filter((source) => source.status === "connected");
  const syncing = sources.filter((source) => source.status === "syncing");
  const needsReconnect = sources.filter((source) => source.status === "needs_reconnect");
  const active = [...connected, ...syncing];
  const latestSync = active
    .map((source) => source.lastSyncAt)
    .filter(Boolean)
    .sort()
    .at(-1);

  const countLabel = `${active.length} ${active.length === 1 ? "source" : "sources"} active`;
  const syncLabel = needsReconnect.length
    ? `${needsReconnect.length} ${needsReconnect.length === 1 ? "source needs" : "sources need"} reconnect`
    : latestSync
      ? `last synced ${formatSyncTime(latestSync)}`
      : active.length
        ? "sync status pending"
        : "no sources connected";

  return {
    activeCount: active.length,
    connectedCount: connected.length,
    syncingCount: syncing.length,
    reconnectCount: needsReconnect.length,
    latestSync,
    countLabel,
    syncLabel,
    headline: `${countLabel} · ${syncLabel}`,
  };
}

export function getSourceByType(sources = [], type) {
  return sources.find((source) => source.type === type) || null;
}

export function formatSourceStatus(source) {
  if (!source) return "Not connected";
  if (source.status === "needs_reconnect") return "Needs reconnect";
  if (source.status === "syncing") return "Syncing now";
  if (source.status === "connected") {
    return source.lastSyncAt ? `Synced ${formatSyncTime(source.lastSyncAt)}` : "Connected";
  }
  return "Not connected";
}

function formatSyncTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "recently";
  const day = date.toLocaleDateString([], { month: "short", day: "numeric" });
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `${day}, ${time}`;
}

export async function listDataSources(clientId) {
  if (!isSupabaseConfigured() || !clientId || clientId.startsWith("demo-")) {
    return { ok: true, records: [] };
  }

  const { data, error } = await supabase
    .from("data_sources")
    .select("id, client_id, type, status, config, last_sync_at, created_at")
    .eq("client_id", clientId)
    .order("created_at", { ascending: true });

  if (error) return { ok: false, error: error.message, records: [] };
  return { ok: true, records: data || [] };
}

export async function connectDataSource(clientId, type) {
  if (!clientId) return { ok: false, error: "Select a client before connecting a source" };
  if (!isSupabaseConfigured() || clientId.startsWith("demo-")) {
    return {
      ok: true,
      record: {
        id: `demo-source-${type}`,
        client_id: clientId,
        type,
        status: "connected",
        config: {},
        last_sync_at: new Date().toISOString(),
      },
    };
  }

  const nextPayload = {
    status: "connected",
    last_sync_at: new Date().toISOString(),
  };

  const { data: existing, error: lookupError } = await supabase
    .from("data_sources")
    .select("id")
    .eq("client_id", clientId)
    .eq("type", type)
    .limit(1)
    .maybeSingle();

  if (lookupError) return { ok: false, error: lookupError.message };

  if (existing?.id) {
    const { data, error } = await supabase
      .from("data_sources")
      .update(nextPayload)
      .eq("id", existing.id)
      .select("id, client_id, type, status, config, last_sync_at, created_at")
      .single();

    if (error) return { ok: false, error: error.message };
    return { ok: true, record: data };
  }

  const { data, error } = await supabase
    .from("data_sources")
    .insert({
      client_id: clientId,
      type,
      config: {},
      ...nextPayload,
    })
    .select("id, client_id, type, status, config, last_sync_at, created_at")
    .single();

  if (error) return { ok: false, error: error.message };
  return { ok: true, record: data };
}

export async function startKlaviyoOAuth(client, { newTab = false } = {}) {
  if (!client?.id) return { ok: false, error: "Select a client before connecting Klaviyo" };
  if (client.id.startsWith("demo-")) {
    return { ok: false, error: "Sign in to connect a real Klaviyo account" };
  }

  const session = await getSession();
  if (!session?.access_token) return { ok: false, error: "Sign in to connect Klaviyo" };

  const response = await fetch("/py/connectors/klaviyo/oauth/start", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.access_token}`,
    },
    body: JSON.stringify({
      client_id: client.id,
      client_name: client.name,
      oauth_context: oauthContextForTab(newTab),
    }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `Klaviyo OAuth returned ${response.status}` };
  }
  if (!body?.authorization_url) {
    return { ok: false, error: "Klaviyo OAuth did not return an authorization URL" };
  }

  navigateForOAuth(body.authorization_url, { newTab });
  return { ok: true };
}

export async function startHubSpotOAuth(client, { newTab = false } = {}) {
  if (!client?.id) return { ok: false, error: "Select a client before connecting HubSpot" };
  if (client.id.startsWith("demo-")) {
    return { ok: false, error: "Sign in to connect a real HubSpot account" };
  }

  const session = await getSession();
  if (!session?.access_token) return { ok: false, error: "Sign in to connect HubSpot" };

  const response = await fetch("/py/connectors/hubspot/oauth/start", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.access_token}`,
    },
    body: JSON.stringify({
      client_id: client.id,
      client_name: client.name,
      oauth_context: oauthContextForTab(newTab),
    }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `HubSpot OAuth returned ${response.status}` };
  }
  if (!body?.authorization_url) {
    return { ok: false, error: "HubSpot OAuth did not return an authorization URL" };
  }

  navigateForOAuth(body.authorization_url, { newTab });
  return { ok: true };
}

export async function startOpenRouterOAuth(client, model, { newTab = false } = {}) {
  if (!client?.id) return { ok: false, error: "Select a client before connecting OpenRouter" };
  if (client.id.startsWith("demo-")) {
    return { ok: false, error: "Sign in to connect a real OpenRouter account" };
  }

  const session = await getSession();
  if (!session?.access_token) return { ok: false, error: "Sign in to connect OpenRouter" };

  const response = await fetch("/py/connectors/openrouter/oauth/start", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.access_token}`,
    },
    body: JSON.stringify({
      client_id: client.id,
      client_name: client.name,
      model,
      oauth_context: oauthContextForTab(newTab),
    }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `OpenRouter OAuth returned ${response.status}` };
  }
  if (!body?.authorization_url) {
    return { ok: false, error: "OpenRouter OAuth did not return an authorization URL" };
  }

  navigateForOAuth(body.authorization_url, { newTab });
  return { ok: true };
}

export async function startOpenAICodexAuth(client, model, { newTab = false } = {}) {
  if (!client?.id) return { ok: false, error: "Select a client before connecting OpenAI" };
  if (client.id.startsWith("demo-")) {
    return { ok: false, error: "Sign in to connect a real ChatGPT account" };
  }

  const session = await getSession();
  if (!session?.access_token) return { ok: false, error: "Sign in to connect OpenAI with ChatGPT" };

  try {
    const response = await fetch("/py/connectors/openai/codex/start", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${session.access_token}`,
      },
      body: JSON.stringify({
        client_id: client.id,
        client_name: client.name,
        // No fallback to a hardcoded model id here anymore (this used to
        // default to "gpt-4.1-mini") - that's an OpenAI Responses-API model
        // id, not one Codex CLI's ChatGPT-account auth mode accepts at all
        // (confirmed live: it's rejected outright), so silently sending it
        // just meant the connector's stored model label could never be
        // correct from the moment it was connected. Sending nothing here
        // lets the backend leave it unset until real discovery
        // (py/app/codex.py's discover_codex_models, run right after this
        // login finishes) establishes the actual verified default.
        model: model || "",
        oauth_context: oauthContextForTab(newTab),
      }),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      return { ok: false, error: body?.detail || `OpenAI Codex login returned ${response.status}` };
    }
    if (!body?.session_id) {
      return { ok: false, error: "OpenAI Codex login did not return a session id" };
    }
    if (!body?.authorization_url && !body?.opened_in_browser) {
      return { ok: false, error: "OpenAI Codex login did not return an authorization URL" };
    }

    if (body.authorization_url) navigateForOAuth(body.authorization_url, { newTab });
    return {
      ok: true,
      sessionId: body.session_id,
      openedInBrowser: Boolean(body.opened_in_browser),
      userCode: body.user_code || null,
      authorizationUrl: body.authorization_url || null,
    };
  } catch (error) {
    return { ok: false, error: error?.message || "Could not start OpenAI Codex login" };
  }
}

export async function getOpenAICodexServerConnection(client) {
  if (!client?.id || client.id.startsWith("demo-")) return { ok: true, connected: false };

  const session = await getSession();
  if (!session?.access_token) return { ok: true, connected: false };

  const query = new URLSearchParams({
    client_id: client.id,
    client_name: client.name || "",
  });
  try {
    const response = await fetch(`/py/connectors/openai/codex/status?${query}`, {
      headers: { Authorization: `Bearer ${session.access_token}` },
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      return { ok: false, connected: false, pending: false, error: body?.detail || `OpenAI Codex status returned ${response.status}` };
    }
    return {
      ok: true,
      connected: Boolean(body?.connected),
      pending: body?.status === "pending",
      needsReconnect: body?.status === "needs_reconnect",
      error: body?.error || null,
      storage: body?.storage || "none",
      model: body?.model,
      userCode: body?.user_code || null,
      authorizationUrl: body?.authorization_url || null,
    };
  } catch (error) {
    return {
      ok: false,
      connected: false,
      pending: false,
      error: error?.message || "Could not reach OpenAI Codex status",
      storage: "none",
    };
  }
}

// Claude Code CLI sign-in is PER USER (each teammate connects their own Claude
// account). The backend prints a Claude sign-in URL, the user signs in on
// Anthropic's own page, and pastes the code that page shows back here - we
// only forward it to the CLI, never store it.
export async function startClaudeCodeAuth(client, { newTab = false } = {}) {
  if (!client?.id) return { ok: false, error: "Select a client before connecting Claude" };
  if (client.id.startsWith("demo-")) {
    return { ok: false, error: "Sign in to connect a real Claude account" };
  }

  const session = await getSession();
  if (!session?.access_token) return { ok: false, error: "Sign in to connect Claude" };

  try {
    const response = await fetch("/py/connectors/anthropic/claude-code/start", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${session.access_token}`,
      },
      body: JSON.stringify({
        client_id: client.id,
        client_name: client.name,
        oauth_context: oauthContextForTab(newTab),
      }),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      return { ok: false, error: body?.detail || `Claude sign-in returned ${response.status}` };
    }
    if (!body?.authorization_url) {
      return { ok: false, error: "Claude sign-in did not return a sign-in URL" };
    }
    navigateForOAuth(body.authorization_url, { newTab: true });
    return { ok: true, sessionId: body.session_id, authorizationUrl: body.authorization_url };
  } catch (error) {
    return { ok: false, error: error?.message || "Could not start Claude sign-in" };
  }
}

export async function submitClaudeCodeCode(client, code) {
  if (!client?.id) return { ok: false, error: "Select a client first" };
  const session = await getSession();
  if (!session?.access_token) return { ok: false, error: "Sign in to connect Claude" };
  if (!code?.trim()) return { ok: false, error: "Paste the code Claude showed you" };

  try {
    const response = await fetch("/py/connectors/anthropic/claude-code/submit-code", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${session.access_token}`,
      },
      body: JSON.stringify({ client_id: client.id, client_name: client.name, code: code.trim() }),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      return { ok: false, error: body?.detail || `Claude sign-in returned ${response.status}` };
    }
    return { ok: true, connected: Boolean(body?.connected), model: body?.model };
  } catch (error) {
    return { ok: false, error: error?.message || "Could not finish Claude sign-in" };
  }
}

export async function getClaudeCodeServerConnection(client) {
  if (!client?.id || client.id.startsWith("demo-")) return { ok: true, connected: false };

  const session = await getSession();
  if (!session?.access_token) return { ok: true, connected: false };

  const query = new URLSearchParams({
    client_id: client.id,
    client_name: client.name || "",
  });
  try {
    const response = await fetch(`/py/connectors/anthropic/claude-code/status?${query}`, {
      headers: { Authorization: `Bearer ${session.access_token}` },
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      return { ok: false, connected: false, pending: false, error: body?.detail || `Claude status returned ${response.status}` };
    }
    return {
      ok: true,
      connected: Boolean(body?.connected),
      pending: body?.status === "pending",
      needsReconnect: body?.status === "needs_reconnect",
      error: body?.error || null,
      storage: body?.storage || "none",
      model: body?.model,
      authorizationUrl: body?.authorization_url || null,
    };
  } catch (error) {
    return {
      ok: false,
      connected: false,
      pending: false,
      error: error?.message || "Could not reach Claude status",
      storage: "none",
    };
  }
}

export async function getKlaviyoServerConnection(client) {
  if (!client?.id || client.id.startsWith("demo-")) return { ok: true, connected: false };

  const session = await getSession();
  if (!session?.access_token) return { ok: true, connected: false };

  const query = new URLSearchParams({
    client_id: client.id,
    client_name: client.name || "",
  });
  const response = await fetch(`/py/connectors/klaviyo/status?${query}`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `Klaviyo status returned ${response.status}` };
  }
  return {
    ok: true,
    connected: Boolean(body?.connected),
    storage: body?.storage || "server",
  };
}

export async function getHubSpotServerConnection(client) {
  if (!client?.id || client.id.startsWith("demo-")) return { ok: true, connected: false };

  const session = await getSession();
  if (!session?.access_token) return { ok: true, connected: false };

  const query = new URLSearchParams({
    client_id: client.id,
    client_name: client.name || "",
  });
  const response = await fetch(`/py/connectors/hubspot/status?${query}`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `HubSpot status returned ${response.status}` };
  }
  return {
    ok: true,
    connected: Boolean(body?.connected),
    storage: body?.storage || "server",
  };
}

export async function getOpenRouterServerConnection(client) {
  if (!client?.id || client.id.startsWith("demo-")) return { ok: true, connected: false };

  const session = await getSession();
  if (!session?.access_token) return { ok: true, connected: false };

  const query = new URLSearchParams({
    client_id: client.id,
    client_name: client.name || "",
  });
  const response = await fetch(`/py/connectors/openrouter/status?${query}`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `OpenRouter status returned ${response.status}` };
  }
  return {
    ok: true,
    connected: Boolean(body?.connected),
    storage: body?.storage || "server",
    model: body?.model,
  };
}

export async function saveBYOKAIProvider(client, provider, apiKey, model) {
  if (!client?.id) return { ok: false, error: "Select a client before connecting an AI provider" };
  if (client.id.startsWith("demo-")) {
    return { ok: false, error: "Sign in to connect a real AI provider" };
  }
  if (!apiKey?.trim()) return { ok: false, error: "Paste an API key first" };

  const session = await getSession();
  if (!session?.access_token) return { ok: false, error: "Sign in to connect an AI provider" };

  const response = await fetch("/py/connectors/ai/byok", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.access_token}`,
    },
    body: JSON.stringify({
      client_id: client.id,
      client_name: client.name,
      provider,
      api_key: apiKey,
      model,
    }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `AI provider returned ${response.status}` };
  }
  return { ok: true, model: body?.model };
}

export async function getBYOKAIProviderConnection(client, provider) {
  if (!client?.id || client.id.startsWith("demo-")) return { ok: true, connected: false };

  const session = await getSession();
  if (!session?.access_token) return { ok: true, connected: false };

  const query = new URLSearchParams({
    provider,
    client_id: client.id,
    client_name: client.name || "",
  });
  const response = await fetch(`/py/connectors/ai/byok/status?${query}`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `AI provider status returned ${response.status}` };
  }
  return {
    ok: true,
    connected: Boolean(body?.connected),
    storage: body?.storage || "server",
    model: body?.model,
  };
}

export async function disconnectAIProvider(client, provider) {
  if (!client?.id) return { ok: false, error: "Select a client before disconnecting an AI provider" };

  const session = await getSession();
  if (!session?.access_token) return { ok: false, error: "Sign in to disconnect an AI provider" };

  const response = await fetch("/py/connectors/ai/disconnect", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.access_token}`,
    },
    body: JSON.stringify({
      client_id: client.id,
      client_name: client.name,
      provider,
    }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `AI provider disconnect returned ${response.status}` };
  }
  return { ok: true, provider: body?.provider || provider };
}

export async function disconnectDataConnector(client, sourceType) {
  if (!client?.id) return { ok: false, error: "Select a client first" };

  const session = await getSession();
  if (!session?.access_token) return { ok: false, error: "Sign in to disconnect" };

  const response = await fetch("/py/connectors/data/disconnect", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.access_token}`,
    },
    body: JSON.stringify({
      client_id: client.id,
      client_name: client.name,
      source_type: sourceType,
    }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `Disconnect returned ${response.status}` };
  }
  return { ok: true, source_type: body?.source_type || sourceType };
}

export async function listAIProviderModels(client, provider) {
  if (!client?.id || !provider) return { ok: true, models: [] };

  const session = await getSession();
  if (!session?.access_token) return { ok: false, error: "Sign in to load models", models: [] };

  const query = new URLSearchParams({
    provider,
    client_id: client.id,
    client_name: client.name || "",
  });
  const response = await fetch(`/py/connectors/ai/models?${query}`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `AI models returned ${response.status}`, models: [] };
  }
  return {
    ok: true,
    models: Array.isArray(body?.models) ? body.models : [],
    // The backend verifies Codex models in the background and reports it here.
    loading: Boolean(body?.loading),
  };
}

export async function previewAIProviderModels(client, provider, apiKey = "") {
  if (!client?.id || !provider) return { ok: true, models: [] };

  const session = await getSession();
  if (!session?.access_token) return { ok: false, error: "Sign in to load models", models: [] };

  const response = await fetch("/py/connectors/ai/models/preview", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.access_token}`,
    },
    body: JSON.stringify({
      client_id: client.id,
      client_name: client.name,
      provider,
      api_key: apiKey || null,
    }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `AI models returned ${response.status}`, models: [] };
  }
  return {
    ok: true,
    models: Array.isArray(body?.models) ? body.models : [],
  };
}

export async function startGoogleAdsOAuth(client, { newTab = false } = {}) {
  if (!client?.id) return { ok: false, error: "Select a client before connecting Google Ads" };
  if (client.id.startsWith("demo-")) {
    return { ok: false, error: "Sign in to connect a real Google Ads account" };
  }

  const session = await getSession();
  if (!session?.access_token) return { ok: false, error: "Sign in to connect Google Ads" };

  const response = await fetch("/py/connectors/google-ads/oauth/start", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.access_token}`,
    },
    body: JSON.stringify({
      client_id: client.id,
      client_name: client.name,
      oauth_context: oauthContextForTab(newTab),
    }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `Google Ads OAuth returned ${response.status}` };
  }
  if (!body?.authorization_url) {
    return { ok: false, error: "Google Ads OAuth did not return an authorization URL" };
  }

  navigateForOAuth(body.authorization_url, { newTab });
  return { ok: true };
}

export async function getGoogleAdsServerConnection(client) {
  if (!client?.id || client.id.startsWith("demo-")) return { ok: true, connected: false };

  const session = await getSession();
  if (!session?.access_token) return { ok: true, connected: false };

  const query = new URLSearchParams({
    client_id: client.id,
    client_name: client.name || "",
  });
  const response = await fetch(`/py/connectors/google-ads/status?${query}`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `Google Ads status returned ${response.status}` };
  }
  return {
    ok: true,
    connected: Boolean(body?.connected),
    storage: body?.storage || "server",
  };
}


export async function startGA4OAuth(client, { newTab = false } = {}) {
  if (!client?.id) return { ok: false, error: "Select a client before connecting Google Analytics 4" };
  if (client.id.startsWith("demo-")) {
    return { ok: false, error: "Sign in to connect a real Google Analytics 4 account" };
  }

  const session = await getSession();
  if (!session?.access_token) return { ok: false, error: "Sign in to connect Google Analytics 4" };

  const response = await fetch("/py/connectors/google-analytics-4/oauth/start", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.access_token}`,
    },
    body: JSON.stringify({
      client_id: client.id,
      client_name: client.name,
      oauth_context: oauthContextForTab(newTab),
    }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `Google Analytics 4 OAuth returned ${response.status}` };
  }
  if (!body?.authorization_url) {
    return { ok: false, error: "Google Analytics 4 OAuth did not return an authorization URL" };
  }

  navigateForOAuth(body.authorization_url, { newTab });
  return { ok: true };
}

export async function getGA4ServerConnection(client) {
  if (!client?.id || client.id.startsWith("demo-")) return { ok: true, connected: false };

  const session = await getSession();
  if (!session?.access_token) return { ok: true, connected: false };

  const query = new URLSearchParams({
    client_id: client.id,
    client_name: client.name || "",
  });
  const response = await fetch(`/py/connectors/google-analytics-4/status?${query}`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `Google Analytics 4 status returned ${response.status}` };
  }
  return {
    ok: true,
    connected: Boolean(body?.connected),
    storage: body?.storage || "server",
  };
}


export async function startMetaAdsOAuth(client, { newTab = false } = {}) {
  if (!client?.id) return { ok: false, error: "Select a client before connecting Meta Ads" };
  if (client.id.startsWith("demo-")) {
    return { ok: false, error: "Sign in to connect a real Meta Ads account" };
  }

  const session = await getSession();
  if (!session?.access_token) return { ok: false, error: "Sign in to connect Meta Ads" };

  const response = await fetch("/py/connectors/meta-ads/oauth/start", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.access_token}`,
    },
    body: JSON.stringify({
      client_id: client.id,
      client_name: client.name,
      oauth_context: oauthContextForTab(newTab),
    }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `Meta Ads OAuth returned ${response.status}` };
  }
  if (!body?.authorization_url) {
    return { ok: false, error: "Meta Ads OAuth did not return an authorization URL" };
  }

  navigateForOAuth(body.authorization_url, { newTab });
  return { ok: true };
}

export async function getMetaAdsServerConnection(client) {
  if (!client?.id || client.id.startsWith("demo-")) return { ok: true, connected: false };

  const session = await getSession();
  if (!session?.access_token) return { ok: true, connected: false };

  const query = new URLSearchParams({
    client_id: client.id,
    client_name: client.name || "",
  });
  const response = await fetch(`/py/connectors/meta-ads/status?${query}`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    return { ok: false, error: body?.detail || `Meta Ads status returned ${response.status}` };
  }
  return {
    ok: true,
    connected: Boolean(body?.connected),
    storage: body?.storage || "server",
  };
}

export async function startShopifyOAuth(clientId, shopDomain) {
  if (!clientId) return { ok: false, error: "Select a client before connecting Shopify" };
  if (!shopDomain?.trim()) return { ok: false, error: "Enter the Shopify shop domain" };
  if (!isSupabaseConfigured() || clientId.startsWith("demo-")) {
    return { ok: false, error: "Shopify OAuth needs Supabase auth and the Python service configured" };
  }

  const session = await getSession();
  const token = session?.access_token;
  if (!token) return { ok: false, error: "Sign in again before connecting Shopify" };

  const params = new URLSearchParams({
    client_id: clientId,
    shop: shopDomain.trim(),
  });
  const response = await fetch(`/py/connectors/shopify/oauth/start?${params}`, {
    headers: { Authorization: `Bearer ${token}` },
  });

  if (!response.ok) {
    let detail = `Shopify OAuth returned ${response.status}`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      // Keep the HTTP status fallback when the API returns a non-JSON error.
    }
    return { ok: false, error: detail };
  }

  const payload = await response.json();
  if (!payload.auth_url) return { ok: false, error: "Shopify OAuth URL was not returned" };
  return { ok: true, authUrl: payload.auth_url };
}
