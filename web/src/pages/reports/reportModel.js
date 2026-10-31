// SPDX-License-Identifier: AGPL-3.0-only
// Shared, UI-agnostic model for Reports: catalogs, formatting, the adapter
// that turns the EXISTING dashboard payloads into a report data snapshot, and
// the chart geometry used by both the on-screen preview and the PDF so the two
// cannot drift apart.
//
// Nothing here calculates a metric. Cards come pre-formatted from the same
// dashboard endpoints the Dashboards page reads; the adapter only selects and
// formats what the dashboards already returned.

export const REPORT_SOURCES = [
  { id: "google_analytics_4", label: "Google Analytics", icon: "ti-chart-line", color: "#E8710A" },
  { id: "google_ads", label: "Google Ads", icon: "ti-brand-google", color: "#185FA5" },
  { id: "meta_ads", label: "Meta Ads", icon: "ti-brand-meta", color: "#0866FF" },
  { id: "hubspot_crm", label: "HubSpot", icon: "ti-address-book", color: "#FF7A59" },
  { id: "klaviyo", label: "Klaviyo", icon: "ti-mail", color: "#1D9E75" },
  { id: "shopify", label: "Shopify", icon: "ti-shopping-bag", color: "#5E8E3E" },
];
export const SOURCE_BY_ID = Object.fromEntries(REPORT_SOURCES.map((source) => [source.id, source]));
export const AI_SOURCE_TYPES = new Set(["openrouter_ai", "openai_ai", "anthropic_ai"]);

// Templates are no longer chosen when creating a report. This map only labels
// reports that were created with one before; new reports have no template.
export const TEMPLATE_BY_ID = {
  monthly: { id: "monthly", label: "Monthly Performance" },
  weekly: { id: "weekly", label: "Weekly Performance" },
  executive: { id: "executive", label: "Executive Summary" },
};

// Quick periods offered when creating/editing a report. (14 days is gone from
// the UI; reports that already use it keep working - see periodOptionsFor.)
export const PERIOD_OPTIONS = [
  { days: 7, label: "Last 7 days" },
  { days: 30, label: "Last 30 days" },
  { days: 90, label: "Last 90 days" },
];
export const LEGACY_PERIOD_OPTIONS = [{ days: 14, label: "Last 14 days" }];

// Custom-range rules mirror the dashboards (pages/Dashboards.jsx and the
// backend's resolve_date_window): inclusive dates, at most 365 days, not in the
// future, nothing before the earliest supported day.
export const MAX_CUSTOM_RANGE_DAYS = 365;
export const EARLIEST_CUSTOM_DATE = "2015-08-14";

// Rolling windows each dashboard backend really honours (see Dashboards.jsx).
export const ADS_PRESET_DAYS = [7, 14, 30, 90];
export const RANGE_PRESET_DAYS = [7, 14, 30, 90, 180, 365];

export const CHART_TYPES = [
  { id: "line", label: "Line", icon: "ti-chart-line" },
  { id: "area", label: "Area", icon: "ti-chart-area-line" },
  { id: "bar", label: "Bar", icon: "ti-chart-bar" },
  { id: "hbar", label: "Horizontal bar", icon: "ti-menu-2" },
];
export const DEFAULT_CHART_TYPE = "bar";

export const STATUS_META = {
  draft: { label: "Draft", tone: "draft" },
  final: { label: "Final", tone: "final" },
  sent: { label: "Sent", tone: "sent" },
  archived: { label: "Archived", tone: "archived" },
};

export const AI_SECTION_IDS = new Set(["summary", "recommendations"]);
export const isCommentary = (id) => id.startsWith("commentary:");
export const isAiSection = (id) => AI_SECTION_IDS.has(id) || isCommentary(id);

export const isChartSection = (id) => id === "chart" || id.startsWith("chart:");

// "chart:shopify:revenue" -> { sourceId: "shopify", key: "revenue" }
export function chartSectionParts(id) {
  const [, sourceId, key] = id.split(":");
  return { sourceId, key };
}
export const chartSectionId = (sourceId, key) => `chart:${sourceId}:${key}`;

