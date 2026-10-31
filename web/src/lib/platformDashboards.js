// SPDX-License-Identifier: AGPL-3.0-only
import { getSession } from "./auth";
import { dashboardErrorMessage, toErrorMessage } from "./apiErrors";

// ── Mock data for demo clients ──────────────────────────────────────────────

const DEMO_GA4 = {
  ok: true, has_data: true, empty_reason: "",
  generated_at: new Date().toISOString(),
  date_range: { start: "2026-07-26", end: "2026-08-24", days: 30 },
  properties: [{ id: "123456789", name: "Demo Website - GA4", account: "Demo Account" }],
  selected_property: { id: "123456789", name: "Demo Website - GA4", account: "Demo Account" },
  cards: [
    { label: "Active users", value: "12.4k", tone: "blue", detail: "users with engaged activity" },
    { label: "New users", value: "8.2k", tone: "green", detail: "first-time visitors" },
    { label: "Sessions", value: "18.7k", tone: "violet", detail: "total visits" },
    { label: "Engagement rate", value: "64.2%", tone: "amber", detail: "quality of visits" },
    { label: "Key events", value: "1.2k", tone: "rose", detail: "conversion-like actions" },
    { label: "Total revenue", value: "$24.8k", tone: "emerald", detail: "GA4 attributed revenue" },
  ],
  trend: Array.from({ length: 30 }, (_, i) => ({
    date: new Date(Date.now() - (29 - i) * 86400000).toISOString().split("T")[0],
    sessions: 500 + Math.floor(Math.random() * 300),
    activeUsers: 400 + Math.floor(Math.random() * 200),
    newUsers: 200 + Math.floor(Math.random() * 150),
    keyEvents: 30 + Math.floor(Math.random() * 20),
    revenue: 600 + Math.floor(Math.random() * 400),
    conversionRate: parseFloat((3 + Math.random() * 2).toFixed(2)),
  })),
  channels: [
    { channel: "Organic Search", sessions: 7200, revenue: 12000, keyEvents: 480, engagementRate: "68.0%" },
    { channel: "Direct", sessions: 4100, revenue: 8000, keyEvents: 210, engagementRate: "72.0%" },
    { channel: "Paid Search", sessions: 2800, revenue: 6000, keyEvents: 180, engagementRate: "58.0%" },
    { channel: "Social", sessions: 2100, revenue: 3000, keyEvents: 120, engagementRate: "48.0%" },
    { channel: "Email", sessions: 1500, revenue: 2800, keyEvents: 95, engagementRate: "78.0%" },
    { channel: "Organic Video", sessions: 900, revenue: 1500, keyEvents: 45, engagementRate: "52.0%" },
  ],
  pages: [
    { title: "Home", path: "/", views: 8200, activeUsers: 6100, events: 420, keyEvents: 210 },
    { title: "Products", path: "/products", views: 4800, activeUsers: 3600, events: 280, keyEvents: 140 },
    { title: "About Us", path: "/about", views: 2100, activeUsers: 1800, events: 95, keyEvents: 30 },
    { title: "Contact", path: "/contact", views: 1400, activeUsers: 1200, events: 60, keyEvents: 80 },
    { title: "Pricing", path: "/pricing", views: 1200, activeUsers: 980, events: 55, keyEvents: 120 },
  ],
  devices: [
    { device: "Mobile", activeUsers: 6800, sessions: 9200 },
    { device: "Desktop", activeUsers: 4800, sessions: 7400 },
    { device: "Tablet", activeUsers: 800, sessions: 1100 },
  ],
  events: [
    { event: "page_view", count: 48200, keyEvents: 0, revenue: 0 },
    { event: "purchase", count: 1240, keyEvents: 1240, revenue: 24800 },
    { event: "add_to_cart", count: 3800, keyEvents: 0, revenue: 0 },
    { event: "begin_checkout", count: 2100, keyEvents: 0, revenue: 0 },
    { event: "sign_up", count: 840, keyEvents: 840, revenue: 0 },
  ],
  countries: [
    { country: "United States", countryId: "US", activeUsers: 6200, sessions: 8400 },
    { country: "United Kingdom", countryId: "GB", activeUsers: 1800, sessions: 2400 },
    { country: "Canada", countryId: "CA", activeUsers: 1200, sessions: 1600 },
    { country: "Australia", countryId: "AU", activeUsers: 900, sessions: 1200 },
    { country: "Germany", countryId: "DE", activeUsers: 700, sessions: 950 },
    { country: "France", countryId: "FR", activeUsers: 580, sessions: 780 },
    { country: "Brazil", countryId: "BR", activeUsers: 420, sessions: 580 },
    { country: "India", countryId: "IN", activeUsers: 380, sessions: 520 },
  ],
  conversion_rate: 6.42,
  organic_sessions: 7200,
};

