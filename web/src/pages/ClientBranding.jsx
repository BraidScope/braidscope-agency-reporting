// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useMemo, useRef, useState } from "react";
import {
  FONT_PAIRINGS,
  buildAccentRamp,
  computeClientReadiness,
  contrastOnWhite,
  isValidHex,
  readLogoFile,
  resolveClientBranding,
} from "../lib/branding";
import { useUnsavedChanges } from "../lib/UnsavedChangesContext.jsx";

const PREVIEW_TABS = [
  { id: "dash", label: "Dashboard" },
  { id: "report", label: "Report" },
  { id: "email", label: "Email" },
  { id: "login", label: "Portal" },
];

const RING_CIRCUMFERENCE = 2 * Math.PI * 46;

// Client-scoped Branding - always shows/edits the CURRENTLY SELECTED
// client's branding, inheriting from Agency Branding wherever the client
// hasn't customized a field. This promotes the old BrandOverrideDrawer's
// fields into a full first-class page (same visual language as Agency
// Branding) instead of a side drawer, and adds the fields (tagline, support
// email) that drawer never exposed.
export default function ClientBranding({
  client,
  agencyName,
  accent,
  branding,
  onOverrideChange,
  onSave,
  saving,
  error,
  saved,
  active,
  sourceSummary,
  canEdit = true,
}) {
  const [previewTab, setPreviewTab] = useState("dash");
  const [logoError, setLogoError] = useState("");
  const previewRef = useRef(null);
  const { registerChanges, clearChanges } = useUnsavedChanges();

  const override = (client && client.branding_override) || {};
  const enabled = Boolean(override.enabled);

  // Dirty-tracking is scoped to just this client's override (lives on the
  // client row itself, client.branding_override - see
  // SHARED_CLIENT_CONTEXT_FIX.md) and resets whenever the selected client
  // changes, so switching clients never carries over a stale "unsaved
  // changes" prompt from the client you just left, and never flashes their
  // data either - the client list is already fully loaded in memory, so
  // this is a synchronous lookup, not a fetch.
  const [savedOverrideSnapshot, setSavedOverrideSnapshot] = useState(() => JSON.stringify(override));
  useEffect(() => {
    setSavedOverrideSnapshot(JSON.stringify((client && client.branding_override) || {}));
    setLogoError("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client?.id]);

  const currentOverrideSnapshot = JSON.stringify(override);
  const dirty = Boolean(active && currentOverrideSnapshot !== savedOverrideSnapshot);

  const save = async () => {
    const result = await onSave?.();
    if (result !== false) setSavedOverrideSnapshot(currentOverrideSnapshot);
    return result;
  };

  useEffect(() => {
    if (!active) { clearChanges(); return undefined; }
    registerChanges({
      isDirty: dirty,
      onSave: async () => (await save()) !== false,
      onDiscard: () => onOverrideChange(JSON.parse(savedOverrideSnapshot)),
    });
    return () => clearChanges();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, dirty, savedOverrideSnapshot, registerChanges, clearChanges]);

  if (!client) {
    return (
      <div className="page brand-page">
        <div className="studio-hint">Select a client workspace to manage its branding.</div>
      </div>
    );
  }

  const effective = resolveClientBranding({ ownerAgencyName: agencyName, ownerAccent: accent, ownerBranding: branding, client });
  const readiness = computeClientReadiness({ effective, enabled });
  const ramp = buildAccentRamp(isValidHex(effective.accent) ? effective.accent : "#111111");
  const contrast = contrastOnWhite(isValidHex(effective.accent) ? effective.accent : "#111111");
  const initial = (effective.name || client.name || "C").trim()[0]?.toUpperCase() || "C";

  const patch = (next) => onOverrideChange({ ...override, ...next });

  const handleLogo = async (file) => {
    setLogoError("");
    const result = await readLogoFile(file);
    if (!result.ok) {
      setLogoError(result.error);
      return;
    }
    patch({ logo: result.dataUrl });
  };

  const scrollToPreview = () => {
    previewRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const renderMark = (className) => (
    <div className={className} style={effective.logo ? undefined : { background: effective.accent }}>
      {effective.logo ? <img src={effective.logo} alt="" /> : initial}
    </div>
  );

  return (
    <div className="page brand-page">
      {/* ---------------- Hero ---------------- */}
      <section className="brand-hero">
        <span className="brand-orb orb-1" />
        <span className="brand-orb orb-2" />

        <div className="brand-hero-left">
          <div className="brand-hero-eyebrow"><span className="pulse-dot" />{client.name}&apos;s branding</div>
          <h1 className="brand-hero-title">
            {enabled ? <>Custom branding<br /><em>for {client.name}.</em></> : <>Using agency branding<br /><em>by default.</em></>}
          </h1>
          <p className="brand-hero-sub">
            {enabled
              ? `This client's branding overrides your agency defaults on their dashboards, reports and portal.`
              : `This client currently inherits your agency branding defaults. Customize it to give ${client.name} its own logo, colour and identity.`}
          </p>
          <div className="brand-hero-actions">
            {enabled ? (
              <>
                <button className="hbtn hbtn-primary" onClick={save} disabled={saving || !canEdit}>
                  <i className="ti ti-check" />
                  {saving ? "Saving..." : `Save ${client.name}'s branding`}
                </button>
                <button className="hbtn hbtn-ghost" onClick={() => patch({ enabled: false })} disabled={!canEdit}>
                  <i className="ti ti-rotate" /> Revert to agency branding
                </button>
              </>
            ) : (
              <button className="hbtn hbtn-primary" onClick={() => patch({ enabled: true })} disabled={!canEdit}>
                <i className="ti ti-sparkles" /> Customize for this client
              </button>
            )}
          </div>
          {!canEdit && (
            <div className="studio-hint" style={{ marginTop: 12 }}>Only editors and above can change this client's branding.</div>
          )}
        </div>

        <div className="brand-hero-right">
          <div className="brand-mark-wrap">
            <span className="brand-mark-ring" />
            {renderMark("brand-mark-lg")}
          </div>
          <div className="brand-readiness" title={`${readiness.done} of ${readiness.total} steps complete`}>
            <svg width="104" height="104" viewBox="0 0 104 104">
              <circle cx="52" cy="52" r="46" fill="none" stroke="rgba(255,255,255,.12)" strokeWidth="8" />
              <circle
                cx="52" cy="52" r="46" fill="none" stroke="url(#clientBrandRing)" strokeWidth="8"
                strokeLinecap="round"
                strokeDasharray={RING_CIRCUMFERENCE}
                strokeDashoffset={RING_CIRCUMFERENCE * (1 - readiness.percent / 100)}
              />
              <defs>
                <linearGradient id="clientBrandRing" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0%" stopColor="#ffffff" />
                  <stop offset="100%" stopColor="#b8c4b2" />
                </linearGradient>
              </defs>
            </svg>
            <div className="brand-readiness-label">
              <div className="brand-readiness-val">{readiness.percent}%</div>
              <div className="brand-readiness-cap">ready</div>
            </div>
            <div className="brand-readiness-title">Branding readiness</div>
          </div>
        </div>
      </section>

      <div className="brand-context-banner">
        <i className="ti ti-info-circle" />
        {enabled
          ? <>Editing <b>{client.name}</b>&apos;s own branding. Turn it off above to fall back to your agency defaults.</>
          : <><b>{client.name}</b> is inheriting your agency branding — switch clients at the top to manage a different one, or customize this client above.</>}
      </div>

      {error && (
        <div className="inline-error"><i className="ti ti-alert-triangle" /> {error}</div>
      )}
      {saved && (
        <div className="inline-success"><i className="ti ti-check" /> {client.name}'s branding saved.</div>
      )}

      {/* ---------------- Studio ---------------- */}
      <div className="studio-grid">
        <div className="studio-col">
          <fieldset className="studio-fieldset" disabled={!enabled || !canEdit}>
            {/* Identity */}
            <section className="studio-card">
              <div className="studio-card-head">
                <span className="studio-icn"><i className="ti ti-user-circle" /></span>
                <div>
                  <h3>Identity</h3>
                  <p>How {client.name} appears on their own dashboards and reports</p>
                </div>
                <span className={`studio-tag${effective.name.trim() ? " tag-done" : " tag-pending"}`}>
                  {effective.name.trim() ? "Complete" : "Name required"}
                </span>
              </div>
              <div className="studio-field-row">
                <label className="studio-field">
                  <span>Client display name</span>
                  <input
                    value={override.name ?? client.name}
                    onChange={(e) => patch({ name: e.target.value })}
                    placeholder={client.name}
                  />
                </label>
                <label className="studio-field">
                  <span>Support email</span>
                  <input
                    type="email"
                    placeholder="support@youragency.com"
                    value={override.supportEmail || ""}
                    onChange={(e) => patch({ supportEmail: e.target.value })}
                  />
                </label>
              </div>
              <label className="studio-field">
                <span>Tagline <em>— shown on report covers</em></span>
                <input
                  placeholder="Performance marketing, reported beautifully."
                  value={override.tagline || ""}
                  onChange={(e) => patch({ tagline: e.target.value })}
                />
              </label>
            </section>

            {/* Logo */}
            <section className="studio-card">
              <div className="studio-card-head">
                <span className="studio-icn"><i className="ti ti-photo" /></span>
                <div>
                  <h3>Logo</h3>
                  <p>Falls back to your agency logo until {client.name} has its own</p>
                </div>
                <span className={`studio-tag${override.logo ? " tag-done" : " tag-pending"}`}>
                  {override.logo ? "Custom" : "Inherited"}
                </span>
              </div>
              <div className="bd-asset-row">
                <div className={`bd-asset-preview${effective.logo ? "" : " tinted"}`} style={effective.logo ? undefined : { "--bd-tile-color": effective.accent }}>
                  {effective.logo ? <img src={effective.logo} alt="" /> : initial}
                </div>
                <div className="bd-asset-info">
                  <div className="at">{override.logo ? "Custom logo" : "Using agency logo"}</div>
                  <div className="as">PNG or SVG, under 512KB</div>
                </div>
                <label className="bd-asset-btn">
                  {override.logo ? "Replace" : "Upload"}
                  <input
                    type="file"
                    accept="image/*"
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      event.target.value = "";
                      if (file) handleLogo(file);
                    }}
                  />
                </label>
                {override.logo && (
                  <button className="bd-asset-clear" onClick={() => patch({ logo: null })} aria-label="Remove logo">
                    <i className="ti ti-trash" />
                  </button>
                )}
              </div>
              {logoError && <div className="studio-field-error">{logoError}</div>}
            </section>

            {/* Colours & typography */}
            <section className="studio-card">
              <div className="studio-card-head">
                <span className="studio-icn"><i className="ti ti-palette" /></span>
                <div>
                  <h3>Colours &amp; typography</h3>
                  <p>{client.name}'s accent generates its own tonal palette</p>
                </div>
                <span className={`studio-tag${isValidHex(effective.accent) ? " tag-done" : " tag-pending"}`}>
                  {isValidHex(effective.accent) ? "Complete" : "Invalid hex"}
                </span>
              </div>

              <div className="hex-row">
                <input
                  type="color"
                  className="hex-sw"
                  value={isValidHex(override.accent) ? override.accent : effective.accent}
                  onChange={(e) => patch({ accent: e.target.value })}
                  aria-label="Pick client accent colour"
                />
                <input
                  className="hex-input"
                  value={override.accent ?? effective.accent}
                  maxLength={7}
                  onChange={(e) => patch({ accent: e.target.value })}
                  aria-label="Client accent hex value"
                />
                <div className={`contrast-badge${contrast.passes ? "" : " warn"}`}>
                  <i className={`ti ti-${contrast.passes ? "check" : "alert-triangle"}`} />
                  {contrast.grade} · {contrast.ratio}:1
                </div>
              </div>
              {!contrast.passes && (
                <div className="studio-hint">
                  White text on this accent falls below the 4.5:1 AA threshold — buttons and
                  badges may be hard to read.
                </div>
              )}

              <div className="studio-sub-label">Generated tonal ramp</div>
              <div className="ramp">
                {ramp.map((stop, i) => (
                  <div key={`${stop}-${i}`} style={{ background: stop }} title={stop} />
                ))}
              </div>

              <div className="studio-sub-label">
                Typography
                {override.fontPairing && (
                  <button type="button" className="bd-reset-font" onClick={() => patch({ fontPairing: null })}>
                    Reset to agency font
                  </button>
                )}
              </div>
              <div className="type-row">
                {FONT_PAIRINGS.map((font) => (
                  <button
                    key={font.id}
                    className={`type-card${effective.fontPairing === font.id ? " active" : ""}`}
                    onClick={() => patch({ fontPairing: font.id })}
                    aria-pressed={effective.fontPairing === font.id}
                  >
                    {effective.fontPairing === font.id && (
                      <span className="type-check"><i className="ti ti-check" /></span>
                    )}
                    <span className="type-aa" style={{ fontFamily: font.stack }}>Aa</span>
                    <span className="tn">{font.name}</span>
                    <span className="tb">{font.body}</span>
                  </button>
                ))}
              </div>
            </section>
          </fieldset>
        </div>

        {/* ---------------- Live preview ---------------- */}
        <div className="studio-col preview-col" ref={previewRef}>
          <div className="preview-wrap">
            <section className="studio-card preview-card">
              <div className="studio-card-head">
                <span className="studio-icn alt"><i className="ti ti-eye" /></span>
                <div>
                  <h3>Live preview</h3>
                  <p>Exactly what {client.name} sees</p>
                </div>
              </div>

              <div className="device-tabs" role="tablist" aria-label="Preview surface">
                {PREVIEW_TABS.map((tab) => (
                  <button
                    key={tab.id}
                    role="tab"
                    aria-selected={previewTab === tab.id}
                    className={previewTab === tab.id ? "active" : ""}
                    onClick={() => setPreviewTab(tab.id)}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              <div className="preview-surface" style={{ fontFamily: effective.fontStack }}>
                {previewTab === "dash" && (
                  <>
                    <div className="mock-topbar">
                      {renderMark("mock-mark")}
                      <div className="mock-name">{effective.name || client.name}</div>
                      <span className="mock-pill" style={{ background: ramp[0], color: ramp[7] }}>
                        {sourceSummary.countLabel}
                      </span>
                    </div>
                    <div className="mock-body">
                      <div className="mock-chart" style={{ background: `linear-gradient(180deg, ${ramp[0]}, transparent)` }}>
                        <svg width="100%" height="64" viewBox="0 0 300 64" preserveAspectRatio="none">
                          <path
                            d="M0 50 L40 40 L80 44 L120 24 L160 30 L200 12 L240 20 L300 6"
                            fill="none" stroke={effective.accent} strokeWidth="2.5"
                          />
                        </svg>
                      </div>
                      <div className="mock-kpis">
                        <div className="mock-kpi"><div className="k">Revenue</div><div className="v">$35.0k</div></div>
                        <div className="mock-kpi"><div className="k">ROAS</div><div className="v">4.2x</div></div>
                        <div className="mock-kpi"><div className="k">Orders</div><div className="v">342</div></div>
                      </div>
                    </div>
                  </>
                )}

                {previewTab === "report" && (
                  <div className="mock-report">
                    <div className="mock-report-cover">
                      <span className="mock-report-glow" style={{ background: `radial-gradient(circle, ${effective.accent}, transparent 70%)` }} />
                      {renderMark("mock-report-mark")}
                      <div className="mock-report-title">{effective.name || client.name}</div>
                      {effective.tagline && <div className="mock-report-tag">{effective.tagline}</div>}
                    </div>
                    <div className="mock-report-body">
                      <div className="mock-report-summary"><b>Performance overview</b><span>Revenue, reach and conversion highlights</span></div>
                    </div>
                  </div>
                )}

                {previewTab === "email" && (
                  <div className="mock-email">
                    <div className="mock-email-head">
                      {renderMark("mock-mark")}
                      <div>
                        <div className="mock-email-title">Your weekly performance digest</div>
                        <div className="mock-email-from">from {effective.name || client.name}</div>
                      </div>
                    </div>
                    <div className="mock-email-body">
                      <div className="mock-email-summary"><b>Your performance is ready</b><span>Review the latest workspace insights.</span></div>
                      <div className="mock-email-btn" style={{ background: effective.accent }}>View full report →</div>
                      {effective.supportEmail && (
                        <div className="mock-email-foot">Questions? Reply to {effective.supportEmail}</div>
                      )}
                    </div>
                  </div>
                )}

                {previewTab === "login" && (
                  <div className="mock-login">
                    {renderMark("mock-mark lg")}
                    <div className="lt">Welcome back</div>
                    <div className="ls">Sign in to your {effective.name || client.name} workspace</div>
                    <div className="mock-login-field" />
                    <div className="mock-login-field" />
                    <div className="mock-login-btn" style={{ background: effective.accent }}>Sign in</div>
                  </div>
                )}
              </div>

              <p className="preview-foot">
                The accent, logo mark and font above update live across {client.name}'s dashboards,
                PDF reports, emails and login screen.
              </p>
            </section>

            {/* Readiness checklist */}
            <section className="studio-card checklist-card">
              <div className="studio-card-head">
                <span className="studio-icn alt"><i className="ti ti-checklist" /></span>
                <div>
                  <h3>Branding readiness</h3>
                  <p>{readiness.done} of {readiness.total} complete</p>
                </div>
              </div>
              <ul className="checklist">
                {readiness.checks.map((check) => (
                  <li key={check.id} className={check.done ? "done" : ""}>
                    <i className={`ti ti-${check.done ? "circle-check-filled" : "circle"}`} />
                    {check.label}
                  </li>
                ))}
              </ul>
            </section>
          </div>
        </div>
      </div>

    </div>
  );
}