export function sectionLabel(id) {
  if (id === "summary") return "Executive summary";
  if (id === "kpis") return "KPI summary";
  if (id === "chart") return "Performance charts";
  if (id === "tables") return "Top campaigns & products";
  if (id === "recommendations") return "Recommendations";
  if (isCommentary(id)) return `${SOURCE_BY_ID[id.split(":")[1]]?.label || "Source"} commentary`;
  if (id.startsWith("chart:")) {
    const { sourceId, key } = chartSectionParts(id);
    const metric = [...(SOURCE_SPECS[sourceId]?.trends || []), ...(BREAKDOWN_SPECS[sourceId] || [])].find((item) => item.key === key);
    return `${SOURCE_BY_ID[sourceId]?.label || "Source"} · ${metric?.label || key}`;
  }
  return id;
}

export function sectionIcon(id) {
  if (id === "summary") return "ti-align-left";
  if (id === "kpis") return "ti-square-number-1";
  if (id === "chart" || id.startsWith("chart:")) return "ti-chart-bar";
  if (id === "tables") return "ti-table";
  if (id === "recommendations") return "ti-bulb";
  if (isCommentary(id)) return SOURCE_BY_ID[id.split(":")[1]]?.icon || "ti-message";
  return "ti-square";
}

// Sources a client can put in a report: connected AND one the report supports.
export function connectedReportSources(sources = []) {
  const connected = new Set(sources.filter((source) => source.connected).map((source) => source.type));
  return REPORT_SOURCES.filter((source) => connected.has(source.id));
}

export function hasAiProvider(sources = []) {
  return sources.some((source) => AI_SOURCE_TYPES.has(source.type) && source.connected);
}

// ── Formatting ──────────────────────────────────────────────────────────────

const parseDay = (value) => (value ? new Date(`${String(value).slice(0, 10)}T00:00:00Z`) : null);

export function formatDay(value, options = { month: "short", day: "numeric" }) {
  const date = parseDay(value);
  if (!date || Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", { ...options, timeZone: "UTC" }).format(date);
}

export function formatPeriod(start, end) {
  if (!start || !end) return "No period set";
  const sameYear = String(start).slice(0, 4) === String(end).slice(0, 4);
  return `${formatDay(start, sameYear ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" })} – ${formatDay(end, { month: "short", day: "numeric", year: "numeric" })}`;
}

export function formatTimestamp(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
}

export function relativeTime(iso, now = Date.now()) {
  const time = new Date(iso).getTime();
  if (!Number.isFinite(time)) return "";
  const seconds = Math.max(0, Math.round((now - time) / 1000));
  if (seconds < 60) return "Just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return formatDay(iso, { month: "short", day: "numeric", year: "numeric" });
}

export function periodDaysToRange(days, today = new Date()) {
  const end = new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()));
  const start = new Date(end.getTime() - (days - 1) * 86400000);
  const iso = (date) => date.toISOString().slice(0, 10);
  return { start: iso(start), end: iso(end), days };
}

export function defaultReportName(clientName, period, today = new Date()) {
  const client = clientName || "Client";
  if (period?.mode === "custom") return `${client} — Performance ${formatPeriod(period.start, period.end)}`;
  if (period?.days === 7) return `${client} — Weekly performance`;
  if (period?.days === 90) return `${client} — Quarterly performance`;
  const month = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric" }).format(today);
  return `${client} — ${month} Performance`;
}

// ── Period helpers (shared by the dialog, builder, loader and PDF) ─────────