const DEMO_GOOGLE_ADS = {
  ok: true, has_data: true, empty_reason: "",
  generated_at: new Date().toISOString(),
  customer_id: "1234567890",
  customers: [{ id: "1234567890", name: "Demo Ads Account", accessible: true }],
  date_range: { preset: "LAST_30_DAYS", days: 30 },
  cards: [
    { label: "Impressions", value: "243k", tone: "blue", detail: "total ad impressions" },
    { label: "Clicks", value: "8.4k", tone: "green", detail: "total clicks" },
    { label: "CTR", value: "3.5%", tone: "violet", detail: "click-through rate" },
    { label: "Avg CPC", value: "$1.24", tone: "amber", detail: "average cost per click" },
    { label: "Cost", value: "$10.4k", tone: "rose", detail: "total ad spend" },
    { label: "Conversions", value: "284", tone: "emerald", detail: "total conversions" },
    { label: "Cost / Conv.", value: "$36.62", tone: "amber", detail: "cost per conversion" },
  ],
  trend: Array.from({ length: 30 }, (_, i) => ({
    date: new Date(Date.now() - (29 - i) * 86400000).toISOString().split("T")[0],
    impressions: 7000 + Math.floor(Math.random() * 3000),
    clicks: 250 + Math.floor(Math.random() * 100),
    cost: 300 + Math.floor(Math.random() * 100),
    conversions: 8 + Math.floor(Math.random() * 6),
    cpm: parseFloat((12 + Math.random() * 4).toFixed(2)),
    cpc: parseFloat((1.1 + Math.random() * 0.4).toFixed(2)),
    cpl: parseFloat((34 + Math.random() * 10).toFixed(2)),
  })),
  campaigns: [
    { name: "Brand - Search", status: "ENABLED", impressions: 48000, clicks: 2400, ctr: 5.0, cost: 2800, conversions: 96, value: 4800, costPerConversion: 29.17 },
    { name: "Retargeting - Display", status: "ENABLED", impressions: 82000, clicks: 1640, ctr: 2.0, cost: 2100, conversions: 58, value: 2900, costPerConversion: 36.21 },
    { name: "Prospecting - Search", status: "ENABLED", impressions: 61000, clicks: 2440, ctr: 4.0, cost: 3200, conversions: 84, value: 4200, costPerConversion: 38.10 },
    { name: "Shopping - Products", status: "ENABLED", impressions: 52000, clicks: 1920, ctr: 3.7, cost: 2300, conversions: 46, value: 2300, costPerConversion: 50.0 },
  ],
  devices: [
    { device: "MOBILE", impressions: 128000, clicks: 4200, cost: 4800, conversions: 112 },
    { device: "DESKTOP", impressions: 96000, clicks: 3600, cost: 4400, conversions: 142 },
    { device: "TABLET", impressions: 19000, clicks: 600, cost: 1200, conversions: 30 },
  ],
  impressions_monthly: [
    { month: "2026-05", impressions: 198000 },
    { month: "2026-06", impressions: 224000 },
    { month: "2026-07", impressions: 218000 },
    { month: "2026-08", impressions: 243000 },
  ],
  funnel: [
    { stage: "Impressions", value: 243000 },
    { stage: "Clicks", value: 8400 },
    { stage: "Conversions", value: 284 },
  ],
  cost_per_conversion: 36.62,
  conversion_actions: [
    { category: "PURCHASE", name: "Purchase", conversions: 142 },
    { category: "SUBMIT_LEAD_FORM", name: "Contact form", conversions: 86 },
    { category: "PAGE_VIEW", name: "Key page view", conversions: 56 },
  ],
  keywords: [
    { keyword: "digital marketing agency", matchType: "BROAD", impressions: 12400, clicks: 620, ctr: 5.0, cost: 744, conversions: 28 },
    { keyword: "seo services", matchType: "PHRASE", impressions: 9800, clicks: 490, ctr: 5.0, cost: 588, conversions: 22 },
    { keyword: "google ads management", matchType: "EXACT", impressions: 7200, clicks: 504, ctr: 7.0, cost: 605, conversions: 32 },
    { keyword: "social media marketing", matchType: "BROAD", impressions: 8600, clicks: 344, ctr: 4.0, cost: 413, conversions: 14 },
    { keyword: "ppc agency", matchType: "EXACT", impressions: 5400, clicks: 378, ctr: 7.0, cost: 454, conversions: 24 },
  ],
  click_types: [
    { clickType: "Url Clicks", clicks: 7200, impressions: 198000, cost: 8640, ctr: 3.6 },
    { clickType: "Sitelinks", clicks: 840, impressions: 32000, cost: 1008, ctr: 2.6 },
    { clickType: "Headline", clicks: 360, impressions: 13000, cost: 432, ctr: 2.8 },
  ],
};

