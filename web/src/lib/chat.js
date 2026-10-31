// SPDX-License-Identifier: AGPL-3.0-only
import { getSession } from "./auth";

function connectedSourceTypes(sources = []) {
  return sources
    .filter((source) => source.connected)
    .map((source) => source.type);
}

async function authenticatedFetch(path, options = {}) {
  // getSession() (lib/auth.js) is itself now bounded by a 15s timeout -
  // see its own comment for why that matters here specifically: this used
  // to be able to hang the entire Ask AI request for minutes, completely
  // silently, before the actual /py/chat fetch below even started.
  const session = await getSession();
  if (!session?.access_token) {
    throw new Error("Sign in to use Ask AI");
  }

  return fetch(path, {
    ...options,
    headers: {
      ...options.headers,
      Authorization: `Bearer ${session.access_token}`,
    },
  });
}

export async function getChatConfig(client, sources) {
  const sourceTypes = connectedSourceTypes(sources);
  const query = new URLSearchParams({
    client_id: client?.id || "",
    client_name: client?.name || "",
    connected_sources: sourceTypes.join(","),
  });
  const response = await authenticatedFetch(`/py/chat/config?${query}`);

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail || `Chat config API returned ${response.status}`);
  }

  return response.json();
}

export async function streamChat({
  question,
  client,
  sources,
  aiProvider,
  aiModel,
  onDelta,
  onTool,
  onQuestion,
}) {
  const controller = new AbortController();
  // This single deadline covers the WHOLE request: the backend's own MCP
  // tool-selection/fetch phase (main.py, before the response even starts
  // streaming) runs first, THEN the actual provider call. For the Codex/
  // ChatGPT provider that second phase is a real subprocess run
  // (CODEX_EXEC_TIMEOUT_SECONDS, up to 90s on its own - py/app/codex.py),
  // not a quick API call, so 90s total was too tight to ever let a
  // legitimate multi-step answer finish - it would abort mid-flight with
  // no error surfaced yet, reading as a stuck "Working...". 170s gives the
  // worst case (MCP tool-list ~30s + MCP tool-call ~45s + full Codex run
  // ~90s) realistic room while still failing loudly instead of hanging
  // forever.
  const timeout = window.setTimeout(() => controller.abort(), 170000);
  try {
    const response = await authenticatedFetch("/py/chat", {
      method: "POST",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        question,
        client_id: client?.id,
        client_name: client?.name,
        connected_sources: connectedSourceTypes(sources),
        ai_provider: aiProvider || null,
        ai_model: aiModel || null,
      }),
    }).catch((error) => {
      if (error.name === "AbortError") {
        throw new Error("The AI request took too long. Try a narrower question.");
      }
      throw error;
    });

    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(body?.detail || `Chat API returned ${response.status}`);
    }

    if (!response.body) {
      throw new Error("Chat API did not return a response stream");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read().catch((error) => {
        if (error.name === "AbortError") {
          throw new Error("The AI request took too long. Try a narrower question.");
        }
        throw error;
      });
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() || "";

      for (const eventBlock of events) {
        const lines = eventBlock.split("\n");
        const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
        const dataLine = lines.find((line) => line.startsWith("data:"));
        if (!dataLine) continue;

        const data = JSON.parse(dataLine.slice(5).trim());
        if (event === "message" && data.delta) onDelta(data.delta);
        // An MCP call the backend actually made — shown as a trace row.
        if (event === "tool") onTool?.(data);
        // The model needs a decision before it can answer.
        if (event === "question") onQuestion?.(data);
        if (event === "error") throw new Error(data.message || "Chat request failed");
      }
    }
  } finally {
    window.clearTimeout(timeout);
  }
}