const pad = (value) => String(value).padStart(2, "0");
export const todayIso = (now = new Date()) => `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;

export function daysBetween(start, end) {
  return Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86400000);
}

// "" when the range is usable, otherwise the reason (same copy as Dashboards).
export function validateCustomRange(start, end, now = new Date()) {
  if (!start || !end) return "Pick a start and an end date.";
  if (start > end) return "The start date must be on or before the end date.";
  if (end > todayIso(now)) return "The end date can't be in the future.";
  if (start < EARLIEST_CUSTOM_DATE) return "That's before the earliest supported date.";
  if (daysBetween(start, end) + 1 > MAX_CUSTOM_RANGE_DAYS) return `Choose ${MAX_CUSTOM_RANGE_DAYS} days or fewer.`;
  return "";
}

// The period a report's config describes: { mode, days, start, end }.
export function periodFromConfig(config, now = new Date()) {
  if (config?.periodMode === "custom" && config.customStart && config.customEnd) {
    return { mode: "custom", start: config.customStart, end: config.customEnd, days: daysBetween(config.customStart, config.customEnd) + 1 };
  }
  const days = config?.periodDays || 30;
  return { mode: "preset", days, ...periodDaysToRange(days, now) };
}

export function periodKey(config) {
  return config?.periodMode === "custom" ? `custom:${config.customStart}:${config.customEnd}` : `preset:${config?.periodDays || 30}`;
}

// Quick options for the period picker; a report that still uses the retired
// 14-day preset keeps showing it so its selection isn't lost.
export function periodOptionsFor(config) {
  return config?.periodMode !== "custom" && config?.periodDays === 14
    ? [PERIOD_OPTIONS[0], LEGACY_PERIOD_OPTIONS[0], ...PERIOD_OPTIONS.slice(1)]
    : PERIOD_OPTIONS;
}

// Which dashboard window to request for a source. GA4 takes an exact custom
// range. Every other dashboard only honours rolling windows ending today, so for
// a custom range they get the smallest supported window that reaches back to the
// start date - and the report says so, rather than presenting other data as if
// it were the chosen range.
export function sourceWindow(sourceId, period, now = new Date()) {
  if (period.mode !== "custom") return { days: period.days, exact: true };
  if (sourceId === "google_analytics_4") return { days: period.days, exact: true, customStart: period.start, customEnd: period.end };
  const allowed = sourceId === "google_ads" || sourceId === "meta_ads" ? ADS_PRESET_DAYS : RANGE_PRESET_DAYS;
  const needed = daysBetween(period.start, todayIso(now)) + 1;
  const days = allowed.find((option) => option >= needed) || allowed[allowed.length - 1];
  const win = periodDaysToRange(days, now);
  return { days, exact: false, reaches: days >= needed, start: win.start, end: win.end };
}

export function reportFilename(clientName, report) {
  const slug = (value) => String(value || "").replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60) || "Report";
  const client = slug(clientName);
  const name = slug(report?.name);
  // Default report names already start with the client's name.
  const parts = name.toLowerCase().startsWith(client.toLowerCase()) ? [name] : [client, name];
  return `${[...parts, report?.periodEnd].filter(Boolean).join("_")}.pdf`;
}

const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const grouped = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

// Same compact style the dashboards' cards use ("$35.0k", "1.2k").
const lowerCompact = (text) => text.replace(/(\d)([KMBT])(?![A-Za-z])/g, (_, digit, unit) => `${digit}${unit === "K" ? "k" : unit}`);

function formatNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value ?? "—");
  return Math.abs(number) >= 1000 ? lowerCompact(compact.format(number)) : grouped.format(number);
}

function formatMoney(value, currency) {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value ?? "—");
  try {
    const big = Math.abs(number) >= 1000;
    const text = new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: String(currency || "usd").toUpperCase(),
      notation: big ? "compact" : "standard",
      maximumFractionDigits: big ? 1 : 2,
    }).format(number);
    return lowerCompact(text);
  } catch {
    return formatNumber(number);
  }
}

function formatCell(value, kind, currency) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string" && Number.isNaN(Number(value))) return value;
  if (kind === "money") return formatMoney(value, currency);
  if (kind === "ratio") return `${Number(value).toFixed(2)}x`;
  if (kind === "num") return formatNumber(value);
  return String(value);
}

// ── Dashboard payload -> report snapshot ────────────────────────────────────

const SOURCE_SPECS = {
  google_analytics_4: {
    trends: [
      { key: "sessions", label: "Sessions" },
      { key: "activeUsers", label: "Active users" },
      { key: "revenue", label: "Revenue", fmt: "money" },
    ],
    table: { list: "channels", title: "Channel", columns: [
      { key: "channel", label: "Channel" }, { key: "sessions", label: "Sessions", fmt: "num" },
      { key: "keyEvents", label: "Key events", fmt: "num" }, { key: "revenue", label: "Revenue", fmt: "money" },
    ] },
  },
  google_ads: {
    trends: [
      { key: "clicks", label: "Clicks" },
      { key: "cost", label: "Cost", fmt: "money" },
      { key: "conversions", label: "Conversions" },
      { key: "impressions", label: "Impressions" },
    ],
    table: { list: "campaigns", title: "Campaign", columns: [
      { key: "name", label: "Campaign" }, { key: "clicks", label: "Clicks", fmt: "num" },
      { key: "cost", label: "Cost", fmt: "money" }, { key: "conversions", label: "Conv.", fmt: "num" },
    ] },
  },
  meta_ads: {
    trends: [
      { key: "spend", label: "Spend", fmt: "money" },
      { key: "clicks", label: "Clicks" },
      { key: "impressions", label: "Impressions" },
    ],
    table: { list: "campaigns", title: "Campaign", columns: [
      { key: "name", label: "Campaign" }, { key: "spend", label: "Spend", fmt: "money" },
      { key: "reach", label: "Reach", fmt: "num" }, { key: "roas", label: "ROAS", fmt: "ratio" },
    ] },
  },
  hubspot_crm: {
    trends: [{ key: "contacts", label: "New contacts" }],
    table: { list: "deals", title: "Deal", columns: [
      { key: "name", label: "Deal" }, { key: "stage", label: "Stage" }, { key: "amount", label: "Amount", fmt: "money" },
    ] },
  },
  klaviyo: {
    trends: [],
    table: { list: "campaigns", title: "Campaign", columns: [{ key: "name", label: "Campaign" }, { key: "status", label: "Status" }] },
  },
  shopify: {
    trends: [
      { key: "revenue", label: "Revenue", fmt: "money" },
      { key: "orders", label: "Orders" },
      { key: "customers", label: "Customers" },
    ],
    table: { list: "top_products", title: "Product", columns: [
      { key: "product", label: "Product" }, { key: "quantity", label: "Units", fmt: "num" }, { key: "revenue", label: "Revenue", fmt: "money" },
    ] },
  },
};

// Breakdown charts: the same arrays the dashboards chart by campaign, platform,
// device, channel, pipeline stage, status... Each becomes one report chart
// section. `group: true` counts the returned rows per label (what the Klaviyo
// dashboard does for status / trigger breakdowns); otherwise the value comes
// straight from `valueKey`. Nothing is recalculated beyond that grouping.
const BREAKDOWN_SPECS = {
  google_analytics_4: [
    { key: "channels_sessions", label: "Sessions by channel", list: "channels", labelKey: "channel", valueKey: "sessions" },
    { key: "devices_sessions", label: "Sessions by device", list: "devices", labelKey: "device", valueKey: "sessions" },
    { key: "pages_views", label: "Top pages by views", list: "pages", labelKey: "title", valueKey: "views" },
    { key: "countries_sessions", label: "Sessions by country", list: "countries", labelKey: "country", valueKey: "sessions" },
    { key: "events_count", label: "Top events", list: "events", labelKey: "event", valueKey: "count" },
  ],
  google_ads: [
    { key: "campaigns_clicks", label: "Clicks by campaign", list: "campaigns", labelKey: "name", valueKey: "clicks" },
    { key: "campaigns_cost", label: "Cost by campaign", list: "campaigns", labelKey: "name", valueKey: "cost", fmt: "money" },
    { key: "campaigns_conversions", label: "Conversions by campaign", list: "campaigns", labelKey: "name", valueKey: "conversions" },
    { key: "devices_clicks", label: "Clicks by device", list: "devices", labelKey: "device", valueKey: "clicks" },
    { key: "funnel", label: "Performance funnel", list: "funnel", labelKey: "stage", valueKey: "value", keepOrder: true },
    { key: "conversion_actions", label: "Conversion actions", list: "conversion_actions", labelKey: "name", valueKey: "conversions" },
    { key: "keywords_clicks", label: "Top keywords by clicks", list: "keywords", labelKey: "keyword", valueKey: "clicks" },
  ],
  meta_ads: [
    { key: "campaigns_spend", label: "Spend by campaign", list: "campaigns", labelKey: "name", valueKey: "spend", fmt: "money" },
    { key: "platforms_spend", label: "Spend by platform", list: "platforms", labelKey: "platform", valueKey: "spend", fmt: "money" },
    { key: "campaigns_clicks", label: "Clicks by campaign", list: "campaigns", labelKey: "name", valueKey: "clicks" },
    { key: "platforms_clicks", label: "Clicks by platform", list: "platforms", labelKey: "platform", valueKey: "clicks" },
    { key: "campaigns_reach", label: "Reach by campaign", list: "campaigns", labelKey: "name", valueKey: "reach" },
  ],
  hubspot_crm: [
    { key: "stages_value", label: "Pipeline value by stage", list: "stages", labelKey: "stage", valueKey: "value", fmt: "money" },
    { key: "stages_count", label: "Deals by stage", list: "stages", labelKey: "stage", valueKey: "count" },
    { key: "deals_amount", label: "Largest deals", list: "deals", labelKey: "name", valueKey: "amount", fmt: "money" },
  ],
  klaviyo: [
    { key: "campaigns_status", label: "Campaigns by status", list: "campaigns", labelKey: "status", group: true },
    { key: "flows_status", label: "Flows by status", list: "flows", labelKey: "status", group: true },
    { key: "flows_trigger", label: "Flows by trigger type", list: "flows", labelKey: "trigger_type", group: true },
    { key: "campaigns_channel", label: "Campaigns by channel", list: "campaigns", labelKey: "channel", group: true },
  ],
  shopify: [
    { key: "products_revenue", label: "Revenue by product", list: "top_products", labelKey: "product", valueKey: "revenue", fmt: "money" },
    { key: "products_units", label: "Units sold by product", list: "top_products", labelKey: "product", valueKey: "quantity" },
  ],
};
const MAX_BREAKDOWN_ROWS = 10;

function buildBreakdowns(sourceId, data) {
  return (BREAKDOWN_SPECS[sourceId] || []).map((spec) => {
    const list = Array.isArray(data[spec.list]) ? data[spec.list] : [];
    let points;
    if (spec.group) {
      const counts = new Map();
      for (const row of list) {
        const label = String(row?.[spec.labelKey] ?? "").trim() || "Unknown";
        counts.set(label, (counts.get(label) || 0) + 1);
      }
      points = [...counts].map(([label, value]) => ({ label, value }));
    } else {
      points = list
        .map((row) => ({ label: String(row?.[spec.labelKey] ?? "").trim() || "(unnamed)", value: Number(row?.[spec.valueKey]) }))
        .filter((point) => Number.isFinite(point.value));
    }
    if (!spec.keepOrder) points.sort((a, b) => b.value - a.value);
    return { key: spec.key, label: spec.label, kind: "category", fmt: spec.fmt || "num", points: points.slice(0, MAX_BREAKDOWN_ROWS) };
  // A breakdown that is zero everywhere would only draw empty bars.
  }).filter((series) => series.points.length && series.points.some((point) => point.value > 0));
}

// Bump when the snapshot gains new kinds of data (v2: breakdown charts), so reports
// saved earlier are flagged for a data update instead of silently lacking them.
export const DATA_VERSION = 2;

export const MAX_TABLE_ROWS = 8;
const MAX_CARDS = 8;
// Charts switched on by default per source; the rest are one toggle away.
const CHARTS_ON_BY_DEFAULT = 2;

// `window` is the dashboard window that was requested for this source (see
// sourceWindow); `clip` trims trend points to the report's own date range when
// that window had to be wider than the range.
export function buildSourceSnapshot(sourceId, result, { days, currency = "usd", now = new Date(), window: win = null, clip = null } = {}) {
  const label = SOURCE_BY_ID[sourceId]?.label || sourceId;
  if (!result?.ok || !result.data) {
    return { ok: false, label, error: result?.error || `${label} could not be loaded` };
  }
  const data = result.data;
  const spec = SOURCE_SPECS[sourceId] || {};
  let range;
  if (data.date_range?.start && data.date_range?.end) range = { start: data.date_range.start, end: data.date_range.end, days };
  else if (win && !win.exact && win.start) range = { start: win.start, end: win.end, days: win.days };
  else range = periodDaysToRange(days, now);
  const cards = (Array.isArray(data.cards) ? data.cards : []).slice(0, MAX_CARDS)
    .map((card) => ({ label: String(card.label ?? ""), value: String(card.value ?? "—"), detail: card.detail ? String(card.detail) : "" }));

  const rows = Array.isArray(data.trend) ? data.trend : [];
  const trends = (spec.trends || []).map((metric) => {
    const points = rows
      .map((row) => ({ date: String(row.date || "").slice(0, 10), value: Number(row[metric.key]) }))
      .filter((point) => point.date && Number.isFinite(point.value))
      .filter((point) => !clip || (point.date >= clip.start && point.date <= clip.end));
    return { key: metric.key, label: metric.label, fmt: metric.fmt || "num", points };
    // A metric that is zero all period would only draw an empty chart.
  }).filter((series) => series.points.length && series.points.some((point) => point.value !== 0));

  let table = null;
  const list = spec.table && Array.isArray(data[spec.table.list]) ? data[spec.table.list] : [];
  if (list.length) {
    table = {
      title: spec.table.title,
      columns: spec.table.columns.map(({ key, label: columnLabel }) => ({ key, label: columnLabel })),
      rows: list.slice(0, MAX_TABLE_ROWS).map((row) => Object.fromEntries(
        spec.table.columns.map((column) => [column.key, formatCell(row[column.key], column.fmt, currency)]),
      )),
    };
  }

  const breakdowns = buildBreakdowns(sourceId, data);

  let windowNote = "";
  if (win && !win.exact) {
    windowNote = win.reaches
      ? `Totals cover ${formatPeriod(win.start, win.end)} (a ${win.days}-day rolling window) because ${label} can't report a custom range.`
      : `${label} can only report its last ${win.days} days, so earlier days in your range aren't included.`;
  }

  const empty = data.has_data === false || (!cards.length && !trends.length && !breakdowns.length && !table);
  return { ok: true, label, empty, emptyReason: empty ? String(data.empty_reason || "") : "", dateRange: range, windowNote, cards, trends, breakdowns, table };
}

