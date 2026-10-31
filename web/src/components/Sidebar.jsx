// SPDX-License-Identifier: AGPL-3.0-only
import { NAV_ITEMS } from "../lib/data";
import { canAccessView } from "../lib/permissions";

// Short git hash baked in at build time (web/Dockerfile ARG VITE_GIT_COMMIT,
// set by the deploy workflow). Shown in the sidebar foot so anyone can see
// which commit is live without server access. Local dev shows "dev".
const BUILD_COMMIT = import.meta.env.VITE_GIT_COMMIT || "dev";

export default function Sidebar({ view, onGoTo, collapsed, onToggle, client, org }) {
  // Demo mode (org is null) and the brief pre-load window before the client
  // list/role has resolved both show every item - the real gate only
  // applies once there's an actual org + client to resolve a role against.
  // Otherwise a demo visitor or a brand-new account mid-onboarding would
  // see an empty sidebar instead of the full nav their own Owner role
  // grants them.
  const visibleItems = org && client
    ? NAV_ITEMS.filter((item) => canAccessView(client, org, item.id))
    : NAV_ITEMS;

  return (
    <aside className={`sidebar${collapsed ? " collapsed" : ""}`}>
      <button
        className="side-toggle"
        type="button"
        onClick={onToggle}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
      >
        <i className={`ti ${collapsed ? "ti-chevron-right" : "ti-chevron-left"}`} />
      </button>

      <nav className="side-nav">
        {visibleItems.map((item) => (
          <div
            key={item.id}
            id={`nav-${item.id}`}
            className={`side-item${view === item.id ? " active" : ""}`}
            onClick={() => onGoTo(item.id)}
            title={collapsed ? item.label : undefined}
          >
            <i className={`ti ${item.icon}`} />
            <span>{item.label}</span>
          </div>
        ))}
      </nav>

      <div className="side-foot">
        <div className="side-divider" />
        <div className="side-tip">
          <div className="side-tip-head"><i className="ti ti-bolt" /> Trial</div>
          <div className="side-tip-body">21 days left on your trial.</div>
          <button className="side-tip-btn">Upgrade</button>
        </div>
        <div className="side-build" title="Build commit">build {BUILD_COMMIT}</div>
      </div>
    </aside>
  );
}
