import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { ComposableMap, Geographies, Geography, ZoomableGroup } from "react-simple-maps";
import { useLocation, useNavigate } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, Pie, PieChart, ResponsiveContainer, Sector, XAxis, YAxis } from "recharts";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";
import { DataTable, SortableHeader } from "@/components/ui/data-table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import ConnectorLogo from "../components/ConnectorLogo";
import { getGA4Dashboard, getGoogleAdsDashboard, getHubSpotDashboard, getKlaviyoDashboard, getMetaAdsDashboard, getShopifyDashboard } from "../lib/platformDashboards";
import { formatSourceStatus, getSourceByType } from "../lib/dataSources";
import { formatCurrency } from "../lib/currency";
import { toErrorMessage } from "../lib/apiErrors";
import "../styles/dashboards.css";

/* ────────────────────────────── constants ────────────────────────────── */

const PLATFORM_TABS = [
  { id: "google_analytics_4", label: "Google Analytics 4", nav: "GA4", short: "GA4", icon: "ti-chart-bar", color: "#f97316" },
  { id: "google_ads", label: "Google Ads", nav: "Google Ads", short: "Ads", icon: "ti-brand-google", color: "#2563eb" },
  { id: "meta_ads", label: "Meta Ads", nav: "Meta Ads", short: "Meta", icon: "ti-brand-meta", color: "#0866ff" },
  { id: "klaviyo", label: "Klaviyo", nav: "Klaviyo", short: "Email", icon: "ti-mail", color: "#1f2a44" },
  { id: "hubspot_crm", label: "HubSpot", nav: "HubSpot", short: "CRM", icon: "ti-building-store", color: "#00a4bd" },
  { id: "shopify", label: "Shopify", nav: "Shopify", short: "Shop", icon: "ti-shopping-cart", color: "#96bf48" },
];

const TONE_COLORS = { blue: "#2563eb", green: "#14b8a6", violet: "#7c3aed", amber: "#f59e0b", rose: "#e11d48", emerald: "#22c55e" };

const GA4_TREND_CONFIG = { sessions: { label: "Sessions", color: "var(--accent)" }, activeUsers: { label: "Active users", color: "#14b8a6" } };
const GA4_NEW_USERS_CONFIG = { newUsers: { label: "New users", color: "var(--accent)" } };
const GA4_ORGANIC_CONFIG = { sessions: { label: "Organic sessions", color: "var(--accent)" } };
const GA4_CONVERSION_CONFIG = { conversionRate: { label: "Conversion rate %", color: "var(--accent)" } };
const GADS_IMPRESSIONS_CONFIG = { impressions: { label: "Impressions", color: "var(--accent)" } };
const GADS_FUNNEL_CONFIG = { value: { label: "Count", color: "var(--accent)" } };
const GADS_CPM_CONFIG = { impressions: { label: "Impressions", color: "var(--accent)" }, cpm: { label: "CPM ($)", color: "#14b8a6" } };
const GADS_CPC_CONFIG = { clicks: { label: "Clicks", color: "var(--accent)" }, cpc: { label: "CPC ($)", color: "#f59e0b" } };
const GADS_CPL_CONFIG = { conversions: { label: "Conversions", color: "var(--accent)" }, cpl: { label: "CPL ($)", color: "#7c3aed" } };
const GADS_KEYWORD_CONFIG = { clicks: { label: "Clicks", color: "var(--accent)" }, impressions: { label: "Impressions", color: "#14b8a6" } };
const GADS_CLICK_TYPE_CONFIG = { clicks: { label: "Clicks", color: "var(--accent)" } };
const GA4_CHANNEL_CONFIG = { sessions: { label: "Sessions", color: "var(--accent)" } };
const GA4_DEVICE_CONFIG = {
  activeUsers: { label: "Active users" },
  desktop: { label: "Desktop", color: "var(--accent)" },
  mobile: { label: "Mobile", color: "#14b8a6" },
  tablet: { label: "Tablet", color: "#f97316" },
  other: { label: "Other", color: "#7c3aed" },
};

const GADS_TREND_CONFIG = { clicks: { label: "Clicks", color: "var(--accent)" }, conversions: { label: "Conversions", color: "#14b8a6" } };
const GADS_CAMPAIGN_CONFIG = { cost: { label: "Cost", color: "#2563eb" } };
const META_SPEND_ROAS_CONFIG = { spend: { label: "Spend ($)", color: "var(--accent)" }, roas: { label: "ROAS", color: "#14b8a6" } };
const META_CPM_CTR_CONFIG = { cpm: { label: "CPM ($)", color: "var(--accent)" }, ctr: { label: "CTR (%)", color: "#f59e0b" } };
const META_REACH_FREQ_CONFIG = { reach: { label: "Reach", color: "var(--accent)" }, frequency: { label: "Frequency", color: "#7c3aed" } };
const META_TREND_CONFIG = { impressions: { label: "Impressions", color: "var(--accent)" }, clicks: { label: "Clicks", color: "#14b8a6" } };
const META_CAMPAIGN_CONFIG = { spend: { label: "Spend", color: "var(--accent)" } };

const HUBSPOT_TREND_CONFIG = { contacts: { label: "New contacts", color: "var(--accent)" } };
const HUBSPOT_STAGE_CONFIG = { value: { label: "Pipeline value", color: "var(--accent)" } };
const KLAVIYO_CAMPAIGN_CONFIG = { campaigns: { label: "Campaigns", color: "#1f2a44" } };
const SHOPIFY_TREND_CONFIG = { revenue: { label: "Revenue", color: "var(--accent)" }, orders: { label: "Orders", color: "#14b8a6" } };
const SHOPIFY_AOV_CONFIG = { aov: { label: "Avg Order Value", color: "var(--accent)" } };
const SHOPIFY_PRODUCT_CONFIG = { revenue: { label: "Revenue", color: "var(--accent)" } };
const KLAVIYO_TREND_CONFIG = { sent: { label: "Sent", color: "var(--accent)" }, draft: { label: "Draft", color: "#94a3b8" } };
const GADS_DEVICE_BAR_CONFIG = { clicks: { label: "Clicks", color: "var(--accent)" }, cost: { label: "Cost ($)", color: "#14b8a6" } };
const CROSS_PLATFORM_CONFIG = { google_ads: { label: "Google Ads", color: "#2563eb" }, meta_ads: { label: "Meta Ads", color: "#0866ff" }, google_analytics_4: { label: "GA4", color: "#f97316" }, hubspot_crm: { label: "HubSpot", color: "#00a4bd" }, klaviyo: { label: "Klaviyo", color: "#1f2a44" } };
const FUNNEL_COLORS = ["#00a4bd", "#0084a0", "#006580", "#004d61", "#003545"];

let activeCurrency = "usd";

// Date ranges each backend actually honours. Google Ads / Meta Ads map `days`
// onto fixed 7/14/30/90 presets (anything else silently becomes 30 days);
// HubSpot / Klaviyo / Shopify accept 7..365. GA4 accepts every preset.
const ADS_PRESET_DAYS = [7, 14, 30, 90];
const RANGE_PRESET_DAYS = [7, 14, 30, 90, 180, 365];

const DEVICE_COLORS = ["#2f7df6", "#14b8a6", "#f97316", "#7c3aed", "#ec4899"];

/* ────────────────────────────── formatters ───────────────────────────── */

function money(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(Number(value || 0));
}

function moneyPrecise(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(Number(value || 0));
}

function compact(value) {
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(Number(value || 0));
}

function prettyDate(value) {
  if (!value || value.length !== 8) return value || "";
  return `${value.slice(4, 6)}/${value.slice(6, 8)}`;
}

function getAccent() {
  if (typeof document === "undefined") return "#111111";
  return getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#111111";
}

function useAccentColor() {
  const [accent, setAccent] = useState(() => getAccent());
  useEffect(() => {
    let polls = 0;
    const interval = setInterval(() => {
      setAccent(getAccent());
      polls++;
      if (polls >= 10) clearInterval(interval);
    }, 500);
    const obs = new MutationObserver(() => setAccent(getAccent()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["style", "class", "data-theme"] });
    obs.observe(document.head, { childList: true, subtree: true });
    return () => { clearInterval(interval); obs.disconnect(); };
  }, []);
  return accent || "#111111";
}

function shortDate(value) {
  if (!value) return "";
  // YYYY-MM-DD, and full ISO timestamps (Klaviyo send_time / created), which
  // used to fall through and render raw ("2026-09-25T17:09:53.549Z").
  if (/^\d{4}-\d{2}-\d{2}/.test(value)) return `${value.slice(5, 7)}/${value.slice(8, 10)}`;
  return prettyDate(value);
}

function pct(value) {
  return `${Number(value || 0).toFixed(1)}%`;
}

function deviceKey(value) {
  const key = String(value || "other").toLowerCase().replace(/[^a-z0-9]+/g, "_");
  if (key.includes("desktop")) return "desktop";
  if (key.includes("mobile")) return "mobile";
  if (key.includes("tablet")) return "tablet";
  return "other";
}

/* ────────────────────────────── column defs ─────────────────────────── */

// Table status pills. The label is display-only: sorting, filtering and the
// data keep the raw provider value. Tone gives live/enabled/sent/won a green
// pill and archived/lost/paused a quieter one.
const POSITIVE_STATUSES = new Set(["enabled", "sent", "live", "active", "closedwon"]);
const MUTED_STATUSES = new Set(["archived", "removed", "paused", "cancelled", "canceled", "closedlost"]);
// HubSpot returns its default pipeline's internal stage ids ("appointmentscheduled").
const HUBSPOT_STAGE_LABELS = {
  appointmentscheduled: "Appointment scheduled",
  qualifiedtobuy: "Qualified to buy",
  presentationscheduled: "Presentation scheduled",
  decisionmakerboughtin: "Decision maker bought-in",
  contractsent: "Contract sent",
  closedwon: "Closed won",
  closedlost: "Closed lost",
};

function stageLabel(value) {
  if (value == null || value === "") return "-";
  return HUBSPOT_STAGE_LABELS[String(value).toLowerCase()] || String(value);
}