export function snapshotFromResults(resultsBySource, { days, currency, now, periodKey: key } = {}) {
  const sources = {};
  for (const [sourceId, result] of Object.entries(resultsBySource)) {
    sources[sourceId] = buildSourceSnapshot(sourceId, result, { days, currency, now });
  }
  return { days, periodKey: key, sources };
}

// The trend series a source's snapshot holds (older snapshots stored a single
// unnamed trend).
export function seriesOf(sourceData) {
  if (!sourceData) return [];
  const breakdowns = Array.isArray(sourceData.breakdowns) ? sourceData.breakdowns : [];
  if (Array.isArray(sourceData.trends)) return [...sourceData.trends, ...breakdowns];
  if (sourceData.trend?.points?.length) return [{ key: "trend", ...sourceData.trend }, ...breakdowns];
  return breakdowns;
}

// The period a snapshot really covers (for the cover + column headers): the
// intersection-free union of what the sources reported, falling back to days.
export function snapshotPeriod(snapshot, fallback) {
  const ranges = Object.values(snapshot?.sources || {}).map((source) => source.dateRange).filter(Boolean);
  if (!ranges.length) return fallback;
  const start = ranges.map((range) => range.start).sort()[0];
  const end = ranges.map((range) => range.end).sort().slice(-1)[0];
  return { start, end };
}

