// SPDX-License-Identifier: AGPL-3.0-only
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { canPerformAction } from "../../lib/permissions";
import { useUnsavedChanges } from "../../lib/UnsavedChangesContext.jsx";
import { changeReportStatus, downloadReportPdf, generateNarrative, getReport, saveBlob, updateReport, uploadReportPdf } from "./api";
import { loadReportData } from "./loadReportData";
import PeriodPicker from "./PeriodPicker";
import ReportPreview from "./ReportPreview";
import { Banner, Modal, Spinner, StatusBadge, Switch, Tabs } from "./ReportControls";
import {
  availableChartTypes, CHART_TYPES, chartSectionParts, chartTypeFor, DEFAULT_CHART_TYPE, daysBetween, draftFromReport, draftKey, formatPeriod,
  formatTimestamp, isAiSection, isChartSection, isCommentary, normalizeConfig, periodDaysToRange, periodFromConfig, periodOptionsFor,
  reconcileSections, reportFilename, sectionIcon, sectionLabel, seriesOf, snapshotIsStale, snapshotPeriod, SOURCE_BY_ID, TEMPLATE_BY_ID,
} from "./reportModel";

const MAX_NARRATIVE = 6000;

function SaveIndicator({ state, dirty, savedAt, onRetry }) {
  if (state === "saving") return <span className="rp-save-state" role="status"><Spinner /> Saving…</span>;
  if (state === "error") return <span className="rp-save-state bad" role="status">Save failed <button type="button" className="rp-link" onClick={onRetry}>Retry</button></span>;
  if (dirty) return <span className="rp-save-state warn" role="status"><i className="ti ti-circle-dot" aria-hidden="true" /> Unsaved changes</span>;
  return <span className="rp-save-state ok" role="status"><i className="ti ti-check" aria-hidden="true" /> Saved{savedAt ? ` ${formatTimestamp(savedAt)}` : ""}</span>;
}

