// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useMemo, useRef, useState } from "react";
import ConnectorLogo from "../components/ConnectorLogo";
import { CONNECTOR_FILTERS } from "../lib/data";
import { formatSourceStatus } from "../lib/dataSources";
import { canPerformAction } from "../lib/permissions";

const AI_PROVIDER_TYPES = new Set([
  "openrouter_ai", "openai_ai", "anthropic_ai",
]);

const DATA_CONNECTOR_TYPES = new Set([
  "google_ads", "google_analytics_4", "meta_ads", "hubspot_crm", "klaviyo",
]);

function canDisconnect(source) {
  return source.connected && (
    AI_PROVIDER_TYPES.has(source.type)
    || DATA_CONNECTOR_TYPES.has(source.type)
  );
}

function stateClass(source) {
  if (source.status === "needs_reconnect") return "st-warn";
  if (source.status === "pending") return "st-warn";
  if (source.status === "syncing") return "st-sync";
  return "st-live";
}

function stateLabel(source) {
  if (source.status === "needs_reconnect") return "Reconnect";
  if (source.status === "pending") return source.type === "anthropic_ai" ? "Waiting for Claude" : "Waiting for ChatGPT";
  if (source.status === "syncing") return "Syncing";
  return "Connected";
}

export default function Integrations({
  client,
  org,
  sources,
  sourcesLoading,
  sourcesError,
  onConnect,
  onManage,
  onDisconnect,
  onAddClient,
  onRefresh,
}) {
  // Per the RBAC permission matrix (lib/settings.js CLIENT_ROLE_INFO):
  // Viewer can only see connection status (no buttons at all). Editor can
  // connect/manage but not disconnect (Integrations' EDIT tier has no
  // delete). Admin/Owner get everything, including disconnect.
  const canModify = canPerformAction(client, org, "Integrations", "edit");
  const canRemove = canPerformAction(client, org, "Integrations", "delete");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [pendingDisconnect, setPendingDisconnect] = useState(null);
  const [disconnecting, setDisconnecting] = useState(false);
  const [disconnectError, setDisconnectError] = useState("");
  const cancelDisconnectRef = useRef(null);

  useEffect(() => {
    if (!pendingDisconnect) return undefined;

    const previousFocus = document.activeElement;
    const focusTimer = window.setTimeout(() => cancelDisconnectRef.current?.focus(), 0);
    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setPendingDisconnect(null);
        setDisconnectError("");
      }
    };

    document.addEventListener("keydown", handleKeyDown, true);
    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", handleKeyDown, true);
      previousFocus?.focus?.();
    };
  }, [pendingDisconnect]);

  const requestDisconnect = (source) => {
    setDisconnectError("");
    setPendingDisconnect(source);
  };

  const confirmDisconnect = async () => {
    if (!pendingDisconnect) return;
    setDisconnecting(true);
    setDisconnectError("");
    const result = await onDisconnect(pendingDisconnect);
    setDisconnecting(false);
    if (result === false) {
      setDisconnectError(`Could not disconnect ${pendingDisconnect.name}. Please try again.`);
      return;
    }
    setPendingDisconnect(null);
  };

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return sources.filter((source) => {
      if (filter === "connected" && !source.connected) return false;
      if (filter !== "all" && filter !== "connected" && source.group !== filter) return false;
      if (!needle) return true;
      return [source.name, source.cat, source.desc]
        .filter(Boolean)
        .some((field) => field.toLowerCase().includes(needle));
    });
  }, [sources, query, filter]);

  const dataConnectors = filtered.filter((s) => !AI_PROVIDER_TYPES.has(s.type) && !s.comingSoon);
  const comingSoon = filtered.filter((s) => s.comingSoon);
  const aiProviders = filtered.filter((s) => AI_PROVIDER_TYPES.has(s.type));

  const connectedCount = sources.filter((s) => s.connected).length;
  const nothingMatches = !sourcesLoading
    && !dataConnectors.length && !comingSoon.length && !aiProviders.length;

  const renderConnectorCard = (source) => {
    // A source that dropped its token is not `connected`, but it still needs to
    // read as a live-but-broken connection rather than an untouched one.
    const needsReconnect = source.status === "needs_reconnect";
    const isPending = source.status === "pending";
    const showState = source.connected || needsReconnect || isPending;

    return (
      <article
        key={source.type}
        className={`conn-card${source.connected ? " live" : ""}${needsReconnect ? " attention" : ""}`}
      >
        <div className="conn-top">
          <ConnectorLogo source={source} />
          <div className="conn-head">
            <div className="conn-name">
              {source.name}
              {showState ? (
                <span className={`conn-state ${stateClass(source)}`}>
                  <span className="dot" />{stateLabel(source)}
                </span>
              ) : source.popular ? (
                <span className="conn-pop">POPULAR</span>
              ) : null}
            </div>
            <div className="conn-cat">{source.cat}</div>
          </div>
        </div>

        {source.desc && <p className="conn-desc">{source.desc}</p>}

        <div className="conn-foot">
          <span className="conn-meta">
            {showState ? formatSourceStatus(source) : source.setup}
          </span>
          {source.connected ? (
            <>
              {canModify && <button className="conn-btn quiet" onClick={() => onManage(source)}>Manage</button>}
              {canRemove && canDisconnect(source) && (
                <button
                  className="conn-btn icon danger"
                  onClick={() => requestDisconnect(source)}
                  title={`Disconnect ${source.name}`}
                  aria-label={`Disconnect ${source.name}`}
                >
                  <i className="ti ti-x" aria-hidden="true" />
                </button>
              )}
            </>
          ) : canModify ? (
            <button className="conn-btn primary" onClick={() => onConnect(source)}>
              {needsReconnect ? "Reconnect" : "Connect"}
            </button>
          ) : null}
        </div>
      </article>
    );
  };

  const renderSoonCard = (source) => (
    <article key={source.type} className="conn-card soon">
      <div className="conn-top">
        <ConnectorLogo source={source} />
        <div className="conn-head">
          <div className="conn-name">{source.name}</div>
          <div className="conn-cat">{source.cat}</div>
        </div>
      </div>
      {source.desc && <p className="conn-desc">{source.desc}</p>}
      <div className="conn-foot">
        <span className="conn-meta">
          <span className="conn-state st-soon"><span className="dot" />{source.eta || "In build"}</span>
        </span>
        <button className="conn-btn quiet" disabled>Coming soon</button>
      </div>
    </article>
  );

  const renderAiCard = (source) => {
    const model = source.config?.model;
    const userCode = source.config?.user_code;
    const needsReconnect = source.status === "needs_reconnect";
    const isPending = source.status === "pending";
    const showState = source.connected || needsReconnect || isPending;
    const isOpenAIChatGPT = source.type === "openai_ai" && source.authType === "oauth";
    const isClaudeAccount = source.type === "anthropic_ai" && source.authType === "oauth";
    const canManage = canModify && !isOpenAIChatGPT && !isClaudeAccount;
    return (
      <article
        key={source.type}
        className={`conn-card ai${source.connected ? " live" : ""}${needsReconnect ? " attention" : ""}`}
      >
        <div className="conn-top">
          <ConnectorLogo source={source} />
          <div className="conn-head">
            <div className="conn-name">{source.name}</div>
            <div className="conn-cat mono">
              {isPending ? (userCode ? `Code ${userCode}` : isClaudeAccount ? "Paste the code from Claude" : "Finish login in the OpenAI tab") : source.connected ? (model || source.defaultModel || (isClaudeAccount ? "Your Claude account" : "")) : "Not connected"}
            </div>
          </div>
          <span className="conn-mode">{isOpenAIChatGPT ? "ChatGPT" : isClaudeAccount ? "Claude" : source.authType === "oauth" ? "OAuth" : "BYOK"}</span>
        </div>

        <div className={`conn-foot flush${isPending && userCode ? " wrap" : ""}`}>
          <span className="conn-meta">
            {showState ? (
              <span className={`conn-state ${stateClass(source)}`}>
                <span className="dot" />{stateLabel(source)}
              </span>
            ) : source.setup}
          </span>
          {source.connected ? (
            <>
              {canManage && <button className="conn-btn quiet" onClick={() => onManage(source)}>Manage</button>}
              {canRemove && canDisconnect(source) && (
                <button
                  className="conn-btn icon danger"
                  onClick={() => requestDisconnect(source)}
                  title={`Disconnect ${source.name}`}
                  aria-label={`Disconnect ${source.name}`}
                >
                  <i className="ti ti-x" aria-hidden="true" />
                </button>
              )}
            </>
          ) : canModify ? (
            <>
              {isPending && userCode && (
                <button
                  className="conn-btn quiet"
                  onClick={() => navigator.clipboard?.writeText(userCode)}
                >
                  Copy code
                </button>
              )}
              <button className="conn-btn primary" onClick={() => onConnect(source)} disabled={isPending && !isClaudeAccount}>
                {isPending ? (isClaudeAccount ? "Enter code" : "Authenticating...") : needsReconnect ? "Reconnect" : isClaudeAccount ? "Sign in with Claude" : isOpenAIChatGPT ? "Connect with ChatGPT" : source.authType === "oauth" ? "Connect" : "Add API key"}
              </button>
            </>
          ) : null}
        </div>
      </article>
    );
  };

  if (!client) {
    return (
      <div className="page">
        <div className="empty-state">
          <i className="ti ti-building-store" />
          <div className="empty-title">Add a client first</div>
          <div className="empty-sub">Connectors attach to a specific client workspace.</div>
          <button className="primary-btn" onClick={onAddClient}>
            <i className="ti ti-plus" /> Add client workspace
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="page conn-page">
      <div className="conn-head-row">
        <div>
          <h1 className="conn-title">Connectors</h1>
          <p className="conn-sub">
            Connect the platforms {client.name} runs on. One source of truth for every report.
          </p>
        </div>
        <div className="conn-actions">
          <button className="btn-ghost" onClick={onRefresh} disabled={sourcesLoading}>
            <i className={`ti ti-refresh${sourcesLoading ? " spin" : ""}`} />
            {sourcesLoading ? "Refreshing..." : "Refresh status"}
          </button>
        </div>
      </div>

      <div className="conn-toolbar">
        <div className="conn-search">
          <i className="ti ti-search" />
          <input
            data-unsaved-ignore="true"
            placeholder="Search connectors..."
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Search connectors"
          />
          {query && (
            <button className="conn-search-clear" onClick={() => setQuery("")} aria-label="Clear search">
              <i className="ti ti-x" />
            </button>
          )}
        </div>
        <div className="conn-filters">
          {CONNECTOR_FILTERS.map(({ id, label }) => (
            <button
              key={id}
              className={`conn-filter${filter === id ? " active" : ""}`}
              onClick={() => setFilter(id)}
              aria-pressed={filter === id}
            >
              {label}
              {id === "connected" && connectedCount > 0 && <em>{connectedCount}</em>}
            </button>
          ))}
        </div>
      </div>

      {sourcesError && (
        <div className="inline-error">
          <i className="ti ti-alert-triangle" /> {sourcesError}
        </div>
      )}

      {sourcesLoading && (
        <section className="conn-sec">
          <div className="conn-grid">
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="conn-card skeleton">
                <div className="conn-top">
                  <div className="skel-circle" />
                  <div style={{ flex: 1 }}>
                    <div className="skel-line skel-w120" />
                    <div className="skel-line skel-w80 skel-mt4" />
                  </div>
                </div>
                <div className="skel-line skel-mt4" />
                <div className="skel-line skel-w80 skel-mt4" />
                <div className="conn-foot"><div className="skel-btn" /></div>
              </div>
            ))}
          </div>
        </section>
      )}

      {nothingMatches && (
        <div className="empty-state">
          <i className="ti ti-plug-off" />
          <div className="empty-title">No connectors match</div>
          <div className="empty-sub">Try a different search term or filter.</div>
          <button className="primary-btn" onClick={() => { setQuery(""); setFilter("all"); }}>
            <i className="ti ti-rotate" /> Reset filters
          </button>
        </div>
      )}

      {!sourcesLoading && dataConnectors.length > 0 && (
        <section className="conn-sec">
          <div className="conn-sec-head">
            <h2>Most used</h2>
            <span className="conn-sec-note">The connectors teams set up first</span>
            <span className="conn-sec-rule" />
          </div>
          <div className="conn-grid">{dataConnectors.map(renderConnectorCard)}</div>
        </section>
      )}

      {!sourcesLoading && comingSoon.length > 0 && (
        <section className="conn-sec">
          <div className="conn-sec-head">
            <h2>Coming soon</h2>
            <span className="conn-sec-note">In build — not connectable yet</span>
            <span className="conn-sec-rule" />
          </div>
          <div className="conn-grid">{comingSoon.map(renderSoonCard)}</div>
        </section>
      )}

      {!sourcesLoading && aiProviders.length > 0 && (
        <section className="conn-sec">
          <div className="conn-sec-head">
            <h2>LLM providers</h2>
            <span className="conn-sec-note">Models that power AI summaries in reports</span>
            <span className="conn-sec-rule" />
          </div>
          <div className="conn-grid">{aiProviders.map(renderAiCard)}</div>
        </section>
      )}
      {pendingDisconnect && (
        <div className="modal-bg conn-confirm-bg" role="dialog" aria-modal="true" aria-labelledby="disconnect-title">
          <section className="modal conn-confirm-modal">
            <div className="conn-confirm-icon">
              <i className="ti ti-plug-off" aria-hidden="true" />
            </div>
            <div className="conn-confirm-copy">
              <h2 id="disconnect-title">Disconnect {pendingDisconnect.name}?</h2>
              <p>
                Removing this connection will stop Braidscope from accessing and syncing data from this source.
              </p>
            </div>
            {disconnectError && (
              <div className="inline-error conn-confirm-error" role="alert">
                <i className="ti ti-alert-triangle" /> {disconnectError}
              </div>
            )}
            <div className="conn-confirm-actions">
              <button
                ref={cancelDisconnectRef}
                type="button"
                className="conn-btn quiet"
                onClick={() => {
                  setPendingDisconnect(null);
                  setDisconnectError("");
                }}
                disabled={disconnecting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="conn-btn danger solid"
                onClick={confirmDisconnect}
                disabled={disconnecting}
              >
                {disconnecting ? "Disconnecting..." : "Disconnect"}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