// ── Chart geometry shared by the preview (SVG) and the PDF ─────────────────

export function bucketPoints(points, maxBuckets = 30) {
  if (points.length <= maxBuckets) return points.map((point) => ({ label: point.date, value: point.value }));
  const size = Math.ceil(points.length / maxBuckets);
  const buckets = [];
  for (let index = 0; index < points.length; index += size) {
    const slice = points.slice(index, index + size);
    buckets.push({ label: slice[0].date, value: slice.reduce((sum, point) => sum + point.value, 0) });
  }
  return buckets;
}

export function chartBars(points, { width, height, gap = 3, maxBuckets = 30 }) {
  const buckets = bucketPoints(points, maxBuckets);
  const max = Math.max(...buckets.map((bucket) => bucket.value), 0);
  const barWidth = buckets.length ? Math.max(1, (width - gap * (buckets.length - 1)) / buckets.length) : 0;
  return {
    max,
    first: buckets[0]?.label,
    last: buckets[buckets.length - 1]?.label,
    bars: buckets.map((bucket, index) => {
      const barHeight = max > 0 ? Math.max(bucket.value > 0 ? 1 : 0, (bucket.value / max) * height) : 0;
      return { x: index * (barWidth + gap), y: height - barHeight, width: barWidth, height: barHeight, label: bucket.label, value: bucket.value };
    }),
  };
}

