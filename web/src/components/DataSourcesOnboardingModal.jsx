// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useRef, useState } from "react";
import ConnectorLogo from "./ConnectorLogo";
import ConnectModal from "./ConnectModal";
import { CONNECTOR_FILTERS } from "../lib/data";
import { formatSourceStatus } from "../lib/dataSources";

// Fixed progression, independent of CONNECTOR_FILTERS' own array order — "All"
// and "Connected" (the first two filters) are deliberately excluded here.
const STEP_ORDER = ["advertising", "analytics", "ecommerce", "crm"];
const STEPS = STEP_ORDER
  .map((id) => CONNECTOR_FILTERS.find((filter) => filter.id === id))
  .filter(Boolean);

function DataSourceCard({ source, onSelect }) {
  const connected = source.connected;
  const needsReconnect = source.status === "needs_reconnect";

  return (
    <button
      type="button"
      className={`llm-provider-card${connected ? " is-connected" : ""}`}
      disabled={connected}
      title={connected ? "Already connected. Manage or change this connection from Integrations." : undefined}
      onClick={connected ? undefined : onSelect}
    >
      <div className="llm-provider-head">
        <ConnectorLogo source={source} className="llm-provider-logo" />
        <span className={`llm-provider-badge${connected ? " is-live" : ""}`}>
          {connected ? (needsReconnect ? "Reconnect" : "Connected") : "OAuth"}
        </span>
      </div>
      <div className="llm-provider-name">{source.name}</div>
      <div className="llm-provider-desc">{source.desc}</div>
      <div className="llm-provider-foot">
        <span>{connected ? formatSourceStatus(source) : "Connect"}</span>
        <i className={`ti ${connected ? "ti-check" : "ti-arrow-right"}`} aria-hidden="true" />
      </div>
    </button>
  );
}