const DEMO_META = {
  ok: true, has_data: true,
  cards: [
    { label: "Reach", value: "84k", tone: "blue", detail: "unique accounts reached" },
    { label: "Impressions", value: "142k", tone: "green", detail: "total impressions" },
    { label: "Spend", value: "$8.4k", tone: "rose", detail: "total ad spend" },
    { label: "CPM", value: "$59.15", tone: "amber", detail: "cost per thousand impressions" },
    { label: "ROAS", value: "3.9x", tone: "emerald", detail: "return on ad spend" },
    { label: "Revenue", value: "$32.8k", tone: "emerald", detail: "attributed revenue" },
  ],
  trend: Array.from({ length: 30 }, (_, i) => ({
    date: new Date(Date.now() - (29 - i) * 86400000).toISOString().split("T")[0],
    impressions: 4000 + Math.floor(Math.random() * 2000),
    clicks: 120 + Math.floor(Math.random() * 80),
    spend: 250 + Math.floor(Math.random() * 80),
    reach: 2500 + Math.floor(Math.random() * 1000),
    cpm: parseFloat((50 + Math.random() * 20).toFixed(2)),
    ctr: parseFloat((1.5 + Math.random() * 1.5).toFixed(2)),
    roas: parseFloat((3 + Math.random() * 2).toFixed(2)),
    frequency: parseFloat((1.5 + Math.random() * 1).toFixed(2)),
    purchases: Math.floor(Math.random() * 8) + 2,
  })),  accounts: [{ id: "demo-meta-123", name: "Demo Meta Account" }],
  campaigns: [
    { name: "Retargeting - Carousel", status: "ACTIVE", reach: 28000, impressions: 48000, spend: 2800, cpm: 58.33, roas: 4.2, revenue: 11760 },
    { name: "Prospecting - Video", status: "ACTIVE", reach: 42000, impressions: 72000, spend: 3600, cpm: 50.0, roas: 3.8, revenue: 13680 },
    { name: "Brand Awareness", status: "ACTIVE", reach: 14000, impressions: 22000, spend: 2000, cpm: 90.9, roas: 3.7, revenue: 7400 },
  ],
  platforms: [
    { platform: "Facebook", publisher: "Facebook", spend: 4800, impressions: 84000, clicks: 2520 },
    { platform: "Instagram", publisher: "Instagram", spend: 2800, impressions: 46000, clicks: 1840 },
    { platform: "Audience Network", publisher: "Audience Network", spend: 800, impressions: 12000, clicks: 240 },
  ],
};