export function trendTotal(trend) {
  return trend.points.reduce((sum, point) => sum + point.value, 0);
}

export function formatTrendTotal(trend, currency = "usd") {
  return formatCell(trendTotal(trend), trend.fmt, currency);
}

export const formatMetric = (series, value, currency = "usd") => formatCell(value, series.fmt, currency);

// Line/area need a line to draw; bars work for any series with data.
export function availableChartTypes(series) {
  // Breakdowns (category series) support every type too; line/area still need 2+ points.
  const count = series?.points?.length || 0;
  return CHART_TYPES.filter((type) => (type.id === "line" || type.id === "area" ? count >= 2 : count >= 1));
}

// The chart type a section is saved with, falling back when the data can no
// longer support it (for example a line chart of a single day).
export function chartTypeFor(config, sectionId, series) {
  // Breakdowns start as horizontal bars (readable labels); every type is one click away.
  const fallback = series?.kind === "category" ? "hbar" : DEFAULT_CHART_TYPE;
  const wanted = config?.chartTypes?.[sectionId] || fallback;
  return availableChartTypes(series).some((type) => type.id === wanted) ? wanted : fallback;
}

// Line and area share one geometry: same buckets, same scale.
export function chartLine(points, { width, height, maxBuckets = 30 }) {
  const buckets = bucketPoints(points, maxBuckets);
  const max = Math.max(...buckets.map((bucket) => bucket.value), 0);
  const step = buckets.length > 1 ? width / (buckets.length - 1) : 0;
  const coords = buckets.map((bucket, index) => ({
    x: index * step,
    y: max > 0 ? height - (bucket.value / max) * height : height,
    value: bucket.value,
    label: bucket.label,
  }));
  const line = coords.map((coord, index) => `${index ? "L" : "M"}${coord.x.toFixed(2)} ${coord.y.toFixed(2)}`).join(" ");
  const last = coords[coords.length - 1];
  const area = coords.length ? `${line} L${last.x.toFixed(2)} ${height} L${coords[0].x.toFixed(2)} ${height} Z` : "";
  return { max, first: buckets[0]?.label, last: buckets[buckets.length - 1]?.label, coords, line, area };
}

