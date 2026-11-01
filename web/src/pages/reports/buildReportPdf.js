// SPDX-License-Identifier: AGPL-3.0-only
// Builds a report's PDF from the report exactly as it is SAVED right now - the
// same definition (enabled sections, their order, chart types, data snapshot,
// text, branding) the on-screen preview renders. Used by every download and by
// "Send to teammates", so a PDF can never lag behind the report.
//
// The stored copy is refreshed at the same time (so the file kept on the report
// and the one you just downloaded are identical). `silent` skips the "report
// generated" notification: only a report's first PDF is news.
import { uploadReportPdf } from "./api";
import { isChunkLoadError, NEW_VERSION_MESSAGE, reloadForNewVersion } from "../../lib/chunkReload";
import { draftFromReport, reportFilename } from "./reportModel";

export async function buildReportPdf({ clientId, report, clientName, branding, currency, silent = true, store = true }) {
  const draft = draftFromReport(report);
  if (!draft.snapshot) {
    return { ok: false, error: "This report has no data yet. Open it, load the data and save it first." };
  }
  let rendered;
  try {
    const { renderReportPdf } = await import("./pdf/renderReportPdf");
    rendered = await renderReportPdf({
      branding: { name: branding?.name, logo: branding?.logo, accent: branding?.accent },
      clientName,
      name: draft.name,
      config: draft.config,
      narratives: draft.narratives,
      snapshot: draft.snapshot,
      period: { start: draft.periodStart, end: draft.periodEnd },
      generatedAt: draft.snapshotAt,
      currency,
    });
  } catch (error) {
    console.error("[Reports] PDF render failed", error);
    if (isChunkLoadError(error)) {
      reloadForNewVersion();
      return { ok: false, error: NEW_VERSION_MESSAGE };
    }
    return { ok: false, error: `Couldn't create the PDF (${error?.message || "unknown error"}). Try again, and if it keeps failing check that your logo and text are valid.` };
  }
  const filename = reportFilename(clientName, { name: draft.name, periodEnd: draft.periodEnd });
  if (!store) return { ok: true, blob: rendered.blob, pages: rendered.pages, filename, stored: null, storeError: "" };
  const upload = await uploadReportPdf(clientId, report.id, rendered.blob, rendered.pages, { silent });
  return {
    ok: true,
    blob: rendered.blob,
    pages: rendered.pages,
    filename,
    stored: upload.ok ? upload.data : null,
    storeError: upload.ok ? "" : upload.error,
  };
}
