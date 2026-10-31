// SPDX-License-Identifier: AGPL-3.0-only
// OpenAI's mark isn't in the pinned Simple Icons release (the "openai" slug
// 404s there — confirmed against the published npm package, which only ships
// openaigym.svg), so it's bundled locally instead of depending on an
// unpinned/floating CDN version. Same silhouette-plus-mask treatment as
// every other connector once imported.
import openaiLogo from "../assets/icons/openai.svg";

// Filter pills shown above the connector catalogue. `group` on each source decides
// which pill it belongs to; "all" and "connected" are computed from live status.
export const CONNECTOR_FILTERS = [
  { id: "all", label: "All" },
  { id: "connected", label: "Connected" },
  { id: "advertising", label: "Advertising" },
  { id: "analytics", label: "Analytics" },
  { id: "ecommerce", label: "E-commerce" },
  { id: "crm", label: "CRM & Email" },
];

// Real brand marks come from the Simple Icons collection (pinned to a major
// version so an upstream slug rename can't silently break a card). Each icon
// ships as an unfilled silhouette, so we recolor it client-side with a CSS
// mask instead of baking in a single color.
const SIMPLE_ICONS_BASE = "https://cdn.jsdelivr.net/npm/simple-icons@16/icons/";

export function connectorLogoUrl(logoSlug) {
  return logoSlug ? `${SIMPLE_ICONS_BASE}${logoSlug}.svg` : null;
}

// Google's favicon proxy covers brands missing from Simple Icons (e.g. Klaviyo)
// so every connector still gets its real mark instead of a generic fallback.
export function connectorFaviconUrl(domain) {
  return domain ? `https://www.google.com/s2/favicons?domain=${domain}&sz=128` : null;
}

export const SOURCES = [
  {
    type: "google_ads",
    bg: "#185FA5",
    l: "G",
    logoSlug: "googleads",
    name: "Google Ads",
    cat: "Advertising",
    group: "advertising",
    desc: "Campaign spend, ROAS and conversion data pulled across every ad account.",
    setup: "OAuth · 2 min setup",
    manageUrl: "https://ads.google.com/aw/overview",
    popular: true,
    connected: false,
    authType: "oauth",
  },
  {
    type: "meta_ads",
    bg: "#0866FF",
    l: "M",
    logoSlug: "meta",
    name: "Meta Ads",
    cat: "Advertising",
    group: "advertising",
    desc: "Facebook and Instagram campaigns with creative-level performance and CPA.",
    setup: "OAuth · 2 min setup",
    manageUrl: "https://business.facebook.com/adsmanager",
    popular: true,
    connected: false,
    authType: "oauth",
  },
  {
    type: "shopify",
    bg: "#5E8E3E",
    l: "S",
    logoSlug: "shopify",
    name: "Shopify",
    cat: "E-commerce",
    group: "ecommerce",
    desc: "Orders, refunds, products and customer cohorts from your storefront.",
    setup: "OAuth · 3 min setup",
    connected: true,
    connectorMode: "api",
    authType: "oauth",
    scopes: ["read_orders", "read_products", "read_customers", "read_analytics"],
    syncEntities: ["orders", "refunds", "products", "customers"],
  },
  {
    type: "google_analytics_4",
    bg: "#E8702A",
    l: "A",
    logoSlug: "googleanalytics",
    name: "Google Analytics 4",
    cat: "Analytics",
    group: "analytics",
    desc: "Sessions, funnels and channel attribution mapped to your revenue reporting.",
    setup: "OAuth · 2 min setup",
    manageUrl: "https://analytics.google.com/analytics/web/",
    popular: true,
    connected: false,
    authType: "oauth",
  },
  {
    type: "search_console",
    bg: "#4285F4",
    l: "C",
    logoSlug: "googlesearchconsole",
    name: "Search Console",
    cat: "SEO",
    group: "analytics",
    desc: "Impressions, clicks and query-level rankings alongside paid performance.",
    eta: "Q3 2026",
    connected: false,
    comingSoon: true,
  },
  {
    type: "klaviyo",
    bg: "#1F2A44",
    l: "K",
    logoDomain: "klaviyo.com",
    name: "Klaviyo",
    cat: "Email & SMS",
    group: "crm",
    desc: "Flow and campaign revenue, list growth and deliverability in one place.",
    setup: "OAuth · 3 min setup",
    manageUrl: "https://www.klaviyo.com/account#integrations",
    connected: false,
    authType: "oauth",
  },
  {
    type: "paypal",
    bg: "#003087",
    l: "P",
    logoSlug: "paypal",
    name: "PayPal",
    cat: "Payments",
    group: "ecommerce",
    desc: "Settlements, fees and payout timing reconciled against store revenue.",
    eta: "Q4 2026",
    connected: false,
    comingSoon: true,
  },
  {
    type: "tiktok_ads",
    bg: "#FF6900",
    l: "T",
    logoSlug: "tiktok",
    name: "TikTok Ads",
    cat: "Advertising",
    group: "advertising",
    desc: "Spend, video performance and conversion tracking for TikTok campaigns.",
    eta: "Q3 2026",
    connected: false,
    comingSoon: true,
  },
  {
    type: "hubspot_crm",
    bg: "#00A4BD",
    l: "H",
    logoSlug: "hubspot",
    name: "HubSpot",
    cat: "CRM",
    group: "crm",
    desc: "Deals, pipeline stages and lifecycle events joined to marketing spend.",
    setup: "OAuth · 2 min setup",
    manageUrl: "https://app.hubspot.com/settings",
    connected: false,
    authType: "oauth",
  },
  {
    type: "openrouter_ai",
    bg: "#111827",
    l: "O",
    logoSlug: "openrouter",
    name: "OpenRouter",
    cat: "AI Provider",
    group: "ai",
    desc: "Access multiple AI models through one account.",
    setup: "Account connect",
    manageUrl: "https://openrouter.ai/settings/keys",
    connected: false,
    authType: "oauth",
    defaultModel: "moonshotai/kimi-k2.6:free",
  },
  {
    type: "openai_ai",
    bg: "#10A37F",
    l: "AI",
    logoAsset: openaiLogo,
    name: "OpenAI",
    cat: "AI Provider",
    group: "ai",
    desc: "Use your ChatGPT/Codex account for reports and Ask AI.",
    setup: "Account connect",
    manageUrl: "https://platform.openai.com/settings/organization/api-keys",
    connected: false,
    authType: "oauth",
    authMode: "codex_chatgpt",
    defaultModel: "gpt-4.1-mini",
  },
  {
    type: "anthropic_ai",
    bg: "#D97757",
    l: "C",
    logoSlug: "anthropic",
    name: "Claude",
    cat: "AI Provider",
    group: "ai",
    desc: "Sign in with your own Claude account for reports and Ask AI.",
    setup: "Account connect",
    // The Claude API-key (BYOK) path is hidden, not removed: same approach as
    // the OpenAI card above. To bring it back, restore authType: "api_key",
    // manageUrl (https://console.anthropic.com/settings/keys) and
    // defaultModel: "claude-sonnet-4-20250514". The backend routes, byok.py
    // and the ConnectModal/AiDrawer key forms are all still in place.
    connected: false,
    authType: "oauth",
    authMode: "claude_code",
  },
];

