// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useMemo, useRef, useState } from "react";
import { PRESETS } from "../lib/data";
import {
  FONT_PAIRINGS,
  LOGO_SLOTS,
  buildAccentRamp,
  computeReadiness,
  contrastOnWhite,
  isValidHex,
  readLogoFile,
} from "../lib/branding";
import { useUnsavedChanges } from "../lib/UnsavedChangesContext.jsx";

const PREVIEW_TABS = [
  { id: "dash", label: "Dashboard" },
  { id: "report", label: "Report" },
  { id: "email", label: "Email" },
  { id: "login", label: "Portal" },
];

const RING_CIRCUMFERENCE = 2 * Math.PI * 46;

// Agency Branding is GLOBAL - it must never change when the selected client
// changes, and this page never reads client override data for its own
// editing (the "Client overrides" table below is a read-mostly overview;
// editing a client's branding happens on the client-scoped Branding page).
export default function AgencyBranding({
  agencyName,
  setAgencyName,
  accent,
  hexInput,
  setHexInput,
  applyHex,
  client,
  clients,
  sourceSummary,
  branding,
  onBrandingChange,
  onSave,
  saving,
  error,
  saved,
  savedSnapshot,
  active,
  onEditClientBranding,
}) {
  const [previewTab, setPreviewTab] = useState("dash");
  const [logoError, setLogoError] = useState("");
  const previewRef = useRef(null);
  const { registerChanges, clearChanges } = useUnsavedChanges();
  const currentSnapshot = useMemo(() => JSON.stringify({ agencyName, accent, hexInput, branding }), [agencyName, accent, hexInput, branding]);
  const brandingDirty = Boolean(active && savedSnapshot !== null && savedSnapshot !== currentSnapshot);

  useEffect(() => {
    if (!active) { clearChanges(); return undefined; }
    registerChanges({
      isDirty: brandingDirty,
      onSave: async () => (await onSave?.()) !== false,
      onDiscard: () => {
        if (!savedSnapshot) return;
        const saved = JSON.parse(savedSnapshot);
        setAgencyName(saved.agencyName);
        applyHex(saved.hexInput);
        onBrandingChange(saved.branding);
      },
    });
    return () => clearChanges();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, brandingDirty, savedSnapshot, registerChanges, clearChanges]);

  // Per-client overrides now live on each client row itself
  // (client.branding_override), not inside this agency's own branding
  // blob - see SHARED_CLIENT_CONTEXT_FIX.md. `clients` here is already
  // filtered to this agency's OWNED clients only (App.jsx) - a client
  // shared in from another agency is never "this agency's" branding.
  const overrideCount = clients.filter((row) => row.branding_override?.enabled).length;

  const ramp = buildAccentRamp(isValidHex(accent) ? accent : "#111111");
  const contrast = contrastOnWhite(isValidHex(accent) ? accent : "#111111");
  const readiness = computeReadiness({ agencyName, accent, branding, overrideCount });
  const filledLogos = LOGO_SLOTS.filter((slot) => branding.logos?.[slot.id]).length;
  const fontStack = (FONT_PAIRINGS.find((f) => f.id === branding.fontPairing) || FONT_PAIRINGS[0]).stack;
  const resolvedLogo = branding.logos?.icon || branding.logos?.primary || null;
  const ini = (agencyName || "A").trim()[0]?.toUpperCase() || "A";

  const patch = (next) => onBrandingChange({ ...branding, ...next });

  const handleLogoPick = async (slotId, file) => {
    setLogoError("");
    const result = await readLogoFile(file);
    if (!result.ok) {
      setLogoError(result.error);
      return;
    }
    patch({ logos: { ...branding.logos, [slotId]: result.dataUrl } });
  };

  const clearLogo = (slotId) => {
    patch({ logos: { ...branding.logos, [slotId]: null } });
  };

  const scrollToPreview = () => {
    previewRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const renderMark = (className) => (
    <div className={className} style={resolvedLogo ? undefined : { background: accent }}>
      {resolvedLogo ? <img src={resolvedLogo} alt="" /> : ini}
    </div>
  );

  return (
    <div className="page brand-page">
      {/* ---------------- Hero ---------------- */}
      <section className="brand-hero">
        <span className="brand-orb orb-1" />
        <span className="brand-orb orb-2" />

        <div className="brand-hero-left">
          <div className="brand-hero-eyebrow"><span className="pulse-dot" />Brand Studio</div>
          <h1 className="brand-hero-title">
            White-label everything,<br /><em>down to the pixel.</em>
          </h1>
          <p className="brand-hero-sub">
            Your logo, colours and typography flow into every dashboard, portal, report
            and email your clients see — so it reads as your agency, not ours.
          </p>
          <div className="brand-hero-actions">
            <button className="hbtn hbtn-primary" onClick={() => onSave?.()} disabled={saving}>
              <i className="ti ti-check" />
              {saving ? "Publishing..." : "Publish changes"}
            </button>
            <button className="hbtn hbtn-ghost" onClick={scrollToPreview}>
              <i className="ti ti-eye" /> Preview as client
            </button>
          </div>
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
                cx="52" cy="52" r="46" fill="none" stroke="url(#brandRing)" strokeWidth="8"
                strokeLinecap="round"
                strokeDasharray={RING_CIRCUMFERENCE}
                strokeDashoffset={RING_CIRCUMFERENCE * (1 - readiness.percent / 100)}
              />
              <defs>
                <linearGradient id="brandRing" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0%" stopColor="#ffffff" />
                  <stop offset="100%" stopColor="#b8c4b2" />
                </linearGradient>
              </defs>
            </svg>
            <div className="brand-readiness-label">
              <div className="brand-readiness-val">{readiness.percent}%</div>
              <div className="brand-readiness-cap">ready</div>
            </div>
            <div className="brand-readiness-title">White-label readiness</div>
          </div>
        </div>
      </section>

      {error && (
        <div className="inline-error"><i className="ti ti-alert-triangle" /> {error}</div>
      )}
      {saved && (
        <div className="inline-success"><i className="ti ti-check" /> Branding published.</div>
      )}

      {/* ---------------- Studio ---------------- */}
      <div className="studio-grid">
        <div className="studio-col">
          {/* Identity */}
          <section className="studio-card">
            <div className="studio-card-head">
              <span className="studio-icn"><i className="ti ti-user-circle" /></span>
              <div>
                <h3>Identity</h3>
                <p>How your agency name appears across every surface</p>
              </div>
              <span className={`studio-tag${agencyName.trim() ? " tag-done" : " tag-pending"}`}>
                {agencyName.trim() ? "Complete" : "Name required"}
              </span>
            </div>
            <div className="studio-field-row">
              <label className="studio-field">
                <span>Agency display name</span>
                <input value={agencyName} onChange={(e) => setAgencyName(e.target.value)} />
              </label>
              <label className="studio-field">
                <span>Support email</span>
                <input
                  type="email"
                  placeholder="support@youragency.com"
                  value={branding.supportEmail}
                  onChange={(e) => patch({ supportEmail: e.target.value })}
                />
              </label>
            </div>
            <label className="studio-field">
              <span>Tagline <em>— shown on report covers</em></span>
              <input
                placeholder="Performance marketing, reported beautifully."
                value={branding.tagline}
                onChange={(e) => patch({ tagline: e.target.value })}
              />
            </label>
          </section>

          {/* Logo & assets */}
          <section className="studio-card">
            <div className="studio-card-head">
              <span className="studio-icn"><i className="ti ti-photo" /></span>
              <div>
                <h3>Logo &amp; assets</h3>
                <p>Upload variants for light, dark and compact surfaces</p>
              </div>
              <span className={`studio-tag${filledLogos === LOGO_SLOTS.length ? " tag-done" : " tag-pending"}`}>
                {filledLogos} of {LOGO_SLOTS.length} uploaded
              </span>
            </div>

            <div className="asset-grid">
              {LOGO_SLOTS.map((slot) => {
                const value = branding.logos?.[slot.id];
                return (
                  <div key={slot.id} className={`asset-tile${value ? " filled" : ""}`}>
                    {value ? (
                      <>
                        <div className="asset-preview">
                          <img src={value} alt="" />
                        </div>
                        <div className="asset-info">
                          <div className="at">{slot.label}</div>
                          <div className="as">{slot.hint}</div>
                        </div>
                        <label className="asset-btn">
                          Replace
                          <input
                            type="file" accept="image/*"
                            onChange={(e) => {
                              const file = e.target.files?.[0];
                              e.target.value = "";
                              if (file) handleLogoPick(slot.id, file);
                            }}
                          />
                        </label>
                        <button
                          className="asset-clear"
                          onClick={() => clearLogo(slot.id)}
                          aria-label={`Remove ${slot.label}`}
                        >
                          <i className="ti ti-trash" />
                        </button>
                      </>
                    ) : (
                      <label className="asset-drop">
                        <span className="asset-drop-icn"><i className="ti ti-cloud-upload" /></span>
                        <span className="at">{slot.label}</span>
                        <span className="as">{slot.hint}</span>
                        <input
                          type="file" accept="image/*"
                          onChange={(e) => {
                            const file = e.target.files?.[0];
                            e.target.value = "";
                            if (file) handleLogoPick(slot.id, file);
                          }}
                        />
                      </label>
                    )}
                  </div>
                );
              })}
            </div>
            {logoError && <div className="studio-field-error">{logoError}</div>}
          </section>

          {/* Colours & typography */}
          <section className="studio-card">
            <div className="studio-card-head">
              <span className="studio-icn"><i className="ti ti-palette" /></span>
              <div>
                <h3>Colours &amp; typography</h3>
                <p>Your accent generates a full tonal palette automatically</p>
              </div>
              <span className={`studio-tag${isValidHex(accent) ? " tag-done" : " tag-pending"}`}>
                {isValidHex(accent) ? "Complete" : "Invalid hex"}
              </span>
            </div>

            <div className="hex-row">
              <input
                type="color"
                className="hex-sw"
                value={isValidHex(accent) ? accent : "#111111"}
                onChange={(e) => { setHexInput(e.target.value); applyHex(e.target.value); }}
                aria-label="Pick accent colour"
              />
              <input
                className="hex-input"
                value={hexInput}
                maxLength={7}
                onChange={(e) => { setHexInput(e.target.value); applyHex(e.target.value); }}
                aria-label="Accent hex value"
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

            <div className="preset-row">
              {PRESETS.map((preset) => (
                <button
                  key={preset}
                  className={`preset${accent.toLowerCase() === preset.toLowerCase() ? " active" : ""}`}
                  style={{ background: preset }}
                  onClick={() => { setHexInput(preset); applyHex(preset); }}
                  aria-label={`Use accent ${preset}`}
                />
              ))}
            </div>

            <div className="studio-sub-label">Generated tonal ramp</div>
            <div className="ramp">
              {ramp.map((stop, i) => (
                <div key={`${stop}-${i}`} style={{ background: stop }} title={stop} />
              ))}
            </div>

            <div className="studio-sub-label">Preview typography</div>
            <div className="type-row">
              {FONT_PAIRINGS.map((font) => (
                <button
                  key={font.id}
                  className={`type-card${branding.fontPairing === font.id ? " active" : ""}`}
                  onClick={() => patch({ fontPairing: font.id })}
                  aria-pressed={branding.fontPairing === font.id}
                >
                  {branding.fontPairing === font.id && (
                    <span className="type-check"><i className="ti ti-check" /></span>
                  )}
                  <span className="type-aa" style={{ fontFamily: font.stack }}>Aa</span>
                  <span className="tn">{font.name}</span>
                  <span className="tb">{font.body}</span>
                </button>
              ))}
            </div>
          </section>

          {/* Client overrides overview */}
          <section className="studio-card">
            <div className="studio-card-head">
              <span className="studio-icn"><i className="ti ti-users" /></span>
              <div>
                <h3>Client overrides</h3>
                <p>See which clients bring their own logo &amp; colours instead of yours</p>
              </div>
              <span className={`studio-tag${overrideCount ? " tag-done" : " tag-pending"}`}>
                {overrideCount} of {clients.length} custom
              </span>
            </div>

            {clients.length === 0 ? (
              <div className="studio-hint">Add a client workspace to configure per-client branding.</div>
            ) : (
              <div className="override-table">
                {clients.map((row) => {
                  const entry = row.branding_override;
                  const isCustom = Boolean(entry?.enabled);
                  const isViewing = row.id === client?.id;
                  const rowAccent = isCustom && isValidHex(entry?.accent) ? entry.accent : accent;
                  const label = (entry?.name || row.name || "C").trim()[0]?.toUpperCase() || "C";
                  return (
                    <div key={row.id} className={`override-row${isViewing ? " is-viewing" : ""}`}>
                      <div className="ov-logo" style={entry?.logo ? undefined : { background: rowAccent }}>
                        {entry?.logo ? <img src={entry.logo} alt="" /> : label}
                      </div>
                      <div className="ov-name">
                        {entry?.name || row.name}
                        {isViewing && <span className="ov-viewing-tag">Active client</span>}
                      </div>
                      <span className={`ov-badge${isCustom ? " ov-custom" : " ov-inherit"}`}>
                        {isCustom ? "Custom branding" : "Inherits agency"}
                      </span>
                      <button className="ov-edit" onClick={() => onEditClientBranding?.(row.id)}>Edit</button>
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        </div>

        {/* ---------------- Live preview ---------------- */}
        <div className="studio-col preview-col" ref={previewRef}>
          <div className="preview-wrap">
            <section className="studio-card preview-card">
              <div className="studio-card-head">
                <span className="studio-icn alt"><i className="ti ti-eye" /></span>
                <div>
                  <h3>Live preview</h3>
                  <p>Exactly what your clients see</p>
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

              <div className="preview-surface" style={{ fontFamily: fontStack }}>
                {previewTab === "dash" && (
                  <>
                    <div className="mock-topbar">
                      {renderMark("mock-mark")}
                      <div className="mock-name">{agencyName || "Your agency"}</div>
                      <span className="mock-pill" style={{ background: ramp[0], color: ramp[7] }}>
                        {sourceSummary.countLabel}
                      </span>
                    </div>
                    <div className="mock-body">
                      <div className="mock-chart" style={{ background: `linear-gradient(180deg, ${ramp[0]}, transparent)` }}>
                        <svg width="100%" height="64" viewBox="0 0 300 64" preserveAspectRatio="none">
                          <path
                            d="M0 50 L40 40 L80 44 L120 24 L160 30 L200 12 L240 20 L300 6"
                            fill="none" stroke={accent} strokeWidth="2.5"
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
                      <span className="mock-report-glow" style={{ background: `radial-gradient(circle, ${accent}, transparent 70%)` }} />
                      {renderMark("mock-report-mark")}
                      <div className="mock-report-title">{agencyName || "Your agency"}</div>
                      {branding.tagline && <div className="mock-report-tag">{branding.tagline}</div>}
                      <div className="mock-report-sub">Prepared for {client?.name || "your client"}</div>
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
                        <div className="mock-email-from">from {agencyName || "your agency"}</div>
                      </div>
                    </div>
                    <div className="mock-email-body">
                      <div className="mock-email-summary"><b>Your performance is ready</b><span>Review the latest workspace insights.</span></div>
                      <div className="mock-email-btn" style={{ background: accent }}>View full report →</div>
                      {branding.supportEmail && (
                        <div className="mock-email-foot">Questions? Reply to {branding.supportEmail}</div>
                      )}
                    </div>
                  </div>
                )}

                {previewTab === "login" && (
                  <div className="mock-login">
                    {renderMark("mock-mark lg")}
                    <div className="lt">Welcome back</div>
                    <div className="ls">Sign in to your {agencyName || "agency"} workspace</div>
                    <div className="mock-login-field" />
                    <div className="mock-login-field" />
                    <div className="mock-login-btn" style={{ background: accent }}>Sign in</div>
                  </div>
                )}
              </div>

              <p className="preview-foot">
                The accent and logo mark above update live across dashboards, PDF reports,
                emails and the client login screen.
              </p>
            </section>

            {/* Readiness checklist */}
            <section className="studio-card checklist-card">
              <div className="studio-card-head">
                <span className="studio-icn alt"><i className="ti ti-checklist" /></span>
                <div>
                  <h3>Readiness checklist</h3>
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