const DEMO_HUBSPOT = {
  ok: true, has_data: true,
  cards: [
    { label: "Total contacts", value: "2.4k", tone: "blue", detail: "contacts in CRM" },
    { label: "New contacts", value: "184", tone: "green", detail: "added this period" },
    { label: "Open deals", value: "42", tone: "violet", detail: "active pipeline deals" },
    { label: "Pipeline value", value: "$284k", tone: "emerald", detail: "total open deal value" },
    { label: "Closed won", value: "18", tone: "emerald", detail: "deals won this period" },
    { label: "Close rate", value: "38%", tone: "amber", detail: "win rate" },
  ],
  trend: Array.from({ length: 30 }, (_, i) => ({
    date: new Date(Date.now() - (29 - i) * 86400000).toISOString().split("T")[0],
    contacts: 4 + Math.floor(Math.random() * 8),
  })),
  stages: [
    { stage: "Qualified Lead", value: 84000, count: 12 },
    { stage: "Demo Scheduled", value: 62000, count: 8 },
    { stage: "Proposal Sent", value: 48000, count: 6 },
    { stage: "Negotiation", value: 52000, count: 5 },
    { stage: "Closed Won", value: 38000, count: 11 },
  ],
  deals: [
    { name: "Enterprise SaaS Deal", stage: "Negotiation", amount: 24000, owner: "Sarah Johnson", closeDate: "2026-09-15" },
    { name: "Mid-Market Expansion", stage: "Proposal Sent", amount: 12000, owner: "Mike Chen", closeDate: "2026-09-30" },
    { name: "SMB Package", stage: "Demo Scheduled", amount: 4800, owner: "Emily Davis", closeDate: "2026-10-05" },
  ],
};

const DEMO_KLAVIYO = {
  ok: true, has_data: true,
  cards: [
    { label: "Total profiles", value: "18.4k", tone: "blue", detail: "subscriber profiles" },
    { label: "Active lists", value: "12", tone: "green", detail: "active subscriber lists" },
    { label: "Campaigns sent", value: "24", tone: "violet", detail: "campaigns this period" },
    { label: "Avg open rate", value: "28.4%", tone: "amber", detail: "average email open rate" },
    { label: "Avg CTR", value: "4.2%", tone: "rose", detail: "average click-through rate" },
    { label: "Active flows", value: "8", tone: "emerald", detail: "automation flows" },
  ],
  campaigns: Array.from({ length: 12 }, (_, i) => ({
    id: `demo-camp-${i}`,
    name: ["Summer Sale", "Newsletter", "Product Launch", "Re-engagement", "Welcome Series"][i % 5],
    status: i % 3 === 0 ? "draft" : "sent",
    send_time: new Date(Date.now() - i * 7 * 86400000).toISOString(),
    created: new Date(Date.now() - i * 7 * 86400000).toISOString(),
  })),
  flows: [
    { id: "f1", name: "Welcome Series", status: "live" },
    { id: "f2", name: "Abandoned Cart", status: "live" },
    { id: "f3", name: "Post-Purchase", status: "live" },
    { id: "f4", name: "Win-Back", status: "draft" },
    { id: "f5", name: "Browse Abandon", status: "live" },
    { id: "f6", name: "Sunset Flow", status: "archived" },
    { id: "f7", name: "Birthday", status: "manual" },
    { id: "f8", name: "VIP Onboarding", status: "live" },
  ],
  lists: [
    { id: "l1", name: "Newsletter", profile_count: 8400 },
    { id: "l2", name: "Customers", profile_count: 5200 },
    { id: "l3", name: "VIP Members", profile_count: 1800 },
    { id: "l4", name: "Prospects", profile_count: 3000 },
  ],
};

