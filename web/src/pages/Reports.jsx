// SPDX-License-Identifier: AGPL-3.0-only
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { canPerformAction } from "../lib/permissions";
import { getReport, sendReport } from "./reports/api";
import { buildReportPdf } from "./reports/buildReportPdf";
import ReportBuilder from "./reports/ReportBuilder";
import ReportCreateDialog from "./reports/ReportCreateDialog";
import ReportsErrorBoundary from "./reports/ReportsErrorBoundary";
import ReportsLibrary from "./reports/ReportsLibrary";
import SendToTeammatesModal from "./reports/SendToTeammatesModal";
import { Banner } from "./reports/ReportControls";
import { connectedReportSources, hasAiProvider } from "./reports/reportModel";
import "../styles/reports.css";

// Reports for the CURRENTLY OPEN client workspace. Everything below is keyed
// by client id, so switching client drops the previous client's library,
// open report, teammate list and builder state; nothing is shared between clients.
//
// The URL carries the open report and how it is shown:
//   /reports                            library
//   /reports?report=<id>&mode=view      read-only preview (the default)
//   /reports?report=<id>&mode=edit      editor + live preview
function ReportsWorkspace({ active, branding, onGoTo, client, org, sources, workspaceCurrency, prefill, onPrefillConsumed }) {
  const clientId = client.id;
  const clientName = client.name || "this client";
  const canCreate = canPerformAction(client, org, "Reports", "edit");
  const connectedSources = useMemo(() => connectedReportSources(sources), [sources]);
  const aiConnected = hasAiProvider(sources);

  const [searchParams, setSearchParams] = useSearchParams();
  const reportParam = searchParams.get("report");
  const modeParam = searchParams.get("mode") === "edit" ? "edit" : "view";
  const [openReport, setOpenReport] = useState(null);
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [prefillSource, setPrefillSource] = useState(null);
  const [sendTarget, setSendTarget] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [notice, setNotice] = useState(null);
  const noticeTimer = useRef(null);

  const notify = useCallback((kind, text) => {
    window.clearTimeout(noticeTimer.current);
    setNotice({ kind, text, id: Date.now() });
    noticeTimer.current = window.setTimeout(() => setNotice(null), kind === "error" ? 8000 : 4500);
  }, []);
  useEffect(() => () => window.clearTimeout(noticeTimer.current), []);

  const setReportParam = useCallback((id, mode) => {
    const next = new URLSearchParams(window.location.search);
    if (id) {
      next.set("report", id);
      if (mode === "edit") next.set("mode", "edit"); else next.set("mode", "view");
    } else {
      next.delete("report");
      next.delete("mode");
    }
    setSearchParams(next);
  }, [setSearchParams]);

  // Load the report named in the URL (deep link, notification, refresh, or a
  // click in the Library) - always through the client-scoped API.
  useEffect(() => {
    if (!active) return undefined;
    if (!reportParam) { setOpenReport(null); setOpenError(""); return undefined; }
    if (openReport?.id === reportParam) return undefined;
    let cancelled = false;
    setOpening(true);
    setOpenError("");
    getReport(clientId, reportParam).then((result) => {
      if (cancelled) return;
      setOpening(false);
      if (!result.ok) {
        setOpenReport(null);
        setOpenError(result.code === "not_found" ? "That report doesn't exist for this client. It may have been deleted." : result.error);
        return;
      }
      setOpenReport(result.data);
    });
    return () => { cancelled = true; };
  }, [active, reportParam, clientId]); // eslint-disable-line react-hooks/exhaustive-deps

  // "Build report" from a dashboard: open the creation dialog with that
  // source pre-selected.
  useEffect(() => {
    if (!active || !prefill) return;
    if (canCreate) { setPrefillSource(prefill.source || null); setCreateOpen(true); }
    onPrefillConsumed?.();
  }, [active, prefill]); // eslint-disable-line react-hooks/exhaustive-deps

  const closeReport = useCallback(() => {
    setOpenReport(null);
    setOpenError("");
    setReportParam(null);
    setRefreshKey((value) => value + 1);
  }, [setReportParam]);
  // Eye / name -> "view"; three-dot Edit -> "edit".
  const openFromLibrary = (report, mode = "view") => {
    if (report.id !== openReport?.id) setOpenReport(null);
    setReportParam(report.id, mode);
  };

  // Send to teammates: make sure the PDF matches the report as it is saved NOW
  // (re-render it, silently), then have the server email that stored file to the
  // chosen teammates. The server re-checks that every recipient is on this client.
  const deliver = async (report, recipientIds, sendId) => {
    const full = await getReport(clientId, report.id);
    if (!full.ok) return full;
    const built = await buildReportPdf({ clientId, report: full.data, clientName, branding, currency: workspaceCurrency });
    if (!built.ok) return built;
    if (built.storeError) return { ok: false, error: built.storeError };
    const sent = await sendReport(clientId, report.id, { recipientIds, sendId });
    if (sent.ok) setRefreshKey((value) => value + 1);
    return sent;
  };

  const liveRegion = notice && (
    <div key={notice.id} className={`rp-toast rp-toast--${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>{notice.text}</div>
  );

  if (reportParam) {
    return (
      <div className="page rp-page">
        {opening && !openReport && (
          <div className="rp-card" aria-busy="true" aria-live="polite">
            <div className="rp-skel-list"><div className="rp-skel-row" /><div className="rp-skel-row" /></div>
          </div>
        )}
        {openError && (
          <div className="rp-card rp-empty-state" role="alert">
            <div className="rp-empty-icon" aria-hidden="true"><i className="ti ti-file-off" /></div>
            <h2>Couldn't open this report</h2>
            <p>{openError}</p>
            <button type="button" className="primary-btn" onClick={closeReport}>Back to library</button>
          </div>
        )}
        {openReport && (
          <ReportBuilder
            key={openReport.id}
            report={openReport}
            mode={modeParam}
            onModeChange={(mode) => setReportParam(openReport.id, mode)}
            clientId={clientId}
            clientName={clientName}
            client={client}
            org={org}
            connectedSources={connectedSources}
            branding={branding}
            currency={workspaceCurrency}
            aiConnected={aiConnected}
            active={active}
            onExit={closeReport}
            onReportChanged={(updated) => setOpenReport((current) => (current?.id === updated.id ? { ...current, ...updated } : current))}
            onNotice={notify}
            onGoTo={onGoTo}
          />
        )}
        {liveRegion}
      </div>
    );
  }

  return (
    <div className="page rp-page">
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Reports</div>
          <h1 className="page-title">Client reports</h1>
          <div className="page-sub">Build, review and export client-ready reports from {clientName}'s connected data.</div>
        </div>
        <div className="page-actions">
          {canCreate && (
            <button type="button" className="primary-btn" onClick={() => { setPrefillSource(null); setCreateOpen(true); }}>
              <i className="ti ti-plus" aria-hidden="true" /> New report
            </button>
          )}
        </div>
      </div>

      <ReportsLibrary
        clientId={clientId}
        clientName={clientName}
        branding={branding}
        currency={workspaceCurrency}
        client={client}
        org={org}
        refreshKey={refreshKey}
        canCreate={canCreate}
        onOpen={openFromLibrary}
        onSend={setSendTarget}
        onCreate={() => { setPrefillSource(null); setCreateOpen(true); }}
        onNotice={notify}
        onGoTo={onGoTo}
      />

      {createOpen && (
        <ReportCreateDialog
          clientId={clientId}
          clientName={clientName}
          connectedSources={connectedSources}
          prefillSource={prefillSource}
          onClose={() => setCreateOpen(false)}
          onGoToIntegrations={() => { setCreateOpen(false); onGoTo("integrations"); }}
          onCreated={(report) => { setCreateOpen(false); setOpenReport(report); setReportParam(report.id, "edit"); notify("success", "Draft created"); }}
        />
      )}
      {sendTarget && (
        <SendToTeammatesModal
          clientId={clientId}
          clientName={clientName}
          report={sendTarget}
          onClose={() => setSendTarget(null)}
          onSend={(recipientIds, sendId) => deliver(sendTarget, recipientIds, sendId)}
        />
      )}
      {liveRegion}
    </div>
  );
}

export default function Reports(props) {
  const { client } = props;
  const [, setSearchParams] = useSearchParams();
  const previousClientId = useRef(client?.id);

  // Switching client: a report id from the old client must not linger in the URL.
  useEffect(() => {
    const previous = previousClientId.current;
    previousClientId.current = client?.id;
    if (previous && client?.id && previous !== client.id) {
      const next = new URLSearchParams(window.location.search);
      if (next.has("report")) { next.delete("report"); next.delete("mode"); setSearchParams(next, { replace: true }); }
    }
  }, [client?.id, setSearchParams]);

  if (!client) {
    return (
      <div className="page rp-page">
        <div className="page-head"><div><div className="page-eyebrow">Reports</div><h1 className="page-title">Client reports</h1></div></div>
        <Banner tone="info">Select a client workspace to see its reports.</Banner>
      </div>
    );
  }
  return (
    <ReportsErrorBoundary key={client.id}>
      <ReportsWorkspace key={client.id} {...props} />
    </ReportsErrorBoundary>
  );
}