// Horizontal bars: at most `maxRows` rows so every row stays labelled and readable.
export function chartRows(points, { maxRows = 10 } = {}) {
  const buckets = bucketPoints(points, maxRows);
  const max = Math.max(...buckets.map((bucket) => bucket.value), 0);
  return { max, rows: buckets.map((bucket) => ({ label: bucket.label, value: bucket.value, pct: max > 0 ? bucket.value / max : 0 })) };
}

// Category series (a breakdown, not a time series): one row per label, in the
// order given, scaled to the largest value.
export function categoryRows(points) {
  const max = Math.max(...points.map((point) => point.value), 0);
  return { max, rows: points.map((point) => ({ label: point.label, value: point.value, pct: max > 0 ? point.value / max : 0 })) };
}

// Vertical drawings of a breakdown (bar / line / area): one slot per category,
// each point at the centre of its slot so the labels underneath line up.
export function categoryColumns(points, { width, height }) {
  const max = Math.max(...points.map((point) => point.value), 0);
  const slot = points.length ? width / points.length : 0;
  const barWidth = Math.min(slot * 0.62, 72);
  const yOf = (value) => (max > 0 ? height - (value / max) * height : height);
  const coords = points.map((point, index) => ({ x: (index + 0.5) * slot, y: yOf(point.value), value: point.value, label: point.label }));
  const bars = points.map((point, index) => {
    const barHeight = max > 0 ? Math.max(point.value > 0 ? 1 : 0, (point.value / max) * height) : 0;
    return { x: index * slot + (slot - barWidth) / 2, y: height - barHeight, width: barWidth, height: barHeight, label: point.label, value: point.value };
  });
  const line = coords.map((coord, index) => `${index ? "L" : "M"}${coord.x.toFixed(2)} ${coord.y.toFixed(2)}`).join(" ");
  const last = coords[coords.length - 1];
  const area = coords.length ? `${line} L${last.x.toFixed(2)} ${height} L${coords[0].x.toFixed(2)} ${height} Z` : "";
  return { max, slot, bars, coords, line, area };
}

// What sits next to a chart's title: the period total for a time series, the
// number of bars for a breakdown.
export function seriesSummary(series, currency = "usd") {
  if (series?.kind === "category") return `${series.points.length} ${series.points.length === 1 ? "item" : "items"}`;
  return formatTrendTotal(series, currency);
}

// What a report needs from the loaded snapshot, in one place.
export function connectedSnapshotSources(config, snapshot) {
  return (config?.sources || []).map((id) => ({ id, meta: SOURCE_BY_ID[id], data: snapshot?.sources?.[id] || null }));
}

// Is the loaded data still what the current settings describe?
export function snapshotIsStale(config, snapshot) {
  if (!snapshot?.sources) return false;
  if ((snapshot.v || 1) < DATA_VERSION) return true;
  const loaded = Object.keys(snapshot.sources).sort().join(",");
  const wanted = [...(config?.sources || [])].sort().join(",");
  if (loaded !== wanted) return true;
  if (snapshot.periodKey) return snapshot.periodKey !== periodKey(config);
  return config?.periodMode === "custom" || snapshot.days !== config?.periodDays;
}