// ── Fetch helper ─────────────────────────────────────────────────────────────

const DEMO_SHOPIFY = {
  ok: true, has_data: true,
  shop_domain: "demo-store.myshopify.com",
  currency: "USD",
  date_range: { days: 30 },
  cards: [
    { label: "Total revenue", value: "$84.2k", tone: "emerald", detail: "net revenue" },
    { label: "Orders", value: "1.2k", tone: "blue", detail: "total orders" },
    { label: "Avg order value", value: "$68.50", tone: "violet", detail: "net revenue ÷ orders" },
    { label: "Customers", value: "892", tone: "amber", detail: "unique customers" },
    { label: "Gross revenue", value: "$89.4k", tone: "green", detail: "before refunds" },
    { label: "Refunds", value: "$5.2k", tone: "rose", detail: "refunded value" },
  ],
  trend: Array.from({ length: 30 }, (_, i) => ({
    date: new Date(Date.now() - (29 - i) * 86400000).toISOString().split("T")[0],
    orders: 30 + Math.floor(Math.random() * 20),
    revenue: 2400 + Math.floor(Math.random() * 1200),
    refunds: 100 + Math.floor(Math.random() * 200),
    aov: 60 + Math.floor(Math.random() * 20),
    customers: 25 + Math.floor(Math.random() * 15),
  })),
  top_products: [
    { product: "Premium Widget Pro", revenue: 18400, quantity: 184 },
    { product: "Starter Bundle", revenue: 12800, quantity: 256 },
    { product: "Deluxe Kit", revenue: 9600, quantity: 96 },
    { product: "Basic Package", revenue: 7200, quantity: 360 },
    { product: "Add-on Module", revenue: 5400, quantity: 270 },
  ],
};


