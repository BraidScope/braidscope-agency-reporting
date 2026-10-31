// SPDX-License-Identifier: AGPL-3.0-only
import { getSession } from "./auth";

export const FALLBACK_DASHBOARD = {
  overview: [
    { label: "Total revenue", value: "$35.0k", direction: "up", detail: "+12% vs last period" },
    { label: "Ad spend", value: "$8.4k", direction: "up", detail: "+4%" },
    { label: "Blended ROAS", value: "4.2x", direction: "up", detail: "+8%" },
    { label: "Orders", value: "342", direction: "up", detail: "+9%" },
  ],
  google_ads: [
    { label: "Clicks", value: "12.4k", direction: "up", detail: "+8%" },
    { label: "CTR", value: "3.8%", direction: "up", detail: "+0.2pt" },
    { label: "Conversions", value: "284", direction: "dn", detail: "-6%" },
    { label: "CPA", value: "$18.2", direction: "dn", detail: "+14%" },
  ],
  meta_ads: [
    { label: "Reach", value: "84k", direction: "up", detail: "+5%" },
    { label: "CPM", value: "$12.4", direction: "dn", detail: "+22%" },
    { label: "ROAS", value: "3.9x", direction: "dn", detail: "-7%" },
    { label: "Frequency", value: "3.8", direction: "dn", detail: "fatigue" },
  ],
  shopify: [
    { label: "Orders", value: "342", direction: "up", detail: "+9%" },
    { label: "AOV", value: "$102", direction: "up", detail: "+3%" },
    { label: "Revenue", value: "$34.8k", direction: "up", detail: "+11%" },
    { label: "Conv. rate", value: "2.4%", direction: "dn", detail: "-2%" },
  ],
  clicks_by_day: [55, 70, 48, 82, 64, 90, 58],
  revenue_by_week: [60, 72, 85, 95],
  top_campaigns: [
    { campaign: "Summer Sale — Video", spend: "$1,420", roas: "6.1x", revenue: "$8,662" },
    { campaign: "Retargeting — Cart abandoners", spend: "$890", roas: "5.2x", revenue: "$4,628" },
    { campaign: "Prospecting — Lookalike 2%", spend: "$1,210", roas: "3.4x", revenue: "$4,114" },
    { campaign: "Brand awareness — Static", spend: "$680", roas: "1.1x", revenue: "$748" },
  ],
};

export async function getDashboardSummary(clientId, days = 30) {
  if (!clientId) return { ok: true, data: FALLBACK_DASHBOARD, fallback: true };

  try {
    const session = await getSession();
    const headers = session?.access_token
      ? { Authorization: `Bearer ${session.access_token}` }
      : {};
    const params = new URLSearchParams({ client_id: clientId, days: String(days) });
    const response = await fetch(`/py/dashboard/summary?${params}`, { headers });

    if (!response.ok) {
      return { ok: false, data: FALLBACK_DASHBOARD, error: `Dashboard API returned ${response.status}` };
    }

    return { ok: true, data: await response.json(), fallback: false };
  } catch (error) {
    return { ok: false, data: FALLBACK_DASHBOARD, error: error.message };
  }
}
