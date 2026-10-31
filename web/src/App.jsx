// SPDX-License-Identifier: AGPL-3.0-only
import { Suspense, lazy, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import "./App.css";
import { getSession, onAuthChange, signOut, getMyOrganization, ensureOwnOrganization, updateOrganizationBranding, getMfaRequirement } from "./lib/auth";
import Topbar from "./components/Topbar";
import Sidebar from "./components/Sidebar";
import AiDrawer from "./components/AiDrawer";
import ConnectModal from "./components/ConnectModal";
import ClientWorkspaceModal from "./components/ClientWorkspaceModal";
import LLMProviderOnboardingModal from "./components/LLMProviderOnboardingModal";
import Login from "./pages/Login";
import AcceptInvitation from "./pages/AcceptInvitation";
import Workspace from "./pages/Workspace";
// Heavy, authed-only pages load on first visit instead of in the sign-in
// bundle. Each becomes its own chunk (charts, maps, PDF engine stay out of
// the initial download).
const Dashboards = lazy(() => import("./pages/Dashboards"));
const Reports = lazy(() => import("./pages/Reports"));
const Integrations = lazy(() => import("./pages/Integrations"));
const AgencyBranding = lazy(() => import("./pages/AgencyBranding"));
const ClientBranding = lazy(() => import("./pages/ClientBranding"));
const AdminWorkspace = lazy(() => import("./pages/AdminWorkspace"));
const Settings = lazy(() => import("./pages/Settings"));
const Notifications = lazy(() => import("./pages/Notifications"));
import Walkthrough from "./components/Walkthrough";
import { DEMO_CLIENTS, createClient, listClients, listSharedClients, updateClientBrandingOverride } from "./lib/clients";
import { canAccess, canAccessView, currentClientRoleLabel } from "./lib/permissions";
import AccessDenied from "./components/AccessDenied";
import { useUnsavedChanges } from "./lib/UnsavedChangesContext.jsx";
import { emptyBranding, mergeBranding, resolveClientBranding } from "./lib/branding";
import {
  accentForTheme,
  accentSoft,
  applyTheme,
  prefersDark,
  watchSystemTheme,
} from "./lib/theme";
import { checkClientReminders, getInAppNotifications, getProfile, getUiPreferences, markInAppNotificationsRead, notifyBrandingPublished, recordSessionActivity, saveUiPreferences } from "./lib/settings";
import {
  connectDataSource,
  disconnectAIProvider,
  disconnectDataConnector,
  getClaudeCodeServerConnection,
  getGoogleAdsServerConnection,
  getGA4ServerConnection,
  getMetaAdsServerConnection,
  getHubSpotServerConnection,
  getKlaviyoServerConnection,
  getOpenRouterServerConnection,
  getOpenAICodexServerConnection,
  navigateForOAuth,
  OAUTH_RESULT_CHANNEL,
  saveBYOKAIProvider,
  getSourceSummary,
  listDataSources,
  mergeSourceCatalog,
  startGoogleAdsOAuth,
  startGA4OAuth,
  startMetaAdsOAuth,
  startHubSpotOAuth,
  startKlaviyoOAuth,
  startOpenRouterOAuth,
  startOpenAICodexAuth,
  startClaudeCodeAuth,
  submitClaudeCodeCode,
  startShopifyOAuth,
} from "./lib/dataSources";
import DataSourcesOnboardingModal from "./components/DataSourcesOnboardingModal";


const VIEW_PATHS = {
  workspace: "/workspace",
  dashboards: "/dashboards",
  reports: "/reports",
  integrations: "/integrations",
  branding: "/branding",
  agencyBranding: "/agency-branding",
  admin: "/admin",
  settings: "/settings",
  notifications: "/notifications",
};

const DASHBOARD_PATHS = {
  google_analytics_4: "/dashboards/google-analytics-4",
  google_ads: "/dashboards/google-ads",
  meta_ads: "/dashboards/meta-ads",
  klaviyo: "/dashboards/klaviyo",
  hubspot_crm: "/dashboards/hubspot",
  shopify: "/dashboards/shopify",
};

const PATH_VIEWS = {
  ...Object.fromEntries(Object.entries(VIEW_PATHS).map(([view, path]) => [path, view])),
  ...Object.fromEntries(Object.values(DASHBOARD_PATHS).map((path) => [path, "dashboards"])),
};

const LLM_ONBOARDING_CLIENT_KEY = "braidscope:llm-onboarding-client-id";
const DATA_SOURCES_ONBOARDING_CLIENT_KEY = "braidscope:data-sources-onboarding-client-id";
const FIRST_WORKSPACE_ONBOARDING_KEY = "firstWorkspaceOnboarding";

// Onboarding progress is stored per-client (keyed by client id) so that one
// client completing onboarding can never affect another client's status.
// Older persisted data (pre-dating this change) was a single flat record
// {clientId, llmStatus, ...} shared across every client - normalize that
// legacy shape into the new map on read so existing users' in-flight
// onboarding isn't lost.
function normalizeOnboardingMap(value) {
  if (!value) return {};
  if (typeof value.clientId === "string") return { [value.clientId]: value };
  return value;
}
const AI_PROVIDER_TYPES = new Set(["openrouter_ai", "openai_ai", "anthropic_ai"]);
// Matches index.html's #app-favicon starting href: the Braidscope mark.
// An organization's own favicon (Branding) replaces it while signed in and
// this default comes back on logout or when switching to a client without one.
const EMPTY_FAVICON = "/favicon.svg";

function normalizePath(pathname) {
  if (!pathname || pathname === "/") return "/";
  return pathname.replace(/\/+$/, "") || "/";
}

function OAuthCallbackPage() {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const status = params.get("status") || "error";
  const source = params.get("source") || "";
  const message = params.get("message") || "";
  const connected = status === "connected";

  useEffect(() => {
    const payload = {
      type: OAUTH_RESULT_CHANNEL,
      status,
      source,
      message,
    };

    if ("BroadcastChannel" in window) {
      const channel = new BroadcastChannel(OAUTH_RESULT_CHANNEL);
      channel.postMessage(payload);
      channel.close();
    }

    if (window.opener && window.opener !== window) {
      window.opener.postMessage(payload, window.location.origin);
    }

    if (connected) {
      const closeTimer = window.setTimeout(() => window.close(), 700);
      return () => window.clearTimeout(closeTimer);
    }

    return undefined;
  }, [connected, message, source, status]);

  return (
    <main style={{
      minHeight: "100vh",
      display: "grid",
      placeItems: "center",
      padding: 24,
      background: "var(--bg)",
      color: "var(--ink)",
    }}>
      <section style={{
        width: "min(420px, 100%)",
        border: "1px solid var(--line)",
        borderRadius: 16,
        padding: 28,
        background: "var(--panel)",
        boxShadow: "var(--shadow-lg)",
        textAlign: "center",
      }}>
        <div style={{ fontSize: 28, marginBottom: 12 }}>
          <i className={`ti ${connected ? "ti-circle-check" : "ti-alert-circle"}`} aria-hidden="true" />
        </div>
        <h1 style={{ fontSize: 22, margin: "0 0 8px" }}>
          {connected ? "Connected successfully." : "Connection failed."}
        </h1>
        <p style={{ color: "var(--muted)", lineHeight: 1.5, margin: 0 }}>
          {connected ? "You can close this tab." : (message || "Return to Braidscope and try connecting again.")}
        </p>
      </section>
    </main>
  );
}

export default function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const currentPath = normalizePath(location.pathname);
  const view = PATH_VIEWS[currentPath] || "workspace";
  const routeClientId = new URLSearchParams(location.search).get("client");
  const isRecoveryRoute = currentPath === "/reset-password" || currentPath === "/auth/invite";
  const isOAuthCallbackRoute = currentPath === "/oauth/callback";
  const isAcceptInvitationRoute = currentPath === "/accept-invitation";

  const [bootstrapping, setBootstrapping] = useState(true);
  const [session, setSession] = useState(null);
  const [demoMode, setDemoMode] = useState(false);

  const [agencyName, setAgencyName] = useState("Acme Media");
  const [accent, setAccent] = useState("#111111");
  const [hexInput, setHexInput] = useState("#111111");
  const [aiOpen, setAiOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [modal, setModal] = useState(null);
  const [org, setOrg] = useState(null);
  const [profile, setProfile] = useState(null);
  const [clients, setClients] = useState([]);
  const [selectedClientId, setSelectedClientId] = useState("");
  const [clientsLoading, setClientsLoading] = useState(false);
  const [clientsLoaded, setClientsLoaded] = useState(false);
  const [clientsError, setClientsError] = useState("");
  const [clientModalOpen, setClientModalOpen] = useState(false);
  const [clientSaving, setClientSaving] = useState(false);
  const [clientCreateError, setClientCreateError] = useState("");
  const [settingsFocusAccountTab, setSettingsFocusAccountTab] = useState(0);
  // Mandatory first-run onboarding for accounts with zero client workspaces.
  const [firstWorkspaceModalOpen, setFirstWorkspaceModalOpen] = useState(false);
  const [llmOnboardingClientId, setLlmOnboardingClientId] = useState("");
  const [dataSourcesOnboardingClientId, setDataSourcesOnboardingClientId] = useState("");
  const [sourceRecords, setSourceRecords] = useState([]);
  const [sourcesLoading, setSourcesLoading] = useState(false);
  const [sourcesError, setSourcesError] = useState("");
  const [sourceSaving, setSourceSaving] = useState(false);
  const [sourceConnectError, setSourceConnectError] = useState("");
  const [codexAuthPendingClientId, setCodexAuthPendingClientId] = useState("");
  const [claudeAuthPendingClientId, setClaudeAuthPendingClientId] = useState("");
  const [brandingSaving, setBrandingSaving] = useState(false);
  const [brandingError, setBrandingError] = useState("");
  const [brandingSaved, setBrandingSaved] = useState(false);
  const [branding, setBranding] = useState(emptyBranding);
  const [savedBrandingSnapshot, setSavedBrandingSnapshot] = useState(null);
  const [sourcesRefreshKey, setSourcesRefreshKey] = useState(0);
  const [notifications, setNotifications] = useState([]);
  const [notificationsLoading, setNotificationsLoading] = useState(false);
  const [mfaRequired, setMfaRequired] = useState(false);
  const [themePref, setThemePref] = useState("system");
  const [uiPreferences, setUiPreferences] = useState({});
  const [uiPreferencesLoaded, setUiPreferencesLoaded] = useState(false);
  // Set by a dashboard's "Build report" so Reports can pre-select that source.
  const [reportPrefill, setReportPrefill] = useState(null);
  const genericDirtyRef = useRef(false);
  const genericBaselineRef = useRef(null);
  const browserBackBypassRef = useRef(false);
  const { registerChanges, clearChanges, setGenericFallback, confirmNavigation, isDirty: hasUnsavedChanges } = useUnsavedChanges();

  // Tracks the data-sources reload below. Every sourcesRefreshKey bump
  // restarts that effect and its cleanup discards the in-flight run, so the
  // pending-auth poller must not bump while a reload is still running: the
  // reload fans out to nine status endpoints and routinely takes longer than
  // the 2s poll interval, which meant every run was cancelled before it could
  // store results - the Claude/Codex card stayed "Authenticating..." (and the
  // poller never learned it had connected) until a full page refresh.
  const sourcesLoadRef = useRef({ inFlight: false, startedAt: 0 });
  const SOURCES_LOAD_STALL_MS = 45_000;

  const authPendingClientId = codexAuthPendingClientId || claudeAuthPendingClientId;
  useEffect(() => {
    if (!authPendingClientId) return undefined;
    const startedAt = Date.now();
    const poll = () => {
      if (Date.now() - startedAt > 10 * 60 * 1000) {
        window.clearInterval(timer);
        setCodexAuthPendingClientId("");
        setClaudeAuthPendingClientId("");
        return;
      }
      // Let the current reload finish (it is what flips the card to
      // connected); only restart one that looks genuinely stuck.
      const load = sourcesLoadRef.current;
      if (load.inFlight && Date.now() - load.startedAt < SOURCES_LOAD_STALL_MS) return;
      setSourcesRefreshKey((key) => key + 1);
    };
    const timer = window.setInterval(poll, 2000);
    // The ChatGPT device-code approval happens in a SEPARATE tab
    // (startOpenAICodexAuth always opens one) - the user finishes it there,
    // then switches back to this tab. Background tabs get throttled by the
    // browser (Chrome clamps setInterval to ~1/sec, and more aggressively
    // the longer a tab stays hidden), so the plain 2s interval alone could
    // leave a real, several-seconds-or-longer gap where the card still
    // reads "Authenticating..." even though ChatGPT already approved it -
    // exactly the "need to refresh to see it connect" symptom. Polling
    // immediately the instant this tab regains focus/visibility closes that
    // gap without waiting on a possibly-throttled timer tick.
    const onFocusReturn = () => { if (document.visibilityState !== "hidden") poll(); };
    window.addEventListener("focus", onFocusReturn);
    document.addEventListener("visibilitychange", onFocusReturn);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocusReturn);
      document.removeEventListener("visibilitychange", onFocusReturn);
    };
  }, [authPendingClientId]);
  const [systemTheme, setSystemTheme] = useState(() => (prefersDark() ? "dark" : "light"));

  const resolvedTheme = themePref === "system" ? systemTheme : themePref;
  const persistUiPreferences = (patch) => {
    const next = { ...uiPreferences, ...patch };
    setUiPreferences(next);
    if (org?.id && session?.user?.id) saveUiPreferences(org.id, session.user.id, next).then((result) => { if (result.ok) window.dispatchEvent(new Event("braidscope:saved")); });
  };
  // Walkthrough's own effect destroys/recreates the driver.js tour whenever
  // its onFinish/onCancel prop changes reference - persistUiPreferences is a
  // fresh closure every render, so a naive inline handler would restart the
  // tour (and could prematurely fire its "ended" callback) on every
  // unrelated App re-render. Routing calls through this ref keeps
  // handleWalkthroughFinish/Cancel referentially stable for the component's
  // whole lifetime while still always calling the latest persistUiPreferences.
  const persistUiPreferencesRef = useRef(persistUiPreferences);
  persistUiPreferencesRef.current = persistUiPreferences;
  const handleWalkthroughFinish = useCallback(() => {
    persistUiPreferencesRef.current({ walkthroughComplete: true });
    navigate(VIEW_PATHS.workspace, { replace: true });
  }, [navigate]);
  const handleWalkthroughCancel = useCallback(() => {
    persistUiPreferencesRef.current({ walkthroughComplete: true });
  }, []);
  const persistOnboardingState = async (clientId, patch) => {
    const currentMap = normalizeOnboardingMap(uiPreferences[FIRST_WORKSPACE_ONBOARDING_KEY]);
    const nextMap = { ...currentMap, [clientId]: { ...currentMap[clientId], ...patch, clientId } };
    const next = { ...uiPreferences, [FIRST_WORKSPACE_ONBOARDING_KEY]: nextMap };
    setUiPreferences(next);
    if (!org?.id || !session?.user?.id) return true;
    const result = await saveUiPreferences(org.id, session.user.id, next);
    if (result.ok) window.dispatchEvent(new Event("braidscope:saved"));
    return result.ok;
  };
  const changeTheme = (next) => {
    setThemePref(next);
    persistUiPreferences({ theme: next });
    if (next === "system") setSystemTheme(prefersDark() ? "dark" : "light");
  };

  const ini = (agencyName.trim()[0] || "A").toUpperCase();
  const isAuthed = Boolean(session) || demoMode;
  const selectedClient = clients.find((client) => client.id === selectedClientId) || null;
  const isAdminView = view === "admin";
  const brandLogo = branding.logos?.primary || branding.logos?.icon || null;
  // The active workspace's owning agency - who actually owns the currently
  // selected client, which is the viewer's own agency for an owned client
  // (unchanged today) but a DIFFERENT agency for a shared one. The Topbar
  // header and the app-wide accent/font resolution must both follow this,
  // never the viewer's own agencyName/branding unconditionally - that was
  // the pre-shared-client assumption Bug 3 in SHARED_CLIENT_CONTEXT_FIX.md
  // describes. agencyName/brandLogo/branding themselves are untouched and
  // still used everywhere that must always show the VIEWER's own identity
  // (Login/signup, Settings' own agency fields, Agency Branding).
  const isSharedActiveClient = selectedClient?.access === "shared";
  const activeOwnerOrg = isSharedActiveClient ? selectedClient?.ownerOrg : null;
  const activeOwnerBranding = isSharedActiveClient ? mergeBranding(null, activeOwnerOrg?.branding_config || {}) : branding;
  const activeOwnerAccentColor = isSharedActiveClient ? (activeOwnerOrg?.accent_color || accent) : accent;
  const headerAgencyName = isSharedActiveClient ? (activeOwnerOrg?.name || agencyName) : agencyName;
  const headerAgencyLogo = isSharedActiveClient
    ? (activeOwnerBranding.logos?.primary || activeOwnerBranding.logos?.icon || activeOwnerOrg?.logo_url || null)
    : brandLogo;
  // Branding a report is issued under: the owning agency (never the viewer's
  // own agency for a shared client), with this client's enabled override applied
  // - the same resolution the topbar and accent use.
  const effectiveClientBranding = resolveClientBranding({
    ownerAgencyName: headerAgencyName,
    ownerAccent: activeOwnerAccentColor,
    ownerBranding: activeOwnerBranding,
    client: selectedClient,
  });
  const reportBranding = {
    name: (effectiveClientBranding.enabled && selectedClient?.branding_override?.name) || headerAgencyName,
    logo: effectiveClientBranding.logo || headerAgencyLogo,
    accent: effectiveClientBranding.accent,
  };
  const sources = mergeSourceCatalog(sourceRecords, demoMode || !session);
  const firstWorkspaceRequired = !demoMode && Boolean(org?.id) && clientsLoaded && !clientsLoading && !clientsError && clients.length === 0;
  const showFirstWorkspaceModal = firstWorkspaceModalOpen || firstWorkspaceRequired;
  // Scoped to the CURRENTLY selected client only - never a remembered
  // "last onboarding client id". This is what lets switching clients work:
  // a completed (or absent) onboarding record for the newly selected client
  // simply shows no onboarding modal, and never forces selection back to
  // whichever client onboarded most recently.
  const onboardingByClient = normalizeOnboardingMap(uiPreferences[FIRST_WORKSPACE_ONBOARDING_KEY]);
  const selectedClientOnboarding = selectedClient?.id ? onboardingByClient[selectedClient.id] || null : null;
  const selectedClientHasConnectedLlmProvider = sources.some((source) => (
    AI_PROVIDER_TYPES.has(source.type) && source.connected
  ));
  const persistedLlmPending = Boolean(
    !demoMode
    && !selectedClientOnboarding?.completed
    && selectedClientOnboarding?.llmStatus === "pending"
  );
  const persistedDataSourcesPending = Boolean(
    !demoMode
    && !selectedClientOnboarding?.completed
    && selectedClientOnboarding?.llmStatus === "complete"
    && selectedClientOnboarding?.dataSourcesStatus !== "complete"
  );
  const showLlmProviderOnboarding = Boolean(
    !showFirstWorkspaceModal
    && selectedClient?.id
    && (selectedClient.id === llmOnboardingClientId || persistedLlmPending)
  );
  const showDataSourcesOnboarding = Boolean(
    !showFirstWorkspaceModal
    && !showLlmProviderOnboarding
    && selectedClient?.id
    && (selectedClient.id === dataSourcesOnboardingClientId || persistedDataSourcesPending)
  );
  const sourceSummary = getSourceSummary(sources);
  // Also waits on clientsLoaded (not just org/uiPreferences) so the full app
  // shell never mounts while the client list fetch is still in flight -
  // without that, Workspace's own internal "Loading client workspace"
  // fallback (a visually distinct card, not this boot-shell) would flash
  // for that gap before the real onboarding/workspace state is known.
  const workspaceReady = demoMode || !session || Boolean(org && uiPreferencesLoaded && clientsLoaded) || Boolean(brandingError);
  useEffect(() => {
    const legacyView = window.location.hash.replace(/^#\/?/, "");
    if (VIEW_PATHS[legacyView]) {
      navigate(VIEW_PATHS[legacyView], { replace: true });
    }
  }, [navigate]);

  useEffect(() => {
    if (!session || demoMode || !routeClientId?.startsWith("demo-")) return;
    const params = new URLSearchParams(location.search);
    params.delete("client");
    navigate({ pathname: currentPath, search: params.toString() ? `?${params.toString()}` : "" }, { replace: true });
  }, [currentPath, demoMode, location.search, navigate, routeClientId, session]);

  // Bootstrap: read existing session + subscribe to changes
  useEffect(() => {
    let unsub;
    (async () => {
      const s = await getSession();
      setSession(s);
      setBootstrapping(false);
      unsub = onAuthChange((next) => {
        setSession(next);
        if (next) setDemoMode(false);
      });
    })();
    return () => unsub && unsub();
  }, []);

  // Once signed in, hydrate org name + accent from DB
  useEffect(() => {
    if (!session) return;
    setSavedBrandingSnapshot(null);
    (async () => {
      let org = await getMyOrganization();
      if (org?.__error) {
        setBrandingError("Could not load your workspace: " + org.__error);
        return;
      }
      if (!org) {
        // No membership at all — most likely this account's only workspace
        // was just deleted via Settings (that intentionally keeps the auth
        // user around). Provision a fresh organization exactly like signup
        // does, so the account lands back in the normal onboarding state
        // instead of staying orgless.
        const provisioned = await ensureOwnOrganization();
        if (provisioned?.__error) {
          setBrandingError("Could not load your workspace: " + provisioned.__error);
          return;
        }
        if (!provisioned) {
          setBrandingError("No workspace is linked to this account.");
          return;
        }
        org = provisioned;
      }
      setOrg(org);
      const hydrated = mergeBranding(null, org.branding_config || {});
      const hydratedName = hydrated.agencyName || org.name || "My workspace";
      const nextAccent = hydrated.accent || org.accent_color;
      const hydratedAccent = /^#[0-9a-fA-F]{6}$/.test(nextAccent || "") ? nextAccent : "#111111";
      setBranding(hydrated);
      setAgencyName(hydratedName);
      setAccent(hydratedAccent);
      setHexInput(hydratedAccent);
      setSavedBrandingSnapshot(JSON.stringify({ agencyName: hydratedName, accent: hydratedAccent, hexInput: hydratedAccent, branding: hydrated }));
    })();
  }, [session]);

  // Topbar's avatar/menu need the signed-in user's own identity (name,
  // avatar), not the agency's — loaded once per session, same call
  // Settings.jsx already makes for its own Account tab.
  useEffect(() => {
    if (!session) { setProfile(null); return; }
    let alive = true;
    getProfile().then((result) => { if (alive && result.ok) setProfile(result.profile); });
    return () => { alive = false; };
  }, [session?.user?.id]);

  useEffect(() => {
    if (!org?.id || !session?.user?.id) { setUiPreferencesLoaded(false); return; }
    let alive = true;
    getUiPreferences(org.id, session.user.id).then((result) => {
      if (!alive) return;
      const preferences = result.ok ? result.preferences : {};
      setUiPreferences(preferences);
      setThemePref(["light", "dark", "system"].includes(preferences.theme) ? preferences.theme : "system");
      setUiPreferencesLoaded(true);
    });
    return () => { alive = false; };
  }, [org?.id, session?.user?.id]);
  useEffect(() => {
    if (!session || !org?.id) return;
    recordSessionActivity(org.id).then((result) => {
      if (!result.ok || result.recorded === false) {
        console.warn("[Braidscope] Session activity was not recorded", result.error || result.reason || "unknown reason");
      }
    });
  }, [session?.access_token, org?.id]);
  const refreshNotifications = useCallback(async ({ showLoading = false } = {}) => {
    if (!session || !org?.id) { setNotifications([]); return; }
    if (showLoading) setNotificationsLoading(true);
    const result = await getInAppNotifications(org.id, {
      clientId: isAdminView ? null : selectedClient?.id,
      scope: isAdminView ? "agency" : "client",
    });
    if (showLoading) setNotificationsLoading(false);
    if (result.ok) setNotifications(result.notifications || []);
  }, [session, org?.id, selectedClient?.id, isAdminView]);

  useEffect(() => {
    refreshNotifications({ showLoading: true });
  }, [refreshNotifications]);

  // Bell data refresh, per notification_improvements.md section 8: refetch
  // whenever the shared connector-status signal fires (covers every OAuth
  // popup connect, Shopify, and window-focus-return — see the
  // sourcesRefreshKey effect above), plus a light poll as a floor so a
  // notification created by another session/background job (e.g. a Shopify
  // sync flipping to needs_reconnect) still shows up without a manual
  // action. Individual connect/disconnect/branding-save handlers below also
  // call this directly right after their own success, so the common case
  // doesn't wait on the poll at all.
  useEffect(() => {
    if (!session || !org?.id) return;
    refreshNotifications();
  }, [sourcesRefreshKey, session, org?.id, refreshNotifications]);

  useEffect(() => {
    if (!session || !org?.id) return undefined;
    const id = window.setInterval(() => refreshNotifications(), 90000);
    return () => window.clearInterval(id);
  }, [session, org?.id, refreshNotifications]);
  useEffect(() => {
    if (!session) { setMfaRequired(false); return; }
    let alive = true;
    getMfaRequirement().then((result) => { if (alive && result.ok) setMfaRequired(Boolean(result.required)); });
    return () => { alive = false; };
  }, [session?.access_token]);
  // Signed-in branding is hydrated from the organization row above.
  useEffect(() => {
    if (!org) setBranding(emptyBranding());
  }, [org?.id]);

  useEffect(() => {
    if (bootstrapping || session) return;
    setOrg(null);
    setClients(DEMO_CLIENTS);
    setSelectedClientId(DEMO_CLIENTS[0].id);
    setClientsLoaded(true);
    setClientsError("");
    setSourceRecords([]);
    setSourcesError("");
  }, [bootstrapping, session]);

  // Client list FETCH is separate from active-client SELECTION. The list is
  // fetched when the org changes (or via clientsFetchKey, see the
  // selected-client-missing effect below); route/preference changes no longer
  // refetch it - the selection is resolved from the fetched list, using the
  // exact same precedence as before (valid route client > saved preference >
  // first client). Route and preferences are read through refs so the
  // resolution always sees their latest values without being an effect
  // dependency.
  const [clientsFetchKey, setClientsFetchKey] = useState(0);
  const routeClientIdRef = useRef(routeClientId);
  routeClientIdRef.current = routeClientId;
  const uiPreferencesRef = useRef(uiPreferences);
  uiPreferencesRef.current = uiPreferences;
  const uiPreferencesLoadedRef = useRef(uiPreferencesLoaded);
  uiPreferencesLoadedRef.current = uiPreferencesLoaded;
  // A fetched list that arrived before the saved preferences did - selection
  // must not be resolved without them, so it is finalized once they load.
  const pendingClientListRef = useRef(null);
  const missingClientRefetchRef = useRef("");

  const resolveSelectedClient = (nextClients) => {
    const savedClientId = uiPreferencesRef.current.selectedClientId;
    const preferredClientId = savedClientId && !savedClientId.startsWith("demo-")
      ? savedClientId
      : null;
    const currentRouteClientId = routeClientIdRef.current;
    const preferredId = currentRouteClientId && !currentRouteClientId.startsWith("demo-")
      ? currentRouteClientId
      : preferredClientId;
    return nextClients.find((client) => client.id === preferredId) || nextClients[0] || null;
  };

  useEffect(() => {
    pendingClientListRef.current = null;
    if (!org?.id) {
      setClientsLoaded(false);
      return;
    }
    let alive = true;

    (async () => {
      setClientsLoading(true);
      setClientsLoaded(false);
      setClientsError("");
      try {
        const [result, sharedResult] = await Promise.all([
          listClients(org.id),
          listSharedClients(session?.user?.id),
        ]);
        if (!alive) return;

        if (!result.ok) {
          setClients([]);
          setClientsError(result.error || "Could not load clients");
          setClientsLoading(false);
          setClientsLoaded(true);
          return;
        }

        // Owned clients (this org's own) plus clients another agency shared
        // with this user via client_memberships - merged into one list, each
        // tagged with its access kind so the switcher/badges/permission
        // gating can tell them apart without a second data source. Every
        // client (owned or shared) also carries `role` and `ownerOrg` in the
        // same uniform shape - header identity/branding/permission checks
        // resolve against ownerOrg, never against "the viewer's own org",
        // since that assumption breaks for a shared client. For an owned
        // client, ownerOrg is simply the viewer's own already-loaded org.
        const ownOrgIdentity = { id: org.id, name: org.name, logo_url: org.logo_url, accent_color: org.accent_color, branding_config: org.branding_config };
        const owned = result.clients.map((client) => ({ ...client, access: "owner", role: org.role, ownerOrg: ownOrgIdentity }));
        // A failed shared-clients fetch (RLS gap, network blip, etc.) used to
        // be swallowed completely here - the switcher would just silently
        // show zero shared clients with no trace anywhere, indistinguishable
        // from "this user genuinely has none". Logging it loudly (mirrors
        // the same "never fail silently" fix already applied to email
        // sending in client_access.py) at least makes a real fetch failure
        // visible in devtools instead of masquerading as "no shared access".
        if (!sharedResult.ok) {
          console.error("[Braidscope] Could not load shared client workspaces:", sharedResult.error);
        }
        const shared = sharedResult.ok ? sharedResult.clients : [];
        const nextClients = [...owned, ...shared];
        if (!uiPreferencesLoadedRef.current) {
          // Saved preferences are part of the selection precedence; hold the
          // list (clientsLoaded stays false, so the shell stays as it was)
          // until the effect below can resolve the selection with them.
          pendingClientListRef.current = nextClients;
          return;
        }
        const nextSelected = resolveSelectedClient(nextClients);
        setClients(nextClients);
        setSelectedClientId(nextSelected?.id || "");
        setClientsLoading(false);
        setClientsLoaded(true);
      } catch (error) {
        if (!alive) return;
        console.error("[Braidscope] Client workspace loading failed", error);
        setClients([]);
        setClientsError(error?.message || "Could not load clients");
        setClientsLoading(false);
        setClientsLoaded(true);
      }
    })();

    return () => { alive = false; };
  }, [org?.id, clientsFetchKey]);

  // Finalizes a client list that was fetched before the saved preferences
  // loaded: same list, same precedence, resolved once preferences are known.
  useEffect(() => {
    if (!uiPreferencesLoaded) return;
    const pending = pendingClientListRef.current;
    if (!pending) return;
    pendingClientListRef.current = null;
    const nextSelected = resolveSelectedClient(pending);
    setClients(pending);
    setSelectedClientId(nextSelected?.id || "");
    setClientsLoading(false);
    setClientsLoaded(true);
  }, [uiPreferencesLoaded]);

  // Explicit list invalidation: a selection can point at a client that is not
  // in the loaded list yet (e.g. accepting an invitation selects the newly
  // shared client). Previously the resulting route change refetched the list
  // as a side effect; now it is refetched once, on purpose, for that id.
  useEffect(() => {
    if (demoMode || !org?.id || !clientsLoaded || !selectedClientId) return;
    if (clients.some((client) => client.id === selectedClientId)) {
      missingClientRefetchRef.current = "";
      return;
    }
    if (missingClientRefetchRef.current === selectedClientId) return;
    missingClientRefetchRef.current = selectedClientId;
    setClientsFetchKey((key) => key + 1);
  }, [demoMode, org?.id, clientsLoaded, selectedClientId, clients]);

  // NOTE: onboarding resume-after-refresh no longer needs an imperative
  // effect here. showLlmProviderOnboarding/showDataSourcesOnboarding above
  // are already pure derivations of selectedClientOnboarding (which onboarding
  // record belongs to the CURRENTLY selected client), so the right modal
  // reappears automatically on reload without ever needing to override
  // selectedClientId. Previously this effect force-reselected whichever
  // client's id was last remembered in a single global onboarding record -
  // that record was never cleared once set, so it permanently hijacked every
  // later client switch back to that one client. Root cause of the
  // client-switching bug; see ClientWorkspaceModal/persistOnboardingState for
  // the per-client fix.

  // While either onboarding surface with a possible external OAuth leg is
  // open, periodically refresh connection status so a source connected in a
  // separate tab shows as connected here without the user needing to do
  // anything else — and refresh immediately when focus returns to this tab,
  // which is the strongest signal that they're done with that other tab.
  useEffect(() => {
    if (!showLlmProviderOnboarding && !showDataSourcesOnboarding) return undefined;
    const refresh = () => setSourcesRefreshKey((key) => key + 1);
    window.addEventListener("focus", refresh);
    const interval = window.setInterval(refresh, 4000);
    return () => {
      window.removeEventListener("focus", refresh);
      window.clearInterval(interval);
    };
  }, [showLlmProviderOnboarding, showDataSourcesOnboarding]);

  useEffect(() => {
    if (!showLlmProviderOnboarding || !selectedClientHasConnectedLlmProvider) return;
    handleLlmOnboardingComplete();
  }, [showLlmProviderOnboarding, selectedClientHasConnectedLlmProvider]);

  // Data Sources onboarding is deliberately multi-category (advertising,
  // analytics, ecommerce, crm) with its own "Continue" gate inside
  // DataSourcesOnboardingModal (connectedCount === 0 blocks it). It must NOT
  // auto-complete the instant the first source connects — that would tear
  // down the modal mid-category and discard the user's progress through the
  // remaining steps. Completion is driven solely by the modal's own
  // onComplete callback (wired to handleDataSourcesOnboardingComplete) once
  // the user explicitly clicks Continue on the last category with >=1
  // connected source.

  // Brand-new accounts must create their first client workspace before using the app.
  useEffect(() => {
    if (firstWorkspaceRequired) {
      setClientCreateError("");
      setClientModalOpen(false);
      setFirstWorkspaceModalOpen(true);
      return;
    }
    if (clientsLoaded && clients.length > 0) setFirstWorkspaceModalOpen(false);
  }, [firstWorkspaceRequired, clientsLoaded, clients.length]);

  const skipClientParamSyncRef = useRef(false);

  useEffect(() => {
    if (!org?.id || !selectedClientId) return;
    if (skipClientParamSyncRef.current) {
      skipClientParamSyncRef.current = false;
      return;
    }
    if (uiPreferencesLoaded && uiPreferences.selectedClientId !== selectedClientId) persistUiPreferences({ selectedClientId });
    const params = new URLSearchParams(location.search);
    if (params.get("client") !== selectedClientId) {
      params.set("client", selectedClientId);
      navigate({ pathname: currentPath, search: `?${params.toString()}` }, { replace: true });
    }
  }, [org?.id, selectedClientId, currentPath, location.search, navigate, uiPreferencesLoaded, uiPreferences.selectedClientId]);

  useEffect(() => {
    if (!selectedClient?.id) {
      sourcesLoadRef.current = { inFlight: false, startedAt: 0 };
      setSourceRecords([]);
      setSourcesError("");
      return;
    }

    // Drop any other client's records from state immediately, synchronously,
    // before the fetch below even starts - otherwise the previous client's
    // connected cards/pill render for a frame (or, once a slow request lands,
    // for much longer) because mergeSourceCatalog matches by source type only
    // and doesn't know sourceRecords still belongs to the client we just left.
    setSourceRecords((current) => current.filter((record) => record.client_id === selectedClient.id));

    let alive = true;
    sourcesLoadRef.current = { inFlight: true, startedAt: Date.now() };
    (async () => {
      const fallbackStatus = { ok: true, connected: false, pending: false, storage: "none" };
      const settledValue = (settled, fallback = fallbackStatus) => (
        settled.status === "fulfilled" ? settled.value : { ...fallback, ok: false, error: settled.reason?.message || "Could not load connector status" }
      );

      const hasCurrentClientRecords = sourceRecords.some((record) => record.client_id === selectedClient.id);
      if (!hasCurrentClientRecords) setSourcesLoading(true);
      setSourcesError("");
      try {
        const [
          resultSettled, klaviyoSettled, hubspotSettled, googleAdsSettled, ga4Settled,
          metaAdsSettled, openrouterSettled, openaiSettled, anthropicSettled,
        ] = await Promise.allSettled([
          listDataSources(selectedClient.id),
          getKlaviyoServerConnection(selectedClient),
          getHubSpotServerConnection(selectedClient),
          getGoogleAdsServerConnection(selectedClient),
          getGA4ServerConnection(selectedClient),
          getMetaAdsServerConnection(selectedClient),
          getOpenRouterServerConnection(selectedClient),
          getOpenAICodexServerConnection(selectedClient),
          getClaudeCodeServerConnection(selectedClient),
        ]);
        if (!alive) return;

        const result = settledValue(resultSettled, { ok: false, records: [], error: "Could not load data sources" });
        const klaviyoServer = settledValue(klaviyoSettled);
        const hubspotServer = settledValue(hubspotSettled);
        const googleAdsServer = settledValue(googleAdsSettled);
        const ga4Server = settledValue(ga4Settled);
        const metaAdsServer = settledValue(metaAdsSettled);
        const openrouterServer = settledValue(openrouterSettled, { ...fallbackStatus, model: undefined });
        const openaiServer = settledValue(openaiSettled, { ...fallbackStatus, model: undefined });
        const anthropicServer = settledValue(anthropicSettled, { ...fallbackStatus, model: undefined });

        if (!result.ok) {
          setSourceRecords([]);
          setSourcesError(result.error || "Could not load data sources");
          setSourcesLoading(false);
          return;
        }

        const hasKlaviyoRecord = result.records.some((record) => record.type === "klaviyo");
        const hasHubSpotRecord = result.records.some((record) => record.type === "hubspot_crm");
        const hasGoogleAdsRecord = result.records.some((record) => record.type === "google_ads");
        const hasGA4Record = result.records.some((record) => record.type === "google_analytics_4");
        const hasMetaAdsRecord = result.records.some((record) => record.type === "meta_ads");
        const hasOpenRouterRecord = result.records.some((record) => record.type === "openrouter_ai");
        const hasOpenAIRecord = result.records.some((record) => record.type === "openai_ai");
        const hasAnthropicRecord = result.records.some((record) => record.type === "anthropic_ai");
        const aiServerByType = {
          openrouter_ai: { ...openrouterServer, authMode: "oauth_pkce" },
          openai_ai: { ...openaiServer, authMode: "codex_chatgpt" },
          anthropic_ai: { ...anthropicServer, authMode: "claude_code" },
        };
        let records = result.records.map((record) => {
          const server = aiServerByType[record.type];
          if (!server) return record;
          if (!server.connected) {
            const isCodexPending = (record.type === "openai_ai" || record.type === "anthropic_ai") && server.pending;
            return {
              ...record,
              status: isCodexPending ? "pending" : (server.needsReconnect ? "needs_reconnect" : "disabled"),
              config: {
                ...(record.config || {}),
                auth_mode: server.authMode,
                storage: server.storage || "none",
                model: server.model,
                // The device code is transient backend state, not a
                // database field. Preserve it on every status refresh,
                // including when the data_sources row already exists.
                ...(isCodexPending ? {
                  user_code: server.userCode || record.config?.user_code || null,
                  authorization_url: server.authorizationUrl || record.config?.authorization_url || null,
                } : {}),
              },
            };
          }
          return record;
        });
        const enrichAIRecord = (record, server, authMode) => ({
          ...record,
          status: "connected",
          config: {
            ...(record.config || {}),
            auth_mode: authMode,
            storage: server.storage,
            model: server.model,
          },
        });
        if (openrouterServer.connected && hasOpenRouterRecord) {
          records = records.map((record) => (
            record.type === "openrouter_ai"
              ? enrichAIRecord(record, openrouterServer, "oauth_pkce")
              : record
          ));
        }
        if (openaiServer.connected && hasOpenAIRecord) {
          records = records.map((record) => (
            record.type === "openai_ai"
              ? enrichAIRecord(record, openaiServer, "codex_chatgpt")
              : record
          ));
        }
        if (anthropicServer.connected && hasAnthropicRecord) {
          records = records.map((record) => (
            record.type === "anthropic_ai"
              ? enrichAIRecord(record, anthropicServer, "claude_code")
              : record
          ));
        }
        if (klaviyoServer.connected && !hasKlaviyoRecord) {
          records = [
            ...records,
            {
              id: "server-klaviyo",
              client_id: selectedClient.id,
              type: "klaviyo",
              status: "connected",
              config: { auth_mode: "mcp_oauth", storage: klaviyoServer.storage },
              last_sync_at: new Date().toISOString(),
            },
          ];
        }
        if (hubspotServer.connected && !hasHubSpotRecord) {
          records = [
            ...records,
            {
              id: "server-hubspot",
              client_id: selectedClient.id,
              type: "hubspot_crm",
              status: "connected",
              config: { auth_mode: "mcp_oauth", storage: hubspotServer.storage },
              last_sync_at: new Date().toISOString(),
            },
          ];
        }
        if (googleAdsServer.connected && !hasGoogleAdsRecord) {
          records = [
            ...records,
            {
              id: "server-google-ads",
              client_id: selectedClient.id,
              type: "google_ads",
              status: "connected",
              config: { auth_mode: "mcp_oauth", storage: googleAdsServer.storage },
              last_sync_at: new Date().toISOString(),
            },
          ];
        }
        if (ga4Server.connected && !hasGA4Record) {
          records = [
            ...records,
            {
              id: "server-ga4",
              client_id: selectedClient.id,
              type: "google_analytics_4",
              status: "connected",
              config: { auth_mode: "mcp_oauth", storage: ga4Server.storage },
              last_sync_at: new Date().toISOString(),
            },
          ];
        }
        if (metaAdsServer.connected && !hasMetaAdsRecord) {
          records = [
            ...records,
            {
              id: "server-meta-ads",
              client_id: selectedClient.id,
              type: "meta_ads",
              status: "connected",
              config: { auth_mode: "mcp_oauth", storage: metaAdsServer.storage },
              last_sync_at: new Date().toISOString(),
            },
          ];
        }
        if (openrouterServer.connected && !hasOpenRouterRecord) {
          records = [
            ...records,
            {
              id: "server-openrouter",
              client_id: selectedClient.id,
              type: "openrouter_ai",
              status: "connected",
              config: {
                auth_mode: "oauth_pkce",
                storage: openrouterServer.storage,
                model: openrouterServer.model,
              },
              last_sync_at: new Date().toISOString(),
            },
          ];
        }
        if (openaiServer.connected && !hasOpenAIRecord) {
          records = [
            ...records,
            {
              id: "server-openai",
              client_id: selectedClient.id,
              type: "openai_ai",
              status: "connected",
              config: { auth_mode: "codex_chatgpt", storage: openaiServer.storage, model: openaiServer.model },
              last_sync_at: new Date().toISOString(),
            },
          ];
        }
        if (openaiServer.connected) {
          setCodexAuthPendingClientId((current) => (
            current === selectedClient.id ? "" : current
          ));
        }
        if (openaiServer.pending && !hasOpenAIRecord) {
          records = [
            ...records,
            {
              id: "server-openai-pending",
              client_id: selectedClient.id,
              type: "openai_ai",
              status: "pending",
              config: {
                auth_mode: "codex_chatgpt",
                storage: "pending",
                model: openaiServer.model,
                user_code: openaiServer.userCode,
                authorization_url: openaiServer.authorizationUrl,
              },
              last_sync_at: null,
            },
          ];
        }
        if (openaiServer.pending) {
          setCodexAuthPendingClientId(selectedClient.id);
        }
        if (anthropicServer.connected && !hasAnthropicRecord) {
          records = [
            ...records,
            {
              id: "server-anthropic",
              client_id: selectedClient.id,
              type: "anthropic_ai",
              status: "connected",
              config: { auth_mode: "claude_code", storage: anthropicServer.storage, model: anthropicServer.model },
              last_sync_at: new Date().toISOString(),
            },
          ];
        }
        if (anthropicServer.connected) {
          setClaudeAuthPendingClientId((current) => (
            current === selectedClient.id ? "" : current
          ));
        }
        if (anthropicServer.pending && !hasAnthropicRecord) {
          records = [
            ...records,
            {
              id: "server-anthropic-pending",
              client_id: selectedClient.id,
              type: "anthropic_ai",
              status: "pending",
              config: {
                auth_mode: "claude_code",
                storage: "pending",
                authorization_url: anthropicServer.authorizationUrl,
              },
              last_sync_at: null,
            },
          ];
        }
        if (anthropicServer.pending) {
          setClaudeAuthPendingClientId(selectedClient.id);
        }
        setSourceRecords(records);
        if (!klaviyoServer.ok) setSourcesError(klaviyoServer.error || "Could not load Klaviyo status");
        if (!hubspotServer.ok) setSourcesError(hubspotServer.error || "Could not load HubSpot status");
        if (!googleAdsServer.ok) setSourcesError(googleAdsServer.error || "Could not load Google Ads status");
        if (!ga4Server.ok) setSourcesError(ga4Server.error || "Could not load Google Analytics 4 status");
        if (!metaAdsServer.ok) setSourcesError(metaAdsServer.error || "Could not load Meta Ads status");
        if (!openrouterServer.ok) setSourcesError(openrouterServer.error || "Could not load OpenRouter status");
        if (!openaiServer.ok) console.warn("[Braidscope] OpenAI Codex status unavailable", openaiServer.error || "unknown error");
        if (openaiServer.error && openaiServer.connected) setSourcesError(openaiServer.error);
        if (!anthropicServer.ok) setSourcesError(anthropicServer.error || "Could not load Claude status");
        setSourcesLoading(false);
      } catch (error) {
        if (!alive) return;
        console.error("[Braidscope] Source loading failed", error);
        setSourcesError(error?.message || "Could not load data sources");
        setSourcesLoading(false);
      } finally {
        // A superseded run leaves the flag to the run that replaced it.
        if (alive) sourcesLoadRef.current = { inFlight: false, startedAt: 0 };
      }
    })();

    return () => { alive = false; };
  }, [selectedClient?.id, sourcesRefreshKey]);

  // Reminders have no scheduler (deliberately, this phase) - they're checked
  // opportunistically once per client selection instead. Each reminder's
  // stable dedupe_key means the backend only ever creates it once per
  // client, so calling this on every switch is safe and idempotent.
  useEffect(() => {
    if (demoMode || !org?.id || !selectedClient?.id) return;
    checkClientReminders(org.id, selectedClient.id).then((result) => {
      if (!result.ok) console.error("[Braidscope] checkClientReminders failed:", result.error);
    });
  }, [demoMode, org?.id, selectedClient?.id]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const klaviyoStatus = params.get("klaviyo");
    const hubspotStatus = params.get("hubspot");
    const googleAdsStatus = params.get("google_ads");
    const ga4Status = params.get("google_analytics_4");
    const metaAdsStatus = params.get("meta_ads");
    const openrouterStatus = params.get("openrouter");
    if (!klaviyoStatus && !hubspotStatus && !googleAdsStatus && !ga4Status && !metaAdsStatus && !openrouterStatus) return;

    if (
      klaviyoStatus === "connected" ||
      hubspotStatus === "connected" ||
      googleAdsStatus === "connected" ||
      ga4Status === "connected" ||
      metaAdsStatus === "connected" ||
      openrouterStatus === "connected"
    ) {
      if (openrouterStatus === "connected" && window.sessionStorage.getItem(LLM_ONBOARDING_CLIENT_KEY)) {
        window.sessionStorage.removeItem(LLM_ONBOARDING_CLIENT_KEY);
        setLlmOnboardingClientId("");
      }
      navigate(VIEW_PATHS.integrations, { replace: true });
      setSourcesError("");
    } else {
      navigate(VIEW_PATHS.integrations, { replace: true });
      setSourcesError(params.get("message") || "OAuth connection failed");
    }
  }, [navigate]);

  // Onboarding launches OAuth in a second tab (so the onboarding modal/step
  // here survives the round trip). That tab reports back over BroadcastChannel
  // (and postMessage as a fallback for browsers/opener setups where it isn't
  // available) instead of navigating this tab anywhere - we just refetch real
  // connection state and let the onboarding UI re-render from that.
  useEffect(() => {
    const handleOAuthResult = (payload) => {
      if (!payload || payload.type !== OAUTH_RESULT_CHANNEL) return;
      const { status, message } = payload;
      if (status === "connected") {
        // Only refetch the shared `sources` truth here. Do NOT also flip
        // llmOnboardingClientId/dataSourcesOnboardingClientId from this
        // handler - that would be a second, competing completion path
        // racing the modals' own pendingOAuthType/pendingConnectType
        // watchers (which drive onComplete -> handleLlmOnboardingComplete /
        // handleDataSourcesOnboardingComplete once they observe the
        // refreshed `sources`). Two paths flipping the same state is
        // exactly the kind of drift that caused the modal to unmount
        // before the "Connected" state ever rendered.
        setSourcesError("");
        setSourcesRefreshKey((key) => key + 1);
      } else {
        setSourcesError(message || "OAuth connection failed");
      }
    };

    let channel;
    if ("BroadcastChannel" in window) {
      channel = new BroadcastChannel(OAUTH_RESULT_CHANNEL);
      channel.onmessage = (event) => handleOAuthResult(event.data);
    }

    const handleWindowMessage = (event) => {
      if (event.origin !== window.location.origin) return;
      handleOAuthResult(event.data);
    };
    window.addEventListener("message", handleWindowMessage);

    return () => {
      if (channel) channel.close();
      window.removeEventListener("message", handleWindowMessage);
    };
  }, []);

  // General-purpose "connector status may have changed while this tab was
  // backgrounded" fallback. Connecting an LLM/data source opens the OAuth
  // flow in a new tab, which backgrounds this one - and Chrome (and others)
  // throttle backgrounded tabs' setInterval timers down to roughly once a
  // minute or slower the longer they stay hidden, which is exactly why the
  // connected card could take "minutes" to update even with the 4s onboarding
  // poll running. Unlike setInterval, focus/visibilitychange fire immediately
  // when the tab becomes visible again regardless of prior throttling, so
  // this refetches sources the instant the user comes back - covering every
  // return path (OAuth popup, "Manage" link, or just alt-tabbing back) - as
  // a backstop alongside the BroadcastChannel message the OAuth callback
  // page sends for the fast path.
  useEffect(() => {
    const refreshOnReturn = () => {
      if (document.visibilityState === "visible") setSourcesRefreshKey((key) => key + 1);
    };

    window.addEventListener("focus", refreshOnReturn);
    document.addEventListener("visibilitychange", refreshOnReturn);
    return () => {
      window.removeEventListener("focus", refreshOnReturn);
      document.removeEventListener("visibilitychange", refreshOnReturn);
    };
  }, []);

  // Paint the resolved theme on <html> so the CSS token sets swap.
  useLayoutEffect(() => {
    applyTheme(resolvedTheme);
  }, [resolvedTheme]);

  // Only follow the OS while the preference is literally "system".
  useEffect(() => {
    if (themePref !== "system") return;
    return watchSystemTheme(() => setSystemTheme(prefersDark() ? "dark" : "light"));
  }, [themePref]);

  // Reflects Branding's uploaded favicon (branding.logos.favicon, a data:
  // URL - same asset the Branding page's "Favicon" tile already stores, no
  // second storage system) onto the actual browser tab icon. Manages the
  // single #app-favicon <link> already in index.html in place, rather than
  // adding a second competing rel="icon" tag - that keeps "fall back to no
  // icon" trivial (just point it back at the same empty href index.html
  // starts with) and guarantees this effect only ever touches that one
  // element, never any other icon link that might exist. Removing and
  // re-inserting the node (instead of only mutating .href) is deliberate:
  // some browsers don't repaint a favicon on an href change alone, but do
  // pick up a freshly inserted link element - needed here since
  // branding.logos.favicon changes reactively on org switch and logout
  // (both reset `branding`), not just on a fresh upload.
  useEffect(() => {
    // No unmount cleanup needed beyond this: App itself never unmounts for
    // the life of the tab, and every path that should show no icon again
    // (logout, switching to an org/client with no favicon) already resets
    // `branding` to emptyBranding() elsewhere, which re-runs this same
    // effect with a falsy href and naturally falls back on its own.
    const href = branding.logos?.favicon || EMPTY_FAVICON;
    const existing = document.getElementById("app-favicon");
    const link = document.createElement("link");
    link.id = "app-favicon";
    link.rel = "icon";
    link.href = href;
    if (href.endsWith(".svg")) link.type = "image/svg+xml";
    if (existing) existing.replaceWith(link);
    else document.head.appendChild(link);
  }, [branding.logos?.favicon]);

  // The workspace's accent and font both follow whichever client is active in
  // the topbar switcher: if that client has "bring their own branding"
  // enabled, the whole app reads its accent/font via these CSS vars while
  // you're on that client; a client without an override (or none selected)
  // falls back to the agency's own accent/font.
  //
  // Admin Workspace is the one exception: it must have a stable, agency-only
  // identity that never varies with whichever client happens to be selected
  // in the topbar switcher (selecting a client is orthogonal to being in
  // Admin Workspace - the switcher stays interactive there). Passing
  // `client: null` here makes resolveClientBranding fall back to the
  // owner values unconditionally, and those owner values are always this
  // agency's OWN branding (agencyName/accent/branding), not
  // activeOwnerAccentColor/activeOwnerBranding - those two track the
  // selected client's owning org (which for a shared client is a different
  // agency entirely), which is exactly what Admin Workspace must not do.
  useEffect(() => {
    const effective = resolveClientBranding(
      isAdminView
        ? { ownerAgencyName: agencyName, ownerAccent: accent, ownerBranding: branding, client: null }
        : { ownerAgencyName: headerAgencyName, ownerAccent: activeOwnerAccentColor, ownerBranding: activeOwnerBranding, client: selectedClient }
    );
    const resolvedAccent = effective.accent;
    if (/^#[0-9a-fA-F]{6}$/.test(resolvedAccent)) {
      // A near-black brand accent is invisible on the dark canvas, so lift it
      // there; the soft companion is mixed toward whichever surface it sits on.
      const painted = accentForTheme(resolvedAccent, resolvedTheme);
      document.documentElement.style.setProperty("--accent", painted);
      document.documentElement.style.setProperty("--accent-ink", painted);
      document.documentElement.style.setProperty("--accent-soft", accentSoft(painted, resolvedTheme));

      // The auth pages (Login/Signup/Forgot/Reset) paint their primary CTA,
      // active tab and identity mark from their own --auth-accent-* tokens
      // (App.css) so refining Braidscope's *default* auth look never has to
      // touch this override pipeline. But those pages can still render with
      // a workspace/custom brand color loaded here — e.g. right after
      // sign-out, before this state resets — so when the resolved accent is
      // an actual override (not just the untouched "#111111" default),
      // mirror it into the auth tokens too, using the same always-white
      // on-accent label every other accent-colored control in the app uses.
      // When nothing is overriding it, clear the mirror so the CSS
      // stylesheet's own improved defaults apply instead.
      if (resolvedAccent.toLowerCase() !== "#111111") {
        document.documentElement.style.setProperty("--auth-accent", painted);
        document.documentElement.style.setProperty("--auth-accent-ink", "#ffffff");
      } else {
        document.documentElement.style.removeProperty("--auth-accent");
        document.documentElement.style.removeProperty("--auth-accent-ink");
      }
    }

    document.documentElement.style.setProperty("--font-core", effective.fontStack);
  }, [isAdminView, agencyName, accent, branding, headerAgencyName, activeOwnerAccentColor, activeOwnerBranding, selectedClient?.id, selectedClient?.branding_override, resolvedTheme]);

  useEffect(() => {
    if (bootstrapping) return;

    if (isRecoveryRoute) return;

    // /oauth/callback is a standalone, ephemeral page (see OAuthCallbackPage)
    // that must stay put regardless of auth state - it's not in PATH_VIEWS
    // by design, so without this guard the "unknown route" branch below
    // yanks it to /workspace (or /login, if this tab's session doesn't
    // match the origin the OAuth redirect landed on) before it ever gets to
    // notify the opener tab and close itself.
    if (isOAuthCallbackRoute) return;

    // /accept-invitation must work for both signed-out AND signed-in users
    // (an existing account logs in right there; a brand-new one signs up
    // right there) - never force-redirected to /login or bounced as an
    // "unknown route" once authenticated.
    if (isAcceptInvitationRoute) return;

    if (!isAuthed) {
      if (currentPath !== "/login") navigate("/login", { replace: true });
      return;
    }

    if (currentPath === "/" || currentPath === "/login" || !PATH_VIEWS[currentPath]) {
      const nextPath = VIEW_PATHS.workspace;
      navigate(nextPath, { replace: true });
    }
  }, [bootstrapping, currentPath, isAuthed, isRecoveryRoute, isOAuthCallbackRoute, isAcceptInvitationRoute, navigate]);

  useEffect(() => {
  }, [currentPath]);

  const handleSelectClient = (clientId) => {
    // Clear other clients' source records in the same tick as the selection
    // change (not in the fetch effect, which only runs after the next paint)
    // so the topbar's connected-sources pill and any onboarding cards never
    // render the previous client's state for even one frame.
    setSourceRecords((current) => current.filter((record) => record.client_id === clientId));
    setSelectedClientId(clientId);
    if (org?.id) persistUiPreferences({ selectedClientId: clientId });
  };

  // Settings -> Workspace edits/deletes the selected CLIENT row directly
  // (see lib/settings.js updateClientWorkspace/deleteClientWorkspace) -
  // these two callbacks keep App's own client list/selection in sync so the
  // top-left switcher, brand label, etc. update immediately without a
  // reload, exactly like handleCreateClient does for a new client.
  const handleClientUpdated = (updatedClient) => {
    if (!updatedClient?.id) return;
    setClients((current) => current.map((client) => (client.id === updatedClient.id ? { ...client, ...updatedClient } : client)));
  };

  const handleClientDeleted = (clientId) => {
    const next = clients.filter((client) => client.id !== clientId);
    setClients(next);
    if (selectedClientId === clientId) {
      const nextSelectedId = next[0]?.id || "";
      setSelectedClientId(nextSelectedId);
      if (org?.id) persistUiPreferences({ selectedClientId: nextSelectedId });
    }
  };

  const openClientCreation = () => {
    setClientCreateError("");
    if (firstWorkspaceRequired) {
      setClientModalOpen(false);
      setFirstWorkspaceModalOpen(true);
      return;
    }
    setClientModalOpen(true);
  };

  const readActiveFormSnapshot = useCallback(() => {
    const pageRoot = document.querySelector(`[data-page-view="${view}"]`);
    const roots = [pageRoot].filter(Boolean);
    const controls = roots.flatMap((root, rootIndex) => Array.from(root.querySelectorAll("input, select, textarea")).map((field) => ({ field, rootIndex })))
      .filter(({ field }) => !field.disabled && !field.readOnly && field.dataset.unsavedIgnore !== "true");
    return JSON.stringify(controls.map(({ field, rootIndex }, index) => ({
      rootIndex,
      index,
      name: field.name || field.id || "",
      type: field.type || field.tagName,
      value: field.type === "checkbox" || field.type === "radio" ? field.checked : field.value,
    })));
  }, [view]);

  useEffect(() => {
    genericDirtyRef.current = false;
    genericBaselineRef.current = null;
    const captureBaseline = () => { genericBaselineRef.current = readActiveFormSnapshot(); };
    const frame = window.requestAnimationFrame(captureBaseline);
    const timer = window.setTimeout(captureBaseline, 250);
    return () => { window.cancelAnimationFrame(frame); window.clearTimeout(timer); };
  }, [currentPath, readActiveFormSnapshot]);

  useEffect(() => {
    const captureBeforeEditing = (event) => {
      if (!event.isTrusted || genericDirtyRef.current) return;
      const field = event.target;
      if (!(field instanceof HTMLInputElement || field instanceof HTMLSelectElement || field instanceof HTMLTextAreaElement)) return;
      genericBaselineRef.current = readActiveFormSnapshot();
    };
    const markDirty = (event) => {
      if (currentPath === VIEW_PATHS.settings || currentPath === VIEW_PATHS.branding || currentPath === VIEW_PATHS.agencyBranding || currentPath === VIEW_PATHS.admin || currentPath === "/login") return;
      // Dashboards have no draft state to save or discard: every control
      // (section visibility, date range, property/account/device pickers)
      // applies the moment it changes. Treating those inputs as a dirty form
      // raised a false "Unsaved changes" prompt whose Save had nothing to
      // save and whose Discard couldn't undo the already-applied change.
      if (currentPath.startsWith(VIEW_PATHS.dashboards)) return;
      if (!event.isTrusted) return;
      const field = event.target;
      if (!(field instanceof HTMLInputElement || field instanceof HTMLSelectElement || field instanceof HTMLTextAreaElement)) return;
      if (field.disabled || field.readOnly || field.dataset.unsavedIgnore === "true") return;
      window.requestAnimationFrame(() => {
        genericDirtyRef.current = genericBaselineRef.current !== null && readActiveFormSnapshot() !== genericBaselineRef.current;
      });
    };
    const handleReloadKey = (event) => {
      if (event.key !== "F5" && !(event.ctrlKey && event.key.toLowerCase() === "r")) return;
      if (!hasUnsavedChanges()) return;
      event.preventDefault();
      confirmNavigation(() => window.location.reload());
    };
    document.addEventListener("focusin", captureBeforeEditing, true);
    document.addEventListener("input", markDirty, true);
    document.addEventListener("change", markDirty, true);
    window.addEventListener("keydown", handleReloadKey);
    return () => {
      document.removeEventListener("focusin", captureBeforeEditing, true);
      document.removeEventListener("input", markDirty, true);
      document.removeEventListener("change", markDirty, true);
      window.removeEventListener("keydown", handleReloadKey);
    };
  }, [currentPath, readActiveFormSnapshot, hasUnsavedChanges, confirmNavigation]);
  useEffect(() => {
    const handleBrowserBack = () => {
      if (browserBackBypassRef.current) { browserBackBypassRef.current = false; return; }
      if (!hasUnsavedChanges()) return;
      browserBackBypassRef.current = true;
      window.history.forward();
      confirmNavigation(() => { browserBackBypassRef.current = true; window.history.back(); });
    };
    window.addEventListener("popstate", handleBrowserBack);
    return () => window.removeEventListener("popstate", handleBrowserBack);
  }, [hasUnsavedChanges, confirmNavigation]);

  // Lower-priority fallback registration (see UnsavedChangesContext) for any
  // page that never calls registerChanges itself - a DOM-level dirty check
  // plus a best-effort "click whatever save-like button is visible" save
  // action. An explicit registerChanges call from a page always wins over
  // this one while it's active.
  useEffect(() => {
    setGenericFallback({
      isDirty: () => genericDirtyRef.current,
      onSave: () => new Promise((resolve) => {
        const saveButton = Array.from(document.querySelectorAll("button")).find((button) => {
          if (button.disabled || button.closest(".global-unsaved-modal")) return false;
          const visible = button.getClientRects().length > 0;
          return visible && (/save|create|connect|send|update|publish/i.test(button.textContent || "") || button.type === "submit");
        });
        if (!saveButton) { resolve(true); return; }
        saveButton.click();
        setTimeout(() => resolve(true), 300);
      }),
      onDiscard: () => { genericDirtyRef.current = false; },
    });
  }, [setGenericFallback]);

  const goTo = (nextView) => {
    const performNavigate = async () => {
      if (nextView === "login") {
        await signOut();
        setSession(null);
        setDemoMode(false);
        navigate("/login", { replace: true });
        return;
      }
      navigate({ pathname: nextView === "dashboards" ? DASHBOARD_PATHS.google_analytics_4 : (VIEW_PATHS[nextView] || VIEW_PATHS.workspace), search: location.search });
    };
    if (nextView === view) { performNavigate(); return; }
    confirmNavigation(performNavigate);
  };

  const buildReportFromDashboard = (source) => {
    setReportPrefill({ source, id: Date.now() });
    goTo("reports");
  };

  // Settings keeps its own internal tab state and stays mounted across
  // navigations (so it doesn't reset on its own) - the topbar's "Account
  // settings" menu item needs to always land on the Account tab specifically,
  // regardless of whichever tab Settings was last left on. Bumping this
  // counter (rather than a boolean) reliably re-triggers the effect below
  // even when the target tab is already "account".
  const goToAccountSettings = () => {
    setSettingsFocusAccountTab((key) => key + 1);
    goTo("settings");
  };
  const goToAdminWorkspace = () => {
    confirmNavigation(() => navigate({ pathname: VIEW_PATHS.admin, search: "section=clients" }));
  };
  const goToAdminNotifications = () => {
    confirmNavigation(() => navigate({ pathname: VIEW_PATHS.admin, search: "section=notifications" }));
  };
  const goToClientWorkspace = () => {
    goTo("workspace");
  };

  const applyHex = (hex) => {
    if (!hex.startsWith("#")) hex = "#" + hex;
    if (!/^#[0-9a-fA-F]{6}$/.test(hex)) return;
    setHexInput(hex);
    setAccent(hex);
    setBrandingSaved(false);
  };

  const handleAgencyNameChange = (name) => {
    setAgencyName(name);
    setBrandingSaved(false);
  };

  const handleSaveBranding = async () => {
    const cleanName = agencyName.trim();
    if (!cleanName) {
      setBrandingError("Agency display name is required");
      return false;
    }
    if (!/^#[0-9a-fA-F]{6}$/.test(accent)) {
      setBrandingError("Accent color must be a valid hex value");
      return false;
    }

    setBrandingSaving(true);
    setBrandingError("");
    setBrandingSaved(false);

    const nextBranding = { ...branding, agencyName: cleanName, accent };
    if (!org?.id) {
      setBrandingSaving(false);
      setBrandingError("Sign in to a workspace before publishing branding.");
      return false;
    }


    const result = await updateOrganizationBranding(org.id, {
      name: cleanName,
      accentColor: accent,
      brandingConfig: nextBranding,
    });

    setBrandingSaving(false);

    if (!result.ok) {
      setBrandingError(result.error || "Could not save branding");
      return false;
    }

    setOrg((current) => ({ ...current, ...result.organization }));
    setAgencyName(result.organization.name || cleanName);
    const savedAccent = result.organization.accent_color || accent;
    const savedName = result.organization.name || cleanName;
    setAccent(savedAccent);
    setHexInput(savedAccent);
    setBranding(nextBranding);
    setSavedBrandingSnapshot(JSON.stringify({ agencyName: savedName, accent: savedAccent, hexInput: savedAccent, branding: nextBranding }));
    // Explicit follow-up call (not a DB trigger — see
    // 20260919_001_client_scoped_notifications.sql) because only the caller
    // here actually knows whether this save was the agency's own branding or
    // a specific client's override; the backend can then attribute the
    // notification correctly, check preferences, dedupe, and email.
    const publishKey = (window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`);
    const notifyResult = await notifyBrandingPublished(org.id, null, publishKey);
    if (!notifyResult.ok) {
      setBrandingSaved(false);
      setBrandingError(notifyResult.error || "Branding saved, but the notification could not be created.");
      return false;
    }
    setBrandingSaved(true);
    refreshNotifications();
    return true;
  };

  const handleBrandingChange = (next) => {
    setBranding(next);
    setBrandingSaved(false);
  };

  // Client Branding's override now lives on the client row itself
  // (clients.branding_override), never inside either agency's own
  // organizations.branding_config - a shared client's override must
  // resolve/save against the CLIENT, not whichever agency happens to be
  // viewing it. This only updates local draft state (mirrors
  // handleBrandingChange's role for Agency Branding); handleSaveClientBranding
  // below is what actually persists it, via updateClientBrandingOverride
  // (RLS-protected, editor+ on either the owning org or a shared
  // client_memberships role - see SHARED_CLIENT_CONTEXT_FIX.md).
  const handleClientBrandingOverrideChange = (nextOverride) => {
    if (!selectedClient?.id) return;
    setClients((current) => current.map((client) => (
      client.id === selectedClient.id ? { ...client, branding_override: nextOverride } : client
    )));
    setBrandingSaved(false);
  };

  const handleSaveClientBranding = async () => {
    if (!selectedClient?.id) return false;
    setBrandingSaving(true);
    setBrandingError("");
    setBrandingSaved(false);

    const result = await updateClientBrandingOverride(selectedClient.id, selectedClient.branding_override || {});
    setBrandingSaving(false);

    if (!result.ok) {
      setBrandingError(result.error || "Could not save branding");
      return false;
    }

    handleClientUpdated(result.client);
    setBrandingSaved(true);
    const publishKey = (window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`);
    const notifyResult = await notifyBrandingPublished(org.id, selectedClient.id, publishKey);
    if (!notifyResult.ok) {
      setBrandingSaved(false);
      setBrandingError(notifyResult.error || "Branding saved, but the notification could not be created.");
      return false;
    }
    refreshNotifications();
    return true;
  };

  const handleEditClientBranding = (clientId) => {
    handleSelectClient(clientId);
    goTo("branding");
  };
  const handleAdminManageClient = (clientId) => {
    skipClientParamSyncRef.current = true;
    handleSelectClient(clientId);
    navigate({ pathname: VIEW_PATHS.workspace, search: `client=${encodeURIComponent(clientId)}` }, { replace: true });
  };

  const handleCreateClient = async (name) => {
    // Every new client goes through the same LLM -> Data Sources onboarding
    // sequence, not just the very first one - "Add client workspace" reuses
    // the identical first-run onboarding rather than a simplified bypass.
    const shouldStartLlmOnboarding = !demoMode;
    setClientSaving(true);
    setClientCreateError("");
    const result = await createClient(org?.id, name);
    setClientSaving(false);

    if (!result.ok) {
      setClientCreateError(result.error || "Could not create client");
      return false;
    }

    // Tagged with the exact same { access, role, ownerOrg } shape the
    // initial load gives every client (see the client-loading effect above)
    // - previously untagged here, which happened to be harmless today only
    // because every downstream check tests `=== "shared"`, never the
    // inverse; kept explicit so the data model stays uniform.
    const ownOrgIdentity = { id: org.id, name: org.name, logo_url: org.logo_url, accent_color: org.accent_color, branding_config: org.branding_config };
    const newClient = { ...result.client, access: "owner", role: org.role, ownerOrg: ownOrgIdentity };
    setClients((current) => [...current, newClient]);
    // A brand-new client has zero connections by definition - drop the
    // previous client's records in this same tick so the LLM/Data Sources
    // onboarding modal that's about to open never flashes the old client's
    // connected cards before the real (empty) fetch for this client lands.
    setSourceRecords((current) => current.filter((record) => record.client_id === result.client.id));
    setSelectedClientId(result.client.id);
    setClientModalOpen(false);
    setFirstWorkspaceModalOpen(false);
    if (shouldStartLlmOnboarding) {
      setSourceConnectError("");
      await persistOnboardingState(result.client.id, {
        llmStatus: "pending",
        dataSourcesStatus: "pending",
        completed: false,
        startedAt: new Date().toISOString(),
      });
      window.sessionStorage.setItem(LLM_ONBOARDING_CLIENT_KEY, result.client.id);
      setLlmOnboardingClientId(result.client.id);
    }
    return true;
  };

  // Fires on both Skip and a successful provider connection — either way the
  // LLM step is done, so hand off to the data-sources step for the same
  // client rather than closing onboarding outright.
  const handleLlmOnboardingComplete = async (llmAction = "connected") => {
    window.sessionStorage.removeItem(LLM_ONBOARDING_CLIENT_KEY);
    const clientId = llmOnboardingClientId || selectedClient?.id;
    setLlmOnboardingClientId("");
    if (clientId && !demoMode) {
      setSourceConnectError("");
      const existing = onboardingByClient[clientId];
      await persistOnboardingState(clientId, {
        llmStatus: "complete",
        llmAction,
        dataSourcesStatus: existing?.dataSourcesStatus || "pending",
        completed: false,
        llmCompletedAt: new Date().toISOString(),
      });
      window.sessionStorage.setItem(DATA_SOURCES_ONBOARDING_CLIENT_KEY, clientId);
      setDataSourcesOnboardingClientId(clientId);
    }
  };

  const handleDataSourcesOnboardingComplete = async () => {
    window.sessionStorage.removeItem(DATA_SOURCES_ONBOARDING_CLIENT_KEY);
    const clientId = dataSourcesOnboardingClientId || selectedClient?.id;
    if (clientId && !demoMode) {
      await persistOnboardingState(clientId, {
        llmStatus: "complete",
        dataSourcesStatus: "complete",
        completed: true,
        completedAt: new Date().toISOString(),
      });
    }
    setDataSourcesOnboardingClientId("");
  };

  const handleLlmProviderOAuthStart = () => {
    setSourceConnectError("");
    if (selectedClient?.id) {
      window.sessionStorage.setItem(LLM_ONBOARDING_CLIENT_KEY, selectedClient.id);
      setLlmOnboardingClientId(selectedClient.id);
    }
  };

  const handleConnectDataSource = async (source, setup = {}, { newTab = false } = {}) => {
    setSourceSaving(true);
    setSourceConnectError("");

    // Claude BYOK stays wired up here (hidden, not removed): it only runs when
    // the Claude card is switched back to authType "api_key" in lib/data.js.
    const isClaudeAccountLogin = source.type === "anthropic_ai" && source.authType === "oauth";
    if ((source.type === "openai_ai" || source.type === "anthropic_ai") && !isClaudeAccountLogin && !demoMode) {
      const provider = source.type === "anthropic_ai" ? "anthropic" : "openai";
      const result = await saveBYOKAIProvider(
        selectedClient,
        provider,
        setup.apiKey,
        setup.model || source.defaultModel,
      );
      setSourceSaving(false);

      if (!result.ok) {
        setSourceConnectError(result.error || "Could not save AI provider key");
        return false;
      }

      setSourceRecords((current) => {
        const withoutCurrent = current.filter((record) => record.type !== source.type);
        return [
          ...withoutCurrent,
          {
            id: `server-${source.type}`,
            client_id: selectedClient.id,
            type: source.type,
            status: "connected",
            config: { auth_mode: "byok", storage: "database", model: result.model || setup.model },
            last_sync_at: new Date().toISOString(),
          },
        ];
      });
      setModal(null);
      refreshNotifications();
      return true;
    }

    if (source.type === "shopify" && !demoMode) {
      const result = await startShopifyOAuth(selectedClient?.id, setup.shopDomain);
      setSourceSaving(false);

      if (!result.ok) {
        setSourceConnectError(result.error || "Could not start Shopify OAuth");
        return false;
      }

      navigateForOAuth(result.authUrl, { newTab });
      return true;
    }

    const result = await connectDataSource(selectedClient?.id, source.type);
    setSourceSaving(false);

    if (!result.ok) {
      setSourceConnectError(result.error || "Could not connect source");
      return false;
    }

    setSourceRecords((current) => {
      const withoutCurrent = current.filter((record) => record.type !== result.record.type);
      return [...withoutCurrent, result.record];
    });
    setModal(null);
    return true;
  };

  const handleOAuthDataSource = async (source, { newTab = false } = {}) => {
    setSourceSaving(true);
    setSourceConnectError("");

    if (source.type === "openai_ai" && source.authType === "oauth") {
      const result = await startOpenAICodexAuth(
        selectedClient,
        source.model || source.defaultModel,
        { newTab: true },
      );
      setSourceSaving(false);
      if (!result.ok) {
        setSourceConnectError(result.error || "Could not start ChatGPT authentication");
        return false;
      }
      setCodexAuthPendingClientId(selectedClient.id);
      setSourceRecords((current) => {
        const existing = current.find((record) => record.type === "openai_ai");
        const record = existing || {
          id: "server-openai-pending",
          client_id: selectedClient.id,
          type: "openai_ai",
          last_sync_at: null,
        };
        return [
          ...current.filter((item) => item.type !== "openai_ai"),
          {
            ...record,
            status: "pending",
            config: {
              ...(record.config || {}),
              auth_mode: "codex_chatgpt",
              storage: "pending",
              model: result.model || source.model || source.defaultModel,
              user_code: result.userCode || null,
              authorization_url: result.authorizationUrl || null,
            },
          },
        ];
      });
      return true;
    }

    if (source.type === "anthropic_ai" && source.authType === "oauth") {
      const result = await startClaudeCodeAuth(selectedClient, { newTab: true });
      setSourceSaving(false);
      if (!result.ok) {
        setSourceConnectError(result.error || "Could not start Claude sign-in");
        return false;
      }
      setClaudeAuthPendingClientId(selectedClient.id);
      setSourceRecords((current) => {
        const existing = current.find((record) => record.type === "anthropic_ai");
        const record = existing || {
          id: "server-anthropic-pending",
          client_id: selectedClient.id,
          type: "anthropic_ai",
          last_sync_at: null,
        };
        return [
          ...current.filter((item) => item.type !== "anthropic_ai"),
          {
            ...record,
            status: "pending",
            config: {
              ...(record.config || {}),
              auth_mode: "claude_code",
              storage: "pending",
              authorization_url: result.authorizationUrl || null,
            },
          },
        ];
      });
      // Not a bare `true`: the modal needs the URL to offer "open sign-in
      // page again" if the new tab was blocked.
      return { ok: true, authorizationUrl: result.authorizationUrl };
    }

    if (
      source.type !== "klaviyo" &&
      source.type !== "hubspot_crm" &&
      source.type !== "google_ads" &&
      source.type !== "google_analytics_4" &&
      source.type !== "meta_ads" &&
      source.type !== "openrouter_ai"
    ) {
      setSourceSaving(false);
      setSourceConnectError("OAuth is not implemented for this connector yet");
      return false;
    }

    let result;
    if (source.type === "hubspot_crm") {
      result = await startHubSpotOAuth(selectedClient, { newTab });
    } else if (source.type === "google_ads") {
      result = await startGoogleAdsOAuth(selectedClient, { newTab });
    } else if (source.type === "google_analytics_4") {
      result = await startGA4OAuth(selectedClient, { newTab });
    } else if (source.type === "meta_ads") {
      result = await startMetaAdsOAuth(selectedClient, { newTab });
    } else if (source.type === "openrouter_ai") {
      result = await startOpenRouterOAuth(selectedClient, source.model, { newTab });
    } else {
      result = await startKlaviyoOAuth(selectedClient, { newTab });
    }
    if (!result.ok) {
      setSourceSaving(false);
      setSourceConnectError(result.error || "Could not start OAuth");
      return false;
    }
    setSourceSaving(false);
    return true;
  };

  const handleSubmitClaudeCode = async (source, code) => {
    setSourceConnectError("");
    const result = await submitClaudeCodeCode(selectedClient, code);
    if (!result.ok) return result;
    setClaudeAuthPendingClientId("");
    setSourcesRefreshKey((key) => key + 1);
    refreshNotifications();
    return result;
  };

  const handleManageIntegration = (source) => {
    setSourceConnectError("");
    if (source.manageUrl) {
      window.open(source.manageUrl, "_blank", "noopener,noreferrer");
      return;
    }
    setModal(source);
  };

  const handleDisconnectDataSource = async (source) => {
    setSourceSaving(true);
    setSourceConnectError("");

    const aiProviderByType = {
      openrouter_ai: "openrouter",
      openai_ai: "openai",
      anthropic_ai: "claude_code",
    };
    const dataConnectorTypes = new Set([
      "google_ads", "google_analytics_4", "meta_ads", "hubspot_crm", "klaviyo",
    ]);

    let result;
    const aiProvider = aiProviderByType[source.type];
    if (aiProvider) {
      result = await disconnectAIProvider(selectedClient, aiProvider);
    } else if (dataConnectorTypes.has(source.type)) {
      result = await disconnectDataConnector(selectedClient, source.type);
    } else {
      setSourceSaving(false);
      setSourceConnectError("Disconnect is not available for this source type");
      return false;
    }

    setSourceSaving(false);
    if (!result.ok) {
      setSourceConnectError(result.error || "Could not disconnect");
      return false;
    }

    setSourceRecords((current) => current.map((record) => (
      record.type === source.type
        ? {
            ...record,
            status: "disabled",
            last_sync_at: null,
            config: {
              ...(record.config || {}),
              storage: "none",
            },
          }
        : record
    )));
    setSourcesRefreshKey((key) => key + 1);
    return true;
  };

  if (isOAuthCallbackRoute) {
    return <OAuthCallbackPage />;
  }

  if (bootstrapping) {
    return (
      <div className="boot-shell">
        <div className="boot-mark">{ini}</div>
        <div className="boot-text">Loading workspace...</div>
      </div>
    );
  }

  // Takes priority over the generic auth-gating branch below so the SAME
  // component stays mounted across the login/signup transition an invited
  // user goes through - no need to stash the token anywhere and pick it
  // back up after a redirect.
  if (isAcceptInvitationRoute) {
    return (
      <AcceptInvitation
        isAuthed={isAuthed}
        agencyName={agencyName}
        brandLogo={brandLogo}
        ini={ini}
        onAccepted={(clientId) => {
          if (clientId) handleSelectClient(clientId);
          navigate(VIEW_PATHS.workspace, { replace: true });
        }}
      />
    );
  }

  if (!isAuthed || isRecoveryRoute || mfaRequired) {
    return (
      <Login
        ini={ini}
        agencyName={agencyName}
        brandLogo={brandLogo}
        initialMode={isRecoveryRoute ? "reset" : "signin"}
        mfaRequired={mfaRequired}
        onMfaVerified={() => setMfaRequired(false)}
        onAuthed={() => navigate(VIEW_PATHS.workspace, { replace: true })}
        onDemo={() => { setDemoMode(true); navigate(VIEW_PATHS.workspace, { replace: true }); }}
        onResetComplete={() => navigate("/login", { replace: true })}
      />
    );
  }

  if (session && !demoMode && !workspaceReady) {
    return (
      <div className="boot-shell">
        <div className="boot-mark">{ini}</div>
        <div className="boot-text">Preparing your workspace...</div>
      </div>
    );
  }

  return (
    <div className={`app-shell${isAdminView ? " admin-shell" : ""}`}>
      <Topbar
        agencyName={isAdminView ? agencyName : headerAgencyName}
        profile={profile}
        ini={ini}
        brandLogo={isAdminView ? brandLogo : headerAgencyLogo}
        onGoTo={goTo}
        onOpenAccountSettings={goToAccountSettings}
        onOpenAdminWorkspace={goToAdminWorkspace}
        onOpenClientWorkspace={goToClientWorkspace}
        onOpenAdminNotifications={goToAdminNotifications}
        isAdminView={isAdminView}
        onOpenAi={() => setAiOpen((open) => !open)}
        demoMode={demoMode}
        aiOpen={aiOpen}
        clients={clients}
        selectedClient={selectedClient}
        sources={sources}
        sourceSummary={sourceSummary}
        resolvedTheme={resolvedTheme}
        onThemeChange={changeTheme}
        notifications={notifications}
        notificationsLoading={notificationsLoading}
        onReadNotifications={async (ids) => {
          const result = await markInAppNotificationsRead(ids);
          if (result.ok) setNotifications((items) => items.map((item) => ids.includes(item.id) ? { ...item, read_at: new Date().toISOString() } : item));
        }}
        onSelectClient={handleSelectClient}
        onAddClient={openClientCreation}
      />
      <div className={`app-body${isAdminView ? " admin-body" : ""}`}>
        {!isAdminView && (
          <Sidebar
            view={view}
            onGoTo={goTo}
            collapsed={sidebarCollapsed}
            onToggle={() => setSidebarCollapsed((collapsed) => !collapsed)}
            client={selectedClient}
            org={org}
          />
        )}
        <main className={`app-main${isAdminView ? " admin-main" : ""}`}>
          <Suspense fallback={<div className="page-loading" aria-busy="true" />}>
          {isAdminView && (
            <div data-page-view="admin">
              <AdminWorkspace
                clients={clients}
                clientsLoaded={clientsLoaded}
                org={org}
                selectedClient={selectedClient}
                agencyName={agencyName}
                setAgencyName={handleAgencyNameChange}
                accent={accent}
                hexInput={hexInput}
                setHexInput={(value) => {
                  setHexInput(value);
                  setBrandingSaved(false);
                }}
                applyHex={applyHex}
                sourceSummary={sourceSummary}
                branding={branding}
                onBrandingChange={handleBrandingChange}
                onSaveBranding={handleSaveBranding}
                brandingSaving={brandingSaving}
                brandingError={brandingError}
                brandingSaved={brandingSaved}
                savedBrandingSnapshot={savedBrandingSnapshot}
                onEditClientBranding={handleEditClientBranding}
                onManageClient={handleAdminManageClient}
                onGoTo={goTo}
                onNotificationsChanged={refreshNotifications}
                settingsProps={{
                  org,
                  selectedClient,
                  demoMode,
                  onClientUpdated: handleClientUpdated,
                  onClientDeleted: handleClientDeleted,
                  focusAccountTabSignal: settingsFocusAccountTab,
                  themePref,
                  resolvedTheme,
                  onThemeChange: changeTheme,
                }}
              />
            </div>
          )}
          <div data-page-view="workspace" style={{ display: view === "workspace" ? undefined : "none" }}>
            <Workspace
              onGoTo={goTo}
              client={selectedClient}
              org={org}
              clientsLoading={clientsLoading}
              clientsError={clientsError}
              sourceSummary={sourceSummary}
              onAddClient={openClientCreation}
              hideEmptyState={showFirstWorkspaceModal}
              hideContent={showLlmProviderOnboarding || showDataSourcesOnboarding}
            />
          </div>
          <div data-page-view="dashboards" style={{ display: view === "dashboards" ? undefined : "none" }}>
            <Dashboards onGoTo={goTo} onBuildReport={buildReportFromDashboard} client={selectedClient} sources={sources} sourceSummary={sourceSummary} workspaceCurrency={org?.branding_config?.currency || "usd"} />
          </div>
          <div data-page-view="reports" style={{ display: view === "reports" ? undefined : "none" }}>
            <Reports
              active={view === "reports"}
              branding={reportBranding}
              onGoTo={goTo}
              client={selectedClient}
              org={org}
              sources={sources}
              workspaceCurrency={org?.branding_config?.currency || "usd"}
              prefill={reportPrefill}
              onPrefillConsumed={() => setReportPrefill(null)}
            />
          </div>
          <div data-page-view="integrations" style={{ display: view === "integrations" ? undefined : "none" }}>
            <Integrations
              client={selectedClient}
              org={org}
              sources={sources}
              sourcesLoading={sourcesLoading}
              sourcesError={sourcesError}
              onConnect={(source) => {
                setSourceConnectError("");
                setModal(source);
              }}
              onManage={handleManageIntegration}
              onDisconnect={handleDisconnectDataSource}
              onAddClient={openClientCreation}
              onRefresh={() => setSourcesRefreshKey((key) => key + 1)}
            />
          </div>
          <div data-page-view="branding" style={{ display: view === "branding" ? undefined : "none" }}>
            {(!org || !selectedClient || canAccessView(selectedClient, org, "branding")) ? (
              <ClientBranding
                active={view === "branding"}
                client={selectedClient}
                agencyName={headerAgencyName}
                accent={activeOwnerAccentColor}
                branding={activeOwnerBranding}
                canEdit={canAccess(selectedClient, org, "Branding", "edit")}
                onOverrideChange={handleClientBrandingOverrideChange}
                onSave={handleSaveClientBranding}
                saving={brandingSaving}
                error={brandingError}
                saved={brandingSaved}
                sourceSummary={sourceSummary}
              />
            ) : (
              <AccessDenied moduleName="Branding" roleLabel={currentClientRoleLabel(selectedClient, org)} />
            )}
          </div>
          <div data-page-view="agencyBranding" style={{ display: view === "agencyBranding" ? undefined : "none" }}>
            <AgencyBranding
              active={view === "agencyBranding"}
              agencyName={agencyName}
              setAgencyName={handleAgencyNameChange}
              accent={accent}
              hexInput={hexInput}
              setHexInput={(value) => {
                setHexInput(value);
                setBrandingSaved(false);
              }}
              applyHex={applyHex}
              client={selectedClient}
              clients={clients.filter((c) => c.access !== "shared")}
              sourceSummary={sourceSummary}
              branding={branding}
              onBrandingChange={handleBrandingChange}
              onSave={handleSaveBranding}
              saving={brandingSaving}
              error={brandingError}
              saved={brandingSaved}
              savedSnapshot={savedBrandingSnapshot}
              onEditClientBranding={handleEditClientBranding}
            />
          </div>
          <div data-page-view="settings" style={{ display: view === "settings" ? undefined : "none" }}>
            <Settings
              active={view === "settings"}
              org={org}
              selectedClient={selectedClient}
              demoMode={demoMode}
              onClientUpdated={handleClientUpdated}
              onClientDeleted={handleClientDeleted}
              focusAccountTabSignal={settingsFocusAccountTab}
              themePref={themePref}
              resolvedTheme={resolvedTheme}
              onThemeChange={changeTheme}
            />
          </div>
          <div data-page-view="notifications" style={{ display: view === "notifications" ? undefined : "none" }}>
            <Notifications
              org={org}
              client={selectedClient}
              onGoTo={goTo}
              onSelectClient={handleSelectClient}
              onNotificationsChanged={refreshNotifications}
            />
          </div>
          </Suspense>
        </main>
        <AiDrawer
          open={aiOpen}
          onClose={() => setAiOpen(false)}
          client={selectedClient}
          sources={sources}
          sourceSummary={sourceSummary}
        />
      </div>

      <ConnectModal
        data={modal}
        onClose={() => setModal(null)}
        onSave={async (source, setup) => {
          const result = await handleConnectDataSource(source, setup, { newTab: true });
          if (result !== false && source.type === "shopify") setModal(null);
          return result;
        }}
        onSubmitCode={handleSubmitClaudeCode}
        onOAuth={async (source) => {
          const result = await handleOAuthDataSource(source, { newTab: true });
          // Claude's sign-in has a second step (paste the code) inside this modal.
          if (result !== false && source.type !== "anthropic_ai") setModal(null);
          return result;
        }}
        saving={sourceSaving}
        error={sourceConnectError}
      />
      <ClientWorkspaceModal
        open={clientModalOpen}
        mode="add"
        onClose={() => setClientModalOpen(false)}
        onCreate={handleCreateClient}
        saving={clientSaving}
        error={clientCreateError}
      />
      <ClientWorkspaceModal
        open={showFirstWorkspaceModal}
        mode="first"
        onCreate={handleCreateClient}
        saving={clientSaving}
        error={clientCreateError}
      />
      <LLMProviderOnboardingModal
        open={showLlmProviderOnboarding}
        sources={sources}
        onSave={handleConnectDataSource}
        onOAuth={(source) => handleOAuthDataSource(source, { newTab: true })}
        onSubmitCode={handleSubmitClaudeCode}
        onProviderOAuthStart={handleLlmProviderOAuthStart}
        saving={sourceSaving}
        error={sourceConnectError}
        onProviderSelect={() => setSourceConnectError("")}
        onSkip={() => handleLlmOnboardingComplete("skipped")}
        onComplete={() => handleLlmOnboardingComplete("connected")}
      />
      <DataSourcesOnboardingModal
        open={showDataSourcesOnboarding}
        sources={sources}
        onSave={(source, setup) => handleConnectDataSource(source, setup, { newTab: true })}
        onOAuth={(source) => handleOAuthDataSource(source, { newTab: true })}
        saving={sourceSaving}
        error={sourceConnectError}
        onSourceSelect={() => setSourceConnectError("")}
        onComplete={handleDataSourcesOnboardingComplete}
      />
      {/* Gated on clients.length > 0 (not just isAuthed) so a brand-new
          account's tour waits for their first workspace to actually exist —
          driven by the real client list, not a timeout guess — instead of
          running over the empty Workspace state behind the onboarding modal. */}
      <Walkthrough
        active={isAuthed && uiPreferencesLoaded && clients.length > 0 && !showLlmProviderOnboarding && !showDataSourcesOnboarding}
        completed={Boolean(uiPreferences.walkthroughComplete)}
        onFinish={handleWalkthroughFinish}
        onCancel={handleWalkthroughCancel}
      />
    </div>
  );
}
