// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useRef, useState } from "react";
import { NOTIFICATION_CATEGORY_META, formatRelative } from "../lib/settings";

export default function Topbar({
  agencyName,
  profile,
  ini,
  brandLogo,
  onGoTo,
  onOpenAccountSettings,
  onOpenAdminWorkspace,
  onOpenClientWorkspace,
  onOpenAdminNotifications,
  isAdminView = false,
  onOpenAi,
  demoMode,
  aiOpen,
  clients,
  selectedClient,
  sources,
  sourceSummary,
  onSelectClient,
  onAddClient,
  notifications = [],
  notificationsLoading = false,
  onReadNotifications,
  resolvedTheme = "light",
  onThemeChange,
}) {
  const topbarRef = useRef(null);
  const [srcOpen, setSrcOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [clientOpen, setClientOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);

  const connected = sources.filter((s) => s.connected);
  // Far-left brand identity represents whichever agency actually OWNS the
  // active client workspace - the caller (App.jsx) already resolves this:
  // the viewer's own agency for an owned client, or the true owning
  // agency's identity for a client shared with the viewer from elsewhere.
  // It intentionally DOES change when the selected client changes, for
  // exactly that reason - see SHARED_CLIENT_CONTEXT_FIX.md (Bug 3).
  const visibleName = agencyName;
  const visibleInitial = (visibleName.trim()[0] || ini || "A").toUpperCase();
  const visibleLogo = brandLogo;
  // Top-right avatar/menu are the signed-in USER's identity, not the
  // agency's — deliberately separate from visibleName/visibleInitial above.
  const userDisplayName = profile?.fullName || profile?.email || "";
  const userInitial = (userDisplayName.trim()[0] || ini || "A").toUpperCase();

  const close = () => { setSrcOpen(false); setMenuOpen(false); setClientOpen(false); setNotificationsOpen(false); };

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape") close();
    };
    const onPointerDown = (event) => {
      if (!topbarRef.current?.contains(event.target)) close();
    };

    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("pointerdown", onPointerDown, true);
    };
  }, []);

  const toggleClient = (event) => {
    event.stopPropagation();
    const nextOpen = !clientOpen;
    close();
    setClientOpen(nextOpen);
  };

  const toggleSources = (event) => {
    event.stopPropagation();
    const nextOpen = !srcOpen;
    close();
    setSrcOpen(nextOpen);
  };

  const unreadNotifications = notifications.filter((item) => !item.read_at);

  const toggleNotifications = (event) => {
    event.stopPropagation();
    const nextOpen = !notificationsOpen;
    close();
    setNotificationsOpen(nextOpen);
  };

  const toggleMenu = (event) => {
    event.stopPropagation();
    const nextOpen = !menuOpen;
    close();
    setMenuOpen(nextOpen);
  };

  return (
    <div className="topbar" ref={topbarRef} onClick={(e) => e.stopPropagation()}>
      <div className="brand" title={visibleName}>
        <div className="brand-mark client-brand-mark" style={visibleLogo ? { background: "transparent" } : undefined}>
          {visibleLogo ? <img src={visibleLogo} alt="" /> : visibleInitial}
        </div>
        <span className="brand-name">{visibleName}</span>
        {demoMode && <span className="demo-badge">Demo</span>}
      </div>

      <div className="vline" />

      {!isAdminView && (
        <>
          <div className="pill client-pill" onClick={toggleClient}>
            <i className="ti ti-building-store" />
            <span>{selectedClient?.name || "No client selected"}</span>
            <i className="ti ti-chevron-down" />
            {clientOpen && (
              <div className="dropdown" onClick={(e) => e.stopPropagation()}>
                <div className="dd-title">Switch client</div>
                {clients.length === 0 && <div className="dd-empty">No clients yet</div>}
                {clients.map((client, i) => (
                  <div
                    key={client.id}
                    className={`dd-row${selectedClient?.id === client.id ? " active" : ""}`}
                    onClick={() => { close(); onSelectClient(client.id); }}
                  >
                    <div className="dd-ico" style={{ background: ["#111", "#534AB7", "#185FA5", "#0F6E56"][i % 4] }}>
                      {(client.name || "C")[0]}
                    </div>
                    <div className="dd-name-col">
                      <div className="dd-name">{client.name}</div>
                      {client.access === "shared" && client.ownerOrg?.name && <div className="dd-owner-sub">Shared by {client.ownerOrg.name}</div>}
                    </div>
                    <span className={`dd-access-badge${client.access === "shared" ? " shared" : ""}`}>
                      {client.access === "shared" ? "Shared" : "Owner"}
                    </span>
                    {selectedClient?.id === client.id && <i className="ti ti-check" style={{ marginLeft: "auto", color: "var(--ok)" }} />}
                  </div>
                ))}
                <div className="dd-add" onClick={() => { close(); onAddClient(); }}>
                  <i className="ti ti-plus" /> Add client workspace
                </div>
              </div>
            )}
          </div>

          <div className="pill sources-pill" onClick={toggleSources}>
            <span className="src-dot" />
            <span>{sourceSummary.countLabel}</span>
            <i className="ti ti-chevron-down" />
            {srcOpen && (
              <div className="dropdown" onClick={(e) => e.stopPropagation()}>
                <div className="dd-title">Connected</div>
                {connected.map((s) => (
                  <div key={s.name} className="dd-row">
                    <div className="dd-ico" style={{ background: s.bg }}>{s.l}</div>
                    <div className="dd-name">{s.name}</div>
                    <div className="dd-meta">{s.lastSyncAt ? `${new Date(s.lastSyncAt).toLocaleDateString([], { month: "short", day: "numeric" })}, ${new Date(s.lastSyncAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : s.status}</div>
                  </div>
                ))}
                <div className="dd-add" onClick={() => { close(); onGoTo("integrations"); }}>
                  <i className="ti ti-settings" /> Manage data sources
                </div>
              </div>
            )}
          </div>
        </>
      )}

      <div className="topbar-right">
        <div className={`ai-cta${aiOpen ? " active" : ""}`} onClick={onOpenAi}>
          <i className="ti ti-sparkles" />
          <span>Ask AI</span>
        </div>
        {!isAdminView && <i className="ti ti-search icobtn" title="Search" />}
        <button
          type="button"
          className="topbar-icon-btn"
          title={resolvedTheme === "dark" ? "Switch to light" : "Switch to dark"}
          aria-label={resolvedTheme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          onClick={() => onThemeChange?.(resolvedTheme === "dark" ? "light" : "dark")}
        >
          <i className={`ti ${resolvedTheme === "dark" ? "ti-sun" : "ti-moon"}`} />
        </button>
        <button type="button" className="topbar-icon-btn" title="Notifications" onClick={toggleNotifications}>
          <i className="ti ti-bell" />
          {unreadNotifications.length > 0 && <span className="notification-count">{unreadNotifications.length > 9 ? "9+" : unreadNotifications.length}</span>}
        </button>
        {notificationsOpen && (
          <div className="notification-menu" onClick={(e) => e.stopPropagation()}>
            <div className="notification-menu-head">
              <b>Notifications</b>
              <div className="notification-menu-actions">
                {unreadNotifications.length > 0 && (
                  <button type="button" onClick={() => onReadNotifications?.(unreadNotifications.map((item) => item.id))}>Mark all read</button>
                )}
                <button type="button" onClick={() => { close(); isAdminView ? onOpenAdminNotifications?.() : onGoTo("notifications"); }}>See all</button>
              </div>
            </div>
            {notificationsLoading ? (
              <div className="notification-list">
                {[0, 1, 2].map((i) => (
                  <div className="notification-skel-row" key={i}>
                    <span className="notification-skel-icn" />
                    <span className="notification-skel-lines">
                      <span className="skel-line skel-w120" />
                      <span className="skel-line skel-w80 skel-mt4" />
                    </span>
                  </div>
                ))}
              </div>
            ) : notifications.length ? (
              <div className="notification-list">
                {notifications.slice(0, 8).map((item) => {
                  const meta = NOTIFICATION_CATEGORY_META[item.category] || NOTIFICATION_CATEGORY_META.general;
                  return (
                    <button
                      type="button"
                      key={item.id}
                      className={`notification-item${item.read_at ? "" : " unread"}`}
                      onClick={() => {
                        if (!item.read_at) onReadNotifications?.([item.id]);
                        if (item.client_id && item.client_id !== selectedClient?.id) onSelectClient?.(item.client_id);
                        if (item.link) onGoTo(item.link.replace(/^\//, ""));
                        close();
                      }}
                    >
                      <span className="notification-item-icn"><i className={`ti ${meta.icon}`} /></span>
                      <span className="notification-item-body">
                        <span className="notification-item-title-row">
                          <b>{item.title}</b>
                          <em className="notification-item-time">{formatRelative(item.created_at)}</em>
                        </span>
                        {item.body && <span className="notification-item-desc">{item.body}</span>}
                        {item.action_label && <span className="notification-item-cta">{item.action_label}</span>}
                      </span>
                      {!item.read_at && <span className="notification-item-dot" aria-hidden="true" />}
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="notification-empty">
                <div>You&rsquo;re all caught up.</div>
                <span>Important workspace updates will appear here.</span>
              </div>
            )}
          </div>
        )}
        <div id="nav-profile" className="avatar" onClick={toggleMenu}>
          {profile?.avatarUrl ? <img src={profile.avatarUrl} alt="" /> : userInitial}
        </div>
        {menuOpen && (
          <div className="menu" onClick={(e) => e.stopPropagation()}>
            <div className="mi menu-email-row" title={profile?.email || userDisplayName || agencyName}>
              <i className="ti ti-user" />
              <span className="menu-email-text">{profile?.email || userDisplayName || agencyName}</span>
            </div>
            <div className="sep" />
            <div className="mi" onClick={() => { close(); if (isAdminView) { onOpenClientWorkspace ? onOpenClientWorkspace() : onGoTo("workspace"); } else { onOpenAdminWorkspace ? onOpenAdminWorkspace() : onGoTo("admin"); } }}>
              <i className="ti ti-layout-dashboard" /> {isAdminView ? "Client Workspace" : "Admin Workspace"}
            </div>
            <div className="mi" onClick={() => { close(); onOpenAccountSettings ? onOpenAccountSettings() : onGoTo("settings"); }}><i className="ti ti-settings" /> Account settings</div>
            <div className="sep" />
            <div className="mi" onClick={() => { close(); onGoTo("login"); }}><i className="ti ti-logout" /> Log out</div>
          </div>
        )}
      </div>
    </div>
  );
}