async function _fetchDashboard(url, params, { label = "Dashboard", timeout = 18000 } = {}) {
  const session = await getSession();
  const token = session?.access_token;
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeout);

  try {
    const response = await fetch(`${url}?${params}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal: controller.signal,
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      return { ok: false, error: dashboardErrorMessage(data?.detail, { label, status: response.status }), data: null };
    }
    return { ok: Boolean(data?.ok), error: data?.reason ? toErrorMessage(data.reason, `Could not load ${label} dashboard`) : "", data };
  } catch (error) {
    const message = error.name === "AbortError"
      ? `${label} dashboard request timed out. Check backend logs or reconnect for this client.`
      : error.message || `Could not load ${label} dashboard`;
    return { ok: false, error: message, data: null };
  } finally {
    window.clearTimeout(timer);
  }
}

// ── Exports ───────────────────────────────────────────────────────────────────

export async function getGA4Dashboard({ clientId, clientName, days = 30, propertyId, customStart, customEnd, forceRefresh = false } = {}) {
  if (!clientId) return { ok: false, error: "No client selected", data: null };
  if (clientId.startsWith("demo-")) return { ok: true, error: "", data: DEMO_GA4 };

  const params = new URLSearchParams({ client_id: clientId, days: String(days) });
  if (clientName) params.set("client_name", clientName);
  if (propertyId) params.set("property_id", propertyId);
  // A custom range (inclusive YYYY-MM-DD) replaces the `days` preset on the
  // backend; both dates are required, so only send them as a pair.
  if (customStart && customEnd) {
    params.set("start_date", customStart);
    params.set("end_date", customEnd);
  }
  if (forceRefresh) params.set("force_refresh", "true");

  return _fetchDashboard("/py/dashboards/google-analytics-4", params, { label: "GA4", timeout: 60000 });
}

export async function getGoogleAdsDashboard({ clientId, clientName, days = 30, customerId, forceRefresh = false } = {}) {
  if (!clientId) return { ok: false, error: "No client selected", data: null };
  if (clientId.startsWith("demo-")) return { ok: true, error: "", data: DEMO_GOOGLE_ADS };

  const params = new URLSearchParams({ client_id: clientId, days: String(days) });
  if (clientName) params.set("client_name", clientName);
  if (customerId) params.set("customer_id", customerId);
  if (forceRefresh) params.set("force_refresh", "true");

  return _fetchDashboard("/py/dashboards/google-ads", params, { label: "Google Ads", timeout: 60000 });
}

export async function getMetaAdsDashboard({ clientId, clientName, days = 30, accountId, forceRefresh = false } = {}) {
  if (!clientId) return { ok: false, error: "No client selected", data: null };
  if (clientId.startsWith("demo-")) return { ok: true, error: "", data: DEMO_META };

  const params = new URLSearchParams({ client_id: clientId, days: String(days) });
  if (clientName) params.set("client_name", clientName);
  if (accountId) params.set("account_id", accountId);
  if (forceRefresh) params.set("force_refresh", "true");

  // Meta Ads' dashboard fires 4 parallel Graph API insight queries
  // (account overview, campaigns, daily trend, platform breakdown) plus an
  // ad-account discovery call first - the same "several real round trips
  // per load" shape that made GA4/Google Ads/Klaviyo/Shopify bump this
  // past the 18s default (see getKlaviyoDashboard's comment below).
  return _fetchDashboard("/py/dashboards/meta-ads", params, { label: "Meta Ads", timeout: 60000 });
}

export async function getHubSpotDashboard({ clientId, clientName, days = 30, forceRefresh = false } = {}) {
  if (!clientId) return { ok: false, error: "No client selected", data: null };
  if (clientId.startsWith("demo-")) return { ok: true, error: "", data: DEMO_HUBSPOT };

  const params = new URLSearchParams({ client_id: clientId, days: String(days) });
  if (clientName) params.set("client_name", clientName);
  if (forceRefresh) params.set("force_refresh", "true");

  // HubSpot's dashboard runs 4 parallel CRM API calls (contacts total,
  // companies total, deals, recent contacts search) - the same
  // "several real round trips per load" shape that made GA4/Google
  // Ads/Klaviyo/Shopify bump this past the 18s default.
  return _fetchDashboard("/py/dashboards/hubspot", params, { label: "HubSpot", timeout: 60000 });
}

export async function getKlaviyoDashboard({ clientId, clientName, days = 30, forceRefresh = false } = {}) {
  if (!clientId) return { ok: false, error: "No client selected", data: null };
  if (clientId.startsWith("demo-")) return { ok: true, error: "", data: {
    ok: true, has_data: true,
    cards: [
      { label: "Total profiles", value: "18.4k", tone: "blue", detail: "subscriber profiles" },
      { label: "Active lists", value: "12", tone: "green", detail: "active subscriber lists" },
      { label: "Campaigns sent", value: "24", tone: "violet", detail: "campaigns this period" },
      { label: "Avg open rate", value: "28.4%", tone: "amber", detail: "average email open rate" },
      { label: "Avg CTR", value: "4.2%", tone: "rose", detail: "average click-through rate" },
      { label: "Active flows", value: "8", tone: "emerald", detail: "automation flows" },
    ],
    campaigns: Array.from({ length: 12 }, (_, i) => ({
      id: `demo-camp-${i}`,
      name: ["Summer Sale", "Newsletter", "Product Launch", "Re-engagement", "Welcome Series"][i % 5],
      status: i % 3 === 0 ? "draft" : "sent",
      send_time: new Date(Date.now() - i * 7 * 86400000).toISOString(),
      created: new Date(Date.now() - i * 7 * 86400000).toISOString(),
    })),
    flows: [
      { id: "f1", name: "Welcome Series", status: "live" },
      { id: "f2", name: "Abandoned Cart", status: "live" },
      { id: "f3", name: "Post-Purchase", status: "live" },
      { id: "f4", name: "Win-Back", status: "draft" },
      { id: "f5", name: "Browse Abandon", status: "live" },
      { id: "f6", name: "Sunset Flow", status: "archived" },
      { id: "f7", name: "Birthday", status: "manual" },
      { id: "f8", name: "VIP Onboarding", status: "live" },
    ],
    lists: [
      { id: "l1", name: "Newsletter", profile_count: 8400 },
      { id: "l2", name: "Customers", profile_count: 5200 },
      { id: "l3", name: "VIP Members", profile_count: 1800 },
      { id: "l4", name: "Prospects", profile_count: 3000 },
    ],
  }};

  const params = new URLSearchParams({ client_id: clientId, days: String(days) });
  if (clientName) params.set("client_name", clientName);
  if (forceRefresh) params.set("force_refresh", "true");

  // Klaviyo's dashboard makes 5 separate real MCP round trips (account,
  // lists, campaigns, flows, segments), each with its own full session
  // handshake - confirmed live on 2026-09-23 that this routinely exceeds
  // the 18s default timeout, aborting the request client-side before the
  // backend (which has its own 45s httpx timeout) ever gets a chance to
  // finish. GA4/Google Ads/Shopify already use 60s for the same reason.
  return _fetchDashboard("/py/dashboards/klaviyo", params, { label: "Klaviyo", timeout: 60000 });
}

export async function getShopifyDashboard({ clientId, clientName, days = 30, forceRefresh = false } = {}) {
  if (!clientId) return { ok: false, error: "No client selected", data: null };
  if (clientId.startsWith("demo-")) return { ok: true, error: "", data: {
    ok: true, has_data: true, shop_domain: "demo-store.myshopify.com",
    date_range: { days },
    cards: [
      { label: "Total revenue", value: "$48.2k", tone: "emerald", detail: "net revenue" },
      { label: "Orders", value: "284", tone: "blue", detail: "total orders" },
      { label: "Avg order value", value: "$169.7", tone: "violet", detail: "net revenue / orders" },
      { label: "Refunds", value: "$2.4k", tone: "rose", detail: "refunded value" },
      { label: "Customers", value: "218", tone: "amber", detail: "unique customers" },
      { label: "Gross revenue", value: "$50.6k", tone: "green", detail: "before refunds" },
    ],
    trend: Array.from({ length: 30 }, (_, i) => ({
      date: new Date(Date.now() - (29 - i) * 86400000).toISOString().split("T")[0],
      revenue: 1200 + Math.floor(Math.random() * 800),
      orders: 8 + Math.floor(Math.random() * 6),
      aov: 140 + Math.floor(Math.random() * 60),
      customers: 6 + Math.floor(Math.random() * 4),
    })),
    top_products: [
      { product: "Premium T-Shirt", quantity: 142, revenue: 8520, revenueFormatted: "$8.5k" },
      { product: "Classic Hoodie", quantity: 98, revenue: 7840, revenueFormatted: "$7.8k" },
      { product: "Running Shorts", quantity: 176, revenue: 6160, revenueFormatted: "$6.2k" },
      { product: "Sports Cap", quantity: 203, revenue: 4060, revenueFormatted: "$4.1k" },
      { product: "Gym Bag", quantity: 64, revenue: 5120, revenueFormatted: "$5.1k" },
    ],
  }};

  const params = new URLSearchParams({ client_id: clientId, days: String(days) });
  if (clientName) params.set("client_name", clientName);
  if (forceRefresh) params.set("force_refresh", "true");

  return _fetchDashboard("/py/dashboards/shopify", params, { label: "Shopify", timeout: 60000 });
}