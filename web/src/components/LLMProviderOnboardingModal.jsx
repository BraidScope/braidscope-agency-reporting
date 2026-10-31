// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useRef, useState } from "react";
import ConnectorLogo from "./ConnectorLogo";
import ConnectModal from "./ConnectModal";
import { formatSourceStatus } from "../lib/dataSources";

const AI_PROVIDER_TYPES = ["openrouter_ai", "openai_ai", "anthropic_ai"];

function methodLabel(source) {
  if (source.type === "openai_ai") return "ChatGPT";
  if (source.type === "anthropic_ai" && source.authType === "oauth") return "Claude";
  return source.authType === "oauth" ? "OAuth" : "BYOK";
}

function actionLabel(source) {
  if (source.type === "openai_ai") return "Connect with ChatGPT";
  if (source.type === "anthropic_ai" && source.authType === "oauth") return "Sign in with Claude";
  return source.authType === "oauth" ? "Account connect" : "API key";
}

export default function LLMProviderOnboardingModal({
  open,
  sources,
  onSave,
  onOAuth,
  onSubmitCode,
  onProviderOAuthStart,
  saving,
  error,
  onComplete,
  onSkip,
  onProviderSelect,
}) {
  const [selectedProvider, setSelectedProvider] = useState(null);
  // Set when the user starts an OAuth leg (OpenRouter) that now opens in a
  // new tab instead of navigating this page away. There is no in-page
  // redirect to catch completion on anymore, so this modal instead watches
  // the live `sources` prop (kept fresh by App.jsx's BroadcastChannel
  // listener and focus/polling refresh while onboarding is open) for this
  // exact provider flipping to connected, and closes the connect dialog
  // back to the provider grid the moment it does - it does NOT advance
  // onboarding on its own. The user stays on this popup, sees the card read
  // Connected, and can connect another provider or leave via Skip/Continue;
  // only an explicit click on one of those calls onComplete.
  const [pendingOAuthType, setPendingOAuthType] = useState(null);
  const modalRef = useRef(null);

  useEffect(() => {
    if (open) {
      setSelectedProvider(null);
      setPendingOAuthType(null);
    }
  }, [open]);

  useEffect(() => {
    if (!pendingOAuthType) return;
    const source = sources.find((item) => item.type === pendingOAuthType);
    if (source?.connected) {
      setPendingOAuthType(null);
      setSelectedProvider(null);
    }
  }, [sources, pendingOAuthType]);

  useEffect(() => {
    if (!open || selectedProvider) return undefined;

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
  }, [open, selectedProvider]);

  if (!open) return null;

  const providers = AI_PROVIDER_TYPES
    .map((type) => sources.find((source) => source.type === type))
    .filter(Boolean);

  const hasConnectedProvider = providers.some((source) => source.connected);

  if (selectedProvider) {
    return (
      <ConnectModal
        data={selectedProvider}
        onClose={() => setSelectedProvider(null)}
        onSave={onSave}
        onSubmitCode={onSubmitCode}
        onOAuth={(source) => {
          setPendingOAuthType(source.type);
          onProviderOAuthStart?.(source);
          return onOAuth(source);
        }}
        saving={saving}
        error={error}
        cancelLabel="Back"
        showCloseButton={false}
        disableDismiss
        onConnected={() => setSelectedProvider(null)}
      />
    );
  }

  return (
    <div className="llm-onboard-bg" role="dialog" aria-modal="true" aria-labelledby="llm-onboard-title">
      <section className="llm-onboard-modal" ref={modalRef}>
        <button type="button" className="onboard-skip-btn" onClick={onSkip}>
          Skip
        </button>
        <div className="llm-onboard-kicker">
          <i className="ti ti-sparkles" aria-hidden="true" />
        </div>
        <div className="llm-onboard-copy">
          <h2 id="llm-onboard-title">Connect your LLM provider</h2>
          <p>
            Choose the provider that will power AI summaries, reports, and Ask AI for this client workspace.
          </p>
        </div>

        <div className="llm-provider-grid">
          {providers.map((source) => {
            const connected = source.connected;
            return (
              <button
                type="button"
                className={`llm-provider-card${connected ? " is-connected" : ""}`}
                key={source.type}
                disabled={connected}
                title={connected ? "Already connected. Manage or change this connection from Integrations." : undefined}
                onClick={() => {
                  if (connected) return;
                  onProviderSelect?.(source);
                  setSelectedProvider(source);
                }}
              >
                <div className="llm-provider-head">
                  <ConnectorLogo source={source} className="llm-provider-logo" />
                  <span className={`llm-provider-badge${connected ? " is-live" : ""}`}>
                    {connected ? "Connected" : methodLabel(source)}
                  </span>
                </div>
                <div className="llm-provider-name">{source.name}</div>
                <div className="llm-provider-desc">{source.desc}</div>
                <div className="llm-provider-foot">
                  <span>{connected ? formatSourceStatus(source) : actionLabel(source)}</span>
                  <i className={`ti ${connected ? "ti-check" : "ti-arrow-right"}`} aria-hidden="true" />
                </div>
              </button>
            );
          })}
        </div>

        {hasConnectedProvider && (
          <div className="ds-footer">
            <div />
            <button type="button" className="ds-next-btn" onClick={onComplete}>
              Continue
              <i className="ti ti-arrow-right" aria-hidden="true" />
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
