// SPDX-License-Identifier: AGPL-3.0-only
// Central frontend permission engine - the first real consumer of
// CLIENT_ROLE_INFO's per-module matrix (lib/settings.js), which used to be
// purely presentational. Built directly on the existing role-resolution
// primitives (getClientRole/clientRoleMeets, lib/clients.js) rather than a
// second role vocabulary - there is exactly one role model, resolved the
// same way the backend/RLS resolve it (effective_client_role).
//
// Every check here is a UX convenience (hide a button, redirect a route).
// It is NOT the security boundary - the real one is the backend's
// require_client_role/assert_client_role checks and RLS policies
// (py/app/client_access.py, supabase/migrations/20260811_001_...,
// 20260919_003_client_sharing.sql). A user who bypasses the frontend
// entirely still hits those.
import { getClientRole } from "./clients";
import { CLIENT_ROLE_INFO } from "./settings";

const ROLE_MATRIX = Object.fromEntries(CLIENT_ROLE_INFO.map((info) => [info.id, info.matrix]));

// Sidebar/route view id -> permission-matrix module name. `null` means the
// view is never gated by the ACTIVE CLIENT's role - Workspace is the base
// landing view every role can reach, Agency Branding belongs to the
// viewer's OWN agency (never the shared client, see
// SHARED_CLIENT_CONTEXT_FIX.md), and Settings hosts the Account/Security/
// Notifications tabs, which belong to the signed-in PERSON, not the active
// client - the spec itself says the Settings page stays visible, with only
// its client-scoped sections (Workspace info, Team, Billing) gated inside
// (see Settings.jsx's canViewTeamSection/canViewBillingSection).
export const NAV_MODULE_MAP = {
  workspace: null,
  dashboards: "Dashboards",
  reports: "Reports",
  integrations: "Integrations",
  branding: "Branding",
  agencyBranding: null,
  settings: null,
};

function moduleAccessForRole(role, module) {
  return ROLE_MATRIX[role]?.[module] || null;
}

function moduleAccess(client, org, module) {
  return moduleAccessForRole(getClientRole(client, org), module);
}

// module: one of PERMISSION_MODULES (lib/settings.js). action: "view" when
// omitted - use canPerformAction for edit/manage/delete.
export function canAccess(client, org, module, action = "view") {
  return Boolean(moduleAccess(client, org, module)?.[action]);
}

export function canPerformAction(client, org, module, action) {
  return Boolean(moduleAccess(client, org, module)?.[action]);
}

// Lower-level variant for callers that have already resolved the role
// string themselves (e.g. Settings.jsx, which tracks a fresher role than a
// plain client/org pair would give it - see its own `clientRole` comment).
// Same matrix, so there is still exactly one enforcement table.
export function roleCanAccess(role, module, action = "view") {
  return Boolean(moduleAccessForRole(role, module)?.[action]);
}

// Whether a sidebar/route view should be reachable at all for the active
// client. Views with no module mapping (Workspace, Agency Branding) are
// always reachable once the user is authenticated.
export function canAccessView(client, org, viewId) {
  const module = NAV_MODULE_MAP[viewId];
  if (module === null || module === undefined) return true;
  return canAccess(client, org, module, "view");
}

// "New client" creates a workspace under the viewer's OWN org, regardless
// of which (possibly shared) client happens to be selected right now - so
// this checks the org membership role directly, not the active client's
// resolved role. Per the spec: Owner/Admin can create clients; Editor/
// Viewer cannot. Demo mode (no org yet) stays permissive so the empty-state
// "Add client workspace" CTA still works pre-signup.
export function canCreateClient(org) {
  if (!org) return true;
  return org.role === "owner" || org.role === "admin";
}

export function labelForRole(role) {
  return role ? role[0].toUpperCase() + role.slice(1) : "";
}

export function currentClientRoleLabel(client, org) {
  return labelForRole(getClientRole(client, org));
}

// "a Viewer" vs "an Editor/Admin/Owner".
export function articledRoleLabel(roleLabel) {
  if (!roleLabel) return "a member";
  const article = /^[aeiou]/i.test(roleLabel) ? "an" : "a";
  return `${article} ${roleLabel}`;
}

// Matches the spec's exact denial copy, reused everywhere a blocked action
// needs to explain itself (a disabled control that's still reachable via
// keyboard, or a toast on an attempted click).
export function permissionDeniedMessage(client, org) {
  const who = articledRoleLabel(currentClientRoleLabel(client, org));
  return `You are ${who} of this client workspace. You don't have permission to perform this action.`;
}