// One report, two presentations of the SAME state:
//   mode "view" - the report preview only, with an Edit button.
//   mode "edit" - the editor panel + live preview; Save draft persists and
//                 returns to view mode.
// `mode` is UI state (kept in the URL by the caller); Draft/Final is the
// report's lifecycle status. They are independent.
export default function ReportBuilder({
  report: initialReport, mode, onModeChange, clientId, clientName, client, org, connectedSources, branding, currency, aiConnected, active,
  onExit, onReportChanged, onNotice, onGoTo,
}) {
  const { registerChanges, clearChanges, confirmNavigation } = useUnsavedChanges();
  const canEdit = canPerformAction(client, org, "Reports", "edit");

  const [report, setReport] = useState(initialReport);
  const [draft, setDraft] = useState(() => draftFromReport(initialReport));
  const [savedKey, setSavedKey] = useState(() => draftKey(draftFromReport(initialReport)));
  const [savedAt, setSavedAt] = useState(initialReport.updatedAt);
  const [saveState, setSaveState] = useState("idle");
  const [saveError, setSaveError] = useState("");
  const [conflict, setConflict] = useState(null);
  const [gone, setGone] = useState(false);
  const [dataLoading, setDataLoading] = useState(false);
  const [dataError, setDataError] = useState("");
  const [gen, setGen] = useState({});
  const [genErrors, setGenErrors] = useState({});
  const [genWarnings, setGenWarnings] = useState({});
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const [exportWarn, setExportWarn] = useState(null);
  const [busyAction, setBusyAction] = useState("");
  const [dragIndex, setDragIndex] = useState(null);
  const [panelTab, setPanelTab] = useState("setup");
  const [openSec, setOpenSec] = useState({});
  const [reopenAsk, setReopenAsk] = useState(false);

  // `locked`: this user can't change this report at all right now (no edit
  // permission, or it is final/sent/archived). `editing`: the editor is open.
  const locked = !canEdit || report.status !== "draft";
  const editing = !locked && mode === "edit";
  const readOnly = !editing;

  // Eagerly-updated mirrors so event handlers (navigation, unload, unmount)
  // always see the latest values without waiting for a re-render.
  const draftRef = useRef(draft);
  const savedKeyRef = useRef(savedKey);
  const updatedAtRef = useRef(initialReport.updatedAt);
  const reportRef = useRef(report);
  const lockedRef = useRef(locked);
  const readOnlyRef = useRef(readOnly);
  const savingRef = useRef(null);
  const mountedRef = useRef(true);
  lockedRef.current = locked;
  readOnlyRef.current = readOnly;
  reportRef.current = report;

  const currentKey = useMemo(() => draftKey(draft), [draft]);
  const dirty = !locked && currentKey !== savedKey;
  const isDirty = useCallback(() => !lockedRef.current && draftKey(draftRef.current) !== savedKeyRef.current, []);

  const commitDraft = useCallback((next) => {
    draftRef.current = next;
    setDraft(next);
  }, []);
  const patchDraft = useCallback((changes) => commitDraft({ ...draftRef.current, ...changes }), [commitDraft]);

  const applyServerReport = useCallback((serverReport, { resetDraft = false } = {}) => {
    updatedAtRef.current = serverReport.updatedAt;
    setReport(serverReport);
    reportRef.current = serverReport;
    setSavedAt(serverReport.updatedAt);
    onReportChanged?.(serverReport);
    if (resetDraft) {
      const fresh = draftFromReport(serverReport);
      commitDraft(fresh);
      savedKeyRef.current = draftKey(fresh);
      setSavedKey(savedKeyRef.current);
    }
  }, [commitDraft, onReportChanged]);

  // ── Save ─────────────────────────────────────────────────────────────────
  const save = useCallback(async () => {
    if (lockedRef.current) return true;
    if (savingRef.current) await savingRef.current;
    if (!isDirty()) return true;
    const task = (async () => {
      setSaveState("saving");
      setSaveError("");
      const snapshotOfDraft = draftRef.current;
      const result = await updateReport(clientId, reportRef.current.id, {
        expectedUpdatedAt: updatedAtRef.current,
        name: snapshotOfDraft.name.trim() || reportRef.current.name,
        config: snapshotOfDraft.config,
        content: { narratives: snapshotOfDraft.narratives, snapshot: snapshotOfDraft.snapshot, snapshotAt: snapshotOfDraft.snapshotAt },
        ...(snapshotOfDraft.periodStart && snapshotOfDraft.periodEnd ? { periodStart: snapshotOfDraft.periodStart, periodEnd: snapshotOfDraft.periodEnd } : {}),
      });
      if (!mountedRef.current) return result.ok;
      if (result.ok) {
        updatedAtRef.current = result.data.updatedAt;
        setReport(result.data);
        reportRef.current = result.data;
        setSavedAt(result.data.updatedAt);
        savedKeyRef.current = draftKey(snapshotOfDraft); // edits made while saving stay "unsaved"
        setSavedKey(savedKeyRef.current);
        setSaveState("idle");
        setConflict(null);
        onReportChanged?.(result.data);
        return true;
      }
      if (result.code === "report_conflict") { setConflict({ currentUpdatedAt: result.currentUpdatedAt }); setSaveState("error"); setSaveError(result.error); return false; }
      if (result.code === "not_found") { setGone(true); setSaveState("error"); return false; }
      setSaveState("error");
      setSaveError(result.error);
      return false;
    })();
    savingRef.current = task;
    try { return await task; } finally { savingRef.current = null; }
  }, [clientId, isDirty, onReportChanged]);

  const saveRef = useRef(save);
  saveRef.current = save;

  // "Save draft": persist, and only on success go back to the view. On failure
  // stay in the editor with every change intact so it can be retried.
  const saveDraft = async () => {
    if (!(await save())) return;
    onNotice("success", "Draft saved");
    onModeChange("view");
  };

  const reloadLatest = async () => {
    const result = await getReport(clientId, reportRef.current.id);
    if (!result.ok) { if (result.code === "not_found") setGone(true); onNotice("error", result.error); return; }
    applyServerReport(result.data, { resetDraft: true });
    setConflict(null);
    setSaveState("idle");
    setSaveError("");
    onNotice("success", "Loaded the latest version");
  };
  const overwriteWithMine = async () => {
    const latest = await getReport(clientId, reportRef.current.id);
    if (!latest.ok) { onNotice("error", latest.error); return; }
    updatedAtRef.current = latest.data.updatedAt;
    setConflict(null);
    await save();
  };

  // ── Unsaved-changes protection (shared app system) ───────────────────────
  useEffect(() => {
    if (!active) { clearChanges(); return undefined; }
    registerChanges({
      isDirty,
      onSave: async () => (await saveRef.current()) !== false,
      // Discard restores the last SAVED report; it never saves anything.
      onDiscard: () => {
        const fresh = draftFromReport(reportRef.current);
        commitDraft(fresh);
        savedKeyRef.current = draftKey(fresh);
        setSavedKey(savedKeyRef.current);
      },
    });
    return () => clearChanges();
  // dirty/editing are dependencies on purpose: the shared context forgets the
  // registration after a Save/Discard from its prompt, so it must be re-made
  // whenever the state it describes changes.
  }, [active, dirty, editing, registerChanges, clearChanges, isDirty, commitDraft]);

  useEffect(() => {
    if (!active || !dirty) return undefined;
    const warn = (event) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [active, dirty]);

  // If the editor closes some other way (browser Back) with unsaved edits, save
  // them rather than showing a view that silently differs from what was saved.
  const wasEditingRef = useRef(editing);
  useEffect(() => {
    if (wasEditingRef.current && !editing && isDirty()) saveRef.current();
    if (!editing) autoLoadedRef.current = false; // a later Edit may need to load data again
    wasEditingRef.current = editing;
  }, [editing, isDirty]);

  // The app can switch client without asking; never lose an in-progress edit.
  useEffect(() => {
    mountedRef.current = true;
    const id = initialReport.id;
    return () => {
      mountedRef.current = false;
      if (!lockedRef.current && draftKey(draftRef.current) !== savedKeyRef.current) {
        const d = draftRef.current;
        updateReport(clientId, id, {
          expectedUpdatedAt: updatedAtRef.current,
          name: d.name.trim() || reportRef.current.name,
          config: d.config,
          content: { narratives: d.narratives, snapshot: d.snapshot, snapshotAt: d.snapshotAt },
        });
      }
    };
  }, [clientId, initialReport.id]);

  const requestExit = () => confirmNavigation(onExit);
  const closeEditor = () => confirmNavigation(() => onModeChange("view"));

  // ── Edit entry points ────────────────────────────────────────────────────
  // Draft reports open the editor directly. A final/sent report is locked, so
  // editing it means reopening it as a draft first (asked explicitly).
  const needsReopen = canEdit && (report.status === "final" || report.status === "sent");
  const requestEdit = () => {
    if (report.status === "draft") onModeChange("edit");
    else if (needsReopen) setReopenAsk(true);
  };
  useEffect(() => {
    if (mode === "edit" && needsReopen) setReopenAsk(true);
  }, [mode, needsReopen]);

  const reopenAndEdit = async () => {
    setBusyAction("reopen");
    const result = await changeReportStatus(clientId, reportRef.current.id, "draft");
    setBusyAction("");
    if (!result.ok) { onNotice("error", result.error); return; }
    applyServerReport(result.data);
    setReopenAsk(false);
    onNotice("success", "Reopened as a draft");
    onModeChange("edit");
  };
  const cancelReopen = () => { setReopenAsk(false); if (mode === "edit") onModeChange("view"); };

  // ── Data ─────────────────────────────────────────────────────────────────
  const refreshData = useCallback(async ({ only = null, force = false } = {}) => {
    if (readOnlyRef.current) return;
    setDataLoading(true);
    setDataError("");
    const d = draftRef.current;
    const period = periodFromConfig(d.config);
    const { snapshot, snapshotAt } = await loadReportData({
      clientId, clientName, sources: d.config.sources, period, config: d.config, currency, forceRefresh: force, only, previous: d.snapshot,
    });
    if (!mountedRef.current) return;
    // A custom range is the report's own period; presets take what the sources reported.
    const range = period.mode === "custom" ? { start: period.start, end: period.end } : snapshotPeriod(snapshot, { start: period.start, end: period.end });
    patchDraft({ config: normalizeConfig(draftRef.current.config, snapshot), snapshot, snapshotAt, periodStart: range.start, periodEnd: range.end });
    setDataLoading(false);
    const results = Object.values(snapshot.sources);
    if (results.length && results.every((entry) => entry.ok === false)) setDataError("None of the selected sources could be loaded. Check the connections in Integrations, then try again.");
  }, [clientId, clientName, currency, patchDraft]);

  const autoLoadedRef = useRef(false);
  useEffect(() => {
    if (autoLoadedRef.current || readOnly || draftRef.current.snapshot || !draftRef.current.config.sources.length) return;
    autoLoadedRef.current = true;
    refreshData();
  }, [readOnly, refreshData]);

  // ── Settings edits ───────────────────────────────────────────────────────
  const setName = (name) => patchDraft({ name });
  const withoutCustom = (config) => {
    const { customStart, customEnd, ...rest } = config; // eslint-disable-line no-unused-vars
    return rest;
  };
  const setDays = (days) => {
    const range = periodDaysToRange(days);
    patchDraft({ config: { ...withoutCustom(draftRef.current.config), periodMode: "preset", periodDays: days }, periodStart: range.start, periodEnd: range.end });
  };
  const setCustomRange = ({ start, end }) => {
    patchDraft({
      config: { ...draftRef.current.config, periodMode: "custom", periodDays: daysBetween(start, end) + 1, customStart: start, customEnd: end },
      periodStart: start,
      periodEnd: end,
    });
  };
  const toggleSource = (id) => {
    const config = draftRef.current.config;
    const next = config.sources.includes(id) ? config.sources.filter((item) => item !== id) : [...config.sources, id];
    if (!next.length) { onNotice("error", "A report needs at least one data source."); return; }
    patchDraft({ config: { ...config, sources: next, sections: reconcileSections(config.sections, next) } });
  };
  const updateSections = (mapper) => patchDraft({ config: { ...draftRef.current.config, sections: mapper(draftRef.current.config.sections) } });
  const toggleSection = (id) => updateSections((sections) => sections.map((section) => (section.id === id ? { ...section, enabled: !section.enabled } : section)));
  const moveSection = (from, to) => updateSections((sections) => {
    if (to < 0 || to >= sections.length || from === to) return sections;
    const next = [...sections];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    return next;
  });
  const setChartType = (id, type) => patchDraft({ config: { ...draftRef.current.config, chartTypes: { ...draftRef.current.config.chartTypes, [id]: type } } });
  const setNarrative = (id, text) => patchDraft({ narratives: { ...draftRef.current.narratives, [id]: text } });

  // ── AI narrative ─────────────────────────────────────────────────────────
  const generate = useCallback(async (sectionId) => {
    if (readOnlyRef.current) return false;
    if (!aiConnected) { setGenErrors((current) => ({ ...current, [sectionId]: "Connect an AI provider to generate report narratives." })); return false; }
    setGen((current) => ({ ...current, [sectionId]: true }));
    setOpenSec((current) => ({ ...current, [sectionId]: true }));
    setGenErrors((current) => ({ ...current, [sectionId]: "" }));
    // The server writes from the SAVED data snapshot, so save first.
    const saved = await saveRef.current();
    if (!saved) {
      setGen((current) => ({ ...current, [sectionId]: false }));
      setGenErrors((current) => ({ ...current, [sectionId]: "Save the report first, then try again." }));
      return false;
    }
    const result = await generateNarrative(clientId, reportRef.current.id, sectionId);
    if (!mountedRef.current) return false;
    setGen((current) => ({ ...current, [sectionId]: false }));
    if (!result.ok) { setGenErrors((current) => ({ ...current, [sectionId]: result.error })); return false; }
    setNarrative(sectionId, result.data.text);
    setGenWarnings((current) => ({ ...current, [sectionId]: result.data.warnings || [] }));
    return true;
  }, [aiConnected, clientId]); // eslint-disable-line react-hooks/exhaustive-deps

  const generateAll = async () => {
    const targets = draftRef.current.config.sections.filter((section) => section.enabled && isAiSection(section.id));
    for (const section of targets) {
      // Stop at the first failure so a broken provider isn't hammered.
      // eslint-disable-next-line no-await-in-loop
      if (!(await generate(section.id))) break;
    }
  };

  // ── Lifecycle actions ────────────────────────────────────────────────────
  const finalize = async () => {
    if (!(await save())) return;
    setBusyAction("final");
    const result = await changeReportStatus(clientId, report.id, "final");
    setBusyAction("");
    if (!result.ok) { onNotice("error", result.error); return; }
    applyServerReport(result.data);
    if (mode === "edit") onModeChange("view");
    onNotice("success", "Marked as final. The report is now locked.");
  };

  const branded = useMemo(() => ({ name: branding?.name, logo: branding?.logo, accent: branding?.accent }), [branding]);
  const period = { start: draft.periodStart, end: draft.periodEnd };

  // A client PDF must not contain "not written yet" placeholders, so an enabled
  // text section with no text is left out. Say which ones before downloading.
  const emptyEnabledSections = () => draftRef.current.config.sections.filter(
    (section) => section.enabled && isAiSection(section.id) && !(draftRef.current.narratives[section.id] || "").trim(),
  );

  // Builds a fresh PDF from the report exactly as it is now (saving any edits
  // first), stores it on the report and downloads it.
  const exportPdf = async ({ force = false } = {}) => {
    if (exporting) return;
    if (!force) {
      const empty = emptyEnabledSections();
      if (empty.length) { setExportWarn(empty.map((section) => sectionLabel(section.id))); return; }
    }
    setExportWarn(null);
    setExporting(true);
    setExportError("");
    try {
      if (!(await saveRef.current())) { setExportError("Couldn't save the report, so the PDF wasn't created."); return; }
      const d = draftRef.current;
      if (!d.snapshot) { setExportError("Load the report data before exporting."); return; }
      const { renderReportPdf } = await import("./pdf/renderReportPdf");
      const { blob, pages } = await renderReportPdf({
        branding: branded, clientName, name: d.name, config: d.config, narratives: d.narratives, snapshot: d.snapshot,
        period: { start: d.periodStart, end: d.periodEnd }, generatedAt: d.snapshotAt, currency,
      });
      // Only a report's first PDF announces itself to the team; later downloads are silent.
      const upload = await uploadReportPdf(clientId, reportRef.current.id, blob, pages, { silent: (reportRef.current.pdf?.version || 0) > 0 });
      saveBlob(blob, reportFilename(clientName, { name: d.name, periodEnd: d.periodEnd }));
      if (!mountedRef.current) return;
      if (upload.ok) {
        applyServerReport(upload.data);
        onNotice("success", "PDF exported and saved to this report");
      } else {
        setExportError(`The PDF was downloaded, but couldn't be saved to the library. ${upload.error}`);
      }
    } catch (error) {
      console.error("[Reports] PDF export failed", error);
      if (mountedRef.current) setExportError("Couldn't create the PDF. Try again, and if it keeps failing check that your logo and text are valid.");
    } finally {
      if (mountedRef.current) setExporting(false);
    }
  };

  const downloadStored = async () => {
    setBusyAction("download");
    const result = await downloadReportPdf(clientId, report.id);
    setBusyAction("");
    if (!result.ok) { onNotice("error", result.error); return; }
    saveBlob(result.blob, reportFilename(clientName, report));
  };

  // ── Derived UI state ─────────────────────────────────────────────────────
  const stale = snapshotIsStale(draft.config, draft.snapshot);
  const totalSourcesCount = draft.config.sources.length;
  const failedSources = Object.entries(draft.snapshot?.sources || {}).filter(([, entry]) => entry.ok === false).map(([id]) => id);
  const enabledAi = draft.config.sections.filter((section) => section.enabled && isAiSection(section.id));
  const includable = useMemo(() => {
    const ids = new Set([...connectedSources.map((source) => source.id), ...draft.config.sources]);
    return [...ids].map((id) => SOURCE_BY_ID[id]).filter(Boolean);
  }, [connectedSources, draft.config.sources]);
  const disconnectedIncluded = draft.config.sources.filter((id) => !connectedSources.some((source) => source.id === id));
  const periodState = periodFromConfig(draft.config);

  if (gone) {
    return (
      <div className="rp-card rp-empty-state">
        <div className="rp-empty-icon" aria-hidden="true"><i className="ti ti-file-off" /></div>
        <h2>This report is no longer available</h2>
        <p>It was deleted, or you no longer have access to this client.</p>
        <button type="button" className="primary-btn" onClick={onExit}>Back to library</button>
      </div>
    );
  }

  const loadedOk = Object.values(draft.snapshot?.sources || {}).filter((entry) => entry.ok !== false).length;
  const dataStatus = dataLoading ? "loading" : !draft.snapshot ? "none" : stale ? "stale" : failedSources.length ? "partial" : "ok";
  const dataCopy = {
    loading: ["ti-loader-2", "Loading data…", "Fetching from your connected sources"],
    none: ["ti-database-off", "No data loaded yet", "Load data to fill the report"],
    stale: ["ti-alert-triangle", "Data needs updating", "Update to match your settings and add the latest charts"],
    partial: ["ti-alert-circle", `${loadedOk} of ${totalSourcesCount} sources loaded`, `Loaded ${formatTimestamp(draft.snapshotAt)}`],
    ok: ["ti-circle-check", `${loadedOk} source${loadedOk === 1 ? "" : "s"} loaded`, `Data as of ${formatTimestamp(draft.snapshotAt)}`],
  }[dataStatus];
  const wordCount = (text) => (text.trim() ? text.trim().split(/\s+/).length : 0);

  const renderChartType = (section) => {
    if (!section.enabled || !isChartSection(section.id) || section.id === "chart") return null;
    const { sourceId, key } = chartSectionParts(section.id);
    const series = seriesOf(draft.snapshot?.sources?.[sourceId]).find((item) => item.key === key);
    // breakdowns get the same chart-type picker as time series (horizontal bar by default)
    const types = series ? availableChartTypes(series) : CHART_TYPES;
    const current = series ? chartTypeFor(draft.config, section.id, series) : (draft.config.chartTypes?.[section.id] || DEFAULT_CHART_TYPE);
    return (
      <div className="rp-sec-body rp-sec-body--type">
        <span className="rp-type-label">Chart type</span>
        <div className="rp-type-group" role="radiogroup" aria-label={`Chart type for ${sectionLabel(section.id)}`}>
          {types.map((type) => (
            <button key={type.id} type="button" role="radio" aria-checked={current === type.id} className={`rp-type${current === type.id ? " on" : ""}`} title={type.label} onClick={() => setChartType(section.id, type.id)}>
              <i className={`ti ${type.icon}`} aria-hidden="true" /> {type.label}
            </button>
          ))}
        </div>
      </div>
    );
  };

  const lockedNote = report.status === "archived"
    ? "This report is archived and read-only. Restore it from the Library to edit."
    : report.status === "sent"
      ? (canEdit ? "This report was sent. Choose Edit to reopen it as a draft." : "This report was sent. You have view-only access.")
      : report.status === "draft"
        ? "You have view-only access to this report."
        : (canEdit ? "This report is final. Choose Edit to reopen it as a draft." : "This report is final. You have view-only access.");

  return (
    <div className="rp-builder">
      <button type="button" className="rp-back" onClick={requestExit}><i className="ti ti-arrow-left" aria-hidden="true" /> Client reports</button>

      <div className="rp-builder-head">
        <div className="rp-builder-title">
          <h1 title={draft.name}>{draft.name || "Untitled report"}</h1>
          <div className="rp-builder-meta">
            <StatusBadge status={report.status} />
            {editing && <span className="rp-mode-tag"><i className="ti ti-pencil" aria-hidden="true" /> Editing</span>}
            {editing && <SaveIndicator state={saveState} dirty={dirty} savedAt={savedAt} onRetry={() => save()} />}
            <span className="rp-meta-text">{[report.template && TEMPLATE_BY_ID[report.template]?.label, formatPeriod(draft.periodStart, draft.periodEnd)].filter(Boolean).join(" · ")}</span>
          </div>
        </div>
        <div className="rp-builder-actions">
          {editing && (
            <button type="button" className={dirty ? "primary-btn" : "ghost-btn"} onClick={saveDraft} disabled={!dirty || saveState === "saving"}>
              {saveState === "saving" ? <Spinner /> : <i className="ti ti-device-floppy" aria-hidden="true" />} Save draft
            </button>
          )}
          {canEdit && report.status === "draft" && (
            <button type="button" className="ghost-btn" onClick={finalize} disabled={busyAction === "final" || !draft.snapshot} title={draft.snapshot ? "Lock the report as ready to share" : "Load the report data first"}>
              {busyAction === "final" ? <Spinner /> : <i className="ti ti-circle-check" aria-hidden="true" />} Mark as final
            </button>
          )}
          {canEdit && report.status !== "archived" ? (
            // One button, always current: it builds the PDF from the report as it
            // is now (including sections you just turned on), never an old file.
            <button type="button" className="ghost-btn" onClick={() => exportPdf()} disabled={exporting || !draft.snapshot || dataLoading} title="Builds a fresh PDF from this report exactly as it is now">
              {exporting ? <><Spinner /> Creating PDF…</> : <><i className="ti ti-download" aria-hidden="true" /> Download PDF</>}
            </button>
          ) : (
            report.pdf?.available && (
              <button type="button" className="ghost-btn" onClick={downloadStored} disabled={busyAction === "download"} title={`Last exported ${formatTimestamp(report.pdf.generatedAt)}`}>
                {busyAction === "download" ? <Spinner /> : <i className="ti ti-download" aria-hidden="true" />} Download PDF
              </button>
            )
          )}
          {editing ? (
            <button type="button" className="ghost-btn" onClick={closeEditor}><i className="ti ti-eye" aria-hidden="true" /> View</button>
          ) : (
            canEdit && report.status !== "archived" && (
              <button type="button" className="primary-btn" onClick={requestEdit} disabled={busyAction === "reopen"}>
                <i className="ti ti-pencil" aria-hidden="true" /> Edit
              </button>
            )
          )}
        </div>
      </div>

      <div className="rp-banners">
        {locked && <Banner tone="info" icon="ti-lock">{lockedNote}</Banner>}
        {!locked && !canEdit && <Banner tone="info" icon="ti-lock">You have view-only access to this report.</Banner>}
        {conflict && (
          <Banner tone="error" actions={<><button type="button" className="ghost-btn" onClick={reloadLatest}>Load latest (discard mine)</button><button type="button" className="primary-btn" onClick={overwriteWithMine}>Overwrite with mine</button></>}>
            This report was changed somewhere else since you opened it. Choose whose version to keep.
          </Banner>
        )}
        {saveState === "error" && !conflict && saveError && <Banner tone="error" actions={<button type="button" className="ghost-btn" onClick={() => save()}>Retry</button>}>{saveError} Your changes are still here.</Banner>}
        {exportError && <Banner tone="error">{exportError}</Banner>}
        {dataError && <Banner tone="error" actions={<button type="button" className="ghost-btn" onClick={() => refreshData({ force: true })}>Retry</button>}>{dataError}</Banner>}
        {editing && disconnectedIncluded.length > 0 && (
          <Banner tone="warn">
            {disconnectedIncluded.map((id) => SOURCE_BY_ID[id]?.label).join(", ")} {disconnectedIncluded.length > 1 ? "are" : "is"} no longer connected to this client. Data already loaded stays in the report; reconnect it in Integrations to refresh.
          </Banner>
        )}
        {!editing && !locked && stale && <Banner tone="warn" icon="ti-clock">This report's data is out of date (its settings changed, or newer charts are available). Choose Edit, then Update.</Banner>}
      </div>

      <div className={`rp-workspace${editing ? "" : " rp-workspace--view"}`}>
        {editing && (
          <aside className="rp-panel" aria-label="Report settings">
            <div className={`rp-data-card ${dataStatus}`}>
              <span className="rp-data-icon" aria-hidden="true"><i className={`ti ${dataCopy[0]}${dataStatus === "loading" ? " spin" : ""}`} /></span>
              <div className="rp-data-text">
                <b>{dataCopy[1]}</b>
                <span>{dataCopy[2]}</span>
              </div>
              <button type="button" className={dataStatus === "stale" || dataStatus === "none" ? "primary-btn sm" : "ghost-btn sm"} onClick={() => refreshData({ force: Boolean(draft.snapshot) && dataStatus !== "stale" })} disabled={dataLoading}>
                <i className="ti ti-refresh" aria-hidden="true" /> {draft.snapshot ? (dataStatus === "stale" ? "Update" : "Refresh") : "Load"}
              </button>
            </div>
            {failedSources.length > 0 && !dataLoading && (
              <div className="rp-failed">
                {failedSources.map((id) => (
                  <div key={id} className="rp-failed-row">
                    <span><i className={`ti ${SOURCE_BY_ID[id]?.icon}`} aria-hidden="true" /> {SOURCE_BY_ID[id]?.label} couldn't be loaded</span>
                    <button type="button" className="rp-link" onClick={() => refreshData({ only: [id], force: true })}>Retry</button>
                  </div>
                ))}
              </div>
            )}

            <Tabs
              label="Report settings"
              value={panelTab}
              onChange={setPanelTab}
              tabs={[
                { id: "setup", label: "Setup", icon: "ti-adjustments" },
                { id: "content", label: "Content", icon: "ti-layout-list", count: draft.config.sections.filter((section) => section.enabled).length },
                { id: "brand", label: "Brand", icon: "ti-palette" },
              ]}
            />

            <div className="rp-panel-body" role="tabpanel" id={`rp-tabpanel-${panelTab}`} aria-labelledby={`rp-tab-${panelTab}`}>
              {panelTab === "setup" && (
                <div className="rp-stack">
                  <label className="rp-field">
                    <span>Report name</span>
                    <input className="rp-input" value={draft.name} maxLength={120} onChange={(event) => setName(event.target.value)} />
                  </label>
                  <div className="rp-field">
                    <span>Period</span>
                    <PeriodPicker period={periodState} options={periodOptionsFor(draft.config)} onPreset={setDays} onCustom={setCustomRange} />
                  </div>
                  <fieldset className="rp-field">
                    <legend>Data sources</legend>
                    <div className="rp-source-grid compact">
                      {includable.map((source) => {
                        const on = draft.config.sources.includes(source.id);
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
                  </fieldset>
                </div>
              )}

              {panelTab === "content" && (
                <div className="rp-stack">
                  {!aiConnected && (
                    <Banner tone="info" icon="ti-sparkles" actions={<button type="button" className="ghost-btn sm" onClick={() => onGoTo("integrations")}>Integrations</button>}>
                      Connect an AI provider to generate narratives. You can still write them yourself.
                    </Banner>
                  )}
                  <div className="rp-content-head">
                    <span className="rp-muted-text">Drag or use the arrows to reorder. Turn sections on or off.</span>
                    {enabledAi.length > 0 && (
                      <button type="button" className="ghost-btn sm" onClick={generateAll} disabled={!aiConnected || Object.values(gen).some(Boolean) || !draft.snapshot} title={aiConnected ? "Generate every enabled AI section" : "Connect an AI provider first"}>
                        <i className="ti ti-sparkles" aria-hidden="true" /> Generate all
                      </button>
                    )}
                  </div>
                  <ol className="rp-sections">
                    {draft.config.sections.filter((section) => section.id !== "chart" || !draft.snapshot).map((section) => {
                      const index = draft.config.sections.indexOf(section);
                      const ai = isAiSection(section.id);
                      const text = draft.narratives[section.id] || "";
                      const busy = Boolean(gen[section.id]);
                      const expanded = Boolean(openSec[section.id]) && section.enabled && ai;
                      const sourceColor = isCommentary(section.id) ? SOURCE_BY_ID[section.id.split(":")[1]]?.color : isChartSection(section.id) && section.id !== "chart" ? SOURCE_BY_ID[chartSectionParts(section.id).sourceId]?.color : undefined;
                      return (
                        <li
                          key={section.id}
                          className={`rp-sec${section.enabled ? " on" : " off"}${dragIndex === index ? " dragging" : ""}${expanded ? " open" : ""}`}
                          onDragOver={(event) => { if (dragIndex !== null) event.preventDefault(); }}
                          onDrop={(event) => { event.preventDefault(); if (dragIndex !== null) moveSection(dragIndex, index); setDragIndex(null); }}
                        >
                          <div className="rp-sec-head">
                            <span className="rp-grip" draggable onDragStart={() => setDragIndex(index)} onDragEnd={() => setDragIndex(null)} aria-hidden="true" title="Drag to reorder">
                              <i className="ti ti-grip-vertical" />
                            </span>
                            <span className="rp-sec-tile" style={sourceColor ? { color: sourceColor } : undefined} aria-hidden="true"><i className={`ti ${sectionIcon(section.id)}`} /></span>
                            <div className="rp-sec-info">
                              <span className="rp-sec-name">{sectionLabel(section.id)}</span>
                              <span className="rp-sec-sub">
                                {!section.enabled ? "Hidden" : ai ? (text.trim() ? `${wordCount(text)} words written` : "Not written yet") : "Included"}
                              </span>
                            </div>
                            <span className="rp-move">
                              <button type="button" className="rp-mini-btn" onClick={() => moveSection(index, index - 1)} disabled={index === 0} aria-label={`Move ${sectionLabel(section.id)} up`}><i className="ti ti-chevron-up" aria-hidden="true" /></button>
                              <button type="button" className="rp-mini-btn" onClick={() => moveSection(index, index + 1)} disabled={index === draft.config.sections.length - 1} aria-label={`Move ${sectionLabel(section.id)} down`}><i className="ti ti-chevron-down" aria-hidden="true" /></button>
                            </span>
                            {ai && section.enabled && (
                              <button type="button" className="rp-icon-btn sm" aria-expanded={expanded} aria-label={`${expanded ? "Hide" : "Edit"} ${sectionLabel(section.id)} text`} title={expanded ? "Hide text" : "Edit text"} onClick={() => setOpenSec((current) => ({ ...current, [section.id]: !current[section.id] }))}>
                                <i className={`ti ${expanded ? "ti-chevron-up" : "ti-pencil"}`} aria-hidden="true" />
                              </button>
                            )}
                            <Switch checked={section.enabled} onChange={() => toggleSection(section.id)} label={`Include ${sectionLabel(section.id)}`} />
                          </div>
                          {renderChartType(section)}
                          {expanded && (
                            <div className="rp-sec-body">
                              <textarea
                                rows={6}
                                value={text}
                                maxLength={MAX_NARRATIVE}
                                placeholder="Write this section, or generate a draft from the report data."
                                aria-label={`${sectionLabel(section.id)} text`}
                                onChange={(event) => setNarrative(section.id, event.target.value)}
                              />
                              <div className="rp-sec-actions">
                                <button type="button" className="ghost-btn sm" onClick={() => generate(section.id)} disabled={busy || !aiConnected || !draft.snapshot} title={!aiConnected ? "Connect an AI provider first" : !draft.snapshot ? "Load data first" : undefined}>
                                  {busy ? <><Spinner /> Generating…</> : <><i className="ti ti-sparkles" aria-hidden="true" /> {text.trim() ? "Regenerate" : "Generate"}</>}
                                </button>
                                <span className="rp-muted-text">{text.length}/{MAX_NARRATIVE}</span>
                              </div>
                              {genErrors[section.id] && (
                                <div className="rp-inline-error" role="alert">
                                  {genErrors[section.id]}{" "}
                                  {!aiConnected
                                    ? <button type="button" className="rp-link" onClick={() => onGoTo("integrations")}>Open Integrations</button>
                                    : <button type="button" className="rp-link" onClick={() => generate(section.id)}>Retry</button>}
                                </div>
                              )}
                              {(genWarnings[section.id] || []).length > 0 && (
                                <ul className="rp-warnings" aria-label="Figures to double-check">
                                  {genWarnings[section.id].map((warning) => <li key={warning}><i className="ti ti-alert-triangle" aria-hidden="true" /> {warning}</li>)}
                                </ul>
                              )}
                            </div>
                          )}
                        </li>
                      );
                    })}
                  </ol>
                </div>
              )}

              {panelTab === "brand" && (
                <div className="rp-stack">
                  <div className="rp-brand-card" style={branding?.accent ? { "--rp-accent": branding.accent } : undefined}>
                    <div className="rp-brand-top">
                      <div className="rp-mark lg" style={branding?.logo ? { background: "transparent" } : undefined}>
                        {branding?.logo ? <img src={branding.logo} alt="" /> : (branding?.name || "A").trim().slice(0, 1).toUpperCase()}
                      </div>
                      <div>
                        <div className="rp-brand-name">{branding?.name || "Your agency"}</div>
                        <div className="rp-muted-text">Issued under this identity</div>
                      </div>
                    </div>
                    <dl className="rp-brand-list">
                      <div><dt>Logo</dt><dd>{branding?.logo ? "Set" : "Not set (initial used)"}</dd></div>
                      <div><dt>Accent</dt><dd><span className="rp-swatch" style={{ background: branding?.accent || "var(--accent)" }} aria-hidden="true" /> {branding?.accent || "Theme default"}</dd></div>
                      <div><dt>Footer</dt><dd>{branding?.name || "Your agency"} — confidential</dd></div>
                    </dl>
                  </div>
                  <button type="button" className="ghost-btn" onClick={() => onGoTo("branding")}>
                    <i className="ti ti-palette" aria-hidden="true" /> Edit branding <i className="ti ti-arrow-right" aria-hidden="true" />
                  </button>
                  <p className="rp-muted-text">Branding changes apply to this report the next time you return here.</p>
                </div>
              )}
            </div>
          </aside>
        )}

        <div className="rp-canvas">
          <ReportPreview
            branding={branded}
            clientName={clientName}
            name={draft.name}
            config={draft.config}
            narratives={draft.narratives}
            snapshot={draft.snapshot}
            period={period}
            mode={editing ? "edit" : "view"}
            wide={!editing}
            loading={dataLoading}
            currency={currency}
            generatedAt={draft.snapshotAt}
            onRetrySource={(id) => refreshData({ only: [id], force: true })}
          />
        </div>
      </div>

      {exportWarn && (
        <Modal
          icon="ti-alert-triangle"
          title="Some sections are empty"
          description="These sections are turned on but have no text yet, so they will be left out of the PDF:"
          onClose={() => setExportWarn(null)}
          footer={(
            <>
              <button type="button" className="ghost-btn" onClick={() => setExportWarn(null)}>Go back and write them</button>
              <button type="button" className="primary-btn" onClick={() => exportPdf({ force: true })}>Download anyway</button>
            </>
          )}
        >
          <ul className="rp-warn-list">{exportWarn.map((label) => <li key={label}>{label}</li>)}</ul>
        </Modal>
      )}

      {reopenAsk && (
        <Modal
          icon="ti-rotate"
          title={`Reopen this ${report.status} report?`}
          description="Editing needs a draft, so the report goes back to Draft and you can change it. Mark it as final again when you're done."
          onClose={cancelReopen}
          busy={busyAction === "reopen"}
          footer={(
            <>
              <button type="button" className="ghost-btn" onClick={cancelReopen} disabled={busyAction === "reopen"}>Cancel</button>
              <button type="button" className="primary-btn" onClick={reopenAndEdit} disabled={busyAction === "reopen"}>
                {busyAction === "reopen" ? <><Spinner /> Reopening…</> : <><i className="ti ti-pencil" aria-hidden="true" /> Reopen and edit</>}
              </button>
            </>
          )}
        />
      )}
    </div>
  );
}
