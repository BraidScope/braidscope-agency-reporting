// SPDX-License-Identifier: AGPL-3.0-only
import { supabase, isSupabaseConfigured } from "./supabase";

export const DEMO_CLIENTS = [
  { id: "demo-acme", name: "Acme Clothing Co.", logo_url: null, currency: "usd", industry: "marketing" },
  { id: "demo-northwind", name: "Northwind Apparel", logo_url: null, currency: "usd", industry: "marketing" },
  { id: "demo-lakeside", name: "Lakeside Coffee", logo_url: null, currency: "usd", industry: "marketing" },
  { id: "demo-vertex", name: "Vertex Athletics", logo_url: null, currency: "usd", industry: "marketing" },
];

const CLIENT_COLUMNS = "id, organization_id, name, logo_url, currency, industry, created_at, branding_override";

async function authedJson(path, options = {}) {
  if (!isSupabaseConfigured()) return { ok: false, error: "Auth not configured" };
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) return { ok: false, error: "Not signed in" };
  // A flaky network/DNS hop to the backend used to be able to hang this
  // `fetch` indefinitely (no timeout at all) - since this call sits directly
  // in the client-workspace loading path App.jsx awaits before it ever shows
  // the workspace, a stalled request here reads to the user as the app being
  // permanently "stuck preparing" rather than a clear, timed-out error.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...(options.headers || {}),
      },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) return { ok: false, error: payload.detail || payload.error || response.statusText };
    return { ok: true, ...payload };
  } catch (error) {
    if (error?.name === "AbortError") return { ok: false, error: "Timed out reaching the admin workspace service" };
    return { ok: false, error: "Could not reach the admin workspace service" };
  } finally {
    clearTimeout(timeout);
  }
}

// Single source of truth for "what is this user's role on this client",
// reused by Settings.jsx, App.jsx and ClientBranding.jsx instead of each
// re-deriving owned-vs-shared role resolution separately. A shared client's
// role always comes from its own client_memberships row (client.role); an
// owned client's role is the viewer's own org role (client.role once
// App.jsx has tagged it at load/create time, falling back to org.role
// directly for a brief window before that tagging has happened).
export function getClientRole(client, org) {
  if (!client) return "";
  if (client.access === "shared") return client.role || "";
  return client.role || org?.role || "";
}

export function clientRoleMeets(client, org, minRole) {
  const rank = { viewer: 0, editor: 1, admin: 2, owner: 3 };
  const role = getClientRole(client, org);
  if (!role) return false;
  return (rank[role] ?? -1) >= (rank[minRole] ?? 0);
}

export async function listClients(organizationId) {
  if (!isSupabaseConfigured() || !organizationId) return { ok: true, clients: DEMO_CLIENTS };

  const { data, error } = await supabase
    .from("clients")
    .select(CLIENT_COLUMNS)
    .eq("organization_id", organizationId)
    .order("created_at", { ascending: true });

  if (error) return { ok: false, error: error.message, clients: [] };
  return { ok: true, clients: data || [] };
}

export async function createClient(organizationId, name) {
  const cleanName = name.trim();
  if (!cleanName) return { ok: false, error: "Client name is required" };
  if (!isSupabaseConfigured() || !organizationId) {
    return {
      ok: true,
      client: { id: `demo-${Date.now()}`, name: cleanName, logo_url: null },
    };
  }

  const { data, error } = await supabase
    .from("clients")
    .insert({ organization_id: organizationId, name: cleanName })
    .select(CLIENT_COLUMNS)
    .single();

  if (error) return { ok: false, error: error.message };
  return { ok: true, client: data };
}

