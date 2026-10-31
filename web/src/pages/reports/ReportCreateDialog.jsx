// SPDX-License-Identifier: AGPL-3.0-only
import { useMemo, useState } from "react";
import { createReport } from "./api";
import { Banner, Modal, Spinner } from "./ReportControls";
import PeriodPicker from "./PeriodPicker";
import { defaultReportName, periodFromConfig } from "./reportModel";

function Step({ n, title, hint, children }) {
  return (
    <section className="rp-step">
      <div className="rp-step-head">
        <span className="rp-step-n" aria-hidden="true">{n}</span>
        <div>
          <h3>{title}</h3>
          {hint && <p>{hint}</p>}
        </div>
      </div>
      {children}
    </section>
  );
}

// "New report" creates a real Draft on the server, then hands it to the
// Builder. Only sources actually connected to THIS client are offered.
// There is no template step: every report starts with the full set of sections
// and the builder turns individual ones on or off.
export default function ReportCreateDialog({ clientId, clientName, connectedSources, prefillSource, onClose, onCreated, onGoToIntegrations }) {
  const [period, setPeriod] = useState(() => periodFromConfig({ periodMode: "preset", periodDays: 30 }));
  const [name, setName] = useState(() => defaultReportName(clientName, periodFromConfig({ periodMode: "preset", periodDays: 30 })));
  const [nameTouched, setNameTouched] = useState(false);
  const preselected = useMemo(() => {
    const ids = connectedSources.map((source) => source.id);
    return prefillSource && ids.includes(prefillSource) ? [prefillSource] : ids;
  }, [connectedSources, prefillSource]);
  const [selected, setSelected] = useState(preselected);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const allSelected = selected.length === connectedSources.length;

  const changePeriod = (next) => {
    setPeriod(next);
    if (!nameTouched) setName(defaultReportName(clientName, next));
  };
  const toggleSource = (id) => setSelected((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));

  const submit = async (event) => {
    event?.preventDefault();
    if (busy) return;
    if (!name.trim()) { setError("Give the report a name."); return; }
    if (!selected.length) { setError("Select at least one data source."); return; }
    setBusy(true);
    setError("");
    const result = await createReport(clientId, {
      name: name.trim(),
      periodMode: period.mode,
      periodDays: period.days,
      ...(period.mode === "custom" ? { customStart: period.start, customEnd: period.end } : {}),
      sources: selected,
    });
    setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    onCreated(result.data);
  };

  if (!connectedSources.length) {
    return (
      <Modal
        icon="ti-plug-connected"
        title="Connect a data source first"
        description="Reports are built from the data sources connected to this client. None are connected yet."
        onClose={onClose}
        footer={(
          <>
            <button type="button" className="ghost-btn" onClick={onClose}>Not now</button>
            <button type="button" className="primary-btn" onClick={onGoToIntegrations}><i className="ti ti-plug" aria-hidden="true" /> Go to Integrations</button>
          </>
        )}
      />
    );
  }

  return (
    <Modal
      wide
      icon="ti-file-plus"
      title="New report"
      description={`A draft for ${clientName}. You can change everything in the builder.`}
      onClose={onClose}
      busy={busy}
      footer={(
        <>
          <span className="rp-foot-summary">{selected.length} source{selected.length === 1 ? "" : "s"} · {period.mode === "custom" ? "Custom range" : `Last ${period.days} days`}</span>
          <button type="button" className="ghost-btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" form="rp-create-form" className="primary-btn" disabled={busy}>
            {busy ? <><Spinner /> Creating…</> : <><i className="ti ti-plus" aria-hidden="true" /> Create draft</>}
          </button>
        </>
      )}
    >
      <form id="rp-create-form" onSubmit={submit} className="rp-create" noValidate>
        <Step n="1" title="Name">
          <input className="rp-input" data-autofocus aria-label="Report name" value={name} maxLength={120} onChange={(event) => { setName(event.target.value); setNameTouched(true); }} />
        </Step>

        <Step n="2" title="Period" hint="Data is loaded for this period from your connected sources.">
          <PeriodPicker period={period} onPreset={(days) => changePeriod(periodFromConfig({ periodMode: "preset", periodDays: days }))} onCustom={(range) => changePeriod({ mode: "custom", start: range.start, end: range.end, days: 0 })} />
        </Step>

        <Step n="3" title="Data sources" hint={`Only sources connected to ${clientName} are shown.`}>
          <div className="rp-source-grid">
            {connectedSources.map((source) => {
              const on = selected.includes(source.id);
              return (
                <label key={source.id} className={`rp-source${on ? " on" : ""}`}>
                  <input type="checkbox" checked={on} onChange={() => toggleSource(source.id)} />
                  <span className="rp-source-icon" style={{ color: source.color }} aria-hidden="true"><i className={`ti ${source.icon}`} /></span>
                  <span className="rp-source-name">{source.label}</span>
                  <span className="rp-source-check" aria-hidden="true"><i className="ti ti-check" /></span>
                </label>
              );
            })}
          </div>
          {connectedSources.length > 1 && (
            <button type="button" className="rp-link" onClick={() => setSelected(allSelected ? [] : connectedSources.map((source) => source.id))}>
              {allSelected ? "Clear all" : "Select all"}
            </button>
          )}
        </Step>

        {error && <Banner tone="error">{error}</Banner>}
      </form>
    </Modal>
  );
}