export const PRESETS = ["#111111", "#534AB7", "#0F6E56", "#C0392B", "#185FA5", "#E8702A"];

export const NAV_ITEMS = [
  { id: "workspace", label: "Workspace", icon: "ti-layout-grid" },
  { id: "dashboards", label: "Dashboards", icon: "ti-chart-area-line" },
  { id: "reports", label: "Reports", icon: "ti-file-analytics" },
  { id: "integrations", label: "Integrations", icon: "ti-plug" },
  { id: "branding", label: "Branding", icon: "ti-palette" },
  { id: "settings", label: "Settings", icon: "ti-settings" },
];

export const HERO_KPIS = [
  { label: "Revenue", value: "$35.0k", delta: "+12%", cls: "up", sub: "vs last 30 days" },
  { label: "Blended ROAS", value: "4.2x", delta: "+8%", cls: "up", sub: "across all sources" },
  { label: "Orders", value: "342", delta: "+9%", cls: "up", sub: "Shopify confirmed" },
];

export const PAGES = [
  { id: "overview", icon: "ti-layout-dashboard", name: "Overview", flag: "Live", flagCls: "flag-live", kpis: [["ROAS","4.2x","+8%","up"],["Spend","$8.4k",null,null],["Revenue","$35k","+12%","up"],["CPC","$1.24",null,null]] },
  { id: "google", icon: "ti-brand-google", name: "Google Ads", flag: "Anomaly", flagCls: "flag-alert", kpis: [["Clicks","12.4k",null,null],["CTR","3.8%",null,null],["Conv.","284","-6%","dn"],["CPA","$18.2","+14%","dn"]] },
  { id: "meta", icon: "ti-brand-meta", name: "Meta Ads", flag: "Live", flagCls: "flag-live", kpis: [["Reach","84k",null,null],["CPM","$12.4",null,null],["ROAS","3.9x",null,null],["Freq.","2.1",null,null]] },
  { id: "shopify", icon: "ti-shopping-bag", name: "Shopify", flag: "Live", flagCls: "flag-live", kpis: [["Orders","342","+9%","up"],["AOV","$102",null,null],["Revenue","$34.8k",null,null],["Conv.%","2.4%",null,null]] },
  { id: "email", icon: "ti-mail", name: "Email / Klaviyo", flag: "Live", flagCls: "flag-live", kpis: [["Sent","42k",null,null],["Open","48%","+3%","up"],["Click","8.1%",null,null],["Rev.","$6.2k",null,null]] },
  { id: "monthly", icon: "ti-file-analytics", name: "Monthly Report", flag: "Ready", flagCls: "flag-live", kpis: [["Period","May 2026",null,null],["Pages","6",null,null],["Sent","Yes",null,null],["Format","PDF",null,null]] },
];

export const ACTIVITY = [
  { ico: "ti-check", color: "#1D9E75", text: "Monthly report for April was approved by client", time: "12 min ago" },
  { ico: "ti-alert-triangle", color: "#C77800", text: "Google Ads CPA anomaly detected on Tuesday — +14%", time: "2 hr ago" },
  { ico: "ti-plug", color: "#185FA5", text: "Shopify reconnected after a brief sync gap", time: "5 hr ago" },
  { ico: "ti-sparkles", color: "#111111", text: "AI flagged Meta frequency at 3.8 — recommended creative refresh", time: "1 day ago" },
  { ico: "ti-file-text", color: "#534AB7", text: "Weekly report draft generated and saved", time: "1 day ago" },
];
