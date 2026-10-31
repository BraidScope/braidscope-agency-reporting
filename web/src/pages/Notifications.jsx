// SPDX-License-Identifier: AGPL-3.0-only
import { useCallback, useEffect, useRef, useState } from "react";
import {
  NOTIFICATION_CATEGORIES,
  NOTIFICATION_CATEGORY_META,
  formatRelative,
  getInAppNotifications,
  markInAppNotificationsRead,
} from "../lib/settings";
import { roleCanAccess } from "../lib/permissions";

const PAGE_SIZE = 20;

const FILTERS = [
  { id: "all", label: "All", icon: "ti-inbox" },
  { id: "unread", label: "Unread", icon: "ti-point-filled" },
  ...NOTIFICATION_CATEGORIES.map((c) => ({ id: c.id, label: c.label, icon: c.icon })),
];

const CATEGORY_MODULES = {
  integrations: "Integrations",
  branding: "Branding",
  reports: "Reports",
};

function NotificationRow({ item, onOpen }) {
  const meta = NOTIFICATION_CATEGORY_META[item.category] || NOTIFICATION_CATEGORY_META.general;
  return (
    <button type="button" className={`notif-page-row${item.read_at ? "" : " unread"}`} onClick={() => onOpen(item)}>
      <span className="notif-page-icn"><i className={`ti ${meta.icon}`} /></span>
      <span className="notif-page-body">
        <span className="notif-page-title-row">
          <b>{item.title}</b>
          <span className="notif-page-time">{formatRelative(item.created_at)}</span>
        </span>
        {item.body && <span className="notif-page-desc">{item.body}</span>}
        {item.action_label && <span className="notif-page-cta">{item.action_label} <i className="ti ti-arrow-right" /></span>}
      </span>
      {!item.read_at && <span className="notif-page-dot" aria-hidden="true" />}
    </button>
  );
}

