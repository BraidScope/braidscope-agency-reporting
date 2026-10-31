// SPDX-License-Identifier: AGPL-3.0-only
import assert from "node:assert/strict";
import test from "node:test";
import {
  ADS_PRESET_DAYS, availableChartTypes, buildSourceSnapshot, categoryColumns, categoryRows, seriesSummary, chartLine, chartRows, chartTypeFor, draftFromReport, formatTrendTotal,
  normalizeConfig, PERIOD_OPTIONS, periodFromConfig, periodKey, periodOptionsFor, reconcileSections, seriesOf, snapshotIsStale,
  sourceWindow, validateCustomRange,
} from "../src/pages/reports/reportModel.js";

const NOW = new Date(2026, 8, 28, 12, 0, 0); // 28 Sep 2026, local
const trendRows = (key, values, startDay = 1) => values.map((value, index) => ({ date: `2026-09-${String(startDay + index).padStart(2, "0")}`, [key]: value }));

test("period presets are 7 / 30 / 90 days (14 is gone) and a legacy 14-day report keeps its option", () => {
  assert.deepEqual(PERIOD_OPTIONS.map((option) => option.days), [7, 30, 90]);
  assert.deepEqual(periodOptionsFor({ periodMode: "preset", periodDays: 30 }).map((o) => o.days), [7, 30, 90]);
  assert.deepEqual(periodOptionsFor({ periodMode: "preset", periodDays: 14 }).map((o) => o.days), [7, 14, 30, 90]);
});

test("custom range validation follows the dashboard rules", () => {
  assert.equal(validateCustomRange("2026-09-01", "2026-09-20", NOW), "");
  assert.match(validateCustomRange("", "2026-09-20", NOW), /start and an end/);
  assert.match(validateCustomRange("2026-09-20", "2026-09-01", NOW), /on or before/);
  assert.match(validateCustomRange("2026-09-01", "2026-10-15", NOW), /future/);
  assert.match(validateCustomRange("2001-01-01", "2001-02-01", NOW), /earliest/);
  assert.match(validateCustomRange("2025-01-01", "2026-09-20", NOW), /365 days or fewer/);
  assert.equal(validateCustomRange("2026-09-28", "2026-09-28", NOW), ""); // a single day is fine
});

test("periodFromConfig: presets are rolling windows ending today, custom keeps its dates", () => {
  const preset = periodFromConfig({ periodMode: "preset", periodDays: 30 }, NOW);
  assert.deepEqual([preset.start, preset.end, preset.days], ["2026-08-30", "2026-09-28", 30]);
  const custom = periodFromConfig({ periodMode: "custom", customStart: "2026-09-01", customEnd: "2026-09-20" }, NOW);
  assert.deepEqual([custom.mode, custom.start, custom.end, custom.days], ["custom", "2026-09-01", "2026-09-20", 20]);
  assert.notEqual(periodKey({ periodMode: "custom", customStart: "2026-09-01", customEnd: "2026-09-20" }), periodKey({ periodMode: "preset", periodDays: 30 }));
});

test("a custom range is exact only for GA4; other sources get the smallest window that reaches the start", () => {
  const period = { mode: "custom", start: "2026-09-01", end: "2026-09-20", days: 20 };
  const ga4 = sourceWindow("google_analytics_4", period, NOW);
  assert.deepEqual([ga4.exact, ga4.customStart, ga4.customEnd], [true, "2026-09-01", "2026-09-20"]);
  const shopify = sourceWindow("shopify", period, NOW); // 28 days back -> 30
  assert.deepEqual([shopify.exact, shopify.days, shopify.reaches], [false, 30, true]);
  const ads = sourceWindow("google_ads", period, NOW);
  assert.ok(ADS_PRESET_DAYS.includes(ads.days));
  assert.equal(ads.days, 30);
  const tooOld = sourceWindow("meta_ads", { mode: "custom", start: "2026-01-01", end: "2026-01-31", days: 31 }, NOW);
  assert.deepEqual([tooOld.days, tooOld.reaches], [90, false]); // can't reach back: reported, not hidden
  assert.equal(sourceWindow("shopify", { mode: "preset", days: 7 }, NOW).exact, true);
});

