// SPDX-License-Identifier: AGPL-3.0-only
// Client for the Reports API (py/app/reports.py). Every call is scoped to one
// client: /py/clients/{clientId}/reports/... - the backend re-checks the
// caller's role on that client, so nothing here is a security boundary.
//
// All functions resolve to { ok, ... } and never throw; error copy is written
// for end users (no raw backend text, no stack traces).
import { getSession } from "../../lib/auth";
import { isSupabaseConfigured } from "../../lib/supabase";

const base = (clientId) => `/py/clients/${encodeURIComponent(clientId)}/reports`;

export const isDemoClient = (clientId) => !clientId || String(clientId).startsWith("demo-") || !isSupabaseConfigured();

function friendly(status, detail) {
  const message = typeof detail === "string" ? detail : detail?.message;
  const code = typeof detail === "object" ? detail?.code : undefined;
  if (status === 401) return { code: "session", error: "Your session expired. Sign in again to continue." };
  if (status === 403) return { code: code || "forbidden", error: message || "You don't have permission to do that on this client." };
  if (status === 404) return { code: "not_found", error: message || "This report no longer exists. It may have been deleted." };
  if (status === 409) return { code: code || "conflict", error: message || "That can't be done in the report's current state." };
  if (status === 413) return { code: "too_large", error: message || "That is too large to save." };
  if (status === 422) return { code: "invalid", error: message || "Please check the details and try again." };
  if (status === 502 || status === 503) return { code: code || "unavailable", error: message || "Reports are temporarily unavailable. Try again in a moment." };
  return { code: code || "error", error: message || "Something went wrong. Try again." };
}

async function call(clientId, path, { method = "GET", json, body, headers = {}, timeout = 30000, blob = false } = {}) {
  if (isDemoClient(clientId)) {
    return { ok: false, code: "demo", error: "Reports need a signed-in workspace. Demo clients can't save reports." };
  }
  const session = await getSession();
  const token = session?.access_token;
  if (!token) return { ok: false, code: "session", error: "Your session expired. Sign in again to continue." };
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(`${base(clientId)}${path}`, {
      method,
      signal: controller.signal,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
        ...headers,
      },
      body: json !== undefined ? JSON.stringify(json) : body,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      return { ok: false, status: response.status, ...friendly(response.status, payload?.detail), currentUpdatedAt: payload?.detail?.currentUpdatedAt };
    }
    if (blob) return { ok: true, blob: await response.blob(), response };
    if (response.status === 204) return { ok: true };
    return { ok: true, data: await response.json() };
  } catch (error) {
    if (error?.name === "AbortError") return { ok: false, code: "timeout", error: "That took too long. Check your connection and try again." };
    return { ok: false, code: "network", error: "Couldn't reach the server. Check your connection and try again." };
  } finally {
    window.clearTimeout(timer);
  }
}

export function listReports(clientId, { status = "active", q = "", sort = "updated", limit = 20, offset = 0 } = {}) {
  const params = new URLSearchParams({ sort, limit: String(limit), offset: String(offset) });
  if (status && status !== "active") params.set("status", status);
  if (q.trim()) params.set("q", q.trim());
  return call(clientId, `?${params}`);
}

export const createReport = (clientId, payload) => call(clientId, "", { method: "POST", json: payload });
export const getReport = (clientId, reportId) => call(clientId, `/${encodeURIComponent(reportId)}`);
export const updateReport = (clientId, reportId, payload) => call(clientId, `/${encodeURIComponent(reportId)}`, { method: "PATCH", json: payload });
export const changeReportStatus = (clientId, reportId, status) => call(clientId, `/${encodeURIComponent(reportId)}/status`, { method: "POST", json: { status } });
export const duplicateReport = (clientId, reportId) => call(clientId, `/${encodeURIComponent(reportId)}/duplicate`, { method: "POST" });
export const deleteReport = (clientId, reportId) => call(clientId, `/${encodeURIComponent(reportId)}`, { method: "DELETE" });
export const generateNarrative = (clientId, reportId, section) =>
  call(clientId, `/${encodeURIComponent(reportId)}/narrative`, { method: "POST", json: { section }, timeout: 120000 });

// `silent` is for regenerating the PDF only so an email carries the current
// report: the server then skips the "report generated" notification.
export function uploadReportPdf(clientId, reportId, blob, pageCount, { silent = false } = {}) {
  return call(clientId, `/${encodeURIComponent(reportId)}/pdf`, {
    method: "PUT",
    body: blob,
    headers: { "Content-Type": "application/pdf", "X-Page-Count": String(pageCount || 1), ...(silent ? { "X-Silent": "1" } : {}) },
    timeout: 90000,
  });
}

// Emails the report's stored PDF to teammates of this client. The server
// re-validates every recipient; `sendId` makes a double click harmless.
export const sendReport = (clientId, reportId, { recipientIds, sendId }) =>
  call(clientId, `/${encodeURIComponent(reportId)}/send`, { method: "POST", json: { recipientIds, sendId }, timeout: 90000 });

export const downloadReportPdf = (clientId, reportId) => call(clientId, `/${encodeURIComponent(reportId)}/pdf`, { blob: true, timeout: 90000 });

export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10000);
}