export default function Notifications({ org, client, onGoTo, onSelectClient, onNotificationsChanged, scope = "client", headerAside = null }) {
  const orgId = org?.id || null;
  const clientId = client?.id || null;
  const [filter, setFilter] = useState("all");
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState("");
  const itemsRef = useRef([]);
  itemsRef.current = items;
  // Request-generation guard: switching tabs/clients quickly can leave an
  // older, slower response racing a newer, faster one. Without ignoring a
  // stale response, whichever request happens to resolve LAST wins even if
  // it was started first - e.g. clicking "Branding" then quickly clicking
  // "Integrations" could leave Branding rows showing under the Integrations
  // tab if that Branding request happened to take longer.
  const requestIdRef = useRef(0);
  const clientRole = client?.access === "shared" ? client?.role : org?.role;
  const visibleFilters = (scope === "agency"
    ? FILTERS
      .filter((filterItem) => ["all", "unread", "branding", "account_security"].includes(filterItem.id))
      .map((filterItem) => filterItem.id === "account_security" ? { ...filterItem, label: "Security" } : filterItem)
    : FILTERS).filter((filterItem) => {
    const module = CATEGORY_MODULES[filterItem.id];
    return !module || roleCanAccess(clientRole, module, "view");
  });

  useEffect(() => {
    if (!visibleFilters.some((item) => item.id === filter)) setFilter("all");
  }, [filter, visibleFilters]);

  const fetchPage = useCallback(async (reset) => {
    if (!orgId) { setItems([]); setLoading(false); setHasMore(false); return; }
    const requestId = ++requestIdRef.current;
    const options = { limit: PAGE_SIZE, clientId: scope === "agency" ? null : clientId, scope };
    if (filter === "unread") options.unreadOnly = true;
    else if (filter !== "all") options.category = filter;
    if (!reset && itemsRef.current.length) options.before = itemsRef.current[itemsRef.current.length - 1].created_at;

    if (reset) setLoading(true); else setLoadingMore(true);
    const result = await getInAppNotifications(orgId, options);
    if (requestId !== requestIdRef.current) return; // a newer request has since started - discard this stale one
    if (reset) setLoading(false); else setLoadingMore(false);
    if (!result.ok) { setError(result.error || "Could not load notifications"); return; }
    setError("");
    const rows = result.notifications || [];
    setItems((prev) => (reset ? rows : [...prev, ...rows]));
    setHasMore(rows.length === PAGE_SIZE);
  }, [orgId, clientId, filter, scope]);

  useEffect(() => { fetchPage(true); }, [fetchPage]);

  const handleOpen = async (item) => {
    if (!item.read_at) {
      setItems((prev) => prev.map((row) => (row.id === item.id ? { ...row, read_at: new Date().toISOString() } : row)));
      await markInAppNotificationsRead([item.id]);
      onNotificationsChanged?.();
    }
    // Don't rely on whichever client happens to be active - switch to the
    // notification's own client before navigating, so the destination page
    // is guaranteed correct regardless of what was selected when it arrived.
    if (scope !== "agency" && item.client_id && item.client_id !== clientId) onSelectClient?.(item.client_id);
    if (item.link) onGoTo(item.link.replace(/^\//, ""));
  };

  const handleMarkAllRead = async () => {
    setItems((prev) => prev.map((row) => (row.read_at ? row : { ...row, read_at: new Date().toISOString() })));
    await markInAppNotificationsRead();
    onNotificationsChanged?.();
  };

  const unreadLoadedCount = items.filter((item) => !item.read_at).length;
  const activeFilterMeta = visibleFilters.find((f) => f.id === filter);
  const emptyMessage = filter === "unread"
    ? "You're all caught up."
    : filter === "all"
      ? "No notifications yet."
      : `No ${activeFilterMeta?.label.toLowerCase()} notifications yet.`;

  return (
    <div className="page">
      <div className={`page-head${headerAside ? " page-head-with-aside" : ""}`}>
        <div>
          <div className="page-eyebrow">Workspace</div>
          <h1 className="page-title">Notifications</h1>
          <div className="page-sub">{scope === "agency" ? "Agency-level branding and security activity" : "Everything Braidscope has told you about this workspace"}</div>
        </div>
        <div className="page-head-right">
          <div className="page-actions">
            {unreadLoadedCount > 0 && (
              <button type="button" className="ghost-btn" onClick={handleMarkAllRead}>
                <i className="ti ti-checks" /> Mark all read
              </button>
            )}
          </div>
          {headerAside}
        </div>
      </div>

      <div className="inline-tabs">
        {visibleFilters.map((f) => (
          <div key={f.id} className={`inline-tab${filter === f.id ? " active" : ""}`} onClick={() => setFilter(f.id)}>
            <i className={`ti ${f.icon}`} />{f.label}
          </div>
        ))}
      </div>

      {error && <div className="inline-error"><i className="ti ti-alert-triangle" /> {error}</div>}

      {loading ? (
        <div className="notif-page-list">
          {[0, 1, 2, 3].map((i) => (
            <div className="notif-page-row skel" key={i}>
              <span className="skel-circle" />
              <span className="notif-page-body">
                <span className="skel-line skel-w120" />
                <span className="skel-line skel-w80 skel-mt4" />
              </span>
            </div>
          ))}
        </div>
      ) : items.length ? (
        <>
          <div className="notif-page-list">
            {items.map((item) => <NotificationRow key={item.id} item={item} onOpen={handleOpen} />)}
          </div>
          {hasMore && (
            <button type="button" className="ghost-btn notif-load-more" onClick={() => fetchPage(false)} disabled={loadingMore}>
              {loadingMore ? "Loading..." : "Load more"}
            </button>
          )}
        </>
      ) : (
        <div className="empty-state">
          <i className="ti ti-bell-off" />
          <div className="empty-title">{emptyMessage}</div>
        </div>
      )}
    </div>
  );
}
