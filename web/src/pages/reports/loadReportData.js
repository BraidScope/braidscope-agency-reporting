// SPDX-License-Identifier: AGPL-3.0-only
// Loads the numbers a report needs from the EXISTING dashboard endpoints
// (lib/platformDashboards.js) - the exact same calls, arguments and server-side
// cache the Dashboards page uses, so a report always agrees with the dashboard.
// Reports never recalculate a metric.
//
// Sources are fetched in parallel and independently: one slow or failing source
// never blocks the others, it just becomes an error entry the report can show
// and retry.
//
// Custom date ranges follow the dashboards' own rule: only GA4 can report an
// exact custom range. Every other dashboard only honours rolling windows ending
// today, so it is asked for the smallest supported window that reaches back to
// the range start, its trends are clipped to the range, and the snapshot records
// a note saying the totals cover that wider window (see sourceWindow).
import {
  getGA4Dashboard,
  getGoogleAdsDashboard,
  getHubSpotDashboard,
  getKlaviyoDashboard,
  getMetaAdsDashboard,
  getShopifyDashboard,
} from "../../lib/platformDashboards";
import { buildSourceSnapshot, DATA_VERSION, periodKey, sourceWindow } from "./reportModel";

const FETCHERS = {
  google_analytics_4: getGA4Dashboard,
  google_ads: getGoogleAdsDashboard,
  meta_ads: getMetaAdsDashboard,
  hubspot_crm: getHubSpotDashboard,
  klaviyo: getKlaviyoDashboard,
  shopify: getShopifyDashboard,
};

async function loadOne(sourceId, { clientId, clientName, period, currency, forceRefresh }) {
  const win = sourceWindow(sourceId, period);
  try {
    const result = await FETCHERS[sourceId]({
      clientId,
      clientName,
      days: win.days,
      forceRefresh,
      ...(win.customStart ? { customStart: win.customStart, customEnd: win.customEnd } : {}),
    });
    return buildSourceSnapshot(sourceId, result, {
      days: win.days,
      currency,
      window: win,
      clip: period.mode === "custom" ? { start: period.start, end: period.end } : null,
    });
  } catch (error) {
    return buildSourceSnapshot(sourceId, { ok: false, error: error?.message }, { days: win.days, currency });
  }
}

// `period` is periodFromConfig(config). `only` re-loads just those sources and
// keeps everything else from `previous`.
export async function loadReportData({ clientId, clientName, sources, period, config, currency, forceRefresh = false, only = null, previous = null }) {
  // Data saved by an older version lacks newer kinds of data, so it is reloaded in full.
  const base = previous && (previous.v || 1) >= DATA_VERSION ? previous : null;
  const targets = only && base ? sources.filter((id) => only.includes(id)) : sources;
  const entries = await Promise.all(
    targets.map(async (id) => [id, await loadOne(id, { clientId, clientName, period, currency, forceRefresh })]),
  );
  const kept = Object.fromEntries(Object.entries(base?.sources || {}).filter(([id]) => sources.includes(id) && !targets.includes(id)));
  return {
    snapshot: { v: DATA_VERSION, days: period.days, periodKey: periodKey(config), sources: { ...kept, ...Object.fromEntries(entries) } },
    snapshotAt: new Date().toISOString(),
  };
}