// "ENABLED" / "live" / "sent_later" -> "Enabled" / "Live" / "Sent later";
// mixed-case values ("Qualified Lead") are already human and kept as-is.
function statusLabel(value) {
  const text = String(value ?? "").trim();
  if (!text) return "-";
  if (text !== text.toUpperCase() && text !== text.toLowerCase()) return text;
  const words = text.toLowerCase().replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function StatusPill({ value, label }) {
  const key = String(value ?? "").toLowerCase();
  const tone = POSITIVE_STATUSES.has(key) ? "positive" : MUTED_STATUSES.has(key) ? "muted" : "neutral";
  return <Badge variant="outline" className="bs-pill" data-tone={tone}>{label ?? statusLabel(value)}</Badge>;
}

// Marks a column as a number: right-aligned with tabular figures (DataTable
// turns meta.numeric into data-numeric on its header and cells).
const NUM = { meta: { numeric: true } };

const ga4ChannelCols = [
  { accessorKey: "channel", header: ({ column }) => <SortableHeader column={column}>Channel</SortableHeader> },
  { ...NUM, accessorKey: "sessions", header: ({ column }) => <SortableHeader column={column}>Sessions</SortableHeader>, cell: ({ row }) => compact(row.getValue("sessions")) },
  { ...NUM, accessorKey: "revenue", header: "Revenue", cell: ({ row }) => money(row.getValue("revenue")) },
  { ...NUM, accessorKey: "keyEvents", header: "Key events", cell: ({ row }) => compact(row.getValue("keyEvents")) },
];

const ga4PageCols = [
  { accessorKey: "title", header: "Page", cell: ({ row }) => <div><div className="font-medium">{row.original.title}</div><div className="text-xs text-muted-foreground">{row.original.path}</div></div> },
  { ...NUM, accessorKey: "views", header: ({ column }) => <SortableHeader column={column}>Views</SortableHeader>, cell: ({ row }) => compact(row.getValue("views")) },
  { ...NUM, accessorKey: "activeUsers", header: "Users", cell: ({ row }) => compact(row.getValue("activeUsers")) },
  { ...NUM, accessorKey: "keyEvents", header: "Key events", cell: ({ row }) => compact(row.getValue("keyEvents")) },
];

const ga4EventCols = [
  { accessorKey: "event", header: ({ column }) => <SortableHeader column={column}>Event</SortableHeader> },
  { ...NUM, accessorKey: "count", header: ({ column }) => <SortableHeader column={column}>Event count</SortableHeader>, cell: ({ row }) => compact(row.getValue("count")) },
  { ...NUM, accessorKey: "keyEvents", header: "Key events", cell: ({ row }) => compact(row.getValue("keyEvents")) },
  { ...NUM, accessorKey: "revenue", header: "Revenue", cell: ({ row }) => money(row.getValue("revenue")) },
];

const gadsDeviceCols = [
  { accessorKey: "device", header: "Device" },
  { ...NUM, accessorKey: "clicks", header: ({ column }) => <SortableHeader column={column}>Clicks</SortableHeader>, cell: ({ row }) => compact(row.getValue("clicks")) },
  { ...NUM, accessorKey: "cost", header: ({ column }) => <SortableHeader column={column}>Cost</SortableHeader>, cell: ({ row }) => money(row.getValue("cost")) },
  { ...NUM, accessorKey: "conversions", header: "Conv.", cell: ({ row }) => compact(row.getValue("conversions")) },
];

const gadsCampaignCols = [
  { accessorKey: "name", header: "Campaign" },
  { accessorKey: "status", header: "Status", cell: ({ row }) => <StatusPill value={row.getValue("status")} /> },
  { ...NUM, accessorKey: "impressions", header: "Impr.", cell: ({ row }) => compact(row.getValue("impressions")) },
  { ...NUM, accessorKey: "clicks", header: ({ column }) => <SortableHeader column={column}>Clicks</SortableHeader>, cell: ({ row }) => compact(row.getValue("clicks")) },
  { ...NUM, accessorKey: "ctr", header: "CTR", cell: ({ row }) => pct(row.getValue("ctr")) },
  { ...NUM, accessorKey: "cost", header: ({ column }) => <SortableHeader column={column}>Cost</SortableHeader>, cell: ({ row }) => money(row.getValue("cost")) },
  { ...NUM, accessorKey: "conversions", header: "Conv.", cell: ({ row }) => compact(row.getValue("conversions")) },
  { ...NUM, accessorKey: "costPerConversion", header: ({ column }) => <SortableHeader column={column}>Cost/Conv.</SortableHeader>, cell: ({ row }) => row.getValue("costPerConversion") ? moneyPrecise(row.getValue("costPerConversion")) : "-" },
  { ...NUM, accessorKey: "value", header: "Value", cell: ({ row }) => money(row.getValue("value")) },
];

const metaPlatformCols = [
  { accessorKey: "platform", header: "Platform" },
  { ...NUM, accessorKey: "spend", header: ({ column }) => <SortableHeader column={column}>Spend</SortableHeader>, cell: ({ row }) => money(row.getValue("spend")) },
  { ...NUM, accessorKey: "impressions", header: "Impressions", cell: ({ row }) => compact(row.getValue("impressions")) },
  { ...NUM, accessorKey: "clicks", header: "Clicks", cell: ({ row }) => compact(row.getValue("clicks")) },
];

const metaCampaignCols = [
  { accessorKey: "name", header: "Campaign" },
  { ...NUM, accessorKey: "spend", header: ({ column }) => <SortableHeader column={column}>Spend</SortableHeader>, cell: ({ row }) => money(row.getValue("spend")) },
  { ...NUM, accessorKey: "reach", header: "Reach", cell: ({ row }) => compact(row.getValue("reach")) },
  { ...NUM, accessorKey: "impressions", header: "Impr.", cell: ({ row }) => compact(row.getValue("impressions")) },
  { ...NUM, accessorKey: "clicks", header: ({ column }) => <SortableHeader column={column}>Clicks</SortableHeader>, cell: ({ row }) => compact(row.getValue("clicks")) },
  { ...NUM, accessorKey: "ctr", header: "CTR", cell: ({ row }) => pct(row.getValue("ctr")) },
  { ...NUM, accessorKey: "cpm", header: "CPM", cell: ({ row }) => moneyPrecise(row.getValue("cpm")) },
  { ...NUM, accessorKey: "roas", header: "ROAS", cell: ({ row }) => row.getValue("roas") ? `${Number(row.getValue("roas")).toFixed(1)}x` : "-" },
];

const hubspotStageCols = [
  { accessorKey: "stage", header: "Stage", cell: ({ row }) => stageLabel(row.getValue("stage")) },
  { ...NUM, accessorKey: "count", header: ({ column }) => <SortableHeader column={column}>Deals</SortableHeader>, cell: ({ row }) => compact(row.getValue("count")) },
  { ...NUM, accessorKey: "value", header: ({ column }) => <SortableHeader column={column}>Value</SortableHeader>, cell: ({ row }) => money(row.getValue("value")) },
];

const hubspotDealCols = [
  { accessorKey: "name", header: "Deal" },
  { accessorKey: "stage", header: "Stage", cell: ({ row }) => <StatusPill value={row.getValue("stage")} label={stageLabel(row.getValue("stage"))} /> },
  { ...NUM, accessorKey: "amount", header: ({ column }) => <SortableHeader column={column}>Amount</SortableHeader>, cell: ({ row }) => money(row.getValue("amount")) },
  { accessorKey: "pipeline", header: "Pipeline" },
  { accessorKey: "closedate", header: "Close date", cell: ({ row }) => row.getValue("closedate") ? shortDate(row.getValue("closedate")) : "-" },
];

const gadsKeywordCols = [
  { accessorKey: "keyword", header: ({ column }) => <SortableHeader column={column}>Keyword</SortableHeader> },
  { ...NUM, accessorKey: "impressions", header: ({ column }) => <SortableHeader column={column}>Impr.</SortableHeader>, cell: ({ row }) => compact(row.getValue("impressions")) },
  { ...NUM, accessorKey: "clicks", header: ({ column }) => <SortableHeader column={column}>Clicks</SortableHeader>, cell: ({ row }) => compact(row.getValue("clicks")) },
  { ...NUM, accessorKey: "ctr", header: "CTR", cell: ({ row }) => pct(row.getValue("ctr")) },
  { ...NUM, accessorKey: "cost", header: ({ column }) => <SortableHeader column={column}>Cost</SortableHeader>, cell: ({ row }) => money(row.getValue("cost")) },
  { ...NUM, accessorKey: "conversions", header: "Conv.", cell: ({ row }) => compact(row.getValue("conversions")) },
];

const gadsClickTypeCols = [
  { accessorKey: "clickType", header: "Click type" },
  { ...NUM, accessorKey: "clicks", header: ({ column }) => <SortableHeader column={column}>Clicks</SortableHeader>, cell: ({ row }) => compact(row.getValue("clicks")) },
  { ...NUM, accessorKey: "impressions", header: "Impressions", cell: ({ row }) => compact(row.getValue("impressions")) },
  { ...NUM, accessorKey: "ctr", header: "CTR", cell: ({ row }) => pct(row.getValue("ctr")) },
  { ...NUM, accessorKey: "cost", header: ({ column }) => <SortableHeader column={column}>Cost</SortableHeader>, cell: ({ row }) => money(row.getValue("cost")) },
];

const gadsConversionCols = [
  { accessorKey: "category", header: "Category" },
  { accessorKey: "name", header: "Conversion action" },
  { ...NUM, accessorKey: "conversions", header: ({ column }) => <SortableHeader column={column}>Conversions</SortableHeader>, cell: ({ row }) => compact(row.getValue("conversions")) },
];

const shopifyProductCols = [
  { accessorKey: "product", header: ({ column }) => <SortableHeader column={column}>Product</SortableHeader> },
  { ...NUM, accessorKey: "quantity", header: ({ column }) => <SortableHeader column={column}>Units</SortableHeader>, cell: ({ row }) => compact(row.getValue("quantity")) },
  { ...NUM, accessorKey: "revenueFormatted", header: ({ column }) => <SortableHeader column={column}>Revenue</SortableHeader>, sortingFn: (a, b) => Number(a.original.revenue || 0) - Number(b.original.revenue || 0) },
];

const klaviyoCampaignCols = [
  { accessorKey: "name", header: "Campaign" },
  { accessorKey: "status", header: "Status", cell: ({ row }) => <StatusPill value={row.getValue("status")} /> },
  { accessorKey: "channel", header: "Channel" },
  { accessorKey: "send_time", header: "Updated", cell: ({ row }) => row.getValue("send_time") ? shortDate(row.getValue("send_time")) : "-" },
];

const klaviyoListCols = [
  { accessorKey: "name", header: "List" },
  { accessorKey: "created", header: "Created", cell: ({ row }) => row.getValue("created") ? shortDate(row.getValue("created")) : "-" },
];

const klaviyoFlowCols = [
  { accessorKey: "name", header: "Flow" },
  { accessorKey: "status", header: "Status", cell: ({ row }) => <StatusPill value={row.getValue("status")} /> },
  { accessorKey: "trigger_type", header: "Trigger", cell: ({ row }) => row.getValue("trigger_type") || "-" },
];

/* ────────────────────────────── shared UI ────────────────────────────── */

function SourceBadge({ source }) {
  const connected = source?.status === "connected";
  return (
    <Badge variant="outline" className={connected ? "dash-status connected" : "dash-status muted"}>
      <span />{formatSourceStatus(source)}
    </Badge>
  );
}

function KpiCard({ card, loading, firstLoad }) {
  const accent = TONE_COLORS[card.tone] || "#2563eb";
  return (
    <div className="kpi-card" style={{ "--kpi-accent": accent }}>
      <span className="kpi-label">{firstLoad ? <Skeleton className="kpi-skel-sm" /> : card.label}</span>
      <strong className="kpi-value">{firstLoad ? <Skeleton className="kpi-skel-lg" /> : card.value}</strong>
      <span className="kpi-detail">{firstLoad ? <Skeleton className="kpi-skel-md" /> : card.detail}</span>
    </div>
  );
}

// A zero-height sticky anchor with the pill floating over the content, so a
// refresh doesn't push the KPIs down and snap them back when it finishes.
function DashboardLoadingBar({ loading }) {
  if (!loading) return null;
  return (
    <div className="dash-refresh-anchor" role="status" aria-live="polite">
      <div className="dash-refresh-overlay">
        <div className="dash-refresh-spinner" />
        <span>Updating data...</span>
      </div>
    </div>
  );
}

// Custom-range rules mirror the GA4 endpoint (py/app/ga4_dashboard.py
// resolve_date_window): inclusive dates, at most 365 days, nothing before
// GA4's first day of data. The backend re-validates; this just keeps the
// Apply button honest.
const MAX_CUSTOM_RANGE_DAYS = 365;
const GA4_EARLIEST_DATE = "2015-08-14";

function daysBetween(start, end) {
  return Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86400000);
}

// "Aug 1 – Aug 10, 2026", or with both years when the range crosses one.
function formatDateRange(start, end) {
  const parse = (value) => new Date(`${value}T00:00:00Z`);
  const s = parse(start);
  const e = parse(end);
  if (Number.isNaN(s.getTime()) || Number.isNaN(e.getTime())) return `${start} – ${end}`;
  const sameYear = s.getUTCFullYear() === e.getUTCFullYear();
  const fmt = (d, withYear) => d.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(withYear ? { year: "numeric" } : {}), timeZone: "UTC" });
  if (start === end) return fmt(s, true);
  return `${fmt(s, !sameYear)} – ${fmt(e, true)}`;
}