export function draftKey(draft) {
  return JSON.stringify({
    name: draft.name,
    config: draft.config,
    narratives: draft.narratives,
    snapshot: draft.snapshot,
    snapshotAt: draft.snapshotAt,
    periodStart: draft.periodStart,
    periodEnd: draft.periodEnd,
  });
}

// Keeps the chart sections in step with the data that is actually loaded: one
// section per metric a source really has, the first couple switched on. The
// legacy single "chart" section is expanded into those (keeping its on/off
// state), chart sections for sources that are gone are dropped, and a metric
// that the loaded data no longer has is dropped. Sections for a source that
// failed to load are left alone so a retry finds them.
export function normalizeConfig(config, snapshot) {
  const sources = config?.sources || [];
  let sections = (config?.sections || []).filter(
    (section) => !section.id.startsWith("chart:") || sources.includes(chartSectionParts(section.id).sourceId),
  );
  if (snapshot?.sources) {
    const available = [];
    for (const sourceId of sources) {
      const data = snapshot.sources[sourceId];
      if (!data?.ok) continue;
      seriesOf(data).forEach((series, index) => available.push({ id: chartSectionId(sourceId, series.key), first: index < CHARTS_ON_BY_DEFAULT }));
    }
    const availableIds = new Set(available.map((item) => item.id));
    sections = sections.filter((section) => {
      if (!section.id.startsWith("chart:")) return true;
      const data = snapshot.sources[chartSectionParts(section.id).sourceId];
      return !data || data.ok === false || availableIds.has(section.id);
    });
    const have = new Set(sections.map((section) => section.id));
    const legacy = sections.find((section) => section.id === "chart");
    const added = available
      .filter((item) => !have.has(item.id))
      .map((item) => ({ id: item.id, enabled: legacy ? legacy.enabled && item.first : item.first }));
    if (legacy) {
      const at = sections.indexOf(legacy);
      sections = [...sections.slice(0, at), ...added, ...sections.slice(at + 1)];
    } else if (added.length) {
      let at = -1;
      sections.forEach((section, index) => { if (section.id.startsWith("chart:")) at = index; });
      if (at === -1) at = sections.findIndex((section) => section.id === "kpis");
      sections = [...sections.slice(0, at + 1), ...added, ...sections.slice(at + 1)];
    }
  }
  return { ...config, sections, chartTypes: config?.chartTypes || {} };
}

export function draftFromReport(report) {
  const content = report.content || {};
  const snapshot = content.snapshot || null;
  const stored = report.config || {};
  const custom = stored.periodMode === "custom" && stored.customStart && stored.customEnd;
  const base = {
    sources: stored.sources || [],
    sections: stored.sections || [],
    periodDays: stored.periodDays || 30,
    periodMode: custom ? "custom" : "preset",
    ...(custom ? { customStart: stored.customStart, customEnd: stored.customEnd } : {}),
    chartTypes: stored.chartTypes || {},
  };
  return {
    name: report.name || "",
    config: normalizeConfig(base, snapshot),
    narratives: content.narratives || {},
    snapshot,
    snapshotAt: content.snapshotAt || null,
    periodStart: report.periodStart || null,
    periodEnd: report.periodEnd || null,
  };
}

// Keeps section order/enabled state when sources change: commentary and chart
// sections for a removed source disappear, commentary for an added source is
// appended on (its charts arrive with the data load).
export function reconcileSections(sections, sources) {
  const kept = sections.filter((section) => {
    if (isCommentary(section.id)) return sources.includes(section.id.split(":")[1]);
    if (section.id.startsWith("chart:")) return sources.includes(chartSectionParts(section.id).sourceId);
    return true;
  });
  const have = new Set(kept.map((section) => section.id));
  const added = sources.filter((id) => !have.has(`commentary:${id}`)).map((id) => ({ id: `commentary:${id}`, enabled: true }));
  const recommendationsAt = kept.findIndex((section) => section.id === "recommendations");
  if (!added.length) return kept;
  if (recommendationsAt === -1) return [...kept, ...added];
  return [...kept.slice(0, recommendationsAt), ...added, ...kept.slice(recommendationsAt)];
}