test("snapshot: several trend series, zero-only series dropped, clipped to a custom range, window explained", () => {
  const data = {
    cards: [{ label: "Total revenue", value: "$35.0k" }],
    trend: [...trendRows("revenue", [10, 20, 30, 40, 50]), ...trendRows("orders", [0, 0, 0, 0, 0]), ],
  };
  // rows share dates, so build a proper combined trend
  data.trend = [1, 2, 3, 4, 5].map((day, i) => ({ date: `2026-09-0${day}`, revenue: (i + 1) * 10, orders: 0, customers: i }));
  const window = sourceWindow("shopify", { mode: "custom", start: "2026-09-02", end: "2026-09-04", days: 3 }, NOW);
  const snap = buildSourceSnapshot("shopify", { ok: true, data }, { days: window.days, window, clip: { start: "2026-09-02", end: "2026-09-04" }, now: NOW });
  assert.deepEqual(snap.trends.map((series) => series.key), ["revenue", "customers"]); // "orders" is all zero -> dropped
  assert.deepEqual(snap.trends[0].points.map((point) => point.date), ["2026-09-02", "2026-09-03", "2026-09-04"]);
  assert.match(snap.windowNote, /can't report a custom range/);
  assert.equal(formatTrendTotal(snap.trends[0]), "$90.00"); // revenue is money
  const exact = buildSourceSnapshot("shopify", { ok: true, data }, { days: 30, window: { days: 30, exact: true }, now: NOW });
  assert.equal(exact.windowNote, "");
  assert.equal(buildSourceSnapshot("shopify", { ok: false, error: "x" }, { days: 30 }).ok, false);
});

const snapshotWith = (sources) => ({ periodKey: "preset:30", sources });
const okSource = (...keys) => ({ ok: true, trends: keys.map((key) => ({ key, label: key, fmt: "num", points: [{ date: "2026-09-01", value: 1 }, { date: "2026-09-02", value: 2 }] })) });

test("normalizeConfig expands the legacy chart section into one section per metric, first two on", () => {
  const config = { sources: ["shopify", "klaviyo"], sections: [{ id: "summary", enabled: true }, { id: "kpis", enabled: true }, { id: "chart", enabled: true }, { id: "tables", enabled: true }], chartTypes: {} };
  const out = normalizeConfig(config, snapshotWith({ shopify: okSource("revenue", "orders", "customers"), klaviyo: { ok: true, trends: [] } }));
  assert.deepEqual(out.sections.map((s) => s.id), ["summary", "kpis", "chart:shopify:revenue", "chart:shopify:orders", "chart:shopify:customers", "tables"]);
  assert.deepEqual(out.sections.filter((s) => s.id.startsWith("chart:")).map((s) => s.enabled), [true, true, false]);
  const off = normalizeConfig({ ...config, sections: config.sections.map((s) => (s.id === "chart" ? { ...s, enabled: false } : s)) }, snapshotWith({ shopify: okSource("revenue") }));
  assert.equal(off.sections.find((s) => s.id === "chart:shopify:revenue").enabled, false); // a hidden chart section stays hidden
});

test("normalizeConfig keeps the user's chart choices, drops charts the data can't produce, and leaves failed sources alone", () => {
  const config = {
    sources: ["shopify", "google_ads"],
    sections: [{ id: "chart:shopify:orders", enabled: false }, { id: "chart:shopify:revenue", enabled: true }, { id: "chart:google_ads:clicks", enabled: true }, { id: "chart:meta_ads:spend", enabled: true }],
    chartTypes: { "chart:shopify:revenue": "line" },
  };
  const out = normalizeConfig(config, snapshotWith({ shopify: okSource("revenue"), google_ads: { ok: false, error: "boom" } }));
  assert.deepEqual(out.sections.map((s) => s.id), ["chart:shopify:revenue", "chart:google_ads:clicks"]); // orders gone (no data), ads kept (load failed), meta not selected
  assert.equal(out.sections[0].enabled, true);
  assert.equal(out.chartTypes["chart:shopify:revenue"], "line");
  assert.equal(normalizeConfig(config, null).sections.length, 3); // no data yet: only unselected-source charts are dropped
});

test("chart types: line/area need a line to draw, and a saved type falls back when data can't support it", () => {
  const multi = okSource("revenue").trends[0];
  const single = { ...multi, points: [{ date: "2026-09-01", value: 5 }] };
  assert.deepEqual(availableChartTypes(multi).map((t) => t.id), ["line", "area", "bar", "hbar"]);
  assert.deepEqual(availableChartTypes(single).map((t) => t.id), ["bar", "hbar"]);
  assert.equal(chartTypeFor({ chartTypes: { a: "line" } }, "a", multi), "line");
  assert.equal(chartTypeFor({ chartTypes: { a: "line" } }, "a", single), "bar");
  assert.equal(chartTypeFor({}, "a", multi), "bar");
});

test("line and horizontal-bar geometry use the same data as the bar chart", () => {
  const points = [1, 2, 3, 4].map((day, i) => ({ date: `2026-09-0${day}`, value: (i + 1) * 10 }));
  const line = chartLine(points, { width: 300, height: 100 });
  assert.equal(line.coords.length, 4);
  assert.equal(line.coords[3].y, 0); // the max sits at the top
  assert.equal(line.coords[0].x, 0);
  assert.equal(line.coords[3].x, 300);
  assert.match(line.area, /Z$/);
  const rows = chartRows(points, { maxRows: 10 });
  assert.deepEqual(rows.rows.map((r) => r.value), [10, 20, 30, 40]);
  assert.equal(rows.rows[3].pct, 1);
  const many = Array.from({ length: 90 }, (_, i) => ({ date: `2026-06-${String((i % 28) + 1).padStart(2, "0")}`, value: 1 }));
  const bucketed = chartRows(many, { maxRows: 10 });
  assert.ok(bucketed.rows.length <= 10);
  assert.equal(bucketed.rows.reduce((sum, r) => sum + r.value, 0), 90); // totals preserved
});

test("a snapshot is stale when sources or the period change, including switching to a custom range", () => {
  const snap = { v: 2, periodKey: "preset:30", days: 30, sources: { shopify: {} } };
  assert.equal(snapshotIsStale({ sources: ["shopify"], periodMode: "preset", periodDays: 30 }, snap), false);
  assert.equal(snapshotIsStale({ sources: ["shopify"], periodMode: "preset", periodDays: 90 }, snap), true);
  assert.equal(snapshotIsStale({ sources: ["shopify", "klaviyo"], periodMode: "preset", periodDays: 30 }, snap), true);
  assert.equal(snapshotIsStale({ sources: ["shopify"], periodMode: "custom", customStart: "2026-09-01", customEnd: "2026-09-10", periodDays: 10 }, snap), true);
  assert.equal(snapshotIsStale({ sources: ["shopify"], periodMode: "preset", periodDays: 30 }, { v: 2, days: 30, sources: { shopify: {} } }), false); // snapshot without a period key
});

test("draftFromReport carries the saved chart types, custom range and chart sections", () => {
  const report = {
    name: "R", periodStart: "2026-09-01", periodEnd: "2026-09-20",
    config: { sources: ["shopify"], periodMode: "custom", customStart: "2026-09-01", customEnd: "2026-09-20", periodDays: 20, sections: [{ id: "chart:shopify:revenue", enabled: true }], chartTypes: { "chart:shopify:revenue": "hbar" } },
    content: { snapshot: snapshotWith({ shopify: okSource("revenue") }), narratives: {} },
  };
  const draft = draftFromReport(report);
  assert.equal(draft.config.chartTypes["chart:shopify:revenue"], "hbar");
  assert.equal(draft.config.periodMode, "custom");
  assert.equal(draft.config.customEnd, "2026-09-20");
  assert.deepEqual(draft.config.sections.map((s) => s.id), ["chart:shopify:revenue"]);
  const preset = draftFromReport({ ...report, config: { ...report.config, periodMode: "preset", customStart: null, customEnd: null } });
  assert.equal("customStart" in preset.config, false);
});

test("reconcileSections drops chart and commentary sections for a removed source", () => {
  const sections = [{ id: "kpis", enabled: true }, { id: "chart:shopify:revenue", enabled: true }, { id: "chart:klaviyo:sent", enabled: true }, { id: "commentary:shopify", enabled: true }, { id: "recommendations", enabled: true }];
  const out = reconcileSections(sections, ["shopify", "meta_ads"]).map((s) => s.id);
  assert.deepEqual(out, ["kpis", "chart:shopify:revenue", "commentary:shopify", "commentary:meta_ads", "recommendations"]);
});

test("seriesOf reads both the new and the old snapshot shapes", () => {
  assert.equal(seriesOf({ trends: [{ key: "a", points: [1] }] })[0].key, "a");
  assert.equal(seriesOf({ trend: { label: "Sessions", fmt: "num", points: [{ date: "d", value: 1 }] } })[0].key, "trend");
  assert.deepEqual(seriesOf(null), []);
});

// ── breakdown charts: what the Dashboards page draws besides daily trends ───────────────────

const snapOf = (sourceId, data) => buildSourceSnapshot(sourceId, { ok: true, data }, { days: 30, now: NOW });

test("Meta with no daily trend still gets breakdown charts (spend by campaign / platform)", () => {
  const snap = snapOf("meta_ads", {
    cards: [{ label: "Spend", value: "$8.4k" }],
    trend: [],
    campaigns: [{ name: "Prospecting", spend: 3600, clicks: 120, reach: 42000 }, { name: "Retargeting", spend: 2800, clicks: 300, reach: 28000 }, { name: "Idle", spend: 0, clicks: 0, reach: 0 }],
    platforms: [{ platform: "instagram", spend: 2800, clicks: 240 }, { platform: "facebook", spend: 4800, clicks: 900 }],
  });
  assert.deepEqual(snap.trends, []);
  const keys = snap.breakdowns.map((series) => series.key);
  assert.ok(keys.includes("campaigns_spend") && keys.includes("platforms_spend") && keys.includes("campaigns_clicks"));
  const spend = snap.breakdowns.find((series) => series.key === "campaigns_spend");
  assert.deepEqual(spend.points.map((p) => p.label), ["Prospecting", "Retargeting", "Idle"]); // largest first
  assert.equal(spend.kind, "category");
  assert.equal(spend.fmt, "money");
  assert.deepEqual(snap.breakdowns.find((series) => series.key === "platforms_spend").points.map((p) => p.label), ["facebook", "instagram"]);
  assert.equal(snap.empty, false);
});

test("HubSpot with a flat contacts trend charts the deal pipeline instead", () => {
  const snap = snapOf("hubspot_crm", {
    cards: [{ label: "Contacts", value: "0" }],
    trend: [{ date: "2026-09-01", contacts: 0 }, { date: "2026-09-02", contacts: 0 }],
    stages: [{ stage: "appointmentscheduled", count: 1, value: 2500 }, { stage: "closedwon", count: 0, value: 0 }],
    deals: [{ name: "Software Project", stage: "appointmentscheduled", amount: 2500 }],
  });
  assert.deepEqual(snap.trends, []); // all-zero daily series is dropped ...
  assert.deepEqual(snap.breakdowns.map((series) => series.key), ["stages_value", "stages_count", "deals_amount"]); // ... the pipeline is not
});

test("Klaviyo breakdowns count the returned campaigns and flows per status / trigger", () => {
  const snap = snapOf("klaviyo", {
    cards: [],
    campaigns: [{ name: "a", status: "Sent", channel: "email" }, { name: "b", status: "Sent", channel: "email" }, { name: "c", status: "Draft", channel: "sms" }],
    flows: [{ name: "f1", status: "live", trigger_type: "list" }, { name: "f2", status: "live", trigger_type: "" }, { name: "f3", status: "draft", trigger_type: "list" }],
  });
  const by = (key) => Object.fromEntries(snap.breakdowns.find((series) => series.key === key).points.map((p) => [p.label, p.value]));
  assert.deepEqual(by("campaigns_status"), { Sent: 2, Draft: 1 });
  assert.deepEqual(by("flows_status"), { live: 2, draft: 1 });
  assert.deepEqual(by("flows_trigger"), { list: 2, Unknown: 1 });
});

test("Google Ads, GA4 and Shopify breakdowns come straight from the dashboard arrays", () => {
  const ads = snapOf("google_ads", {
    campaigns: [{ name: "Brand", clicks: 2400, cost: 2800, conversions: 96 }],
    devices: [{ device: "MOBILE", clicks: 4200 }], funnel: [{ stage: "Impressions", value: 243000 }, { stage: "Clicks", value: 8400 }],
    keywords: [{ keyword: "seo services", clicks: 490 }], conversion_actions: [{ name: "Purchase", conversions: 142 }],
  });
  assert.deepEqual(ads.breakdowns.map((s) => s.key), ["campaigns_clicks", "campaigns_cost", "campaigns_conversions", "devices_clicks", "funnel", "conversion_actions", "keywords_clicks"]);
  assert.deepEqual(ads.breakdowns.find((s) => s.key === "funnel").points.map((p) => p.label), ["Impressions", "Clicks"]); // funnel keeps its order
  const ga = snapOf("google_analytics_4", { channels: [{ channel: "Direct", sessions: 272 }, { channel: "Referral", sessions: 3 }], devices: [{ device: "Mobile", sessions: 9 }], pages: [{ title: "Home", views: 80 }], countries: [], events: [] });
  assert.deepEqual(ga.breakdowns.map((s) => s.key), ["channels_sessions", "devices_sessions", "pages_views"]);
  const shop = snapOf("shopify", { top_products: [{ product: "Hoodie", quantity: 98, revenue: 7840 }] });
  assert.deepEqual(shop.breakdowns.map((s) => s.key), ["products_revenue", "products_units"]);
});

test("breakdowns are capped at 10 bars and never invent data for missing arrays", () => {
  const many = Array.from({ length: 25 }, (_, i) => ({ name: `Campaign ${i}`, clicks: i + 1, cost: 1, conversions: 1 }));
  const snap = snapOf("google_ads", { campaigns: many });
  assert.equal(snap.breakdowns.find((s) => s.key === "campaigns_clicks").points.length, 10);
  assert.equal(snap.breakdowns.find((s) => s.key === "campaigns_clicks").points[0].value, 25);
  assert.deepEqual(snapOf("meta_ads", { cards: [{ label: "Spend", value: "$1" }] }).breakdowns, []);
});

test("breakdown charts support the same chart types as time series (horizontal bar by default)", () => {
  const point = (label, value) => ({ label, value });
  const many = { kind: "category", key: "k", label: "Spend by campaign", fmt: "money", points: [point("a", 5), point("b", 10), point("c", 2)] };
  const one = { ...many, points: [point("a", 5)] };
  assert.deepEqual(availableChartTypes(many).map((t) => t.id), ["line", "area", "bar", "hbar"]);
  assert.deepEqual(availableChartTypes(one).map((t) => t.id), ["bar", "hbar"]); // a line needs two points
  assert.equal(chartTypeFor({}, "s", many), "hbar"); // default
  for (const type of ["line", "area", "bar", "hbar"]) assert.equal(chartTypeFor({ chartTypes: { s: type } }, "s", many), type); // choice is honoured
  assert.equal(chartTypeFor({ chartTypes: { s: "line" } }, "s", one), "hbar"); // falls back when the data can't support it
  const geo = categoryColumns(many.points, { width: 300, height: 100 });
  assert.equal(geo.slot, 100);
  assert.deepEqual(geo.coords.map((c) => c.x), [50, 150, 250]); // each point sits at the centre of its slot
  assert.equal(geo.coords[1].y, 0); // the largest value reaches the top
  assert.equal(geo.bars[1].height, 100);
  assert.ok(geo.bars.every((bar) => bar.x >= 0 && bar.x + bar.width <= 300));
  assert.match(geo.area, /Z$/);
});

test("breakdown charts become sections (first two on) and default to horizontal bars", () => {
  const meta = snapOf("meta_ads", { campaigns: [{ name: "P", spend: 10, clicks: 5, reach: 9 }], platforms: [{ platform: "facebook", spend: 10, clicks: 5 }] });
  const snapshot = { v: 2, periodKey: "preset:30", sources: { meta_ads: meta } };
  const out = normalizeConfig({ sources: ["meta_ads"], sections: [{ id: "kpis", enabled: true }, { id: "chart", enabled: true }], chartTypes: {} }, snapshot);
  const charts = out.sections.filter((s) => s.id.startsWith("chart:"));
  assert.ok(charts.length >= 4);
  assert.deepEqual(charts.slice(0, 2).map((s) => s.enabled), [true, true]);
  assert.ok(charts.slice(2).every((s) => s.enabled === false));
  const series = seriesOf(meta).find((item) => item.key === "campaigns_spend");
  assert.deepEqual(availableChartTypes(series).map((t) => t.id), ["bar", "hbar"]); // one campaign: no line
  assert.equal(chartTypeFor({}, "chart:meta_ads:campaigns_spend", series), "hbar");
  assert.equal(seriesSummary(series), "1 item");
  assert.equal(categoryRows([{ label: "a", value: 5 }, { label: "b", value: 10 }]).rows[0].pct, 0.5);
});

test("a snapshot saved before breakdown charts existed is flagged as out of date", () => {
  const config = { sources: ["shopify"], periodMode: "preset", periodDays: 30 };
  assert.equal(snapshotIsStale(config, { periodKey: "preset:30", days: 30, sources: { shopify: {} } }), true); // no version = v1
  assert.equal(snapshotIsStale(config, { v: 2, periodKey: "preset:30", days: 30, sources: { shopify: {} } }), false);
});