function DateRangePicker({ days, onDaysChange, customStart, customEnd, onCustomChange, allowedDays }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const [localStart, setLocalStart] = useState(customStart || "");
  const [localEnd, setLocalEnd] = useState(customEnd || "");
  const triggerRef = useRef(null);

  const allPresets = [
    { label: "Today", value: "1" },
    { label: "Yesterday", value: "2" },
    { label: "Last 7 days", value: "7" },
    { label: "Last 14 days", value: "14" },
    { label: "Last 30 days", value: "30" },
    { label: "Last 90 days", value: "90" },
    { label: "Last 6 months", value: "180" },
    { label: "Last year", value: "365" },
  ];
  // Providers whose backend only honours some ranges pass allowedDays, so the
  // picker never offers a range that 422s or silently returns other data.
  const presets = allowedDays ? allPresets.filter((p) => allowedDays.includes(Number(p.value))) : allPresets;

  const isCustom = !!(customStart && customEnd);
  const currentPreset = allPresets.find(p => p.value === String(days));
  const displayLabel = isCustom ? formatDateRange(customStart, customEnd) : (currentPreset?.label || `Last ${days} days`);
  const today = new Date().toISOString().split("T")[0];
  const customSpan = localStart && localEnd ? daysBetween(localStart, localEnd) + 1 : 0;
  const customError = !localStart || !localEnd ? ""
    : localStart > localEnd ? "Start date must be on or before the end date."
    : customSpan > MAX_CUSTOM_RANGE_DAYS ? `Choose ${MAX_CUSTOM_RANGE_DAYS} days or fewer.`
    : "";
  const canApply = Boolean(localStart && localEnd) && !customError;

  function toggle() {
    if (!open && triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      setPos({ top: rect.bottom + 8, left: rect.left });
    }
    setOpen(o => !o);
  }

  function applyCustom() {
    if (canApply) {
      onCustomChange?.(localStart, localEnd);
      setOpen(false);
    }
  }

  useEffect(() => {
    if (!open) return;
    const close = (e) => { if (!e.target.closest(".date-range-picker") && !e.target.closest(".date-range-dropdown")) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <div className="date-range-picker">
      <button ref={triggerRef} className="date-range-trigger" onClick={toggle}>
        <i className="ti ti-calendar" style={{ marginRight: 6 }} />
        {displayLabel}
        <i className="ti ti-chevron-down" style={{ marginLeft: 6 }} />
      </button>
      {open && (
        <div className="date-range-dropdown" style={{ top: pos.top, left: pos.left }}>
          <div className="date-range-section-label">Quick select</div>
          <div className="date-range-presets-grid">
            {presets.map(p => (
              <button key={p.value} className={`date-range-preset-item${!isCustom && String(days) === p.value ? " active" : ""}`}
                onClick={() => { onDaysChange(p.value); onCustomChange?.("",""); setOpen(false); }}>
                {p.label}
              </button>
            ))}
          </div>
          {onCustomChange && (<>
          <div className="date-range-divider" />
          <div className="date-range-section-label">Custom range</div>
          <div className="date-range-calendar-row">
            <div className="date-range-field">
              <label>From</label>
              <input type="date" className="analytics-date-input" value={localStart} min={GA4_EARLIEST_DATE} max={localEnd || today} onChange={e => setLocalStart(e.target.value)} />
            </div>
            <div className="date-range-field">
              <label>To</label>
              <input type="date" className="analytics-date-input" value={localEnd} min={localStart || GA4_EARLIEST_DATE} max={today} onChange={e => setLocalEnd(e.target.value)} />
            </div>
          </div>
          {customError
            ? <div className="date-range-hint is-error" role="alert">{customError}</div>
            : customSpan > 0 && <div className="date-range-hint">{customSpan} {customSpan === 1 ? "day" : "days"} selected</div>}
          <button className="date-range-apply" onClick={applyCustom} disabled={!canApply}>
            Apply range
          </button>
          </>)}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════ CHART FILTER ══════════════════════════════════════ */

function useChartFilter(storageKey, defaultCharts) {
  const [visible, setVisible] = useState(() => {
    try {
      const saved = sessionStorage.getItem(storageKey);
      return saved ? JSON.parse(saved) : defaultCharts;
    } catch { return defaultCharts; }
  });

  function toggle(key) {
    setVisible(prev => {
      const next = prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key];
      try { sessionStorage.setItem(storageKey, JSON.stringify(next)); } catch {}
      return next;
    });
  }

  function showAll() {
    setVisible(defaultCharts);
    try { sessionStorage.removeItem(storageKey); } catch {}
  }

  return { visible, toggle, showAll, isVisible: (key) => visible.includes(key) };
}

function ChartFilter({ charts, visible, onToggle, onShowAll }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const triggerRef = useRef(null);
  const allVisible = charts.every(c => visible.includes(c.key));

  function toggle() {
    if (!open && triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      setPos({ top: rect.bottom + 8, left: rect.left });
    }
    setOpen(o => !o);
  }

  useEffect(() => {
    if (!open) return;
    const close = (e) => { if (!e.target.closest(".chart-filter") && !e.target.closest(".chart-filter-dropdown")) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <div className="chart-filter">
      <button ref={triggerRef} className="date-range-trigger" onClick={toggle} title="Show/hide charts">
        <i className="ti ti-layout-grid" style={{ marginRight: 6 }} />
        Charts
        {!allVisible && <span className="chart-filter-badge">{visible.length}/{charts.length}</span>}
        <i className="ti ti-chevron-down" style={{ marginLeft: 4 }} />
      </button>
      {open && (
        <div className="chart-filter-dropdown" style={{ top: pos.top, left: pos.left }}>
          <div className="chart-filter-header">
            <span>Visible charts</span>
            {!allVisible && <button className="chart-filter-reset" onClick={() => { onShowAll(); setOpen(false); }}>Show all</button>}
          </div>
          {charts.map(c => (
            <label key={c.key} className="chart-filter-item">
              <input
                type="checkbox"
                checked={visible.includes(c.key)}
                onChange={() => onToggle(c.key)}
              />
              <span>{c.label}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

// The one control bar every provider dashboard uses: connection status and
// the provider's own context (property / account / store) on the left, then
// date range, any provider-specific pickers, section visibility and refresh.
function DashboardToolbar({ source, context, days, onDaysChange, customStart, customEnd, onCustomChange, allowedDays, charts, chartFilter, onRefresh, loading, children }) {
  return (
    <div className="bs-toolbar">
      <div className="bs-toolbar-context">
        <SourceBadge source={source} />
        {context && <span className="bs-toolbar-context-text" title={context}>{context}</span>}
      </div>
      <div className="bs-toolbar-controls">
        <DateRangePicker
          days={days} onDaysChange={onDaysChange}
          customStart={customStart} customEnd={customEnd} onCustomChange={onCustomChange}
          allowedDays={allowedDays}
        />
        {children}
        <ChartFilter charts={charts} visible={chartFilter.visible} onToggle={chartFilter.toggle} onShowAll={chartFilter.showAll} />
        <Button variant="outline" className="analytics-secondary bs-refresh-btn" onClick={onRefresh} disabled={loading}>
          <i className="ti ti-refresh" />
          {loading ? "Loading..." : "Refresh data"}
        </Button>
      </div>
    </div>
  );
}

function AlertBanner({ error, onAction, actionLabel = "Open Integrations" }) {
  if (!error) return null;
  return (
    <div className="analytics-alert" role="alert">
      <i className="ti ti-alert-triangle" />
      <span>{toErrorMessage(error)}</span>
      <button onClick={onAction}>{actionLabel}</button>
    </div>
  );
}

function ChartEmpty({ title = "No chart data", message = "This report returned no rows for the selected range." }) {
  return (
    <div className="analytics-chart-empty">
      <div className="analytics-chart-empty-art"><span /><span /><span /></div>
      <strong>{title}</strong>
      <p>{message}</p>
    </div>
  );
}

function EmptyPanel({ title, message, actionLabel, onAction }) {
  return (
    <div className="analytics-empty">
      <div className="analytics-empty-icon"><i className="ti ti-database-off" /></div>
      <div>
        <h3>{title}</h3>
        <p>{toErrorMessage(message, "")}</p>
      </div>
      {actionLabel && <Button className="analytics-primary primary-btn" onClick={onAction}>{actionLabel}</Button>}
    </div>
  );
}

function PanelHeader({ title, subtitle, badge }) {
  return (
    <div className="analytics-panel-head">
      <div><h3>{title}</h3><p>{subtitle}</p></div>
      {badge && <Badge variant="outline" className="dash-status muted">{badge}</Badge>}
    </div>
  );
}

function renderActiveDeviceShape(props) {
  const { cx, cy, innerRadius, outerRadius, startAngle, endAngle, fill } = props;
  return (
    <g>
      <Sector cx={cx} cy={cy} innerRadius={innerRadius} outerRadius={outerRadius + 10} startAngle={startAngle} endAngle={endAngle} fill={fill} cornerRadius={12} />
      <Sector cx={cx} cy={cy} innerRadius={outerRadius + 14} outerRadius={outerRadius + 17} startAngle={startAngle} endAngle={endAngle} fill={fill} cornerRadius={12} />
    </g>
  );
}

/* ────────────────────────────── hook ─────────────────────────────────── */

function useDashboardData(fetcher, deps) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const seqRef = useRef(0);
  useEffect(() => {
    const id = ++seqRef.current;
    setLoading(true);
    setError("");
    Promise.resolve().then(fetcher).then((result) => {
      if (seqRef.current !== id) return;
      setData(result?.data ?? null);
      setError(result?.ok ? "" : toErrorMessage(result?.error, "Could not load dashboard"));
      setLoading(false);
    }).catch((err) => {
      // Anything the fetcher throws (session lookup, unexpected response
      // shape) becomes the dashboard's normal error state instead of an
      // unhandled rejection that leaves the skeletons spinning forever.
      if (seqRef.current !== id) return;
      setData(null);
      setError(toErrorMessage(err, "Could not load dashboard"));
      setLoading(false);
    });
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, loading, error };
}

/* ═══════════════════════════ GA4 DASHBOARD ═══════════════════════════ */

function GA4ConversionFunnel({ data }) {
  const accent = useAccentColor();
  const cards = data?.cards || [];

  const sessions = cards.find(c => c.label === "Sessions");
  const activeUsers = cards.find(c => c.label === "Active users");
  const keyEvents = cards.find(c => c.label === "Key events");
  const revenue = cards.find(c => c.label === "Total revenue");

  function parseVal(str) {
    if (!str || str === "-") return 0;
    const s = String(str).replace(/[$,]/g, "");
    if (s.endsWith("M")) return parseFloat(s) * 1000000;
    if (s.endsWith("k")) return parseFloat(s) * 1000;
    return parseFloat(s) || 0;
  }

  const funnelData = [
    { stage: "Sessions", value: parseVal(sessions?.value), detail: "Total visits" },
    { stage: "Active Users", value: parseVal(activeUsers?.value), detail: "Engaged visitors" },
    { stage: "Key Events", value: parseVal(keyEvents?.value), detail: "Conversion actions" },
    { stage: "Revenue", value: parseVal(revenue?.value), detail: "Attributed revenue" },
  ].filter(d => d.value > 0);

  if (funnelData.length < 2) return <ChartEmpty title="No funnel data" message="Not enough data to build funnel." />;

  const max = funnelData[0].value;
  const W = 500;
  const sliceH = 80;
  const gap = 6;
  const totalH = funnelData.length * sliceH + (funnelData.length - 1) * gap + 40;
  const sliceOpacity = ["1", "0.78", "0.56", "0.38"];
  const logMax = Math.log10(max + 1);

  return (
    <div className="gads-svg-funnel">
      <svg viewBox={`0 0 ${W} ${totalH}`} width="100%" style={{ overflow: "visible" }}>
        {funnelData.map((item, i) => {
          const logVal = Math.log10(item.value + 1);
          const halfW = Math.max(55, Math.round((logVal / logMax) * 160));
          const nextItem = funnelData[i + 1];
          const nextLogVal = nextItem ? Math.log10(nextItem.value + 1) : logVal * 0.35;
          const nextHalfW = Math.max(35, Math.round((nextLogVal / logMax) * 160));
          const y = i * (sliceH + gap);
          const cx = 180;
          const pts = [`${cx - halfW},${y}`, `${cx + halfW},${y}`, `${cx + nextHalfW},${y + sliceH}`, `${cx - nextHalfW},${y + sliceH}`].join(" ");
          const dropPct = nextItem ? Math.round((nextItem.value / item.value) * 100) : null;
          const midY = y + sliceH / 2;
          const dotX = cx + halfW + 8;
          const lineEndX = W - 70;

          return (
            <g key={item.stage}>
              <polygon points={pts} fill={accent} opacity={sliceOpacity[i % sliceOpacity.length]} />
              <text x={cx} y={midY - 8} textAnchor="middle" fill="white" fontSize="20" fontWeight="700" style={{ fontFamily: "inherit" }}>
                {compact(item.value)}
              </text>
              <circle cx={dotX} cy={midY} r="4" fill={accent} />
              <line x1={dotX + 4} y1={midY} x2={lineEndX} y2={midY} stroke={accent} strokeWidth="1.5" strokeDasharray="4 3" />
              <text x={lineEndX + 6} y={midY - 3} fill="var(--dash-ink)" fontSize="12" fontWeight="600">{item.stage}</text>
              <text x={lineEndX + 6} y={midY + 11} fill="var(--dash-muted)" fontSize="10">{item.detail}</text>
              {dropPct !== null && (
                <text x={cx + nextHalfW + 12} y={y + sliceH + gap / 2 + 5} fill="var(--dash-muted)" fontSize="11">
                  {dropPct}% conversion
                </text>
              )}
            </g>
          );
        })}
        {funnelData.length > 0 && (() => {
          const lastIdx = funnelData.length - 1;
          const lastLogVal = Math.log10((funnelData[lastIdx]?.value || 1) + 1);
          const lastHalfW = Math.max(35, Math.round((lastLogVal / logMax) * 160));
          const y = lastIdx * (sliceH + gap) + sliceH;
          const cx = 180;
          return <polygon points={`${cx - lastHalfW},${y} ${cx + lastHalfW},${y} ${cx},${y + 24}`} fill={accent} opacity={sliceOpacity[lastIdx % sliceOpacity.length]} />;
        })()}
      </svg>
    </div>
  );
}

/* ═══════════════════════ GA4 COUNTRY MAP ════════════════════════════════ */

const GEO_URL = "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json";

const NUM_TO_ALPHA2 = {
  "840":"US","076":"BR","156":"CN","586":"PK","032":"AR","170":"CO","356":"IN",
  "218":"EC","124":"CA","616":"PL","504":"MA","608":"PH","250":"FR","566":"NG",
  "702":"SG","276":"DE","826":"GB","036":"AU","484":"MX","724":"ES","380":"IT",
  "392":"JP","410":"KR","764":"TH","360":"ID","704":"VN","792":"TR","682":"SA",
  "784":"AE","818":"EG","710":"ZA","012":"DZ","024":"AO","050":"BD",
  "144":"LK","458":"MY","020":"AD","008":"AL","051":"AM",
  "040":"AT","031":"AZ","056":"BE","100":"BG","070":"BA","112":"BY","068":"BO",
  "191":"HR","196":"CY","203":"CZ","208":"DK","231":"ET","246":"FI","268":"GE",
  "300":"GR","320":"GT","332":"HT","348":"HU","352":"IS","368":"IQ","376":"IL",
  "400":"JO","398":"KZ","404":"KE","428":"LV","422":"LB","440":"LT","442":"LU",
  "807":"MK","466":"ML","478":"MR","516":"NA","528":"NL","554":"NZ",
  "578":"NO","512":"OM","591":"PA","604":"PE","630":"PR","634":"QA",
  "642":"RO","643":"RU","686":"SN","694":"SL","703":"SK","705":"SI",
  "706":"SO","729":"SD","752":"SE","756":"CH","760":"SY","158":"TW","834":"TZ",
  "800":"UG","804":"UA","858":"UY","860":"UZ","862":"VE","887":"YE","894":"ZM",
  "716":"ZW",
};

function GA4CountryMap({ countryRows = [] }) {
  const accent = useAccentColor();
  const [tooltip, setTooltip] = useState(null);
  const top = countryRows.slice(0, 50);
  const maxUsers = top[0]?.activeUsers || 1;
  const total = top.reduce((s, r) => s + r.activeUsers, 0);

  const countryMap = useMemo(() => {
    const m = {};
    top.forEach(row => { if (row.countryId) m[row.countryId.toUpperCase()] = row; });
    return m;
  }, [top]);

  function flag(code) {
    if (!code || code.length !== 2) return "🌐";
    return code.toUpperCase().replace(/./g, c =>
      String.fromCodePoint(c.charCodeAt(0) + 127397)
    );
  }

  function getColor(alpha2) {
    const row = countryMap[alpha2?.toUpperCase()];
    if (!row) return "var(--dash-line)";
    const intensity = row.activeUsers / maxUsers;
    if (intensity > 0.5) return accent;
    if (intensity > 0.2) return accent + "cc";
    if (intensity > 0.1) return accent + "88";
    if (intensity > 0.05) return accent + "55";
    return accent + "33";
  }

  return (
    <div className="ga4-country-layout">
      <div className="ga4-world-map">
        <ComposableMap width={800} height={400} projectionConfig={{ scale: 150 }} style={{ width: "100%", height: "auto" }}>
          <ZoomableGroup zoom={1}>
            <Geographies geography={GEO_URL}>
              {({ geographies }) =>
                geographies.map((geo) => {
                  const numId = String(geo.id).padStart(3, "0");
                  const alpha2 = NUM_TO_ALPHA2[numId];
                  const row = alpha2 ? countryMap[alpha2] : null;
                  return (
                    <Geography
                      key={geo.rsmKey}
                      geography={geo}
                      fill={getColor(alpha2)}
                      stroke="var(--paper)"
                      strokeWidth={0.5}
                      style={{
                        default: { outline: "none" },
                        hover: { outline: "none", fill: row ? "#ea580c" : "var(--dash-line)", cursor: row ? "pointer" : "default" },
                        pressed: { outline: "none" },
                      }}
                      onMouseEnter={() => row && setTooltip(row)}
                      onMouseLeave={() => setTooltip(null)}
                    />
                  );
                })
              }
            </Geographies>
          </ZoomableGroup>
        </ComposableMap>
        {tooltip && (
          <div className="ga4-map-tooltip">
            <strong>{flag(tooltip.countryId)} {tooltip.country}</strong>
            <span>{compact(tooltip.activeUsers)} users</span>
          </div>
        )}
      </div>

      <div className="ga4-country-list">
        {top.slice(0, 10).map((row, i) => {
          const pct = Math.round((row.activeUsers / maxUsers) * 100);
          const sharePct = total ? Math.round((row.activeUsers / total) * 100) : 0;
          return (
            <div key={row.countryId || row.country} className="ga4-country-item">
              <span className="ga4-country-rank">{i + 1}</span>
              <span className="ga4-country-flag">{flag(row.countryId)}</span>
              <span style={{ fontSize: 12, color: "var(--dash-ink-2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.country}</span>
              <span style={{ fontSize: 11, color: "var(--dash-muted)", textAlign: "right" }}>{sharePct}%</span>
              <span style={{ fontSize: 12, fontWeight: 700, color: "var(--dash-ink)", textAlign: "right" }}>{compact(row.activeUsers)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PropertyDiagnostics({ diagnostics = [], selectedId, onSelect }) {
  if (!diagnostics.length) return null;
  return (
    <div className="analytics-property-diagnostics">
      <div className="analytics-property-diagnostics-head">
        <strong>GA4 property check</strong>
        <span>{diagnostics.length} properties found</span>
      </div>
      <div className="analytics-property-list">
        {diagnostics.map((property) => {
          const active = property.id === selectedId;
          const hasData = Boolean(property.has_data);
          return (
            <button
              key={property.id}
              type="button"
              className={`analytics-property-row ${active ? "active" : ""} ${hasData ? "has-data" : "empty"}`}
              onClick={() => onSelect(property.id)}
            >
              <span>
                <strong>{property.name}</strong>
                <em>{property.account} - {property.id}</em>
              </span>
              <b>{property.error ? "API error" : hasData ? `${compact(property.active_users)} users - ${compact(property.sessions)} sessions` : "0 rows"}</b>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function GA4Dashboard({ client, source, onIntegrations }) {
  const accent = useAccentColor();
  const GA4_CHARTS = [
    { key: "traffic", label: "Traffic & active users" },
    { key: "device", label: "Device mix" },
    { key: "channels", label: "Acquisition channels" },
    { key: "pages", label: "Top pages" },
    { key: "newusers", label: "New users trend" },
    { key: "convrate", label: "Conversion rate" },
    { key: "organic", label: "Organic traffic" },
    { key: "funnel", label: "Conversion funnel" },
    { key: "country", label: "Users by country" },
    { key: "events", label: "Events & key events" },
  ];
  const { visible: visibleCharts, toggle: toggleChart, showAll: showAllCharts, isVisible } = useChartFilter("ga4-charts", GA4_CHARTS.map(c => c.key));
  const [days, setDays] = useState("30");
  const [propertyId, setPropertyId] = useState("");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const { data, loading, error } = useDashboardData(
    () => getGA4Dashboard({ clientId: client?.id, clientName: client?.name, days: Number(days), propertyId: propertyId || undefined, customStart: customStart || undefined, customEnd: customEnd || undefined, forceRefresh: refreshKey > 0 }),
    [client?.id, client?.name, days, propertyId, refreshKey, customStart, customEnd],
  );

  const cards = data?.cards?.length ? data.cards : [
    { label: "Active users", value: "-", detail: "Connect GA4 to load live data", tone: "blue" },
    { label: "New users", value: "-", detail: "Waiting for property access", tone: "green" },
    { label: "Sessions", value: "-", detail: "No report loaded yet", tone: "violet" },
    { label: "Engagement rate", value: "-", detail: "No report loaded yet", tone: "amber" },
    { label: "Key events", value: "-", detail: "No report loaded yet", tone: "rose" },
    { label: "Total revenue", value: "-", detail: "No report loaded yet", tone: "emerald" },
  ];

  const trendRows = data?.trend || [];
  const channelRows = data?.channels || [];
  const pageRows = data?.pages || [];
  const deviceRows = data?.devices || [];
  const eventRows = data?.events || [];
  const countryRows = data?.countries || [];
  const organicSessions = data?.organic_sessions || 0;
  const conversionRate = data?.conversion_rate || 0;
  const hasRows = Boolean(trendRows.length || channelRows.length || pageRows.length || deviceRows.length || eventRows.length);
  const showEmpty = !loading && !hasRows;
  const totalDeviceUsers = useMemo(() => deviceRows.reduce((sum, row) => sum + row.activeUsers, 0), [deviceRows]);
  const deviceChartRows = useMemo(() => deviceRows.map((row, index) => ({
    ...row,
    key: deviceKey(row.device),
    fill: GA4_DEVICE_CONFIG[deviceKey(row.device)]?.color || DEVICE_COLORS[index % DEVICE_COLORS.length],
    share: totalDeviceUsers ? Math.round((row.activeUsers / totalDeviceUsers) * 100) : 0,
  })), [deviceRows, totalDeviceUsers]);
  const [activeDevice, setActiveDevice] = useState("");
  useEffect(() => {
    if (!deviceChartRows.length) { setActiveDevice(""); return; }
    if (!deviceChartRows.some((row) => row.device === activeDevice)) setActiveDevice(deviceChartRows[0].device);
  }, [activeDevice, deviceChartRows]);
  const activeDeviceIndex = Math.max(0, deviceChartRows.findIndex((row) => row.device === activeDevice));
  const selectedDevice = deviceChartRows[activeDeviceIndex] || deviceChartRows[0];

  return (
    <div className={`analytics-dashboard bs-dash ga4-dash${loading && data ? " is-refreshing" : ""}`}>
      <DashboardToolbar
        source={source}
        context={data?.selected_property?.name}
        days={days}
        onDaysChange={(v) => { setDays(v); setCustomStart(""); setCustomEnd(""); }}
        customStart={customStart} customEnd={customEnd}
        onCustomChange={(s, e) => { setCustomStart(s); setCustomEnd(e); setRefreshKey(k => k+1); }}
        charts={GA4_CHARTS}
        chartFilter={{ visible: visibleCharts, toggle: toggleChart, showAll: showAllCharts }}
        onRefresh={() => { setRefreshKey((k) => k + 1); }}
        loading={loading}
      >
        <Select value={propertyId || "auto"} onValueChange={(value) => setPropertyId(value === "auto" ? "" : value)}>
          <SelectTrigger className="analytics-select wide"><SelectValue placeholder="Auto property" /></SelectTrigger>
          <SelectContent position="popper" align="start" sideOffset={6} className="bs-select-content">
            <SelectItem value="auto">Auto property</SelectItem>
            {(data?.properties || []).map((property) => (
              <SelectItem key={property.id} value={property.id}>{property.name}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </DashboardToolbar>

      <AlertBanner error={error} onAction={onIntegrations} />
      <DashboardLoadingBar loading={loading && data} />

      <section className="kpi-grid bs-kpis">
        {cards.map((card) => <KpiCard key={card.label} card={card} firstLoad={loading && !data} />)}
      </section>

      {showEmpty && (
        <>
          <EmptyPanel
            title="No GA4 dashboard rows loaded yet"
            message={data?.empty_reason || error || "GA4 is connected, but this property returned no rows for this range. Try Last 90 days or choose a property below that has users/sessions."}
            actionLabel="Open GA4 setup" onAction={onIntegrations}
          />
          <PropertyDiagnostics diagnostics={data?.property_diagnostics || []} selectedId={data?.selected_property?.id} onSelect={setPropertyId} />
        </>
      )}

      {(isVisible("traffic") || isVisible("device")) && (
        <section className="analytics-grid-main">
          {isVisible("traffic") && (
            <div className="analytics-panel analytics-panel-large">
              <PanelHeader title="Traffic and active users" subtitle={`${data?.date_range?.start || "Start"} to ${data?.date_range?.end || "today"}`} badge="event time" />
              {loading ? <Skeleton className="analytics-chart-skeleton" /> : trendRows.length ? (
                <ChartContainer config={GA4_TREND_CONFIG} className="analytics-chart tall shadcn-line-chart">
                  <LineChart data={trendRows} margin={{ left: 0, right: 16, top: 8, bottom: 4 }}>
                    <CartesianGrid vertical={false} strokeDasharray="3 7" />
                    <XAxis dataKey="date" tickFormatter={prettyDate} tickLine={false} axisLine={false} tickMargin={10} minTickGap={30} />
                    <YAxis tickLine={false} axisLine={false} tickMargin={10} tickFormatter={compact} width={42} domain={["auto", "auto"]} />
                    <ChartTooltip cursor={false} content={<ChartTooltipContent labelFormatter={prettyDate} formatter={compact} />} />
                    <ChartLegend verticalAlign="bottom" content={<ChartLegendContent />} />
                    <Line dataKey="sessions" type="monotone" stroke="var(--color-sessions)" strokeWidth={2.8} dot={false} activeDot={{ r: 5 }} />
                    <Line dataKey="activeUsers" type="monotone" stroke="var(--color-activeUsers)" strokeWidth={2.8} dot={false} activeDot={{ r: 5 }} />
                  </LineChart>
                </ChartContainer>
              ) : <ChartEmpty title="No traffic rows" message="GA4 returned no daily traffic rows for this range. Try Last 90 days or another property." />}
            </div>
          )}

          {isVisible("device") && (
            <div className="analytics-panel analytics-device-card">
              <div className="analytics-panel-head device-head">
                <div><h3>Device mix</h3><p>Active users by device</p></div>
                {deviceChartRows.length ? (
                  <Select value={activeDevice || deviceChartRows[0]?.device} onValueChange={setActiveDevice}>
                    <SelectTrigger className="device-select primary-btn"><SelectValue placeholder="Select device" /></SelectTrigger>
                    <SelectContent position="popper" align="end" sideOffset={6} className="bs-select-content">
                      {deviceChartRows.map((row) => (<SelectItem key={row.device} value={row.device}>{row.device}</SelectItem>))}
                    </SelectContent>
                  </Select>
                ) : null}
              </div>
              {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : deviceChartRows.length ? (
                <div className="device-donut-card">
                  <ChartContainer config={GA4_DEVICE_CONFIG} className="device-donut-chart">
                    <PieChart>
                      <ChartTooltip cursor={false} content={<ChartTooltipContent nameKey="device" formatter={(value) => `${compact(value)} users`} />} />
                      <Pie data={deviceChartRows} dataKey="activeUsers" nameKey="device" innerRadius="55%" outerRadius="85%" activeIndex={activeDeviceIndex} activeShape={renderActiveDeviceShape} paddingAngle={2} strokeWidth={4}>
                        {deviceChartRows.map((entry) => <Cell key={entry.device} fill={entry.fill} />)}
                      </Pie>
                      <text x="50%" y="47%" textAnchor="middle" dominantBaseline="middle" className="device-donut-total">{compact(selectedDevice?.activeUsers || totalDeviceUsers)}</text>
                      <text x="50%" y="58%" textAnchor="middle" dominantBaseline="middle" className="device-donut-label">{selectedDevice?.device || "Users"}</text>
                    </PieChart>
                  </ChartContainer>
                  <div className="device-donut-legend">
                    {deviceChartRows.map((item) => (
                      <button type="button" className={item.device === selectedDevice?.device ? "active" : ""} key={item.device} onClick={() => setActiveDevice(item.device)}>
                        <span><b style={{ background: item.fill }} />{item.device}</span>
                        <strong>{compact(item.activeUsers)} <em>{item.share}%</em></strong>
                      </button>
                    ))}
                  </div>
                </div>
              ) : <ChartEmpty title="No device data" message="No device rows were returned by GA4 for this range." />}
            </div>
          )}
        </section>
      )}

      <section className="analytics-grid-tables">
        <div className="analytics-panel" style={{display: isVisible("channels") ? undefined : "none"}}><PanelHeader title="Acquisition channels" subtitle="Where valuable sessions originate" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : channelRows.length ? (
            <ChartContainer config={GA4_CHANNEL_CONFIG} className="analytics-chart channels">
              <BarChart data={channelRows} layout="vertical" margin={{ left: 12, right: 12 }}>
                <CartesianGrid horizontal={false} strokeDasharray="4 6" />
                <XAxis type="number" hide />
                <YAxis dataKey="channel" type="category" width={120} tickLine={false} axisLine={false} />
                <ChartTooltip content={<ChartTooltipContent formatter={compact} />} />
                <Bar dataKey="sessions" fill={accent} radius={[0, 10, 10, 0]} />
              </BarChart>
            </ChartContainer>
          ) : <ChartEmpty title="No channel data" />}
          <div className="bs-table"><DataTable loading={loading} columns={ga4ChannelCols} data={channelRows} emptyMessage="No channel data for this range." /></div>
        </div>
        <div className="analytics-panel" style={{display: isVisible("pages") ? undefined : "none"}}><PanelHeader title="Top pages" subtitle="Pages pulling attention and actions" />
          <div className="bs-table"><DataTable loading={loading} columns={ga4PageCols} data={pageRows} emptyMessage="No page data for this range." /></div>
        </div>
      </section>

      <section className="analytics-grid-tables">
        <div className="analytics-panel" style={{display: isVisible("newusers") ? undefined : "none"}}><PanelHeader title="New users trend" subtitle="First-time visitors over time" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : trendRows.length ? (
            <ChartContainer config={GA4_NEW_USERS_CONFIG} className="analytics-chart channels" style={{"--bar-fill": accent}}>
              <BarChart data={trendRows} margin={{ left: 12, right: 12, top: 4, bottom: 4 }}>
                <CartesianGrid vertical={false} strokeDasharray="4 6" />
                <XAxis dataKey="date" tickFormatter={prettyDate} tickLine={false} axisLine={false} tickMargin={8} minTickGap={20} />
                <YAxis tickLine={false} axisLine={false} tickMargin={8} tickFormatter={compact} width={38} />
                <ChartTooltip content={<ChartTooltipContent labelFormatter={prettyDate} formatter={compact} />} />
                <Bar dataKey="newUsers" fill={accent} opacity={0.85} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ChartContainer>
          ) : <ChartEmpty title="No new user data" message="No new user rows for this range." />}
        </div>

        <div className="analytics-panel" style={{display: isVisible("convrate") ? undefined : "none"}}><PanelHeader title="Conversion rate" subtitle="Key events ÷ sessions per day (%)" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : trendRows.length ? (
            <ChartContainer config={GA4_CONVERSION_CONFIG} className="analytics-chart channels">
              <LineChart data={trendRows} margin={{ left: 12, right: 22, top: 4, bottom: 4 }}>
                <CartesianGrid vertical={false} strokeDasharray="3 7" />
                <XAxis dataKey="date" tickFormatter={prettyDate} tickLine={false} axisLine={false} tickMargin={8} minTickGap={20} />
                <YAxis tickLine={false} axisLine={false} tickMargin={8} tickFormatter={(v) => `${v}%`} width={42} />
                <ChartTooltip cursor={false} content={<ChartTooltipContent labelFormatter={prettyDate} formatter={(v) => `${v}%`} />} />
                <Line dataKey="conversionRate" type="monotone" stroke="var(--color-conversionRate)" strokeWidth={2.8} dot={false} activeDot={{ r: 5 }} />
              </LineChart>
            </ChartContainer>
          ) : <ChartEmpty title="No conversion data" message="No conversion rate data for this range." />}
          {!loading && conversionRate > 0 && (
            <div className="ga4-conv-summary">Overall rate: <strong>{conversionRate.toFixed(2)}%</strong></div>
          )}
        </div>
      </section>

      <section className="analytics-grid-tables">
        <div className="analytics-panel" style={{display: isVisible("funnel") ? undefined : "none"}}><PanelHeader title="Conversion funnel" subtitle="Sessions → Active Users → Key Events → Revenue" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : (
            <GA4ConversionFunnel data={data} />
          )}
        </div>
        <div className="analytics-panel" style={{display: isVisible("organic") ? undefined : "none"}}><PanelHeader title="Organic traffic" subtitle="Sessions from organic search channel" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : trendRows.length ? (
            <ChartContainer config={GA4_ORGANIC_CONFIG} className="analytics-chart" style={{height:"180px"}}>
              <BarChart data={channelRows.filter((r) => r.channel?.toLowerCase().includes("organic"))} layout="vertical" margin={{ left: 12, right: 12 }}>
                <CartesianGrid horizontal={false} strokeDasharray="4 6" />
                <XAxis type="number" hide />
                <YAxis dataKey="channel" type="category" width={130} tickLine={false} axisLine={false} />
                <ChartTooltip content={<ChartTooltipContent formatter={compact} />} />
                <Bar dataKey="sessions" fill={accent} radius={[0, 8, 8, 0]} maxBarSize={36} />
              </BarChart>
            </ChartContainer>
          ) : <ChartEmpty title="No organic data" message="No organic traffic rows found for this range." />}
          {!loading && organicSessions > 0 && (
            <div className="ga4-conv-summary">Organic sessions: <strong>{compact(organicSessions)}</strong></div>
          )}
        </div>
      </section>

      <section className="analytics-panel" style={{display: isVisible("country") ? undefined : "none"}}>
        <PanelHeader title="Users by country" subtitle="Top countries by active users" />
        {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : countryRows.length ? (
          <GA4CountryMap countryRows={countryRows} />
        ) : <ChartEmpty title="No country data" message="No country breakdown returned for this range." />}
      </section>

      <section className="analytics-panel" style={{display: isVisible("events") ? undefined : "none"}}>
        <PanelHeader title="Events and key events" subtitle="The actions GA4 is seeing most often" />
        <div className="bs-table"><DataTable loading={loading} columns={ga4EventCols} data={eventRows} emptyMessage="No event data for this range." /></div>
      </section>
    </div>
  );
}

/* ═══════════════════════ GOOGLE ADS DASHBOARD ═══════════════════════ */

function GoogleAdsFunnel({ funnelData = [], costPerConversion = 0 }) {
  const accent = useAccentColor();
  const max = funnelData[0]?.value || 1;
  const W = 500;
  const sliceH = 90;
  const gap = 6;
  const labelX = W - 10;
  const totalH = funnelData.length * sliceH + (funnelData.length - 1) * gap + 40;
  const sliceOpacity = ["1", "0.75", "0.5", "0.35"];
  const logMax = Math.log10(max + 1);

  return (
    <div className="gads-svg-funnel">
      <svg viewBox={`0 0 ${W} ${totalH}`} width="100%" style={{ overflow: "visible" }}>
        {funnelData.map((item, i) => {
          const logVal = Math.log10(item.value + 1);
          const halfW = Math.max(55, Math.round((logVal / logMax) * 160));
          const nextItem = funnelData[i + 1];
          const nextLogVal = nextItem ? Math.log10(nextItem.value + 1) : logVal * 0.35;
          const nextHalfW = Math.max(35, Math.round((nextLogVal / logMax) * 160));
          const y = i * (sliceH + gap);
          const cx = 180;
          const pts = [
            `${cx - halfW},${y}`,
            `${cx + halfW},${y}`,
            `${cx + nextHalfW},${y + sliceH}`,
            `${cx - nextHalfW},${y + sliceH}`,
          ].join(" ");
          const dropPct = nextItem ? Math.round((nextItem.value / item.value) * 100) : null;
          const midY = y + sliceH / 2;
          const dotX = cx + halfW + 8;
          const lineEndX = labelX - 60;
          return (
            <g key={item.stage}>
              <polygon points={pts} fill="var(--accent)" opacity={sliceOpacity[i % sliceOpacity.length]} />
              <text x={cx} y={midY - 8} textAnchor="middle" fill="white" fontSize="22" fontWeight="700" style={{ fontFamily: "inherit" }}>
                {compact(item.value)}
              </text>
              <circle cx={dotX} cy={midY} r="4" fill="var(--accent)" />
              <line x1={dotX + 4} y1={midY} x2={lineEndX} y2={midY} stroke="var(--accent)" strokeWidth="1.5" strokeDasharray="4 3" />
              <text x={lineEndX + 6} y={midY + 5} fill="var(--dash-ink)" fontSize="13" fontWeight="500">{item.stage}</text>
              {dropPct !== null && (
                <text x={cx + nextHalfW + 12} y={y + sliceH + gap / 2 + 5} fill="var(--dash-muted)" fontSize="11">
                  {dropPct}%
                </text>
              )}
            </g>
          );
        })}
        {funnelData.length > 0 && (() => {
          const lastIdx = funnelData.length - 1;
          const lastLogVal = Math.log10((funnelData[lastIdx]?.value || 1) + 1);
          const lastHalfW = Math.max(35, Math.round((lastLogVal / logMax) * 160));
          const y = lastIdx * (sliceH + gap) + sliceH;
          const cx = 180;
          return (
            <polygon points={`${cx - lastHalfW},${y} ${cx + lastHalfW},${y} ${cx},${y + 28}`} fill="var(--accent)" opacity="0.5" />
          );
        })()}
      </svg>
      {costPerConversion > 0 && (
        <div className="gads-funnel-cpc">Cost per conversion: <strong>{moneyPrecise(costPerConversion)}</strong></div>
      )}
    </div>
  );
}

function GoogleAdsDashboard({ client, source, onIntegrations }) {
  const accent = useAccentColor();
  const GADS_CHARTS = [
    { key: "trend", label: "Performance trend" },
    { key: "device", label: "Device breakdown" },
    { key: "impressions", label: "Impressions by month" },
    { key: "funnel", label: "Performance funnel" },
    { key: "cpm", label: "Impressions & CPM" },
    { key: "cpc", label: "Clicks & CPC" },
    { key: "cpl", label: "Conversions & CPL" },
    { key: "conversion_actions", label: "Conversion actions" },
    { key: "keywords", label: "Keyword performance" },
    { key: "click_types", label: "Click type performance" },
    { key: "campaigns", label: "Campaign performance" },
  ];
  const { visible: visibleCharts, toggle: toggleChart, showAll: showAllCharts, isVisible } = useChartFilter("gads-charts", GADS_CHARTS.map(c => c.key));
  const [days, setDays] = useState("30");
  const [customerId, setCustomerId] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const { data, loading, error } = useDashboardData(
    () => getGoogleAdsDashboard({ clientId: client?.id, clientName: client?.name, days: Number(days), customerId: customerId || undefined, forceRefresh: refreshKey > 0 }),
    [client?.id, client?.name, days, customerId, refreshKey],
  );

  const cards = data?.cards?.length ? data.cards : [
    { label: "Impressions", value: "-", detail: "Connect Google Ads", tone: "blue" },
    { label: "Clicks", value: "-", detail: "Waiting for data", tone: "green" },
    { label: "CTR", value: "-", detail: "Waiting for data", tone: "violet" },
    { label: "Avg CPC", value: "-", detail: "Waiting for data", tone: "amber" },
    { label: "Cost", value: "-", detail: "Waiting for data", tone: "rose" },
    { label: "Conversions", value: "-", detail: "Waiting for data", tone: "emerald" },
  ];

  const trendRows = data?.trend || [];
  const campaignRows = data?.campaigns || [];
  const deviceRows = data?.devices || [];
  const impressionsMonthly = data?.impressions_monthly || [];
  const funnelData = data?.funnel || [];
  const costPerConversion = data?.cost_per_conversion || 0;
  const conversionActions = data?.conversion_actions || [];
  const keywords = data?.keywords || [];
  const clickTypes = data?.click_types || [];
  const hasRows = Boolean(trendRows.length || campaignRows.length);

  return (
    <div className={`analytics-dashboard bs-dash${loading && data ? " is-refreshing" : ""}`}>
      <DashboardToolbar
        source={source}
        context={data?.customer_id ? `Customer ${data.customer_id}` : undefined}
        days={days} onDaysChange={setDays}
        allowedDays={ADS_PRESET_DAYS}
        charts={GADS_CHARTS}
        chartFilter={{ visible: visibleCharts, toggle: toggleChart, showAll: showAllCharts }}
        onRefresh={() => setRefreshKey(k => k+1)}
        loading={loading}
      >
        {(data?.customers?.length || 0) > 1 && (
          <Select value={customerId || "auto"} onValueChange={(value) => setCustomerId(value === "auto" ? "" : value)}>
            <SelectTrigger className="analytics-select wide"><SelectValue placeholder="Auto account" /></SelectTrigger>
            <SelectContent position="popper" align="start" sideOffset={6} className="bs-select-content">
              <SelectItem value="auto">Auto account</SelectItem>
              {data.customers.map((c) => (
                <SelectItem key={c.id} value={c.id}>{c.name}{c.name !== c.id ? ` (${c.id})` : ""}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </DashboardToolbar>

      <AlertBanner error={error} onAction={onIntegrations} />
      <DashboardLoadingBar loading={loading && data} />

      <section className="kpi-grid bs-kpis">
        {cards.map((card) => <KpiCard key={card.label} card={card} firstLoad={loading && !data} />)}
      </section>

      {!loading && !hasRows && (
        <EmptyPanel
          title="No Google Ads data loaded yet"
          message={data?.reason || error || "Google Ads is connected but returned no data for this period. Check that the account has active campaigns."}
          actionLabel="Open Integrations" onAction={onIntegrations}
        />
      )}

      <section className="analytics-grid-main">
        <div className="analytics-panel analytics-panel-large" style={{display: isVisible("trend") ? undefined : "none"}}>
          <PanelHeader title="Performance trend" subtitle={`Daily clicks and conversions — last ${days} days`} badge="daily" />
          {loading ? <Skeleton className="analytics-chart-skeleton" /> : trendRows.length ? (
            <ChartContainer config={GADS_TREND_CONFIG} className="analytics-chart tall shadcn-line-chart">
              <LineChart data={trendRows} margin={{ left: 0, right: 16, top: 8, bottom: 4 }}>
                <CartesianGrid vertical={false} strokeDasharray="3 7" />
                <XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false} tickMargin={10} minTickGap={30} />
                <YAxis tickLine={false} axisLine={false} tickMargin={10} tickFormatter={compact} width={42} />
                <ChartTooltip cursor={false} content={<ChartTooltipContent labelFormatter={shortDate} formatter={compact} />} />
                <ChartLegend verticalAlign="bottom" content={<ChartLegendContent />} />
                <Line dataKey="clicks" type="monotone" stroke="var(--color-clicks)" strokeWidth={2.8} dot={false} activeDot={{ r: 5 }} />
                <Line dataKey="conversions" type="monotone" stroke="var(--color-conversions)" strokeWidth={2.8} dot={false} activeDot={{ r: 5 }} />
              </LineChart>
            </ChartContainer>
          ) : <ChartEmpty title="No trend data" message="No daily performance rows returned for this range." />}
        </div>

        <div className="analytics-panel" style={{display: isVisible("device") ? undefined : "none"}}>
          <PanelHeader title="Device breakdown" subtitle="Clicks by device type" />
          <GoogleAdsDeviceBarChart devices={deviceRows} loading={loading} />
          <div className="bs-table"><DataTable loading={loading} columns={gadsDeviceCols} data={deviceRows} emptyMessage="No device data for this range." /></div>
        </div>
      </section>

      <section className="analytics-grid-tables">
        <div className="analytics-panel" style={{display: isVisible("impressions") ? undefined : "none"}}>
          <PanelHeader title="Impressions by month" subtitle="Total ad impressions aggregated monthly" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : impressionsMonthly.length ? (
            <ChartContainer config={GADS_IMPRESSIONS_CONFIG} className="analytics-chart channels">
              <BarChart data={impressionsMonthly} margin={{ left: 12, right: 12, top: 4, bottom: 4 }}>
                <CartesianGrid vertical={false} strokeDasharray="4 6" />
                <XAxis dataKey="month" tickLine={false} axisLine={false} tickMargin={8} />
                <YAxis tickLine={false} axisLine={false} tickMargin={8} tickFormatter={compact} width={42} />
                <ChartTooltip content={<ChartTooltipContent formatter={compact} />} />
                <Bar dataKey="impressions" fill={accent} radius={[6, 6, 0, 0]} />
              </BarChart>
            </ChartContainer>
          ) : <ChartEmpty title="No impressions data" message="No monthly impressions data for this range." />}
        </div>

        <div className="analytics-panel" style={{display: isVisible("funnel") ? undefined : "none"}}>
          <PanelHeader title="Performance funnel" subtitle="Impressions → Clicks → Conversions" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : funnelData.length ? (
            <GoogleAdsFunnel funnelData={funnelData} costPerConversion={costPerConversion} />
          ) : <ChartEmpty title="No funnel data" message="No funnel data available for this range." />}
        </div>
      </section>

      <section className="analytics-grid-tables">
        <div className="analytics-panel" style={{display: isVisible("cpm") ? undefined : "none"}}>
          <PanelHeader title="Impressions & CPM" subtitle="Daily impressions vs cost per thousand" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : trendRows.length ? (
            <ChartContainer config={GADS_CPM_CONFIG} className="analytics-chart channels">
              <BarChart data={trendRows} margin={{ left: 12, right: 40, top: 4, bottom: 4 }}>
                <CartesianGrid vertical={false} strokeDasharray="4 6" />
                <XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false} tickMargin={8} minTickGap={20} />
                <YAxis yAxisId="left" tickLine={false} axisLine={false} tickFormatter={compact} width={42} />
                <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} tickFormatter={(v) => `$${v}`} width={42} />
                <ChartTooltip content={<ChartTooltipContent formatter={compact} />} />
                <ChartLegend verticalAlign="bottom" content={<ChartLegendContent />} />
                <Bar yAxisId="left" dataKey="impressions" fill={accent} radius={[4,4,0,0]} opacity={0.7} />
                <Line yAxisId="right" dataKey="cpm" type="monotone" stroke="var(--color-cpm)" strokeWidth={2.5} dot={false} />
              </BarChart>
            </ChartContainer>
          ) : <ChartEmpty title="No data" message="No trend data for this range." />}
        </div>

        <div className="analytics-panel" style={{display: isVisible("cpc") ? undefined : "none"}}>
          <PanelHeader title="Clicks & CPC" subtitle="Daily clicks vs cost per click" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : trendRows.length ? (
            <ChartContainer config={GADS_CPC_CONFIG} className="analytics-chart channels">
              <BarChart data={trendRows} margin={{ left: 12, right: 40, top: 4, bottom: 4 }}>
                <CartesianGrid vertical={false} strokeDasharray="4 6" />
                <XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false} tickMargin={8} minTickGap={20} />
                <YAxis yAxisId="left" tickLine={false} axisLine={false} tickFormatter={compact} width={38} />
                <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} tickFormatter={(v) => `$${v}`} width={42} />
                <ChartTooltip content={<ChartTooltipContent formatter={compact} />} />
                <ChartLegend verticalAlign="bottom" content={<ChartLegendContent />} />
                <Bar yAxisId="left" dataKey="clicks" fill={accent} radius={[4,4,0,0]} opacity={0.8} />
                <Line yAxisId="right" dataKey="cpc" type="monotone" stroke="var(--color-cpc)" strokeWidth={2.5} dot={false} />
              </BarChart>
            </ChartContainer>
          ) : <ChartEmpty title="No data" message="No trend data for this range." />}
        </div>
      </section>

      <section className="analytics-grid-tables">
        <div className="analytics-panel" style={{display: isVisible("cpl") ? undefined : "none"}}>
          <PanelHeader title="Conversions & CPL" subtitle="Daily conversions vs cost per lead" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : trendRows.length ? (
            <ChartContainer config={GADS_CPL_CONFIG} className="analytics-chart channels">
              <BarChart data={trendRows} margin={{ left: 12, right: 40, top: 4, bottom: 4 }}>
                <CartesianGrid vertical={false} strokeDasharray="4 6" />
                <XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false} tickMargin={8} minTickGap={20} />
                <YAxis yAxisId="left" tickLine={false} axisLine={false} tickFormatter={compact} width={38} />
                <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} tickFormatter={(v) => `$${v}`} width={42} />
                <ChartTooltip content={<ChartTooltipContent formatter={compact} />} />
                <ChartLegend verticalAlign="bottom" content={<ChartLegendContent />} />
                <Bar yAxisId="left" dataKey="conversions" fill={accent} radius={[4,4,0,0]} opacity={0.8} />
                <Line yAxisId="right" dataKey="cpl" type="monotone" stroke="var(--color-cpl)" strokeWidth={2.5} dot={false} />
              </BarChart>
            </ChartContainer>
          ) : <ChartEmpty title="No data" message="No trend data for this range." />}
        </div>
        <div className="analytics-panel" style={{display: isVisible("conversion_actions") ? undefined : "none"}}>
          <PanelHeader title="Conversion actions" subtitle="Breakdown by conversion type" />
          <div className="bs-table"><DataTable loading={loading} columns={gadsConversionCols} data={conversionActions} emptyMessage="No conversion data." /></div>
        </div>
      </section>

      <section className="analytics-grid-tables">
        <div className="analytics-panel" style={{display: isVisible("keywords") ? undefined : "none"}}>
          <PanelHeader title="Keyword performance" subtitle="Top keywords by clicks" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : keywords.length ? (
            <ChartContainer config={GADS_KEYWORD_CONFIG} className="analytics-chart channels">
              <BarChart data={keywords.slice(0, 10)} layout="vertical" margin={{ left: 12, right: 24, top: 4, bottom: 4 }}>
                <CartesianGrid horizontal={false} strokeDasharray="4 6" />
                <XAxis type="number" hide />
                <YAxis dataKey="keyword" type="category" width={120} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} />
                <ChartTooltip content={<ChartTooltipContent formatter={compact} />} />
                <Bar dataKey="clicks" fill={accent} radius={[0, 8, 8, 0]} />
              </BarChart>
            </ChartContainer>
          ) : <ChartEmpty title="No keyword data" message="No keyword data returned for this range." />}
          {keywords.length > 0 && (
            <div className="bs-table"><DataTable loading={loading} columns={gadsKeywordCols} data={keywords} emptyMessage="No keywords." /></div>
          )}
        </div>

        <div className="analytics-panel" style={{display: isVisible("click_types") ? undefined : "none"}}>
          <PanelHeader title="Click type performance" subtitle="Clicks breakdown by interaction type" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : clickTypes.length ? (
            <ChartContainer config={GADS_CLICK_TYPE_CONFIG} className="analytics-chart channels">
              <BarChart data={clickTypes} layout="vertical" margin={{ left: 12, right: 24, top: 4, bottom: 4 }}>
                <CartesianGrid horizontal={false} strokeDasharray="4 6" />
                <XAxis type="number" hide />
                <YAxis dataKey="clickType" type="category" width={130} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} />
                <ChartTooltip content={<ChartTooltipContent formatter={compact} />} />
                <Bar dataKey="clicks" fill={accent} radius={[0, 8, 8, 0]} maxBarSize={36} />
              </BarChart>
            </ChartContainer>
          ) : <ChartEmpty title="No click type data" message="No click type data returned for this range." />}
          {clickTypes.length > 0 && (
            <div className="bs-table"><DataTable loading={loading} columns={gadsClickTypeCols} data={clickTypes} emptyMessage="No click types." /></div>
          )}
        </div>
      </section>

      <section className="analytics-panel" style={{display: isVisible("campaigns") ? undefined : "none"}}>
        <PanelHeader title="Campaign performance" subtitle="Top campaigns by spend" />
        <div className="bs-table"><DataTable loading={loading} columns={gadsCampaignCols} data={campaignRows} emptyMessage="No campaign data for this range." /></div>
      </section>
    </div>
  );
}

/* ═══════════════════════ META ADS DASHBOARD ═══════════════════════── */

function MetaAdsDashboard({ client, source, onIntegrations }) {
  const accent = useAccentColor();
  const METAADS_CHARTS = [
    { key: "trend", label: "Delivery trend" },
    { key: "spend_roas", label: "Spend & ROAS" },
    { key: "cpm_ctr", label: "CPM & CTR" },
    { key: "reach_freq", label: "Reach & Frequency" },
    { key: "funnel", label: "Performance funnel" },
    { key: "campaigns", label: "Campaign performance" },
    { key: "platforms", label: "Platform breakdown" },
  ];
  const { visible: visibleCharts, toggle: toggleChart, showAll: showAllCharts, isVisible } = useChartFilter("meta-charts", METAADS_CHARTS.map(c => c.key));
  const [days, setDays] = useState("30");
  const [accountId, setAccountId] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const { data, loading, error } = useDashboardData(
    () => getMetaAdsDashboard({ clientId: client?.id, clientName: client?.name, days: Number(days), accountId: accountId || undefined, forceRefresh: refreshKey > 0 }),
    [client?.id, client?.name, days, accountId, refreshKey],
  );

  const cards = data?.cards?.length ? data.cards : [
    { label: "Reach", value: "-", detail: "Connect Meta Ads", tone: "blue" },
    { label: "Impressions", value: "-", detail: "Waiting for data", tone: "green" },
    { label: "Clicks", value: "-", detail: "Waiting for data", tone: "violet" },
    { label: "CTR", value: "-", detail: "Waiting for data", tone: "amber" },
    { label: "CPM", value: "-", detail: "Waiting for data", tone: "rose" },
    { label: "Spend", value: "-", detail: "Waiting for data", tone: "emerald" },
  ];

  const trendRows = data?.trend || [];
  const campaignRows = data?.campaigns || [];
  const platformRows = data?.platforms || [];
  const hasRows = Boolean(trendRows.length || campaignRows.length);

  return (
    <div className={`analytics-dashboard bs-dash${loading && data ? " is-refreshing" : ""}`}>
      <DashboardToolbar
        source={source}
        context={data?.account_name || undefined}
        days={days} onDaysChange={setDays}
        allowedDays={ADS_PRESET_DAYS}
        charts={METAADS_CHARTS}
        chartFilter={{ visible: visibleCharts, toggle: toggleChart, showAll: showAllCharts }}
        onRefresh={() => setRefreshKey(k => k+1)}
        loading={loading}
      >
        {(data?.accounts?.length || 0) > 1 && (
          <Select value={accountId || "auto"} onValueChange={(value) => setAccountId(value === "auto" ? "" : value)}>
            <SelectTrigger className="analytics-select wide"><SelectValue placeholder="Auto account" /></SelectTrigger>
            <SelectContent position="popper" align="start" sideOffset={6} className="bs-select-content">
              <SelectItem value="auto">Auto account</SelectItem>
              {data.accounts.map((a) => (
                <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </DashboardToolbar>

      <AlertBanner error={error} onAction={onIntegrations} />
      <DashboardLoadingBar loading={loading && data} />

      <section className="kpi-grid bs-kpis">
        {cards.map((card) => <KpiCard key={card.label} card={card} firstLoad={loading && !data} />)}
      </section>

      {!loading && !hasRows && (
        <EmptyPanel
          title="No Meta Ads data loaded yet"
          message={data?.reason || error || "Meta Ads is connected but returned no data for this period. Check that the ad account has active campaigns."}
          actionLabel="Open Integrations" onAction={onIntegrations}
        />
      )}

      {(isVisible("trend") || isVisible("campaigns")) && <section className="analytics-grid-main">
        <div className="analytics-panel analytics-panel-large" style={{display: isVisible("trend") ? undefined : "none"}}>
          <PanelHeader title="Delivery trend" subtitle={`Daily impressions and clicks — last ${days} days`} badge="daily" />
          {loading ? <Skeleton className="analytics-chart-skeleton" /> : trendRows.length ? (
            <ChartContainer config={META_TREND_CONFIG} className="analytics-chart shadcn-line-chart" style={{ height: 200, aspectRatio: "unset" }}>
              <LineChart data={trendRows} margin={{ left: 0, right: 16, top: 8, bottom: 4 }}>
                <CartesianGrid vertical={false} strokeDasharray="3 7" />
                <XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false} tickMargin={10} minTickGap={30} />
                <YAxis tickLine={false} axisLine={false} tickMargin={10} tickFormatter={compact} width={42} />
                <ChartTooltip cursor={false} content={<ChartTooltipContent labelFormatter={shortDate} formatter={compact} />} />
                <ChartLegend verticalAlign="bottom" content={<ChartLegendContent />} />
                <Line dataKey="impressions" type="monotone" stroke="var(--color-impressions)" strokeWidth={2.8} dot={false} activeDot={{ r: 5 }} />
                <Line dataKey="clicks" type="monotone" stroke="var(--color-clicks)" strokeWidth={2.8} dot={false} activeDot={{ r: 5 }} />
              </LineChart>
            </ChartContainer>
          ) : <ChartEmpty title="No trend data" message="No daily delivery rows returned for this range." />}
        </div>
      </section>}

      <section className="analytics-grid-tables">
        <div className="analytics-panel" style={{display: isVisible("spend_roas") ? undefined : "none"}}>
          <PanelHeader title="Spend & ROAS" subtitle="Daily ad spend vs return on ad spend" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : trendRows.length ? (
            <ChartContainer config={META_SPEND_ROAS_CONFIG} className="analytics-chart channels">
              <BarChart data={trendRows} margin={{ left: 0, right: 48, top: 4, bottom: 4 }}>
                <CartesianGrid vertical={false} strokeDasharray="4 6" />
                <XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false} tickMargin={8} minTickGap={20} />
                <YAxis yAxisId="left" tickLine={false} axisLine={false} tickFormatter={v => `$${compact(v)}`} width={48} />
                <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} tickFormatter={v => `${v.toFixed(1)}x`} width={48} />
                <ChartTooltip content={<ChartTooltipContent labelFormatter={shortDate} formatter={compact} />} />
                <ChartLegend verticalAlign="bottom" content={<ChartLegendContent />} />
                <Bar yAxisId="left" dataKey="spend" fill={accent} radius={[4,4,0,0]} opacity={0.85} />
                <Line yAxisId="right" dataKey="roas" type="monotone" stroke="#14b8a6" strokeWidth={2.5} dot={false} />
              </BarChart>
            </ChartContainer>
          ) : <ChartEmpty title="No data" message="No spend data for this range." />}
        </div>
        <div className="analytics-panel" style={{display: isVisible("cpm_ctr") ? undefined : "none"}}>
          <PanelHeader title="CPM & CTR" subtitle="Cost per thousand vs click-through rate" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : trendRows.length ? (
            <ChartContainer config={META_CPM_CTR_CONFIG} className="analytics-chart channels">
              <LineChart data={trendRows} margin={{ left: 0, right: 48, top: 4, bottom: 4 }}>
                <CartesianGrid vertical={false} strokeDasharray="4 6" />
                <XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false} tickMargin={8} minTickGap={20} />
                <YAxis yAxisId="left" tickLine={false} axisLine={false} tickFormatter={v => `$${v.toFixed(2)}`} width={52} />
                <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} tickFormatter={v => `${v.toFixed(2)}%`} width={52} />
                <ChartTooltip content={<ChartTooltipContent labelFormatter={shortDate} formatter={compact} />} />
                <ChartLegend verticalAlign="bottom" content={<ChartLegendContent />} />
                <Line yAxisId="left" dataKey="cpm" type="monotone" stroke={accent} strokeWidth={2.5} dot={false} />
                <Line yAxisId="right" dataKey="ctr" type="monotone" stroke="#f59e0b" strokeWidth={2.5} dot={false} />
              </LineChart>
            </ChartContainer>
          ) : <ChartEmpty title="No data" message="No CPM/CTR data for this range." />}
        </div>
      </section>

      <section className="analytics-grid-tables">
        <div className="analytics-panel" style={{display: isVisible("reach_freq") ? undefined : "none"}}>
          <PanelHeader title="Reach & Frequency" subtitle="Unique reach vs avg frequency per person" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : trendRows.length ? (
            <ChartContainer config={META_REACH_FREQ_CONFIG} className="analytics-chart channels">
              <BarChart data={trendRows} margin={{ left: 0, right: 48, top: 4, bottom: 4 }}>
                <CartesianGrid vertical={false} strokeDasharray="4 6" />
                <XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false} tickMargin={8} minTickGap={20} />
                <YAxis yAxisId="left" tickLine={false} axisLine={false} tickFormatter={compact} width={42} />
                <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} tickFormatter={v => v.toFixed(1)} width={42} />
                <ChartTooltip content={<ChartTooltipContent labelFormatter={shortDate} formatter={compact} />} />
                <ChartLegend verticalAlign="bottom" content={<ChartLegendContent />} />
                <Bar yAxisId="left" dataKey="reach" fill={accent} radius={[4,4,0,0]} opacity={0.8} />
                <Line yAxisId="right" dataKey="frequency" type="monotone" stroke="#7c3aed" strokeWidth={2.5} dot={false} />
              </BarChart>
            </ChartContainer>
          ) : <ChartEmpty title="No data" message="No reach data for this range." />}
        </div>
        <div className="analytics-panel" style={{display: isVisible("funnel") ? undefined : "none"}}>
          <PanelHeader title="Performance funnel" subtitle="Reach → Impressions → Clicks → Purchases" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : trendRows.length > 0 ? (
            <GoogleAdsFunnel
              funnelData={[
                { stage: "Reach", value: trendRows.reduce((s,r) => s + (r.reach||0), 0) },
                { stage: "Impressions", value: trendRows.reduce((s,r) => s + (r.impressions||0), 0) },
                { stage: "Clicks", value: trendRows.reduce((s,r) => s + (r.clicks||0), 0) },
                { stage: "Purchases", value: trendRows.reduce((s,r) => s + (r.purchases||0), 0) },
              ].filter(d => d.value > 0)}
              costPerConversion={0}
            />
          ) : <ChartEmpty title="No funnel data" message="No impression data for this range." />}
        </div>
      </section>

      <section className="analytics-grid-tables">
        <div className="analytics-panel" style={{display: isVisible("platforms") ? undefined : "none"}}>
          <PanelHeader title="Platform breakdown" subtitle="Spend by publisher platform" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : platformRows.length ? (
            <div style={{ height: Math.max(80, platformRows.length * 44 + 16), width: "100%", paddingLeft: 12, paddingRight: 12, paddingBottom: 8 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={platformRows} layout="vertical" margin={{ left: 12, right: 12, top: 4, bottom: 4 }}>
                  <CartesianGrid horizontal={false} strokeDasharray="4 6" />
                  <XAxis type="number" hide />
                  <YAxis dataKey="platform" type="category" width={100} tickLine={false} axisLine={false} />
                  <ChartTooltip formatter={(v) => [moneyPrecise(v), "Spend"]} />
                  <Bar dataKey="spend" fill={accent} radius={[0, 10, 10, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : null}
          <div className="bs-table"><DataTable loading={loading} columns={metaPlatformCols} data={platformRows} emptyMessage="No platform data for this range." /></div>
        </div>
      </section>

      <section className="analytics-panel" style={{display: isVisible("campaigns") ? undefined : "none"}}>
        <PanelHeader title="Campaign performance" subtitle="Top campaigns by spend" />
        <div className="bs-table"><DataTable loading={loading} columns={metaCampaignCols} data={campaignRows} emptyMessage="No campaign data for this range." /></div>
      </section>
    </div>
  );
}

/* ═══════════════════════ HUBSPOT DASHBOARD ═══════════════════════════ */

function HubSpotDashboard({ client, source, onIntegrations }) {
  const accent = useAccentColor();
  const HUBSPOT_CHARTS = [
    { key: "trend", label: "Contacts trend" },
    { key: "pipeline", label: "Deal pipeline" },
    { key: "deals", label: "Recent deals" },
  ];
  const { visible: visibleCharts, toggle: toggleChart, showAll: showAllCharts, isVisible } = useChartFilter("hubspot-charts", HUBSPOT_CHARTS.map(c => c.key));
  const [days, setDays] = useState("30");
  // ✅ FIX: added refreshKey state (was missing)
  const [refreshKey, setRefreshKey] = useState(0);
  const { data, loading, error } = useDashboardData(
    // ✅ FIX: added forceRefresh + refreshKey dep
    () => getHubSpotDashboard({ clientId: client?.id, clientName: client?.name, days: Number(days), forceRefresh: refreshKey > 0 }),
    [client?.id, client?.name, days, refreshKey],
  );

  const cards = data?.cards?.length ? data.cards : [
    { label: "Contacts", value: "-", detail: "Connect HubSpot", tone: "blue" },
    { label: "Companies", value: "-", detail: "Waiting for data", tone: "green" },
    { label: "Open Deals", value: "-", detail: "Waiting for data", tone: "violet" },
    { label: "Pipeline Value", value: "-", detail: "Waiting for data", tone: "amber" },
    { label: "Won Deals", value: "-", detail: "Waiting for data", tone: "emerald" },
    { label: "Won Revenue", value: "-", detail: "Waiting for data", tone: "rose" },
  ];

  const trendRows = data?.trend || [];
  const dealRows = data?.deals || [];
  const stageRows = data?.stages || [];
  const hasRows = Boolean(trendRows.length || dealRows.length || stageRows.length);

  return (
    <div className={`analytics-dashboard bs-dash${loading && data ? " is-refreshing" : ""}`}>
      <DashboardToolbar
        source={source}
        days={days} onDaysChange={setDays}
        allowedDays={RANGE_PRESET_DAYS}
        charts={HUBSPOT_CHARTS}
        chartFilter={{ visible: visibleCharts, toggle: toggleChart, showAll: showAllCharts }}
        onRefresh={() => setRefreshKey(k => k+1)}
        loading={loading}
      />

      <AlertBanner error={error} onAction={onIntegrations} />
      <DashboardLoadingBar loading={loading && data} />

      <section className="kpi-grid bs-kpis">
        {cards.map((card) => <KpiCard key={card.label} card={card} firstLoad={loading && !data} />)}
      </section>

      {!loading && !hasRows && (
        <EmptyPanel
          title="No HubSpot data loaded yet"
          message={data?.reason || error || "HubSpot is connected but returned no data. Check that the account has contacts and deals."}
          actionLabel="Open Integrations" onAction={onIntegrations}
        />
      )}

      <section className="analytics-grid-main">
        <div className="analytics-panel analytics-panel-large" style={{display: isVisible("trend") ? undefined : "none"}}>
          <PanelHeader title="New contacts trend" subtitle={`Daily new contacts — last ${days} days`} badge="daily" />
          {loading ? <Skeleton className="analytics-chart-skeleton" /> : trendRows.length ? (
            <ChartContainer config={HUBSPOT_TREND_CONFIG} className="analytics-chart tall shadcn-line-chart">
              <LineChart data={trendRows} margin={{ left: 0, right: 16, top: 8, bottom: 4 }}>
                <CartesianGrid vertical={false} strokeDasharray="3 7" />
                <XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false} tickMargin={10} minTickGap={30} />
                <YAxis tickLine={false} axisLine={false} tickMargin={10} tickFormatter={compact} width={42} />
                <ChartTooltip cursor={false} content={<ChartTooltipContent labelFormatter={shortDate} formatter={compact} />} />
                <Line dataKey="contacts" type="monotone" stroke="var(--color-contacts)" strokeWidth={2.8} dot={false} activeDot={{ r: 5 }} />
              </LineChart>
            </ChartContainer>
          ) : <ChartEmpty title="No trend data" message="No new contact data returned for this range." />}
        </div>

        <div className="analytics-panel" style={{display: isVisible("pipeline") ? undefined : "none"}}>
          <PanelHeader title="Deal pipeline funnel" subtitle="Stages ranked by total value" />
          <HubSpotFunnelChart stages={stageRows} loading={loading} />
          <div className="bs-table"><DataTable loading={loading} columns={hubspotStageCols} data={stageRows} emptyMessage="No stage data." /></div>
        </div>
      </section>

      <section className="analytics-panel" style={{display: isVisible("deals") ? undefined : "none"}}>
        <PanelHeader title="Recent deals" subtitle="Latest deals in your pipeline" />
        <div className="bs-table"><DataTable loading={loading} columns={hubspotDealCols} data={dealRows} emptyMessage="No deals found." /></div>
      </section>
    </div>
  );
}

/* ═══════════════════════ KLAVIYO DASHBOARD ═══════════════════════════ */

const DRAFT_COLOR = "#94a3b8";

function KlaviyoCampaignTrendChart({ campaigns = [], loading }) {
  const accent = useAccentColor();
  // Draft = slate grey, always visible against white; Sent = brand accent
  const draftColor = "#94a3b8";

  const trendConfig = useMemo(() => ({
    sent:  { label: "Sent",  color: accent },
    draft: { label: "Draft", color: draftColor },
  }), [accent]);

  const chartData = useMemo(() => {
    if (!campaigns.length) return [];
    const buckets = {};
    campaigns.forEach((c) => {
      const raw = c.send_time || c.created || "";
      const label = raw.length >= 7 ? raw.slice(0, 7) : "Unknown";
      if (!buckets[label]) buckets[label] = { month: label, sent: 0, draft: 0 };
      const s = String(c.status || "").toLowerCase();
      if (s === "sent" || s === "scheduled") buckets[label].sent += 1;
      else buckets[label].draft += 1;
    });
    return Object.values(buckets).sort((a, b) => a.month.localeCompare(b.month)).slice(-12);
  }, [campaigns]);

  return (
    <div className="analytics-panel" style={{ display: "flex", flexDirection: "column" }}>
      <PanelHeader title="Campaign activity" subtitle="Sent vs draft campaigns by month" />
      {loading ? (
        <Skeleton className="analytics-chart-skeleton compact" />
      ) : chartData.length ? (
        /* height: 220px matches the flow status donut panel visually */
        <ChartContainer config={trendConfig} className="analytics-chart" style={{ height: 220, width: "100%" }}>
          <BarChart data={chartData} margin={{ left: 12, right: 12, top: 8, bottom: 8 }} barCategoryGap="30%" barGap={4}>
            <CartesianGrid vertical={false} strokeDasharray="4 6" />
            <XAxis dataKey="month" tickLine={false} axisLine={false} tickMargin={10} tick={{ fontSize: 12 }} />
            <YAxis tickLine={false} axisLine={false} tickMargin={8} width={28} allowDecimals={false} />
            <ChartTooltip content={<ChartTooltipContent />} />
            <ChartLegend verticalAlign="bottom" content={<ChartLegendContent />} />
            {/* Grouped (no stackId) — two bars side by side per month */}
            <Bar dataKey="sent"  fill={accent}     radius={[6, 6, 0, 0]} maxBarSize={28} />
            <Bar dataKey="draft" fill={draftColor} radius={[6, 6, 0, 0]} maxBarSize={28} />
          </BarChart>
        </ChartContainer>
      ) : (
        <ChartEmpty title="No campaign history" message="No campaigns with timestamps found. Campaigns will appear here once sent." />
      )}
    </div>
  );
}

const FLOW_STATUS_COLORS = { live: "#22c55e", draft: "#94a3b8", manual: "#f59e0b", archived: "#e11d48" };

// Accent-relative palette: accent is always slice[0]; rest are complementary fixed hues
// that work alongside any brand accent color
const KLAVIYO_PIE_COLORS = ["#14b8a6", "#f97316", "#7c3aed", "#ec4899", "#f59e0b", "#2f7df6"];

function KlaviyoFlowsDonut({ flows = [], loading }) {
  const accent = useAccentColor();
  const chartData = useMemo(() => {
    const buckets = {};
    flows.forEach((f) => {
      const status = String(f.status || "unknown").toLowerCase();
      if (!buckets[status]) buckets[status] = { status, count: 0 };
      buckets[status].count += 1;
    });
    const order = ["live", "draft", "manual", "archived"];
    return Object.values(buckets).sort((a, b) => {
      const ai = order.indexOf(a.status); const bi = order.indexOf(b.status);
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    });
  }, [flows]);

  // Live = brand accent; rest = fixed complementary palette
  const sliceColors = useMemo(() => {
    const rest = KLAVIYO_PIE_COLORS;
    return chartData.map((entry, i) =>
      entry.status === "live" ? accent : rest[(i) % rest.length]
    );
  }, [chartData, accent]);

  const total = chartData.reduce((s, d) => s + d.count, 0);

  return (
    <div className="analytics-panel">
      <PanelHeader title="Flow status" subtitle="Active, draft, and archived automations" />
      {loading ? (
        <Skeleton className="analytics-chart-skeleton compact" />
      ) : chartData.length ? (
        <div style={{ display: "flex", alignItems: "center", gap: 24, padding: "12px 16px 16px" }}>
          <div style={{ width: 180, height: 180, flexShrink: 0, position: "relative" }}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <ChartTooltip formatter={(v, name) => [`${v} flows`, name]} />
                <Pie data={chartData} dataKey="count" nameKey="status" innerRadius={52} outerRadius={80} paddingAngle={3} strokeWidth={0}>
                  {chartData.map((entry, i) => <Cell key={entry.status} fill={sliceColors[i]} />)}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
            <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", pointerEvents: "none" }}>
              <span style={{ fontSize: 22, fontWeight: 700, color: "var(--dash-ink)" }}>{total}</span>
              <span style={{ fontSize: 11, color: "var(--dash-muted)" }}>flows</span>
            </div>
          </div>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 10 }}>
            {chartData.map((entry, i) => (
              <div key={entry.status} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ width: 10, height: 10, borderRadius: "50%", background: sliceColors[i], flexShrink: 0 }} />
                <span style={{ fontSize: 13, color: "var(--dash-ink-2)", textTransform: "capitalize", flex: 1 }}>{entry.status}</span>
                <strong style={{ fontSize: 13, color: "var(--dash-ink)" }}>{entry.count}</strong>
                <span style={{ fontSize: 11, color: "var(--dash-muted)", minWidth: 36, textAlign: "right" }}>{total ? Math.round(entry.count / total * 100) : 0}%</span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <ChartEmpty title="No flow data" message="No flows returned for this account." />
      )}
    </div>
  );
}

function GoogleAdsDeviceBarChart({ devices = [], loading }) {
  const accent = useAccentColor();
  if (loading) return <Skeleton className="analytics-chart-skeleton compact" />;
  if (!devices.length) return <ChartEmpty title="No device data" message="No device rows were returned for this range." />;
  return (
    <>
      {true ? (
        <ChartContainer config={GADS_DEVICE_BAR_CONFIG} className="analytics-chart channels">
          <BarChart data={devices} layout="vertical" margin={{ left: 12, right: 24, top: 4, bottom: 4 }}>
            <CartesianGrid horizontal={false} strokeDasharray="4 6" />
            <XAxis type="number" hide />
            <YAxis dataKey="device" type="category" width={80} tickLine={false} axisLine={false} />
            <ChartTooltip content={<ChartTooltipContent formatter={compact} />} />
            <ChartLegend verticalAlign="bottom" content={<ChartLegendContent />} />
            <Bar dataKey="clicks" fill={accent} radius={[0, 8, 8, 0]} />
          </BarChart>
        </ChartContainer>
      ) : null}
    </>
  );
}

function HubSpotFunnelChart({ stages = [], loading }) {
  const sorted = useMemo(() =>
    [...stages].sort((a, b) => (b.value || 0) - (a.value || 0)).slice(0, 8),
    [stages]
  );
  const maxVal = sorted[0]?.value || 1;

  if (loading) return <Skeleton className="analytics-chart-skeleton compact" />;
  if (!sorted.length) return <ChartEmpty title="No funnel data" message="No deal stage data returned for this range." />;

  return (
    <div className="hs-funnel">
      {sorted.map((stage, i) => {
        const width = Math.max(6, Math.round((stage.value / maxVal) * 100));
        return (
          <div key={stage.stage} className="hs-funnel-row">
            <span className="hs-funnel-label" title={stageLabel(stage.stage)}>{stageLabel(stage.stage)}</span>
            <div className="hs-funnel-bar-wrap">
              <div className="hs-funnel-bar" style={{ width: `${width}%`, background: FUNNEL_COLORS[i % FUNNEL_COLORS.length] }} />
            </div>
            <span className="hs-funnel-value">{money(stage.value)}</span>
            <span className="hs-funnel-count">{compact(stage.count)} deals</span>
          </div>
        );
      })}
    </div>
  );
}

/* ═══════════════════════ SHOPIFY DASHBOARD ════════════════════════════════ */

function ShopifyDashboard({ client, source, onIntegrations }) {
  const accent = useAccentColor();
  const SHOPIFY_CHARTS = [
    { key: "revenue", label: "Revenue trend" },
    { key: "products", label: "Top products" },
    { key: "orders_aov", label: "Orders & AOV" },
    { key: "customers", label: "Customers per day" },
    { key: "weekly", label: "Weekly summary" },
  ];
  const { visible: visibleCharts, toggle: toggleChart, showAll: showAllCharts, isVisible } = useChartFilter("shopify-charts", SHOPIFY_CHARTS.map(c => c.key));
  const [days, setDays] = useState("30");
  const [refreshKey, setRefreshKey] = useState(0);
  const { data, loading, error } = useDashboardData(
    () => getShopifyDashboard({ clientId: client?.id, clientName: client?.name, days: Number(days), forceRefresh: refreshKey > 0 }),
    [client?.id, client?.name, days, refreshKey],
  );

  const cards = data?.cards || [];
  const trend = data?.trend || [];
  const topProducts = data?.top_products || [];
  const shopDomain = data?.shop_domain || "";
  const hasRows = Boolean(trend.length || topProducts.length);
  const showEmpty = !loading && !hasRows;

  return (
    <div className={`analytics-dashboard bs-dash${loading && data ? " is-refreshing" : ""}`}>
      <DashboardToolbar
        source={source}
        context={shopDomain || undefined}
        days={days} onDaysChange={setDays}
        allowedDays={RANGE_PRESET_DAYS}
        charts={SHOPIFY_CHARTS}
        chartFilter={{ visible: visibleCharts, toggle: toggleChart, showAll: showAllCharts }}
        onRefresh={() => setRefreshKey(k => k+1)}
        loading={loading}
      />

      <AlertBanner error={error} onAction={onIntegrations} />
      <DashboardLoadingBar loading={loading && data} />

      {showEmpty && (
        <EmptyPanel
          title="No Shopify data loaded yet"
          message={data?.reason || error || "Shopify is connected but no orders found. Try syncing or expanding the date range."}
          actionLabel="Open Integrations" onAction={onIntegrations}
        />
      )}

      <section className="kpi-grid bs-kpis">
        {cards.map((card) => <KpiCard key={card.label} card={card} firstLoad={loading && !data} />)}
      </section>

      <section className="analytics-grid-main">
        <div className="analytics-panel analytics-panel-large" style={{display: isVisible("revenue") ? undefined : "none"}}>
          <PanelHeader title="Revenue trend" subtitle={`Daily revenue and orders — last ${days} days`} badge="daily" />
          {loading ? <Skeleton className="analytics-chart-skeleton" /> : trend.length ? (
            <ChartContainer config={SHOPIFY_TREND_CONFIG} className="analytics-chart tall shadcn-line-chart">
              <LineChart data={trend} margin={{ left: 0, right: 16, top: 8, bottom: 4 }}>
                <CartesianGrid vertical={false} strokeDasharray="3 7" />
                <XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false} tickMargin={10} minTickGap={30} />
                <YAxis tickLine={false} axisLine={false} tickFormatter={compact} width={42} domain={["auto","auto"]} />
                <ChartTooltip cursor={false} content={<ChartTooltipContent labelFormatter={shortDate} formatter={compact} />} />
                <ChartLegend verticalAlign="bottom" content={<ChartLegendContent />} />
                <Line dataKey="revenue" type="monotone" stroke="var(--color-revenue)" strokeWidth={2.8} dot={false} activeDot={{ r: 5 }} />
                <Line dataKey="orders" type="monotone" stroke="var(--color-orders)" strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
              </LineChart>
            </ChartContainer>
          ) : <ChartEmpty title="No revenue data" message="No orders found for this range." />}
        </div>

        <div className="analytics-panel" style={{display: isVisible("products") ? undefined : "none"}}>
          <PanelHeader title="Top products" subtitle="By revenue" />
          {loading ? <Skeleton className="analytics-chart-skeleton" /> : topProducts.length ? (
            <>
              <ChartContainer config={SHOPIFY_PRODUCT_CONFIG} className="analytics-chart channels">
                <BarChart data={topProducts.slice(0, 8)} layout="vertical" margin={{ left: 8, right: 40, top: 4, bottom: 4 }}>
                  <CartesianGrid horizontal={false} strokeDasharray="4 6" />
                  <XAxis type="number" hide />
                  <YAxis dataKey="product" type="category" width={100} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} />
                  <ChartTooltip content={<ChartTooltipContent formatter={compact} />} />
                  <Bar dataKey="revenue" fill={accent} radius={[0, 8, 8, 0]} />
                </BarChart>
              </ChartContainer>
              <div className="bs-table"><DataTable loading={loading} columns={shopifyProductCols} data={topProducts} emptyMessage="No product data." /></div>
            </>
          ) : <ChartEmpty title="No product data" message="No product data for this range." />}
        </div>
      </section>

      <section className="analytics-grid-tables">
        <div className="analytics-panel" style={{display: isVisible("orders_aov") ? undefined : "none"}}>
          <PanelHeader title="Orders & Avg order value" subtitle="Daily orders vs average order price" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : trend.length ? (
            <ChartContainer config={{ orders: { label: "Orders", color: "var(--accent)" }, aov: { label: "Avg order ($)", color: "#14b8a6" } }} className="analytics-chart channels">
              <BarChart data={trend} margin={{ left: 0, right: 48, top: 4, bottom: 4 }}>
                <CartesianGrid vertical={false} strokeDasharray="4 6" />
                <XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false} tickMargin={8} minTickGap={20} />
                <YAxis yAxisId="left" tickLine={false} axisLine={false} tickFormatter={compact} width={38} />
                <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} tickFormatter={(v) => `$${compact(v)}`} width={48} />
                <ChartTooltip content={<ChartTooltipContent labelFormatter={shortDate} formatter={compact} />} />
                <ChartLegend verticalAlign="bottom" content={<ChartLegendContent />} />
                <Bar yAxisId="left" dataKey="orders" fill={accent} radius={[4,4,0,0]} opacity={0.85} />
                <Line yAxisId="right" dataKey="aov" type="monotone" stroke="#14b8a6" strokeWidth={2.5} dot={false} />
              </BarChart>
            </ChartContainer>
          ) : <ChartEmpty title="No data" message="No order data for this range." />}
        </div>

        <div className="analytics-panel" style={{display: isVisible("customers") ? undefined : "none"}}>
          <PanelHeader title="Customers per day" subtitle="Unique customers ordering" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : trend.length ? (
            <ChartContainer config={{ customers: { label: "Customers", color: "var(--accent)" } }} className="analytics-chart channels">
              <BarChart data={trend} margin={{ left: 0, right: 16, top: 4, bottom: 4 }}>
                <CartesianGrid vertical={false} strokeDasharray="4 6" />
                <XAxis dataKey="date" tickFormatter={shortDate} tickLine={false} axisLine={false} tickMargin={8} minTickGap={20} />
                <YAxis tickLine={false} axisLine={false} tickFormatter={compact} width={38} />
                <ChartTooltip content={<ChartTooltipContent labelFormatter={shortDate} formatter={compact} />} />
                <Bar dataKey="customers" fill={accent} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ChartContainer>
          ) : <ChartEmpty title="No customer data" message="No customer data for this range." />}
        </div>
      </section>

      {isVisible("weekly") && trend.length > 0 && (() => {
        const weeks = [];
        for (let i = 0; i < trend.length; i += 7) {
          const chunk = trend.slice(i, i + 7);
          const weekRevenue = chunk.reduce((s, r) => s + r.revenue, 0);
          const weekOrders = chunk.reduce((s, r) => s + r.orders, 0);
          const weekCustomers = chunk.reduce((s, r) => s + r.customers, 0);
          const weekAov = weekOrders > 0 ? weekRevenue / weekOrders : 0;
          weeks.push({ week: chunk[0]?.date || "", revenue: weekRevenue, orders: weekOrders, customers: weekCustomers, aov: weekAov });
        }
        const weekCols = [
          { accessorKey: "week", header: "Week" },
          { ...NUM, accessorKey: "orders", header: ({ column }) => <SortableHeader column={column}>Orders</SortableHeader>, cell: ({ row }) => compact(row.getValue("orders")) },
          { ...NUM, accessorKey: "revenue", header: ({ column }) => <SortableHeader column={column}>Revenue</SortableHeader>, cell: ({ row }) => money(row.getValue("revenue")) },
          { ...NUM, accessorKey: "aov", header: "AOV", cell: ({ row }) => money(row.getValue("aov")) },
          { ...NUM, accessorKey: "customers", header: "Customers", cell: ({ row }) => compact(row.getValue("customers")) },
        ];
        return (
          <section className="analytics-panel">
            <PanelHeader title="Weekly summary" subtitle="Revenue and orders by week" />
            <div className="bs-table"><DataTable loading={loading} columns={weekCols} data={weeks.reverse()} emptyMessage="No weekly data." /></div>
          </section>
        );
      })()}
    </div>
  );
}

function KlaviyoDashboard({ client, source, onIntegrations }) {
  const accent = useAccentColor();
  const KLAVIYO_CHARTS = [
    { key: "campaign_trend", label: "Campaign activity" },
    { key: "campaign_status", label: "Campaign status breakdown" },
    { key: "campaign_channel", label: "Channel breakdown" },
    { key: "flows_donut", label: "Flow status" },
    { key: "flow_triggers", label: "Flow trigger types" },
    { key: "lists_timeline", label: "Lists created timeline" },
    { key: "campaigns_table", label: "Campaigns table" },
    { key: "lists_table", label: "Lists table" },
    { key: "flows_table", label: "Flows table" },
  ];
  const { visible: visibleCharts, toggle: toggleChart, showAll: showAllCharts, isVisible } = useChartFilter("klaviyo-charts", KLAVIYO_CHARTS.map(c => c.key));
  const [days, setDays] = useState("30");
  const [refreshKey, setRefreshKey] = useState(0);
  const { data, loading, error } = useDashboardData(
    () => getKlaviyoDashboard({ clientId: client?.id, clientName: client?.name, days: Number(days), forceRefresh: refreshKey > 0 }),
    [client?.id, client?.name, days, refreshKey],
  );

  const cards = data?.cards?.length ? data.cards : [
    { label: "Profiles", value: "-", detail: "Connect Klaviyo", tone: "blue" },
    { label: "Lists", value: "-", detail: "Waiting for data", tone: "green" },
    { label: "Campaigns", value: "-", detail: "Waiting for data", tone: "violet" },
    { label: "Active Flows", value: "-", detail: "Waiting for data", tone: "amber" },
    { label: "Draft Flows", value: "-", detail: "Waiting for data", tone: "rose" },
    { label: "Total Flows", value: "-", detail: "Waiting for data", tone: "emerald" },
  ];

  const listRows = data?.lists || [];
  const campaignRows = data?.campaigns || [];
  const flowRows = data?.flows || [];
  const hasRows = Boolean(listRows.length || campaignRows.length || flowRows.length);

  // ── derived data for new charts ──────────────────────────────────────────

  // Campaign status breakdown — sorted: Sent first, Draft second
  const campaignStatusData = useMemo(() => {
    const buckets = {};
    campaignRows.forEach(c => {
      const s = c.status || "Unknown";
      buckets[s] = (buckets[s] || 0) + 1;
    });
    const order = ["Sent", "Draft", "Scheduled", "Cancelled"];
    return Object.entries(buckets)
      .map(([status, count]) => ({ status, count }))
      .sort((a, b) => {
        const ai = order.indexOf(a.status); const bi = order.indexOf(b.status);
        return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
      });
  }, [campaignRows]);

  // Campaign channel breakdown (email / sms)
  const campaignChannelData = useMemo(() => {
    const buckets = {};
    campaignRows.forEach(c => {
      const ch = (c.channel || "email").toLowerCase();
      buckets[ch] = (buckets[ch] || 0) + 1;
    });
    return Object.entries(buckets).map(([channel, count]) => ({ channel, count }));
  }, [campaignRows]);

  // Flow trigger types
  const flowTriggerData = useMemo(() => {
    const buckets = {};
    flowRows.forEach(f => {
      const t = f.trigger_type || "Unknown";
      buckets[t] = (buckets[t] || 0) + 1;
    });
    return Object.entries(buckets)
      .map(([trigger_type, count]) => ({ trigger_type, count }))
      .sort((a, b) => b.count - a.count);
  }, [flowRows]);

  // Lists created per month
  const listsTimelineData = useMemo(() => {
    const buckets = {};
    listRows.forEach(l => {
      const raw = l.created || "";
      const label = raw.length >= 7 ? raw.slice(0, 7) : "Unknown";
      buckets[label] = (buckets[label] || 0) + 1;
    });
    return Object.entries(buckets)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([month, count]) => ({ month, count }))
      .slice(-12);
  }, [listRows]);

  return (
    <div className={`analytics-dashboard bs-dash${loading && data ? " is-refreshing" : ""}`}>
      <DashboardToolbar
        source={source}
        days={days} onDaysChange={setDays}
        allowedDays={RANGE_PRESET_DAYS}
        charts={KLAVIYO_CHARTS}
        chartFilter={{ visible: visibleCharts, toggle: toggleChart, showAll: showAllCharts }}
        onRefresh={() => setRefreshKey(k => k+1)}
        loading={loading}
      />

      <AlertBanner error={error} onAction={onIntegrations} />
      <DashboardLoadingBar loading={loading && data} />

      <section className="kpi-grid bs-kpis">
        {cards.map((card) => <KpiCard key={card.label} card={card} firstLoad={loading && !data} />)}
      </section>

      {!loading && !hasRows && (
        <EmptyPanel
          title="No Klaviyo data loaded yet"
          message={data?.reason || error || "Klaviyo is connected but returned no data. Check that the account has lists, campaigns, or flows."}
          actionLabel="Open Integrations" onAction={onIntegrations}
        />
      )}

      {/* ROW 1: Campaign trend + Flow status donut */}
      <section className="analytics-grid-main">
        <div style={{display: isVisible("campaign_trend") ? undefined : "none"}}>
          <KlaviyoCampaignTrendChart campaigns={campaignRows} loading={loading} />
        </div>
        <div style={{display: isVisible("flows_donut") ? undefined : "none"}}>
          <KlaviyoFlowsDonut flows={flowRows} loading={loading} />
        </div>
      </section>

      {/* ROW 2: Campaign status donut + Channel bar */}
      <section className="analytics-grid-tables">
        <div className="analytics-panel" style={{display: isVisible("campaign_status") ? undefined : "none"}}>
          <PanelHeader title="Campaign status breakdown" subtitle="Sent, draft, scheduled and cancelled" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : campaignStatusData.length ? (
            <div style={{ display: "flex", alignItems: "center", gap: 24, padding: "12px 16px 16px" }}>
              <div style={{ width: 160, height: 160, flexShrink: 0, position: "relative" }}>
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <ChartTooltip formatter={(v, name) => [`${v} campaigns`, name]} />
                    <Pie data={campaignStatusData} dataKey="count" nameKey="status" innerRadius={44} outerRadius={72} paddingAngle={3} strokeWidth={0}>
                      {campaignStatusData.map((entry, i) => (
                        <Cell key={entry.status} fill={entry.status === "Sent" || entry.status === "sent" ? accent : KLAVIYO_PIE_COLORS[i % KLAVIYO_PIE_COLORS.length]} />
                      ))}
                    </Pie>
                  </PieChart>
                </ResponsiveContainer>
                <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", pointerEvents: "none" }}>
                  <span style={{ fontSize: 20, fontWeight: 700, color: "var(--dash-ink)" }}>{campaignRows.length}</span>
                  <span style={{ fontSize: 10, color: "var(--dash-muted)" }}>total</span>
                </div>
              </div>
              <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 10 }}>
                {campaignStatusData.map((entry, i) => {
                  const sliceColor = entry.status === "Sent" || entry.status === "sent" ? accent : KLAVIYO_PIE_COLORS[i % KLAVIYO_PIE_COLORS.length];
                  return (
                    <div key={entry.status} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ width: 10, height: 10, borderRadius: "50%", background: sliceColor, flexShrink: 0 }} />
                      <span style={{ fontSize: 13, color: "var(--dash-ink-2)", flex: 1 }}>{entry.status}</span>
                      <strong style={{ fontSize: 13, color: "var(--dash-ink)" }}>{entry.count}</strong>
                      <span style={{ fontSize: 11, color: "var(--dash-muted)", minWidth: 36, textAlign: "right" }}>{campaignRows.length ? Math.round(entry.count / campaignRows.length * 100) : 0}%</span>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : <ChartEmpty title="No campaign data" message="No campaigns found." />}
        </div>

        <div className="analytics-panel" style={{display: isVisible("campaign_channel") ? undefined : "none"}}>
          <PanelHeader title="Channel breakdown" subtitle="Campaigns by channel type" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : campaignChannelData.length ? (
            <ChartContainer config={{ count: { label: "Campaigns", color: accent } }} className="analytics-chart channels">
              <BarChart data={campaignChannelData} layout="vertical" margin={{ left: 12, right: 24, top: 8, bottom: 8 }}>
                <CartesianGrid horizontal={false} strokeDasharray="4 6" />
                <XAxis type="number" allowDecimals={false} hide />
                <YAxis dataKey="channel" type="category" width={60} tickLine={false} axisLine={false} tick={{ fontSize: 12 }} />
                <ChartTooltip content={<ChartTooltipContent formatter={compact} />} />
                <Bar dataKey="count" radius={[0, 10, 10, 0]} maxBarSize={36}>
                  {campaignChannelData.map((_, i) => <Cell key={i} fill={i === 0 ? accent : "#14b8a6"} />)}
                </Bar>
              </BarChart>
            </ChartContainer>
          ) : <ChartEmpty title="No channel data" message="No channel breakdown available." />}
        </div>
      </section>

      {/* ROW 3: Flow trigger types + Lists timeline */}
      <section className="analytics-grid-tables">
        <div className="analytics-panel" style={{display: isVisible("flow_triggers") ? undefined : "none"}}>
          <PanelHeader title="Flow trigger types" subtitle="What triggers your automations" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : flowTriggerData.length ? (
            <ChartContainer config={{ count: { label: "Flows", color: accent } }} className="analytics-chart channels">
              <BarChart data={flowTriggerData} layout="vertical" margin={{ left: 12, right: 24, top: 8, bottom: 8 }}>
                <CartesianGrid horizontal={false} strokeDasharray="4 6" />
                <XAxis type="number" allowDecimals={false} hide />
                <YAxis dataKey="trigger_type" type="category" width={130} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} />
                <ChartTooltip content={<ChartTooltipContent formatter={compact} />} />
                <Bar dataKey="count" fill={accent} radius={[0, 10, 10, 0]} maxBarSize={36} />
              </BarChart>
            </ChartContainer>
          ) : <ChartEmpty title="No trigger data" message="No flow trigger data available." />}
        </div>

        <div className="analytics-panel" style={{display: isVisible("lists_timeline") ? undefined : "none"}}>
          <PanelHeader title="Lists created over time" subtitle="New audience lists added per month" />
          {loading ? <Skeleton className="analytics-chart-skeleton compact" /> : listsTimelineData.length ? (
            <ChartContainer config={{ count: { label: "Lists", color: accent } }} className="analytics-chart channels">
              <BarChart data={listsTimelineData} margin={{ left: 12, right: 12, top: 8, bottom: 8 }}>
                <CartesianGrid vertical={false} strokeDasharray="4 6" />
                <XAxis dataKey="month" tickLine={false} axisLine={false} tickMargin={8} tick={{ fontSize: 11 }} />
                <YAxis tickLine={false} axisLine={false} tickMargin={8} allowDecimals={false} width={32} />
                <ChartTooltip content={<ChartTooltipContent formatter={compact} />} />
                <Bar dataKey="count" fill={accent} radius={[6, 6, 0, 0]} maxBarSize={56} />
              </BarChart>
            </ChartContainer>
          ) : <ChartEmpty title="No timeline data" message="Lists have no created timestamps." />}
        </div>
      </section>

      {/* ROW 4: Tables */}
      <section className="analytics-grid-tables">
        <div className="analytics-panel" style={{display: isVisible("campaigns_table") ? undefined : "none"}}>
          <PanelHeader title="Email campaigns" subtitle="Recent campaigns sent" />
          <div className="bs-table"><DataTable loading={loading} columns={klaviyoCampaignCols} data={campaignRows} emptyMessage="No campaigns found." /></div>
        </div>
        <div className="analytics-panel" style={{display: isVisible("lists_table") ? undefined : "none"}}>
          <PanelHeader title="Subscriber lists" subtitle="Your audience segments" />
          <div className="bs-table"><DataTable loading={loading} columns={klaviyoListCols} data={listRows} emptyMessage="No lists found." /></div>
        </div>
      </section>

      <section className="analytics-panel" style={{display: isVisible("flows_table") ? undefined : "none"}}>
        <PanelHeader title="Automation flows" subtitle="Active and draft flow automations" />
        <div className="bs-table"><DataTable loading={loading} columns={klaviyoFlowCols} data={flowRows} emptyMessage="No flows found." /></div>
      </section>
    </div>
  );
}

/* ═══════════════════════ COMING SOON ═════════════════════════════════ */

function PlatformComingSoon({ platform, source, onIntegrations }) {
  return (
    <div className="platform-coming-card">
      <div className="platform-coming-visual" style={{ "--platform-color": platform.color }}>
        <div className="platform-orbit one" />
        <div className="platform-orbit two" />
        <div className="platform-letter">{platform.short}</div>
      </div>
      <div className="platform-coming-body">
        <div className="platform-mini-row">
          <SourceBadge source={source} />
          <span>{platform.label}</span>
        </div>
        <h2>{platform.label} command dashboard is next</h2>
        <p>This will use the same premium layout: executive KPI cards, performance trends, campaign or object tables, and AI-ready insight blocks from the connected MCP data.</p>
        <div className="platform-feature-grid">
          <div><i className="ti ti-chart-bar" /> KPI strip</div>
          <div><i className="ti ti-chart-area" /> Trend analysis</div>
          <div><i className="ti ti-table" /> Data tables</div>
          <div><i className="ti ti-sparkles" /> Insight cards</div>
        </div>
        <Button variant="outline" className="analytics-secondary" onClick={onIntegrations}>Check connector</Button>
      </div>
    </div>
  );
}

/* ═══════════════════════ MAIN EXPORT ═════════════════════════════════ */

const DASHBOARD_PATHS = {
  google_analytics_4: "/dashboards/google-analytics-4",
  google_ads: "/dashboards/google-ads",
  meta_ads: "/dashboards/meta-ads",
  klaviyo: "/dashboards/klaviyo",
  hubspot_crm: "/dashboards/hubspot",
  shopify: "/dashboards/shopify",
};

export default function Dashboards({ onGoTo, onBuildReport, client, sources, sourceSummary, workspaceCurrency = "usd" }) {
  activeCurrency = workspaceCurrency;
  const location = useLocation();
  const navigate = useNavigate();
  const activeTab = Object.entries(DASHBOARD_PATHS).find(([, path]) => location.pathname === path)?.[0] || "google_analytics_4";

  // Connected sources first, then the rest, each group keeping PLATFORM_TABS
  // order. `connected` is the catalog's own availability flag (connected or
  // syncing) from mergeSourceCatalog — this only reorders and styles tabs.
  const navPlatforms = useMemo(() => {
    const withSource = PLATFORM_TABS.map((platform) => {
      const source = getSourceByType(sources, platform.id);
      return { ...platform, source, connected: Boolean(source?.connected) };
    });
    return [...withSource.filter((p) => p.connected), ...withSource.filter((p) => !p.connected)];
  }, [sources]);

  return (
    <div className="page dashboard-page">
      <Tabs value={activeTab} onValueChange={(nextTab) => navigate(DASHBOARD_PATHS[nextTab])} className="dashboard-tabs-wrap">
        <div className="dash-sourcebar">
          <TabsList className="dash-source-list" aria-label="Data sources">
            {navPlatforms.map((platform, index) => {
              const startsOffGroup = !platform.connected && index > 0 && navPlatforms[index - 1].connected;
              const status = platform.connected ? "" : formatSourceStatus(platform.source);
              return (
                <Fragment key={platform.id}>
                  {startsOffGroup && <span className="dash-source-divider" aria-hidden="true" />}
                  <TabsTrigger
                    value={platform.id}
                    disabled={!platform.connected && platform.id !== activeTab}
                    className="dash-source-tab"
                    data-connected={platform.connected ? "true" : "false"}
                    title={platform.connected ? platform.label : `${platform.label} · ${status}`}
                  >
                    {platform.source
                      ? <ConnectorLogo source={platform.source} className="dash-source-logo" />
                      : <i className={`ti ${platform.icon}`} aria-hidden="true" />}
                    <span>{platform.nav}</span>
                    {!platform.connected && <span className="sr-only">({status})</span>}
                  </TabsTrigger>
                </Fragment>
              );
            })}
          </TabsList>
          <div className="dash-sourcebar-actions">
            <Button variant="outline" className="dash-bar-btn" onClick={() => onGoTo("integrations")}><i className="ti ti-plug" /> Manage sources</Button>
            <Button className="dash-bar-btn dash-bar-btn--primary" onClick={() => (onBuildReport ? onBuildReport(activeTab) : onGoTo("reports"))}><i className="ti ti-file-export" /> Build report</Button>
          </div>
        </div>

        <TabsContent value="google_analytics_4" className="dashboard-tab-content">
          <GA4Dashboard client={client} source={getSourceByType(sources, "google_analytics_4")} onIntegrations={() => onGoTo("integrations")} />
        </TabsContent>

        <TabsContent value="google_ads" className="dashboard-tab-content">
          <GoogleAdsDashboard client={client} source={getSourceByType(sources, "google_ads")} onIntegrations={() => onGoTo("integrations")} />
        </TabsContent>

        <TabsContent value="meta_ads" className="dashboard-tab-content">
          <MetaAdsDashboard client={client} source={getSourceByType(sources, "meta_ads")} onIntegrations={() => onGoTo("integrations")} />
        </TabsContent>

        <TabsContent value="klaviyo" className="dashboard-tab-content">
          <KlaviyoDashboard client={client} source={getSourceByType(sources, "klaviyo")} onIntegrations={() => onGoTo("integrations")} />
        </TabsContent>

        <TabsContent value="shopify" className="dashboard-tab-content">
          <ShopifyDashboard client={client} source={getSourceByType(sources, "shopify")} onIntegrations={() => onGoTo("integrations")} />
        </TabsContent>

        <TabsContent value="hubspot_crm" className="dashboard-tab-content">
          <HubSpotDashboard client={client} source={getSourceByType(sources, "hubspot_crm")} onIntegrations={() => onGoTo("integrations")} />
        </TabsContent>
      </Tabs>
    </div>
  );
}