// Clients another agency has shared with this user (via client_memberships)
// - separate from listClients' own-org fetch, merged together by the
// caller.
//
// This used to query client_memberships directly from the browser with a
// three-level PostgREST embed (client_memberships -> clients ->
// organizations), relying on RLS (clients_select_members' has_client_access
// OR clause, then org_select_members' matching extension) to cascade
// correctly across all three tables for the row to come back non-null at
// all. In practice that turned out to be fragile in exactly the way "works
// in theory, silently returns nothing in practice" bugs are: verified
// directly against the database that the underlying client_memberships row
// was real and correct, yet the switcher and Admin Workspace both still
// showed zero shared clients - meaning the RLS cascade (or the embed
// resolution itself) was failing silently for reasons no amount of policy
// re-reading surfaced, with no error ever reaching the UI.
//
// /py/admin/clients (see py/app/admin_workspace.py) already computes this
// exact same "clients I can access, owned + shared" list correctly, using
// the service role (bypasses RLS entirely, so there is no policy cascade to
// silently fail) with two flat, verified-correct queries against
// `memberships` and `client_memberships`. Delegating to it here instead of
// re-deriving the same answer a second, more fragile way means there is now
// exactly ONE implementation of "who can access which clients" (matching
// the single-source-of-truth principle already used elsewhere in this
// codebase, e.g. getClientRole/clientRoleMeets), and the switcher can never
// disagree with Admin Workspace about what's shared again.
export async function listSharedClients(userId) {
  if (!isSupabaseConfigured() || !userId) return { ok: true, clients: [] };

  const result = await authedJson("/py/admin/clients");
  if (!result.ok) return { ok: false, error: result.error, clients: [] };
  const clients = (result.clients || [])
    .filter((client) => client.access === "shared")
    .map(({ counts, lastActivityAt, ...client }) => client);
  return { ok: true, clients };
}

export async function listAdminClients() {
  if (!isSupabaseConfigured()) {
    return {
      ok: true,
      clients: DEMO_CLIENTS.map((client, index) => ({
        ...client,
        access: index < 2 ? "owner" : "shared",
        role: index === 3 ? "viewer" : index === 2 ? "editor" : "owner",
        ownerOrg: index < 2 ? { id: "demo-own", name: "Acme Media" } : { id: "demo-shared", name: "AI Data House" },
        counts: { dashboards: 2 + index, reports: 1 + index, integrations: index % 3 },
        lastActivityAt: client.created_at || new Date().toISOString(),
      })),
    };
  }
  const result = await authedJson("/py/admin/clients");
  if (!result.ok) return { ok: false, error: result.error, clients: [] };
  return { ok: true, clients: result.clients || [] };
}

// Client Branding overrides live on the client row itself (branding_override)
// rather than inside either agency's organizations.branding_config - a
// direct RLS-protected write, same pattern as updateClient below. RLS's
// clients_update_editor policy already requires editor+ on EITHER the
// owning org (is_org_editor) or a shared client_memberships role
// (has_client_access(..., 'editor')), so this is correctly gated for both
// owned and shared clients with no separate permission check needed here.
export async function updateClientBrandingOverride(clientId, override) {
  if (!isSupabaseConfigured()) return { ok: false, error: "Auth not configured" };
  if (!clientId) return { ok: false, error: "Client not loaded yet" };

  const { data, error } = await supabase
    .from("clients")
    .update({ branding_override: override || {} })
    .eq("id", clientId)
    .select(CLIENT_COLUMNS)
    .single();

  if (error) return { ok: false, error: error.message };
  return { ok: true, client: data };
}

export async function updateClient(clientId, { name, currency, industry }) {
  if (!isSupabaseConfigured()) return { ok: false, error: "Auth not configured" };
  if (!clientId) return { ok: false, error: "Client not loaded yet" };

  const cleanName = name?.trim();
  if (!cleanName) return { ok: false, error: "Workspace name is required" };

  const { data, error } = await supabase
    .from("clients")
    .update({ name: cleanName, currency, industry })
    .eq("id", clientId)
    .select(CLIENT_COLUMNS)
    .single();

  if (error) return { ok: false, error: error.message };
  return { ok: true, client: data };
}

export async function deleteClient(clientId) {
  if (!isSupabaseConfigured()) return { ok: false, error: "Auth not configured" };
  if (!clientId) return { ok: false, error: "Client not loaded yet" };

  // Mirrors deleteWorkspace()'s exact-count check in settings.js: RLS
  // silently returns 0 rows (no error) when the delete isn't permitted, so
  // an exact count is the only way to tell "blocked" from "deleted".
  const { error, count } = await supabase
    .from("clients")
    .delete({ count: "exact" })
    .eq("id", clientId);

  if (error) return { ok: false, error: error.message };
  if (!count) return { ok: false, error: "You don't have permission to delete this client workspace." };
  return { ok: true };
}
