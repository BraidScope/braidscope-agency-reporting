// SPDX-License-Identifier: AGPL-3.0-only
import { supabase, isSupabaseConfigured } from "./supabase";
import { updateClient, deleteClient } from "./clients";

// Everything the Settings page reads/writes. Profile fields live in Supabase
// Auth user_metadata, workspace fields on the organizations row, team + counters
// come from the org_members/org_counts RPCs, and 2FA uses Supabase's own MFA API.

const NOT_CONFIGURED = { ok: false, error: "Auth not configured" };

const BROWSER_SESSION_KEY = "braidscope:browser-session-id";
function getBrowserSessionId() {
  let id = sessionStorage.getItem(BROWSER_SESSION_KEY);
  if (!id) { id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`; sessionStorage.setItem(BROWSER_SESSION_KEY, id); }
  return id;
}

export const CURRENCIES = [
  { id: "usd", label: "USD ($)" },
  { id: "eur", label: "EUR (€)" },
  { id: "gbp", label: "GBP (£)" },
  { id: "pkr", label: "PKR (₨)" },
  { id: "aed", label: "AED (د.إ)" },
];

export const INDUSTRIES = [
  { id: "marketing", label: "Marketing agency" },
  { id: "ecommerce", label: "E-commerce" },
  { id: "saas", label: "SaaS" },
  { id: "retail", label: "Retail" },
  { id: "other", label: "Other" },
];

export const ASSIGNABLE_ROLES = ["admin", "editor", "viewer"];

export const ROLE_INFO = [
  { id: "owner", name: "Owner", desc: "Full control — billing, team, and workspace deletion." },
  { id: "admin", name: "Admin", desc: "Manage team, clients, integrations and branding." },
  { id: "editor", name: "Editor", desc: "Build dashboards and reports, connect data sources." },
  { id: "viewer", name: "Viewer", desc: "Read-only access to dashboards and reports." },
];

// Category-level preferences, replacing the old per-event matrix — see
// notification_improvements.md section 11/14. `lockedInApp` categories can't
// be turned off in-app (Account & Security is always on).
//
// `emailAvailable` marks categories where the backend actually has an
// email_eligible=True producer wired to check this preference — see
// py/app/supabase_admin.py's create_user_notification(). Everything else
// still renders as a non-interactive "Soon" badge in Settings.jsx: per
// notification_improvements.md's own rule ("do not let users toggle
// controls that have no effect"), a category with no email-worthy producer
// yet must not offer a toggle that would silently do nothing.
export const NOTIFICATION_CATEGORIES = [
  { id: "integrations", label: "Integrations", icon: "ti-plug", desc: "Connection updates, sync issues, reconnect requests.", lockedInApp: false, emailAvailable: true },
  { id: "branding", label: "Branding", icon: "ti-palette", desc: "Publishing and branding completion updates.", lockedInApp: false, emailAvailable: true },
  { id: "reminders", label: "Reminders", icon: "ti-bulb", desc: "Helpful setup and completeness reminders.", lockedInApp: false, emailAvailable: true },
  { id: "account_security", label: "Account & Security", icon: "ti-shield-lock", desc: "Important access and security activity.", lockedInApp: true, emailAvailable: true },
  { id: "reports", label: "Reports", icon: "ti-file-analytics", desc: "Report readiness and delivery updates.", lockedInApp: false, emailAvailable: false },
];

// Icon/label lookup for categories that can appear in a notification row but
// aren't user-configurable in Settings (e.g. "workspace", or "general" for
// anything uncategorized) — keeps the bell/notifications page from showing a
// blank icon for those.
export const NOTIFICATION_CATEGORY_META = {
  ...Object.fromEntries(NOTIFICATION_CATEGORIES.map((c) => [c.id, { label: c.label, icon: c.icon }])),
  workspace: { label: "Workspace", icon: "ti-users" },
  general: { label: "General", icon: "ti-bell" },
};

export function defaultNotificationPrefs() {
  const categories = {};
  for (const cat of NOTIFICATION_CATEGORIES) categories[cat.id] = { inApp: true, email: false };
  return { categories };
}

// Notification prefs are stored per-client (like onboarding progress and
// branding overrides) so switching the active client shows that client's
// own preferences, not a shared organization-wide set. Shape:
// { global: {categories:{...}}, clients: { [clientId]: {categories:{...}} } }.
// A legacy flat { categories: {...} } blob (saved before this reshape) is
// treated as `global` and used as the fallback default for any client that
// doesn't have its own entry yet, so nobody's existing choices are reset.
function normalizeNotificationPrefsTree(saved) {
  if (!saved || typeof saved !== "object") return { global: {}, clients: {} };
  if (saved.global || saved.clients) return { global: saved.global || {}, clients: saved.clients || {} };
  if (saved.categories) return { global: { categories: saved.categories }, clients: {} };
  return { global: {}, clients: {} };
}

// Resolves the effective flat {categories} for a given client - falls back
// to the global bucket (or hardcoded defaults) for any category the client
// hasn't customized. Pass no clientId to resolve the global-only bucket
// (used for account_security, which is genuinely org-level, not per-client).
export function mergeNotificationPrefs(saved, clientId) {
  const tree = normalizeNotificationPrefsTree(saved);
  const base = defaultNotificationPrefs();
  const bucket = (clientId && tree.clients[clientId]) || tree.global || {};
  const categories = { ...base.categories };
  for (const cat of NOTIFICATION_CATEGORIES) {
    const savedCategory = bucket.categories?.[cat.id];
    if (savedCategory && typeof savedCategory === "object") {
      categories[cat.id] = {
        inApp: cat.lockedInApp ? true : savedCategory.inApp !== false,
        email: Boolean(savedCategory.email),
      };
    }
  }
  return { categories };
}

async function settingsRequest(path, options = {}, { requireAuth = true } = {}) {
  if (!isSupabaseConfigured()) return { ok: false, error: "Auth not configured" };
  const { data } = await supabase.auth.getSession();
  if (requireAuth && !data.session?.access_token) return { ok: false, error: "Not signed in" };
  try {
    const authHeaders = data.session?.access_token ? { Authorization: `Bearer ${data.session.access_token}` } : {};
    const response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...authHeaders, ...(options.headers || {}) },
    });
    // A misconfigured/missing dev-server proxy for /py (e.g. `vite preview`
    // without its own `preview.proxy`) falls through to the SPA's
    // index.html with a 200 OK instead of ever reaching the backend - that
    // would otherwise look like a silent success. Any real response from
    // this backend is JSON; anything else is treated as a failure instead
    // of masquerading as one.
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      return { ok: false, error: "Got a non-JSON response — the /py backend may not be reachable (check the dev server proxy)" };
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      // Structured details ({message, code, ...}) carry a machine-readable
      // code alongside the human message; plain string details are unchanged.
      if (payload.detail && typeof payload.detail === "object") {
        return { ok: false, error: payload.detail.message || "Request failed", code: payload.detail.code, invitationId: payload.detail.invitation_id };
      }
      return { ok: false, error: payload.detail || payload.error || "Request failed" };
    }
    return { ok: true, ...payload };
  } catch {
    return { ok: false, error: "Could not reach the account settings service" };
  }
}

export async function inviteTeamMember(organizationId, email, role) {
  return settingsRequest("/py/settings/team/invite", { method: "POST", body: JSON.stringify({ organization_id: organizationId, email, role }) });
}

export async function recordSessionActivity(organizationId) {
  const clientSessionId = getBrowserSessionId();
  return settingsRequest("/py/settings/session-activity", { method: "POST", body: JSON.stringify({ organization_id: organizationId, client_session_id: clientSessionId }) });
}

export async function getRecentSessions() {
  return settingsRequest("/py/settings/sessions", { headers: { "X-Braidscope-Session-Id": getBrowserSessionId() } });
}

export async function getInAppNotifications(organizationId, { clientId, category, unreadOnly, before, limit, scope = "client" } = {}) {
  const params = new URLSearchParams();
  if (organizationId) params.set("organization_id", organizationId);
  if (clientId) params.set("client_id", clientId);
  if (category && category !== "all") params.set("category", category);
  if (unreadOnly) params.set("unread_only", "true");
  if (before) params.set("before", before);
  if (limit) params.set("limit", String(limit));
  if (scope) params.set("scope", scope);
  const qs = params.toString();
  return settingsRequest(`/py/settings/notifications${qs ? `?${qs}` : ""}`);
}

export async function getUnreadNotificationCount(organizationId, clientId, scope = "client") {
  const params = new URLSearchParams();
  if (organizationId) params.set("organization_id", organizationId);
  if (clientId) params.set("client_id", clientId);
  if (scope) params.set("scope", scope);
  const qs = params.toString();
  return settingsRequest(`/py/settings/notifications/unread-count${qs ? `?${qs}` : ""}`);
}

export async function markInAppNotificationsRead(ids) {
  return settingsRequest("/py/settings/notifications/read", { method: "POST", body: JSON.stringify({ ids }) });
}

export async function getAgencyNotificationPrefs(organizationId) {
  return settingsRequest(`/py/organizations/${organizationId}/notification-preferences`);
}

export async function saveAgencyNotificationPrefs(organizationId, prefs) {
  return settingsRequest(`/py/organizations/${organizationId}/notification-preferences`, {
    method: "PUT",
    body: JSON.stringify({ prefs }),
  });
}

export async function notifyBrandingPublished(organizationId, clientId, publishKey) {
  const normalizedClientId = typeof clientId === "string" && clientId.trim() ? clientId : null;
  const normalizedPublishKey = typeof publishKey === "string" && publishKey.trim() ? publishKey : null;
  return settingsRequest("/py/branding/notify-published", {
    method: "POST",
    body: JSON.stringify({
      organization_id: organizationId,
      client_id: normalizedClientId,
      publish_key: normalizedPublishKey,
    }),
  });
}

export async function checkClientReminders(organizationId, clientId) {
  return settingsRequest("/py/reminders/check", {
    method: "POST",
    body: JSON.stringify({ organization_id: organizationId, client_id: clientId }),
  });
}

export async function notifyRoleChanged(organizationId, userId, role) {
  return settingsRequest("/py/account/notify-role-changed", {
    method: "POST",
    body: JSON.stringify({ organization_id: organizationId, user_id: userId, role }),
  });
}

export async function notifyAccountSecurityEvent(organizationId, eventType) {
  return settingsRequest("/py/account/security-notification", {
    method: "POST",
    body: JSON.stringify({ organization_id: organizationId || null, event_type: eventType }),
  });
}

/* ---------------- client team access (shared client workspaces) ---------------- */

// Modules shown in the invite flow's Step 2 permission matrix, in display
// order. This matrix is the single source of truth `lib/permissions.js`
// reads to gate the sidebar, routes, header actions and buttons - it used
// to be purely presentational (one role string per client_memberships row
// was all that was actually enforced), but per-module enforcement is now
// real. Still the SAME 4-role model resolved by role_meets_bar/
// has_client_access/effective_client_role in py/app/client_access.py -
// this is a derived breakdown of that one model, not a second system.
export const PERMISSION_MODULES = ["Dashboards", "Reports", "Integrations", "Branding", "Settings", "Team", "Billing"];

function moduleAccess(view, edit, manage, del) {
  return { view, edit, manage, delete: del };
}
const FULL = moduleAccess(true, true, true, true);
const MANAGE = moduleAccess(true, true, true, false);
const EDIT = moduleAccess(true, true, false, false);
const VIEW = moduleAccess(true, false, false, false);
const NONE = moduleAccess(false, false, false, false);

export const CLIENT_ROLE_INFO = [
  {
    id: "owner",
    name: "Owner",
    desc: "Full control over this client workspace.",
    allowed: ["Billing", "Team management", "Settings", "Integrations", "Branding", "Reports", "Dashboard"],
    matrix: { Dashboards: FULL, Reports: FULL, Integrations: FULL, Branding: FULL, Settings: FULL, Team: FULL, Billing: FULL },
  },
  {
    id: "admin",
    name: "Admin",
    desc: "Manage client workspace operations.",
    allowed: ["Team", "Integrations", "Branding", "Settings sections", "Reports", "Dashboard"],
    matrix: { Dashboards: MANAGE, Reports: MANAGE, Integrations: MANAGE, Branding: MANAGE, Settings: MANAGE, Team: MANAGE, Billing: NONE },
  },
  {
    id: "editor",
    name: "Editor",
    desc: "Create and modify workspace content.",
    allowed: ["Dashboards", "Reports", "Integrations", "Branding"],
    matrix: { Dashboards: EDIT, Reports: EDIT, Integrations: EDIT, Branding: EDIT, Settings: NONE, Team: NONE, Billing: NONE },
  },
  {
    id: "viewer",
    name: "Viewer",
    desc: "Read-only access.",
    allowed: ["View dashboards", "View reports", "View integrations"],
    matrix: { Dashboards: VIEW, Reports: VIEW, Integrations: VIEW, Branding: NONE, Settings: NONE, Team: NONE, Billing: NONE },
  },
];

export async function getClientTeam(clientId) {
  const result = await settingsRequest(`/py/clients/${clientId}/team`);
  if (!result.ok) return { ok: false, error: result.error, members: [] };
  return {
    ok: true,
    members: (result.members || []).map((row) => ({
      userId: row.user_id,
      email: row.email,
      name: row.display_name,
      avatarUrl: row.avatar_url || "",
      role: row.role,
      access: row.access,
      joinedAt: row.joined_at,
      lastSignInAt: row.last_sign_in_at,
    })),
  };
}

export async function getClientInvitations(clientId) {
  return settingsRequest(`/py/clients/${clientId}/invitations`);
}

export async function inviteToClient(clientId, email, role) {
  return settingsRequest(`/py/clients/${clientId}/invitations`, {
    method: "POST",
    body: JSON.stringify({ email, role }),
  });
}

export async function resendClientInvitation(invitationId) {
  return settingsRequest(`/py/invitations/${invitationId}/resend`, { method: "POST" });
}

export async function cancelClientInvitation(invitationId) {
  return settingsRequest(`/py/invitations/${invitationId}/cancel`, { method: "POST" });
}

export async function removeClientMember(clientId, userId) {
  return settingsRequest(`/py/clients/${clientId}/team/${userId}`, { method: "DELETE" });
}

export async function changeClientMemberRole(clientId, userId, role) {
  return settingsRequest(`/py/clients/${clientId}/team/${userId}/role`, {
    method: "POST",
    body: JSON.stringify({ role }),
  });
}

// Public lookup, used from /accept-invitation before the invitee is signed
// in (or even has an account yet) - must not require an existing session
// the way every other settingsRequest() call does.
export async function lookupInvitation(token) {
  return settingsRequest(`/py/invitations/lookup?token=${encodeURIComponent(token)}`, {}, { requireAuth: false });
}

export async function acceptInvitation(token) {
  return settingsRequest("/py/invitations/accept", { method: "POST", body: JSON.stringify({ token }) });
}

/* ---------------- profile ---------------- */

// getUser() is a real network call to Supabase's auth server. When several
// components ask for the profile at the same moment (App's top bar and the
// Settings page both do), they share the ONE request that is already in
// flight instead of each starting their own. Nothing is kept after it
// settles - a later call always fetches fresh - and requests are keyed by the
// current access token, so a call made under a different (or no) session can
// never receive another session's result.
const profileRequestsInFlight = new Map();

export async function getProfile() {
  if (!isSupabaseConfigured()) return { ok: false, error: "Auth not configured", profile: null };

  const { data: sessionData } = await supabase.auth.getSession();
  const key = sessionData?.session?.access_token || "";
  let request = profileRequestsInFlight.get(key);
  if (!request) {
    request = fetchProfile().finally(() => profileRequestsInFlight.delete(key));
    profileRequestsInFlight.set(key, request);
  }
  const result = await request;
  // Each caller gets its own copy, so one caller changing its result can't
  // affect another's.
  return result.ok ? { ...result, profile: { ...result.profile } } : { ...result };
}

async function fetchProfile() {
  const { data, error } = await supabase.auth.getUser();
  if (error) return { ok: false, error: error.message, profile: null };

  const user = data?.user;
  if (!user) return { ok: false, error: "Not signed in", profile: null };

  const meta = user.user_metadata || {};
  return {
    ok: true,
    profile: {
      id: user.id,
      email: user.email || "",
      fullName: meta.full_name || "",
      phone: meta.phone || "",
      avatarUrl: meta.avatar_url || "",
      createdAt: user.created_at || null,
      lastSignInAt: user.last_sign_in_at || null,
    },
  };
}

export async function updateProfile({ fullName, phone }) {
  if (!isSupabaseConfigured()) return NOT_CONFIGURED;

  const { error } = await supabase.auth.updateUser({
    data: {
      full_name: fullName?.trim() || "",
      phone: phone?.trim() || "",
    },
  });

  if (error) return { ok: false, error: error.message };
  return { ok: true };
}

// The user's own profile photo — kept separate from updateProfile() so the
// name/phone form's dirty-state and save button never depend on it (mirrors
// the immediate-upload pattern the workspace logo used to use). Stored in
// Supabase Auth's own user_metadata, same place as full_name/phone, so no
// schema change or storage bucket is needed. Auth metadata rides along in
// EVERY access token/Authorization header, so this must stay tiny — the
// caller compresses the source image down via compressImageToSquareDataUrl()
// before ever calling this. MAX_STORED_AVATAR_BYTES is enforced again here
// as a hard safety net: writing anything larger risks the JWT tripping the
// dev server's (and some proxies') ~16KB request-header-size limit, which
// previously surfaced as every request failing with a 431 error and the
// unrelated-looking "Got a non-JSON response" message.
export const MAX_STORED_AVATAR_BYTES = 12 * 1024;

export async function updateProfilePhoto(dataUrl) {
  if (!isSupabaseConfigured()) return NOT_CONFIGURED;
  if (dataUrl && dataUrl.length > MAX_STORED_AVATAR_BYTES) {
    return { ok: false, error: "Photo is too large to store — try a smaller image" };
  }
  const { error } = await supabase.auth.updateUser({ data: { avatar_url: dataUrl || "" } });
  if (error) return { ok: false, error: error.message };
  return { ok: true };
}

/* ---------------- workspace ---------------- */

export async function getWorkspaceCounts(organizationId) {
  if (!isSupabaseConfigured() || !organizationId) {
    return { ok: true, counts: null };
  }

  const { data, error } = await supabase.rpc("org_counts", { org_id: organizationId });
  if (error) return { ok: false, error: error.message, counts: null };

  const row = Array.isArray(data) ? data[0] : data;
  if (!row) return { ok: true, counts: null };

  return {
    ok: true,
    counts: {
      clients: Number(row.clients) || 0,
      dashboards: Number(row.dashboards) || 0,
      reports: Number(row.reports) || 0,
      connectors: Number(row.connectors) || 0,
    },
  };
}

// `slug` and `logoUrl` are only ever set by callers that manage those fields
// explicitly (neither is edited from Settings anymore) — when omitted, the
// existing column values are left untouched rather than overwritten/cleared.
export async function updateWorkspace(organizationId, { name, slug, currency, industry, logoUrl, brandingConfig }) {
  if (!isSupabaseConfigured()) return NOT_CONFIGURED;
  if (!organizationId) return { ok: false, error: "Workspace not loaded yet" };

  const cleanName = name?.trim();
  if (!cleanName) return { ok: false, error: "Workspace name is required" };

  const payload = {
    name: cleanName,
    // currency/industry have no dedicated columns, so they ride along in the
    // same jsonb blob the Branding page already persists.
    branding_config: { ...(brandingConfig || {}), currency, industry },
  };
  if (typeof slug === "string") {
    const cleanSlug = slug.trim().toLowerCase().replace(/[^a-z0-9-]/g, "-");
    if (!cleanSlug) return { ok: false, error: "Workspace URL is required" };
    payload.slug = cleanSlug;
  }
  if (typeof logoUrl === "string") {
    payload.logo_url = logoUrl || null;
  }

  const { data, error } = await supabase
    .from("organizations")
    .update(payload)
    .eq("id", organizationId)
    .select("id, name, slug, accent_color, logo_url, branding_config")
    .single();

  if (error) {
    if (error.code === "23505") return { ok: false, error: "That workspace URL is already taken" };
    return { ok: false, error: error.message };
  }
  return { ok: true, organization: data };
}

export async function deleteWorkspace(organizationId) {
  if (!isSupabaseConfigured()) return NOT_CONFIGURED;
  if (!organizationId) return { ok: false, error: "Workspace not loaded yet" };

  // A delete blocked entirely by RLS (no matching policy, or the caller isn't
  // the owner) still returns { error: null, data: [] } — a plain .delete()
  // can't tell "nothing was allowed to be deleted" apart from "deleted it".
  // Requesting an exact count makes that distinction explicit so this can
  // never again report success for a workspace that's still there.
  const { error, count } = await supabase
    .from("organizations")
    .delete({ count: "exact" })
    .eq("id", organizationId);

  if (error) return { ok: false, error: error.message };
  if (!count) return { ok: false, error: "You don't have permission to delete this workspace." };
  return { ok: true };
}

// Client-scoped equivalents of updateWorkspace/deleteWorkspace above.
// Settings -> Workspace represents the selected CLIENT workspace, not the
// whole agency account, so these write to the clients row instead of
// organizations. deleteWorkspace() (account-level) is kept as-is for its
// existing RPC/policy but is intentionally no longer called from the UI —
// Danger Zone now deletes only the selected client.
export async function getClientCounts(clientId) {
  if (!isSupabaseConfigured() || !clientId) return { ok: true, counts: null };

  const { data, error } = await supabase.rpc("client_counts", { client_id: clientId });
  if (error) return { ok: false, error: error.message, counts: null };

  const row = Array.isArray(data) ? data[0] : data;
  if (!row) return { ok: true, counts: null };

  return {
    ok: true,
    counts: {
      dashboards: Number(row.dashboards) || 0,
      reports: Number(row.reports) || 0,
      connectors: Number(row.connectors) || 0,
    },
  };
}

export async function updateClientWorkspace(clientId, { name, currency, industry }) {
  const result = await updateClient(clientId, { name, currency, industry });
  if (!result.ok) return result;
  return { ok: true, client: result.client };
}

export async function deleteClientWorkspace(clientId) {
  return deleteClient(clientId);
}

/* ---------------- team ---------------- */

export async function listTeamMembers(organizationId) {
  if (!isSupabaseConfigured() || !organizationId) return { ok: true, members: [] };

  const { data, error } = await supabase.rpc("org_members", { org_id: organizationId });
  if (error) return { ok: false, error: error.message, members: [] };

  return {
    ok: true,
    members: (data || []).map((row) => ({
      userId: row.user_id,
      email: row.email,
      name: row.display_name,
      role: row.role,
      joinedAt: row.joined_at,
      lastSignInAt: row.last_sign_in_at,
    })),
  };
}

export async function updateMemberRole(organizationId, userId, role) {
  if (!isSupabaseConfigured()) return NOT_CONFIGURED;
  if (!organizationId || !userId) return { ok: false, error: "Member not found" };
  if (!ASSIGNABLE_ROLES.includes(role)) return { ok: false, error: "Invalid team role" };

  const { data, error } = await supabase
    .from("memberships")
    .update({ role })
    .eq("organization_id", organizationId)
    .eq("user_id", userId)
    .select("user_id, role")
    .single();

  if (error) return { ok: false, error: error.message };
  return { ok: true, member: data };
}

export async function removeMember(organizationId, userId) {
  return settingsRequest("/py/settings/team/remove", { method: "POST", body: JSON.stringify({ organization_id: organizationId, user_id: userId }) });
}

/* ---------------- notification preferences ---------------- */

export async function getNotificationPrefs(organizationId, userId, clientId) {
  if (!isSupabaseConfigured() || !organizationId || !userId) {
    return { ok: true, prefs: defaultNotificationPrefs() };
  }
  if (clientId) return settingsRequest(`/py/clients/${clientId}/notification-preferences`);

  const { data, error } = await supabase
    .from("memberships")
    .select("notification_prefs")
    .eq("organization_id", organizationId)
    .eq("user_id", userId)
    .maybeSingle();

  if (error) return { ok: false, error: error.message, prefs: defaultNotificationPrefs() };
  return { ok: true, prefs: mergeNotificationPrefs(data?.notification_prefs, clientId) };
}

export async function saveNotificationPrefs(organizationId, userId, clientId, prefs) {
  if (!isSupabaseConfigured()) return NOT_CONFIGURED;
  if (!organizationId || !userId) return { ok: false, error: "Workspace not loaded yet" };
  if (clientId) {
    return settingsRequest(`/py/clients/${clientId}/notification-preferences`, {
      method: "PUT",
      body: JSON.stringify({ prefs }),
    });
  }

  // Read-modify-write so only this client's bucket changes - every other
  // client's saved prefs in the same JSONB column are preserved untouched.
  const { data: current, error: readError } = await supabase
    .from("memberships")
    .select("notification_prefs")
    .eq("organization_id", organizationId)
    .eq("user_id", userId)
    .maybeSingle();
  if (readError) return { ok: false, error: readError.message };

  const tree = normalizeNotificationPrefsTree(current?.notification_prefs);
  const nextTree = clientId
    ? { ...tree, clients: { ...tree.clients, [clientId]: { categories: prefs.categories } } }
    : { ...tree, global: { categories: prefs.categories } };

  const { error } = await supabase
    .from("memberships")
    .update({ notification_prefs: nextTree })
    .eq("organization_id", organizationId)
    .eq("user_id", userId);

  if (error) return { ok: false, error: error.message };
  return { ok: true };
}

/* ---------------- database-backed UI preferences ---------------- */

export async function getUiPreferences(organizationId, userId) {
  if (!isSupabaseConfigured() || !organizationId || !userId) return { ok: true, preferences: {} };
  const { data, error } = await supabase.from("memberships").select("ui_preferences").eq("organization_id", organizationId).eq("user_id", userId).maybeSingle();
  if (error) return { ok: false, error: error.message, preferences: {} };
  return { ok: true, preferences: data?.ui_preferences || {} };
}

export async function saveUiPreferences(organizationId, userId, preferences) {
  if (!isSupabaseConfigured()) return NOT_CONFIGURED;
  const { error } = await supabase.from("memberships").update({ ui_preferences: preferences }).eq("organization_id", organizationId).eq("user_id", userId);
  if (error) return { ok: false, error: error.message };
  return { ok: true };
}
/* ---------------- security ---------------- */

export async function updatePassword(newPassword) {
  if (!isSupabaseConfigured()) return NOT_CONFIGURED;
  if (!newPassword || newPassword.length < 8) {
    return { ok: false, error: "Password must be at least 8 characters" };
  }

  const { error } = await supabase.auth.updateUser({ password: newPassword });
  if (error) return { ok: false, error: error.message };
  return { ok: true };
}

// Supabase verifies the *current* password by attempting a fresh sign-in with
// it — updateUser alone would let anyone with an open tab change the password.
export async function reauthenticate(email, password) {
  if (!isSupabaseConfigured()) return NOT_CONFIGURED;
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) return { ok: false, error: "Current password is incorrect" };
  return { ok: true };
}

export async function listMfaFactors() {
  if (!isSupabaseConfigured()) return { ok: true, factors: [] };

  const { data, error } = await supabase.auth.mfa.listFactors();
  if (error) return { ok: false, error: error.message, factors: [] };

  const totp = (data?.totp || []).filter((f) => f.status === "verified");
  return { ok: true, factors: totp };
}

export async function startMfaEnrollment() {
  if (!isSupabaseConfigured()) return NOT_CONFIGURED;

  const { data, error } = await supabase.auth.mfa.enroll({ factorType: "totp" });
  if (error) return { ok: false, error: error.message };

  return {
    ok: true,
    factorId: data.id,
    qrCode: data.totp?.qr_code || "",
    secret: data.totp?.secret || "",
  };
}

export async function confirmMfaEnrollment(factorId, code) {
  if (!isSupabaseConfigured()) return NOT_CONFIGURED;
  if (!code?.trim()) return { ok: false, error: "Enter the 6-digit code" };

  const challenge = await supabase.auth.mfa.challenge({ factorId });
  if (challenge.error) return { ok: false, error: challenge.error.message };

  const { error } = await supabase.auth.mfa.verify({
    factorId,
    challengeId: challenge.data.id,
    code: code.trim(),
  });
  if (error) return { ok: false, error: error.message };
  return { ok: true };
}

export async function disableMfa(factorId) {
  if (!isSupabaseConfigured()) return NOT_CONFIGURED;

  const { error } = await supabase.auth.mfa.unenroll({ factorId });
  if (error) return { ok: false, error: error.message };
  return { ok: true };
}

// Supabase exposes no session list to the browser (that is admin-API only), but
// it can revoke every session except the current one.
export async function signOutOtherSessions() {
  if (!isSupabaseConfigured()) return NOT_CONFIGURED;

  const { error } = await supabase.auth.signOut({ scope: "others" });
  if (error) return { ok: false, error: error.message };
  return { ok: true };
}

/* ---------------- formatting ---------------- */

export function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

export function formatRelative(value) {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Never";

  const diffMs = Date.now() - date.getTime();
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} min ago`;

  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;

  const days = Math.round(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;
  return formatDate(value);
}
