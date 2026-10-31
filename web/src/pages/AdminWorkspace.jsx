// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import AgencyBranding from "./AgencyBranding";
import Settings from "./Settings";
import Notifications from "./Notifications";
import { getAgencyNotificationPrefs, saveAgencyNotificationPrefs } from "../lib/settings";
import { listAdminClients } from "../lib/clients";

const SECTIONS = [
  { id: "clients", label: "Clients", icon: "ti-building-store" },
  { id: "branding", label: "Branding", icon: "ti-palette" },
  { id: "security", label: "Security", icon: "ti-shield-lock" },
  { id: "notifications", label: "Notifications", icon: "ti-bell" },
];
const OWNERSHIP_FILTERS = [
  { id: "all", label: "All" },
  { id: "owner", label: "Owner" },
  { id: "shared", label: "Shared" },
];
const ROLES = ["owner", "admin", "editor", "viewer"];

function labelize(value) {
  return value ? value[0].toUpperCase() + value.slice(1) : "";
}

function formatActivity(value) {
  if (!value) return "No activity";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "No activity";
  return date.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

function AdminClients({ clients, loading, error, onRetry, onManage, agencyName, orgName }) {
  // Owned clients belong to the signed-in agency - show its name (the same one
  // the topbar shows); shared clients show their own owning agency.
  const agencyLabel = (client) => (
    client.access === "shared"
      ? client.ownerOrg?.name || "Unknown agency"
      : agencyName || client.ownerOrg?.name || orgName || "Unknown agency"
  );
  const [query, setQuery] = useState("");
  const [ownership, setOwnership] = useState("all");
  const [agency, setAgency] = useState("all");
  const [role, setRole] = useState("all");

  const sharedAgencies = useMemo(() => {
    const byId = new Map();
    clients.forEach((client) => {
      if (client.access !== "shared" || !client.ownerOrg?.id) return;
      byId.set(client.ownerOrg.id, client.ownerOrg.name || "Unnamed agency");
    });
    return Array.from(byId, ([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
  }, [clients]);

  useEffect(() => {
    if (agency !== "all" && !sharedAgencies.some((item) => item.id === agency)) setAgency("all");
  }, [agency, sharedAgencies]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return clients.filter((client) => {
      const fields = [client.name, client.industry, client.currency, agencyLabel(client), client.role].join(" ").toLowerCase();
      if (needle && !fields.includes(needle)) return false;
      if (ownership !== "all" && client.access !== ownership) return false;
      if (agency !== "all" && client.ownerOrg?.id !== agency) return false;
      if (role !== "all" && client.role !== role) return false;
      return true;
    });
  }, [agency, clients, ownership, query, role]);

  const ownedCount = clients.filter((client) => client.access !== "shared").length;
  const sharedCount = clients.length - ownedCount;
  const hasFilters = query || ownership !== "all" || agency !== "all" || role !== "all";

  return (
    <section className="admin-section">
      <div className="admin-section-head">
        <div>
          <h2>Clients</h2>
          <p>Manage and access all workspaces available to you.</p>
        </div>
        <div className="admin-client-stats" aria-label="Client summary">
          <span><b>{clients.length}</b> total clients</span>
          <span><b>{ownedCount}</b> owned</span>
          <span><b>{sharedCount}</b> shared</span>
        </div>
      </div>

      <div className="admin-controls">
        <label className="admin-search">
          <i className="ti ti-search" aria-hidden="true" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search clients..." />
          {query && <button type="button" onClick={() => setQuery("")} aria-label="Clear search"><i className="ti ti-x" /></button>}
        </label>
        <div className="admin-filter-row">
          <div className="admin-segmented" aria-label="Ownership">
            {OWNERSHIP_FILTERS.map((item) => (
              <button key={item.id} type="button" className={ownership === item.id ? "active" : ""} onClick={() => setOwnership(item.id)}>
                {item.label}
              </button>
            ))}
          </div>
          {sharedAgencies.length > 0 && (
            <select className="admin-select" value={agency} onChange={(event) => setAgency(event.target.value)} aria-label="Shared agency">
              <option value="all">All agencies</option>
              {sharedAgencies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          )}
          <select className="admin-select" value={role} onChange={(event) => setRole(event.target.value)} aria-label="Role">
            <option value="all">All roles</option>
            {ROLES.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}
          </select>
        </div>
      </div>

      {loading && (
        <div className="admin-table skeleton-table">
          {[0, 1, 2, 3].map((item) => <div className="admin-skel-row" key={item}><span /><span /><span /><span /></div>)}
        </div>
      )}
      {!loading && error && (
        <div className="admin-empty danger">
          <i className="ti ti-alert-triangle" />
          <b>Could not load clients.</b>
          <span>{error}</span>
          <button type="button" className="ghost-btn" onClick={onRetry}>Retry</button>
        </div>
      )}
      {!loading && !error && clients.length === 0 && (
        <div className="admin-empty">
          <i className="ti ti-building-store" />
          <b>No client workspaces yet.</b>
          <span>Create a client workspace from the normal workspace flow to see it here.</span>
        </div>
      )}
      {!loading && !error && clients.length > 0 && filtered.length === 0 && (
        <div className="admin-empty">
          <i className="ti ti-filter-off" />
          <b>No clients match your search.</b>
          <span>Clear filters to return to all available workspaces.</span>
          {hasFilters && <button type="button" className="ghost-btn" onClick={() => { setQuery(""); setOwnership("all"); setAgency("all"); setRole("all"); }}>Clear filters</button>}
        </div>
      )}
      {!loading && !error && filtered.length > 0 && (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Client</th>
                <th>Type</th>
                <th>Agency</th>
                <th>Role</th>
                <th>Industry</th>
                <th>Currency</th>
                <th>Reports</th>
                <th>Integrations</th>
                <th>Last activity</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((client) => (
                <tr key={client.id}>
                  <td className="admin-client-cell">
                    <span className="admin-client-mark">{(client.name || "C")[0]}</span>
                    <span><b>{client.name}</b><em>{client.id}</em></span>
                  </td>
                  <td><span className={`admin-badge ${client.access === "shared" ? "shared" : "owned"}`}>{client.access === "shared" ? "Shared" : "Owned"}</span></td>
                  <td className="admin-muted">{agencyLabel(client)}</td>
                  <td><span className={`role-badge role-${client.role || "viewer"}`}>{labelize(client.role || "viewer")}</span></td>
                  <td className="admin-muted">{labelize(client.industry || "marketing")}</td>
                  <td className="admin-muted">{(client.currency || "usd").toUpperCase()}</td>
                  <td>{client.counts?.reports ?? 0}</td>
                  <td>{client.counts?.integrations ?? 0}</td>
                  <td className="admin-muted">{formatActivity(client.lastActivityAt)}</td>
                  <td><button type="button" className="admin-manage-btn" onClick={() => onManage(client.id)}>Manage</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function AdminWorkspace({
  clients: appClients,
  org,
  selectedClient,
  agencyName,
  setAgencyName,
  accent,
  hexInput,
  setHexInput,
  applyHex,
  sourceSummary,
  branding,
  onBrandingChange,
  onSaveBranding,
  brandingSaving,
  brandingError,
  brandingSaved,
  savedBrandingSnapshot,
  onEditClientBranding,
  onManageClient,
  onGoTo,
  settingsProps,
  clientsLoaded = false,
  onNotificationsChanged,
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const sectionParam = new URLSearchParams(location.search).get("section");
  const activeSection = SECTIONS.some((section) => section.id === sectionParam) ? sectionParam : "clients";
  const [clients, setClients] = useState(appClients || []);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [agencyPrefs, setAgencyPrefs] = useState({ categories: { branding: { inApp: true, email: false }, account_security: { inApp: true, email: false } } });
  const [prefsMessage, setPrefsMessage] = useState("");

  useEffect(() => {
    if (activeSection !== "notifications" || !org?.id) return;
    getAgencyNotificationPrefs(org.id).then((result) => {
      if (result.ok) setAgencyPrefs(result.prefs);
    });
  }, [activeSection, org?.id]);

  const updateAgencyPref = (category, channel) => {
    setAgencyPrefs((current) => ({
      ...current,
      categories: {
        ...current.categories,
        [category]: { ...current.categories[category], [channel]: !current.categories[category]?.[channel] },
      },
    }));
    setPrefsMessage("");
  };

  const saveAgencyPrefs = async () => {
    const result = await saveAgencyNotificationPrefs(org.id, agencyPrefs);
    setPrefsMessage(result.ok ? "Notification preferences saved." : (result.error || "Could not save preferences."));
  };

  useEffect(() => {
    setClients(appClients || []);
  }, [appClients]);

  // The per-client counts / last activity only come from /py/admin/clients
  // (App's own client list has none), so this page loads them itself every
  // time it opens - it used to get them only because App happened to refetch
  // its client list on route changes.
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    listAdminClients().then((result) => {
      if (!alive) return;
      setLoading(false);
      if (!result.ok) {
        setError(result.error || "Could not load clients");
        return;
      }
      setClients(result.clients || []);
    });
    return () => { alive = false; };
  }, [clientsLoaded, refreshKey]);

  const changeSection = (section) => {
    const params = new URLSearchParams(location.search);
    params.set("section", section);
    navigate({ pathname: "/admin", search: params.toString() });
  };

  return (
    <main className="admin-workspace">
      <header className="admin-hero">
        <div>
          <span className="admin-eyebrow"><i className="ti ti-command" /> Control center</span>
          <h1>Admin Workspace</h1>
          <p>{org?.name ? `${org.name} administration, shared access, branding and account security.` : "Administration, shared access, branding and account security."}</p>
        </div>
      </header>

      <nav className="admin-tabs" aria-label="Admin Workspace sections">
        {SECTIONS.map((section) => (
          <button
            key={section.id}
            type="button"
            className={activeSection === section.id ? "active" : ""}
            onClick={() => changeSection(section.id)}
          >
            <i className={`ti ${section.icon}`} aria-hidden="true" />
            {section.label}
          </button>
        ))}
      </nav>

      {activeSection === "clients" && (
        <AdminClients
          clients={clients}
          loading={loading}
          error={error}
          onRetry={() => setRefreshKey((key) => key + 1)}
          onManage={onManageClient}
          agencyName={agencyName}
          orgName={org?.name}
        />
      )}
      {activeSection === "branding" && (
        <div className="admin-branding-shell">
          <AgencyBranding
            active
            agencyName={agencyName}
            setAgencyName={setAgencyName}
            accent={accent}
            hexInput={hexInput}
            setHexInput={setHexInput}
            applyHex={applyHex}
            client={selectedClient}
            clients={clients.filter((client) => client.access !== "shared")}
            sourceSummary={sourceSummary}
            branding={branding}
            onBrandingChange={onBrandingChange}
            onSave={onSaveBranding}
            saving={brandingSaving}
            error={brandingError}
            saved={brandingSaved}
            savedSnapshot={savedBrandingSnapshot}
            onEditClientBranding={onEditClientBranding}
          />
        </div>
      )}
      {activeSection === "security" && (
        <Settings {...settingsProps} active adminSection="security" />
      )}
      {activeSection === "notifications" && (
        // headerAside renders this compact preferences card beside the
        // "Notifications" heading itself (Notifications.jsx's own
        // .page-head), NOT beside the tabs/list - so the tabs and the
        // notification list below stay full-width, instead of being
        // squeezed into a left column for their entire (much taller)
        // height next to a short preferences card. Same
        // state/handlers/markup (agencyPrefs/updateAgencyPref/
        // saveAgencyPrefs/prefsMessage, .notif-table/.notif-category-*) as
        // before - this is a layout/positioning change only.
        <Notifications
          org={org}
          client={null}
          scope="agency"
          onGoTo={onGoTo}
          onNotificationsChanged={onNotificationsChanged}
          headerAside={(
            <section className="admin-notif-prefs" aria-label="Agency notification preferences">
              <div className="admin-notif-prefs-head">
                <div><h3>Notification preferences</h3><p>In-app and email delivery for your agency account.</p></div>
                <button type="button" className="primary-btn" onClick={saveAgencyPrefs}>Save preferences</button>
              </div>
              {prefsMessage && <div className="inline-success compact"><i className="ti ti-check" /> {prefsMessage}</div>}
              <div className="notif-table">
                <div className="notif-category-head"><span /><span>In-app</span><span>Email</span></div>
                {[["branding", "Branding"], ["account_security", "Security"]].map(([id, label]) => (
                  <div className="notif-category-row" key={id}>
                    <div className="notif-category-main">
                      <span className="notif-category-icn"><i className={`ti ${id === "branding" ? "ti-palette" : "ti-shield-lock"}`} /></span>
                      <div className="notif-category-title">{label}</div>
                    </div>
                    {["inApp", "email"].map((channel) => (
                      <button key={channel} type="button" role="switch" aria-checked={Boolean(agencyPrefs.categories?.[id]?.[channel])} className={`bd-switch small${agencyPrefs.categories?.[id]?.[channel] ? " on" : ""}`} onClick={() => updateAgencyPref(id, channel)}><span /></button>
                    ))}
                  </div>
                ))}
              </div>
            </section>
          )}
        />
      )}
    </main>
  );
}