export default function DataSourcesOnboardingModal({
  open,
  sources,
  onSave,
  onOAuth,
  saving,
  error,
  onSourceSelect,
  onComplete,
}) {
  const [activeCategory, setActiveCategory] = useState(STEP_ORDER[0]);
  const [selectedSource, setSelectedSource] = useState(null);
  // Set when the user starts an OAuth leg that now opens in a new tab
  // instead of navigating this page away. selectedSource is a one-time
  // snapshot from when the card was clicked, so it never picks up the
  // connection landing in the background — this instead watches the live
  // `sources` prop (kept fresh by App.jsx's BroadcastChannel listener and
  // focus/polling refresh while onboarding is open) for this exact source
  // flipping to connected, and closes the connect dialog back to the
  // category grid the moment it does. Mirrors LLMProviderOnboardingModal's
  // pendingOAuthType pattern.
  const [pendingConnectType, setPendingConnectType] = useState(null);
  const [validationError, setValidationError] = useState("");
  const modalRef = useRef(null);

  useEffect(() => {
    if (open) {
      setActiveCategory(STEP_ORDER[0]);
      setSelectedSource(null);
      setPendingConnectType(null);
      setValidationError("");
    }
  }, [open]);

  useEffect(() => {
    if (!pendingConnectType) return;
    const source = sources.find((item) => item.type === pendingConnectType);
    if (source?.connected) {
      setPendingConnectType(null);
      setSelectedSource(null);
    }
  }, [sources, pendingConnectType]);

  useEffect(() => {
    if (!open || selectedSource) return undefined;

    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        return;
      }

      if (event.key !== "Tab") return;
      const focusable = Array.from(modalRef.current?.querySelectorAll("button") || [])
        .filter((element) => !element.disabled && element.offsetParent !== null);
      if (!focusable.length) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown, true);
    return () => document.removeEventListener("keydown", handleKeyDown, true);
  }, [open, selectedSource]);

  if (!open) return null;

  const categoryIndex = STEP_ORDER.indexOf(activeCategory);
  const isLastCategory = categoryIndex === STEP_ORDER.length - 1;
  // Onboarding only ever shows connectors a user can actually act on right
  // now — "coming soon" ones stay exclusive to the full Integrations page.
  // Reuses each source's own existing `comingSoon` flag rather than a
  // separate list, so this can never drift from the real catalog.
  const connectableSources = sources.filter((source) => !source.comingSoon);
  const categorySources = connectableSources.filter((source) => source.group === activeCategory);
  const connectedCount = connectableSources.filter((source) => STEP_ORDER.includes(source.group) && source.connected).length;

  const goToCategory = (nextId) => {
    setActiveCategory(nextId);
    setValidationError("");
  };

  const handleNext = () => {
    if (!isLastCategory) {
      goToCategory(STEP_ORDER[categoryIndex + 1]);
      return;
    }
    if (connectedCount === 0) {
      setValidationError("Connect at least one data source to continue.");
      return;
    }
    onComplete?.();
  };

  if (selectedSource) {
    return (
      <ConnectModal
        data={selectedSource}
        onClose={() => { setSelectedSource(null); setPendingConnectType(null); }}
        onSave={onSave}
        onOAuth={(source) => {
          setPendingConnectType(source.type);
          return onOAuth(source);
        }}
        saving={saving}
        error={error}
        cancelLabel="Back"
        showCloseButton={false}
        disableDismiss
        onConnected={() => setSelectedSource(null)}
      />
    );
  }

  return (
    <div className="llm-onboard-bg" role="dialog" aria-modal="true" aria-labelledby="ds-onboard-title">
      <section className="llm-onboard-modal ds-onboard-modal" ref={modalRef}>
        <div className="llm-onboard-kicker">
          <i className="ti ti-plug-connected" aria-hidden="true" />
        </div>
        <div className="llm-onboard-copy">
          <h2 id="ds-onboard-title">Connect your data sources</h2>
          <p>
            Link the platforms this client runs on so dashboards, reports, and Ask AI have real data to work with.
          </p>
        </div>

        <div className="ds-stepper" role="tablist" aria-label="Data source category">
          <span
            className="ds-step-indicator"
            style={{ left: `calc(${categoryIndex * 25}% + 4px)`, width: "calc(25% - 8px)" }}
          />
          {STEPS.map((step, index) => {
            const done = connectableSources.some((source) => source.group === step.id && source.connected);
            return (
              <button
                type="button"
                key={step.id}
                role="tab"
                aria-selected={activeCategory === step.id}
                className={`ds-step${activeCategory === step.id ? " active" : ""}${done ? " done" : ""}`}
                onClick={() => goToCategory(step.id)}
              >
                <span className="ds-step-index">
                  {done ? <i className="ti ti-check" aria-hidden="true" /> : index + 1}
                </span>
                <span className="ds-step-label">{step.label}</span>
              </button>
            );
          })}
        </div>
        <div className="ds-progress-track">
          <span
            className="ds-progress-fill"
            style={{ width: `${((categoryIndex + 1) / STEPS.length) * 100}%` }}
          />
        </div>
        <div className="ds-step-caption">
          Step {categoryIndex + 1} of {STEPS.length} — {STEPS[categoryIndex].label}
        </div>

        <div className="ds-source-grid llm-provider-grid" key={activeCategory}>
          {categorySources.map((source) => (
            <DataSourceCard
              key={source.type}
              source={source}
              onSelect={() => {
                onSourceSelect?.(source);
                setSelectedSource(source);
              }}
            />
          ))}
          {categorySources.length === 0 && (
            <div className="ds-empty-cat">No connectors in this category yet.</div>
          )}
        </div>

        {validationError && (
          <div className="ds-validation" role="alert">
            <i className="ti ti-alert-triangle" aria-hidden="true" />
            {validationError}
          </div>
        )}

        <div className="ds-footer">
          {categoryIndex > 0 ? (
            <button type="button" className="onboard-back-btn" onClick={() => goToCategory(STEP_ORDER[categoryIndex - 1])}>
              <i className="ti ti-arrow-left" aria-hidden="true" /> Back
            </button>
          ) : <div />}
          <button type="button" className={`ds-next-btn${validationError ? " has-error" : ""}`} onClick={handleNext}>
            {isLastCategory ? "Continue" : "Next"}
            {!isLastCategory && <i className="ti ti-arrow-right" aria-hidden="true" />}
          </button>
        </div>
      </section>
    </div>
  );
}
