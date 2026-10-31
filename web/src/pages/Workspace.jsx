// SPDX-License-Identifier: AGPL-3.0-only
import { HERO_KPIS, PAGES, ACTIVITY } from "../lib/data";
import { canCreateClient, canPerformAction } from "../lib/permissions";

export default function Workspace({ onGoTo, client, org, clientsLoading, clientsError, sourceSummary, onAddClient, hideEmptyState = false, hideContent = false }) {
  if (clientsLoading) {
    return (
      <div className="page">
        <div className="empty-state">
          <i className="ti ti-loader-2" />
          <div className="empty-title">Loading client workspace</div>
          <div className="empty-sub">Fetching your Supabase client list.</div>
        </div>
      </div>
    );
  }

  if (clientsError) {
    return (
      <div className="page">
        <div className="empty-state">
          <i className="ti ti-alert-triangle" />
          <div className="empty-title">Could not load clients</div>
          <div className="empty-sub">{clientsError}</div>
        </div>
      </div>
    );
  }

  if ((!client && hideEmptyState) || hideContent) {
    return <div className="page workspace-onboarding-page" aria-hidden="true" />;
  }

  if (!client) {
    return (
      <div className="page">
        <div className="empty-state">
          <i className="ti ti-building-store" />
          <div className="empty-title">No client workspaces yet</div>
          <div className="empty-sub">Create your first client to connect sources and build dashboards.</div>
          {canCreateClient(org) && (
            <button className="primary-btn" onClick={onAddClient}>
              <i className="ti ti-plus" /> Add client workspace
            </button>
          )}
        </div>
      </div>
    );
  }

  const showNewClient = canCreateClient(org);
  const showBuildReport = canPerformAction(client, org, "Reports", "edit");

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-eyebrow">Workspace</div>
          <h1 className="page-title">{client.name}</h1>
          <div className="page-sub">6 pages · {sourceSummary.headline}</div>
        </div>
        <div className="page-actions">
          {showNewClient && (
            <button className="ghost-btn" onClick={onAddClient}><i className="ti ti-plus" /> New client</button>
          )}
          {showBuildReport && (
            <button className="primary-btn" onClick={() => onGoTo("reports")}>
              <i className="ti ti-file-export" /> Build report
            </button>
          )}
        </div>
      </div>

      <div className="hero-row">
        {HERO_KPIS.map((k) => (
          <div key={k.label} className="hero-stat">
            <div className="hero-label">{k.label}</div>
            <div className="hero-value">
              {k.value}
              <span className={`hero-delta ${k.cls}`}>
                <i className={`ti ti-arrow-${k.cls === "up" ? "up" : "down"}-right`} /> {k.delta}
              </span>
            </div>
            <div className="hero-sub">{k.sub}</div>
          </div>
        ))}
      </div>

      <div className="section-bar">
        <div className="section-title">Your pages</div>
        <a className="section-link" onClick={() => onGoTo("dashboards")}>View all dashboards →</a>
      </div>

      <div className="pages-grid">
        {PAGES.map((p) => (
          <div key={p.id} className="pcard" onClick={() => onGoTo("dashboards")}>
            <div className="pcard-head">
              <div className="pcard-ico"><i className={`ti ${p.icon}`} /></div>
              <span className="pcard-name">{p.name}</span>
              <span className={`pcard-flag ${p.flagCls}`}>{p.flag}</span>
            </div>
            <div className="kpis">
              {p.kpis.map(([l, v, d, cls]) => (
                <div key={l} className="kpi">
                  <div className="l">{l}</div>
                  <div className="v">
                    {v}
                    {d && <span className={cls}> {d}</span>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="section-bar">
        <div className="section-title">Recent activity</div>
      </div>
      <div className="activity-list">
        {ACTIVITY.map((a, i) => (
          <div key={i} className="activity-row">
            <div className="activity-ico" style={{ background: a.color }}>
              <i className={`ti ${a.ico}`} />
            </div>
            <div className="activity-text">{a.text}</div>
            <div className="activity-time">{a.time}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
