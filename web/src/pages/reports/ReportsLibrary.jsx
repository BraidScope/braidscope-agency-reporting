// SPDX-License-Identifier: AGPL-3.0-only
import { useCallback, useEffect, useRef, useState } from "react";
import { canPerformAction } from "../../lib/permissions";
import { changeReportStatus, deleteReport, downloadReportPdf, duplicateReport, getReport, listReports, saveBlob } from "./api";
import { buildReportPdf } from "./buildReportPdf";
import { Banner, Modal, RowMenu, Spinner, StatusBadge } from "./ReportControls";
import { formatPeriod, formatTimestamp, relativeTime, reportFilename, SOURCE_BY_ID, TEMPLATE_BY_ID } from "./reportModel";

const PAGE_SIZE = 20;
const STATUS_FILTERS = [
  { id: "active", label: "Active" },
  { id: "draft", label: "Drafts" },
  { id: "final", label: "Final" },
  { id: "archived", label: "Archived" },
  { id: "all", label: "All" },
];
const SORTS = [
  { id: "updated", label: "Recently updated" },
  { id: "oldest", label: "Oldest updated" },
  { id: "name", label: "Name" },
  { id: "period", label: "Period end" },
  { id: "status", label: "Status" },
];

function filenameFrom(response, fallback) {
  const match = /filename="?([^";]+)"?/i.exec(response?.headers?.get("content-disposition") || "");
  return match?.[1] || fallback;
}

