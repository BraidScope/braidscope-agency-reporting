// SPDX-License-Identifier: AGPL-3.0-only
// Route-level permission guard's fallback - rendered in place of a page
// whose module the active client role can't reach at all (e.g. an Editor or
// Viewer navigating straight to /branding?client=... by URL). Distinct from
// a disabled button: this is what closes the "direct URL access" gap the
// RBAC audit spec calls out - hiding the sidebar link isn't enough on its
// own since the route itself was still reachable.
import { articledRoleLabel } from "../lib/permissions";

export default function AccessDenied({ moduleName, roleLabel }) {
  return (
    <div className="page access-denied-page">
      <div className="access-denied-card">
        <span className="access-denied-icon" aria-hidden="true"><i className="ti ti-lock" /></span>
        <h1>You don't have access to this page</h1>
        <p>
          {roleLabel ? `You are ${articledRoleLabel(roleLabel)} of this client workspace. ` : ""}
          {moduleName ? `${moduleName} isn't available at your permission level.` : "This page isn't available at your permission level."}
        </p>
      </div>
    </div>
  );
}