export default function ReportsLibrary({ clientId, clientName, branding, currency, client, org, refreshKey, canCreate, onOpen, onSend, onCreate, onNotice, onGoTo }) {
  const [status, setStatus] = useState("active");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("updated");
  const [page, setPage] = useState(0);
  const [state, setState] = useState({ loading: true, error: "", code: "", reports: [], total: 0 });
  const [reload, setReload] = useState(0);
  const [busyId, setBusyId] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(null);
  const requestRef = useRef(0);

  useEffect(() => {
    const timer = window.setTimeout(() => { setQuery(search); setPage(0); }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const request = ++requestRef.current;
    setState((current) => ({ ...current, loading: true, error: "" }));
    listReports(clientId, { status, q: query, sort, limit: PAGE_SIZE, offset: page * PAGE_SIZE }).then((result) => {
      if (request !== requestRef.current) return; // a newer request (or another client) superseded this one
      if (!result.ok) { setState({ loading: false, error: result.error, code: result.code, reports: [], total: 0 }); return; }
      setState({ loading: false, error: "", code: "", reports: result.data.reports, total: result.data.total });
    });
  }, [clientId, status, query, sort, page, reload, refreshKey]);

  const refresh = useCallback(() => setReload((value) => value + 1), []);
  const allowed = (action) => canPerformAction(client, org, "Reports", action);

  const run = async (report, task, success) => {
    setBusyId(report.id);
    const result = await task();
    setBusyId("");
    if (!result.ok) { onNotice("error", result.error); refresh(); return null; }
    if (success) onNotice("success", success);
    refresh();
    return result;
  };

  // People who can edit always get a PDF built from the report as it is saved
  // NOW (so newly enabled sections, chart types and text are in it); the stored
  // copy is refreshed at the same time. Viewers, and archived reports, get the
  // last exported file.
  const canBuildFresh = (report) => allowed("edit") && report.status !== "archived";

  const download = async (report) => {
    setBusyId(report.id);
    if (canBuildFresh(report)) {
      const full = await getReport(clientId, report.id);
      if (!full.ok) { setBusyId(""); onNotice("error", full.error); return; }
      const built = await buildReportPdf({ clientId, report: full.data, clientName, branding, currency, silent: (report.pdf?.version || 0) > 0 });
      setBusyId("");
      if (!built.ok) { onNotice("error", built.error); return; }
      saveBlob(built.blob, built.filename);
      if (built.storeError) onNotice("error", `The PDF was downloaded, but couldn't be saved to the library. ${built.storeError}`);
      else refresh();
      return;
    }
    const result = await downloadReportPdf(clientId, report.id);
    setBusyId("");
    if (!result.ok) { onNotice("error", result.error); return; }
    saveBlob(result.blob, filenameFrom(result.response, reportFilename(clientName, report)));
  };

  const filtered = status !== "active" || query.trim() !== "";
  const { loading, error, reports, total } = state;
  const from = total ? page * PAGE_SIZE + 1 : 0;
  const to = Math.min(total, page * PAGE_SIZE + reports.length);

  const menuItems = (report) => [
    allowed("edit") && report.status !== "archived" && { key: "edit", label: "Edit", icon: "ti-pencil", onSelect: () => onOpen(report, "edit") },
    !(allowed("edit") && report.status !== "archived") && { key: "open", label: "Open", icon: "ti-eye", onSelect: () => onOpen(report, "view") },
    allowed("edit") && { key: "dup", label: "Duplicate", icon: "ti-copy", onSelect: () => run(report, () => duplicateReport(clientId, report.id), "Duplicate created as a draft").then((result) => result && onOpen(result.data, "edit")) },
    allowed("edit") && report.status === "draft" && { key: "final", label: "Mark as final", icon: "ti-circle-check", onSelect: () => run(report, () => changeReportStatus(clientId, report.id, "final"), "Marked as final") },
    allowed("edit") && report.status === "final" && { key: "reopen", label: "Reopen as draft", icon: "ti-rotate", onSelect: () => run(report, () => changeReportStatus(clientId, report.id, "draft"), "Reopened as a draft") },
    allowed("manage") && report.status !== "archived" && { key: "send", label: "Send to teammates", icon: "ti-send", separator: true, onSelect: () => onSend(report) },
    allowed("manage") && report.status !== "archived" && { key: "archive", label: "Archive", icon: "ti-archive", onSelect: () => run(report, () => changeReportStatus(clientId, report.id, "archived"), "Report archived") },
    allowed("manage") && report.status === "archived" && { key: "restore", label: "Restore as draft", icon: "ti-archive-off", onSelect: () => run(report, () => changeReportStatus(clientId, report.id, "draft"), "Report restored as a draft") },
    allowed("delete") && { key: "delete", label: "Delete…", icon: "ti-trash", danger: true, separator: true, onSelect: () => setConfirmDelete(report) },
  ];

  const doDelete = async () => {
    const report = confirmDelete;
    const result = await run(report, () => deleteReport(clientId, report.id), "Report deleted");
    if (result) setConfirmDelete(null);
  };

  if (loading && !reports.length && !error) {
    return (
      <div className="rp-card" aria-busy="true" aria-live="polite">
        <div className="rp-skel-list" aria-label="Loading reports">
          {[0, 1, 2, 3].map((item) => <div key={item} className="rp-skel-row" />)}
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="rp-toolbar">
        <div className="rp-search">
          <i className="ti ti-search" aria-hidden="true" />
          <input type="text" inputMode="search" autoComplete="off" spellCheck={false} placeholder="Search reports" aria-label="Search reports" value={search} onChange={(event) => setSearch(event.target.value)} />
          {search && (
            <button type="button" className="rp-clear" aria-label="Clear search" onClick={() => { setSearch(""); setQuery(""); setPage(0); }}>
              <i className="ti ti-x" aria-hidden="true" />
            </button>
          )}
        </div>
        <label className="rp-select">
          <span className="sr-only">Status</span>
          <i className="ti ti-filter" aria-hidden="true" />
          <select value={status} onChange={(event) => { setStatus(event.target.value); setPage(0); }} aria-label="Filter by status">
            {STATUS_FILTERS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </label>
        <label className="rp-select">
          <span className="sr-only">Sort</span>
          <i className="ti ti-arrows-sort" aria-hidden="true" />
          <select value={sort} onChange={(event) => { setSort(event.target.value); setPage(0); }} aria-label="Sort reports">
            {SORTS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </label>
        {loading && <span className="rp-inline-status" role="status"><Spinner /> Updating…</span>}
      </div>

      {error && (
        <Banner tone="error" actions={state.code === "session" ? null : <button type="button" className="ghost-btn" onClick={refresh}>Retry</button>}>
          {state.code === "demo" ? error : `Couldn't load reports. ${error}`}
        </Banner>
      )}

      {!error && !loading && reports.length === 0 && (
        <div className="rp-card rp-empty-state">
          <div className="rp-empty-icon" aria-hidden="true"><i className="ti ti-file-analytics" /></div>
          {filtered ? (
            <>
              <h2>No reports match</h2>
              <p>Try a different search or status filter.</p>
              <button type="button" className="ghost-btn" onClick={() => { setSearch(""); setQuery(""); setStatus("active"); }}>Clear filters</button>
            </>
          ) : (
            <>
              <h2>No reports yet</h2>
              <p>Reports turn {clientName}'s connected data into a branded, client-ready PDF, with AI-drafted commentary you review and edit before it goes out.</p>
              <div className="rp-empty-actions">
                {canCreate
                  ? <button type="button" className="primary-btn" onClick={() => onCreate()}><i className="ti ti-plus" aria-hidden="true" /> Create report</button>
                  : <span className="rp-muted-text">You have view-only access. An editor on this client can create reports.</span>}
                <button type="button" className="ghost-btn" onClick={() => onGoTo("dashboards")}><i className="ti ti-layout-dashboard" aria-hidden="true" /> Build from dashboard</button>
              </div>
            </>
          )}
        </div>
      )}

      {reports.length > 0 && (
        <div className="rp-card rp-list" data-busy={loading ? "true" : undefined}>
          <table className="rp-lib-table">
            <caption className="sr-only">Reports for {clientName}</caption>
            <thead>
              <tr>
                <th scope="col" className="rp-cell-name">Report</th>
                <th scope="col" className="rp-cell-period">Period</th>
                <th scope="col" className="rp-cell-updated">Updated</th>
                <th scope="col" className="num">Pages</th>
                <th scope="col">Status</th>
                <th scope="col" className="rp-cell-actions"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {reports.map((report) => {
                const sourceNames = (report.config?.sources || []).map((id) => SOURCE_BY_ID[id]?.label || id).join(", ");
                const busy = busyId === report.id;
                return (
                  <tr key={report.id} data-busy={busy ? "true" : undefined}>
                    <td data-label="Report" className="rp-cell-name">
                      <div className="rp-name-wrap">
                        <span className="rp-file-tile" aria-hidden="true"><i className="ti ti-file-analytics" /></span>
                        <div className="rp-name-text">
                          <button type="button" className="rp-name-btn" onClick={() => onOpen(report, "view")} title={report.name}>{report.name}</button>
                          <div className="rp-row-sub">
                            <span>{TEMPLATE_BY_ID[report.template]?.label || "Performance report"}</span>
                            <span className="rp-src-icons" aria-label={sourceNames}>
                              {(report.config?.sources || []).map((id) => (
                                <i key={id} className={`ti ${SOURCE_BY_ID[id]?.icon || "ti-plug"}`} title={SOURCE_BY_ID[id]?.label} style={{ color: SOURCE_BY_ID[id]?.color }} aria-hidden="true" />
                              ))}
                            </span>
                          </div>
                        </div>
                      </div>
                    </td>
                    <td data-label="Period" className="rp-cell-period">{formatPeriod(report.periodStart, report.periodEnd)}</td>
                    <td data-label="Updated" className="rp-cell-updated" title={formatTimestamp(report.updatedAt)}>{relativeTime(report.updatedAt)}</td>
                    <td data-label="Pages" className="num" title={report.pageCount ? undefined : "Counted when a PDF is exported"}>{report.pageCount ?? "—"}</td>
                    <td data-label="Status"><StatusBadge status={report.status} /></td>
                    <td className="rp-cell-actions">
                      <div className="rp-actions">
                        <button type="button" className="rp-icon-btn" onClick={() => onOpen(report, "view")} aria-label={`View ${report.name}`} title="View">
                          <i className="ti ti-eye" aria-hidden="true" />
                        </button>
                        <button
                          type="button"
                          className="rp-icon-btn"
                          onClick={() => download(report)}
                          disabled={busy || (!canBuildFresh(report) && !report.pdf?.available)}
                          aria-label={`Download PDF of ${report.name}`}
                          title={canBuildFresh(report) ? "Download PDF (built from the report as it is now)" : report.pdf?.available ? "Download the last exported PDF" : "No PDF has been exported yet."}
                        >
                          {busy ? <Spinner /> : <i className="ti ti-download" aria-hidden="true" />}
                        </button>
                        <RowMenu label={`More actions for ${report.name}`} items={menuItems(report)} />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="rp-pager">
            <span>Showing {from}–{to} of {total}</span>
            <div>
              <button type="button" className="ghost-btn" disabled={page === 0 || loading} onClick={() => setPage((value) => value - 1)}>Previous</button>
              <button type="button" className="ghost-btn" disabled={to >= total || loading} onClick={() => setPage((value) => value + 1)}>Next</button>
            </div>
          </div>
        </div>
      )}

      {confirmDelete && (
        <Modal
          icon="ti-trash"
          tone="danger"
          title="Delete this report?"
          description={`“${confirmDelete.name}” and its PDF will be permanently deleted. This can't be undone.`}
          onClose={() => setConfirmDelete(null)}
          busy={busyId === confirmDelete.id}
          footer={(
            <>
              <button type="button" className="ghost-btn" onClick={() => setConfirmDelete(null)} disabled={busyId === confirmDelete.id}>Cancel</button>
              <button type="button" className="primary-btn rp-danger-btn" onClick={doDelete} disabled={busyId === confirmDelete.id}>
                {busyId === confirmDelete.id ? <><Spinner /> Deleting…</> : "Delete report"}
              </button>
            </>
          )}
        />
      )}
    </>
  );
}
