// SPDX-License-Identifier: AGPL-3.0-only
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  CURRENCIES,
  INDUSTRIES,
  NOTIFICATION_CATEGORIES,
  changeClientMemberRole,
  confirmMfaEnrollment,
  defaultNotificationPrefs,
  deleteClientWorkspace,
  disableMfa,
  formatDate,
  formatRelative,
  getClientCounts,
  getClientTeam,
  getNotificationPrefs,
  getRecentSessions,
  notifyAccountSecurityEvent,
  recordSessionActivity,
  getProfile,
  listMfaFactors,
  reauthenticate,
  removeClientMember,
  saveNotificationPrefs,
  signOutOtherSessions,
  startMfaEnrollment,
  updateClientWorkspace,
  updatePassword,
  updateProfile,
  updateProfilePhoto,
} from "../lib/settings";
import { THEME_OPTIONS } from "../lib/theme";
import { compressImageToSquareDataUrl } from "../lib/branding";
import { getClientRole } from "../lib/clients";
import TeamInviteFlow from "../components/TeamInviteFlow";
import { roleCanAccess } from "../lib/permissions";
import { useUnsavedChanges } from "../lib/UnsavedChangesContext.jsx";

const TABS = [
  { id: "account", icon: "ti-user-circle", label: "Account" },
  { id: "workspace", icon: "ti-building-store", label: "Workspace" },
  { id: "team", icon: "ti-users", label: "Team" },
  { id: "billing", icon: "ti-credit-card", label: "Billing" },
  { id: "notifications", icon: "ti-bell", label: "Notifications" },
];
const ADMIN_TABS = [{ id: "security", icon: "ti-shield-lock", label: "Security" }];

function sessionDeviceLabel(userAgent = "") {
  const ua = userAgent.toLowerCase();
  const browser = ua.includes("edg") ? "Edge" : ua.includes("firefox") ? "Firefox" : ua.includes("chrome") ? "Chrome" : ua.includes("safari") ? "Safari" : "Browser";
  const os = ua.includes("android") ? "Android" : ua.includes("iphone") || ua.includes("ipad") ? "iOS" : ua.includes("windows") ? "Windows" : ua.includes("mac os") ? "macOS" : ua.includes("linux") ? "Linux" : "Unknown OS";
  return `${browser} · ${os}`;
}

function isMobileSession(userAgent = "") {
  return /android|iphone|ipad|mobile/i.test(userAgent);
}
function Banner({ error, success }) {
  if (error) return <div className="inline-error"><i className="ti ti-alert-triangle" /> {error}</div>;
  if (success) return <div className="inline-success"><i className="ti ti-check" /> {success}</div>;
  return null;
}

// Premium segmented Settings navigation. Visually related to the onboarding
// stepper's sliding-pill treatment (same easing, same restrained surface),
// but semantically a real tablist: no step numbers, no "done" state, full
// keyboard support, and every item stays reachable at any width instead of
// scrolling off-screen (the old .inline-tabs row could hide entire sections
// on narrow screens with no scroll affordance).
function SettingsNav({ tabs, active, onChange }) {
  const listRef = useRef(null);
  const itemRefs = useRef({});
  const [indicator, setIndicator] = useState({ left: 0, width: 0, ready: false });

  const measure = useCallback(() => {
    const el = itemRefs.current[active];
    const list = listRef.current;
    if (!el || !list) return;
    const elRect = el.getBoundingClientRect();
    const listRect = list.getBoundingClientRect();
    setIndicator({ left: elRect.left - listRect.left + list.scrollLeft, width: elRect.width, ready: true });
  }, [active]);

  useLayoutEffect(() => { measure(); }, [measure]);
  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);

  const focusIndex = (index) => {
    const tab = tabs[(index + tabs.length) % tabs.length];
    itemRefs.current[tab.id]?.focus();
    onChange(tab.id);
  };

  const handleKeyDown = (event, index) => {
    if (event.key === "ArrowRight") { event.preventDefault(); focusIndex(index + 1); }
    else if (event.key === "ArrowLeft") { event.preventDefault(); focusIndex(index - 1); }
    else if (event.key === "Home") { event.preventDefault(); focusIndex(0); }
    else if (event.key === "End") { event.preventDefault(); focusIndex(tabs.length - 1); }
  };

  return (
    <div className="settings-nav" role="tablist" aria-label="Settings sections" ref={listRef}>
      {indicator.ready && (
        <span className="settings-nav-indicator" style={{ left: indicator.left, width: indicator.width }} />
      )}
      {tabs.map((t, index) => (
        <button
          type="button"
          key={t.id}
          ref={(el) => { itemRefs.current[t.id] = el; }}
          role="tab"
          aria-selected={active === t.id}
          tabIndex={active === t.id ? 0 : -1}
          id={`settings-tab-${t.id}`}
          aria-controls={`settings-panel-${t.id}`}
          className={`settings-nav-item${active === t.id ? " active" : ""}`}
          onClick={() => onChange(t.id)}
          onKeyDown={(event) => handleKeyDown(event, index)}
        >
          <i className={`ti ${t.icon}`} aria-hidden="true" />
          <span>{t.label}</span>
        </button>
      ))}
    </div>
  );
}

export default function Settings({ org, selectedClient, onClientUpdated, onClientDeleted, active, demoMode, themePref, resolvedTheme, onThemeChange, focusAccountTabSignal, adminSection = null }) {
  const [tab, setTab] = useState(adminSection || "account");

  // Settings stays mounted across navigations, so its own tab state doesn't
  // reset on its own - the topbar's "Account settings" menu item bumps this
  // signal to force landing on Account specifically, regardless of whatever
  // tab Settings was last left on.
  useEffect(() => {
    if (focusAccountTabSignal) setTab("account");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusAccountTabSignal]);
  useEffect(() => {
    if (adminSection) setTab(adminSection);
  }, [adminSection]);
  const orgId = org?.id || null;
  const clientId = selectedClient?.id || null;
  // Demo mode has no Supabase session at all, so every write below would fail.
  const readOnly = demoMode || !orgId;

  /* ---------------- account ---------------- */
  const [profile, setProfile] = useState(null);
  const [profileForm, setProfileForm] = useState(null);
  const [profileSaving, setProfileSaving] = useState(false);
  const [profileMsg, setProfileMsg] = useState({ error: "", success: "" });
  const [photoUploading, setPhotoUploading] = useState(false);

  /* ---------------- workspace ---------------- */
  const [wsForm, setWsForm] = useState(null);
  const [wsSaving, setWsSaving] = useState(false);
  const [wsMsg, setWsMsg] = useState({ error: "", success: "" });
  const [counts, setCounts] = useState(null);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [deleteError, setDeleteError] = useState("");
  const [deleting, setDeleting] = useState(false);
  const deleteModalRef = useRef(null);

  /* ---------------- team ---------------- */
  const [members, setMembers] = useState([]);
  const [teamLoading, setTeamLoading] = useState(false);
  const [teamMsg, setTeamMsg] = useState({ error: "", success: "" });
  const [addMemberOpen, setAddMemberOpen] = useState(false);

  // The single source of truth for "what is my role on the CURRENTLY
  // SELECTED client" - getClientRole (lib/clients.js) is shared with
  // App.jsx/ClientBranding.jsx so this branching is never duplicated a
  // third time. Deliberately never falls back to org?.role for a SHARED
  // client - that would leak the viewer's own unrelated org role as if it
  // applied to someone else's client, which is exactly the Bug 8 class of
  // stale-permission issue this fixes. currentMember (from the client-scoped
  // team roster) is kept only as the freshest source for an OWNED client
  // while org?.role is a safe same-org fallback before that roster loads.
  const currentMember = members.find((member) => member.userId === profile?.id);
  const effectiveRole = currentMember?.role || org?.role || "";
  const clientRole = getClientRole(
    selectedClient ? { ...selectedClient, role: selectedClient.role || effectiveRole } : selectedClient,
    org,
  ) || (selectedClient?.access === "owner" ? "owner" : "");
  const isClientAdmin = clientRole === "owner" || clientRole === "admin";
  // Section-level gating within Settings, per the RBAC permission matrix
  // (lib/settings.js CLIENT_ROLE_INFO) - reuses `clientRole` above (the
  // freshest-available resolution for this page) rather than re-deriving
  // role from client/org, since that would lose the roster-freshness fix
  // described above. Team is Admin+ (Editor/Viewer never see team
  // management); Billing is Owner-only even for Admin, matching the matrix.
  const canViewTeamSection = roleCanAccess(clientRole, "Team", "view");
  const canViewBillingSection = roleCanAccess(clientRole, "Billing", "view");

  // Bounces back to Account if the active tab stops being reachable - e.g.
  // switching from a client where this user is Admin to one where they're
  // only an Editor while Team/Billing was open.
  useEffect(() => {
    if (tab === "team" && !canViewTeamSection) setTab("account");
    if (tab === "billing" && !canViewBillingSection) setTab("account");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, canViewTeamSection, canViewBillingSection]);

  /* ---------------- security ---------------- */
  const [pwd, setPwd] = useState({ current: "", next: "", confirm: "" });
  const [showPwd, setShowPwd] = useState({ current: false, next: false, confirm: false });
  const [pwdSaving, setPwdSaving] = useState(false);
  const [pwdMsg, setPwdMsg] = useState({ error: "", success: "" });
  const [mfaFactors, setMfaFactors] = useState([]);
  const [enrollment, setEnrollment] = useState(null);
  const [mfaCode, setMfaCode] = useState("");
  const [mfaBusy, setMfaBusy] = useState(false);
  const [mfaMsg, setMfaMsg] = useState({ error: "", success: "" });
  const [sessionsBusy, setSessionsBusy] = useState(false);
  const [recentSessions, setRecentSessions] = useState([]);

  /* ---------------- notifications ---------------- */
  const [prefs, setPrefs] = useState(defaultNotificationPrefs);
  const [savedPrefs, setSavedPrefs] = useState(defaultNotificationPrefs);
  const [prefsSaving, setPrefsSaving] = useState(false);
  const [prefsMsg, setPrefsMsg] = useState({ error: "", success: "" });
  const [canEditNotificationPrefs, setCanEditNotificationPrefs] = useState(false);

  const twoFactorOn = mfaFactors.length > 0;

  /* ---------------- loaders ---------------- */

  useEffect(() => {
    let alive = true;
    (async () => {
      const result = await getProfile();
      if (!alive) return;
      if (result.ok) {
        setProfile(result.profile);
        setProfileForm({
          fullName: result.profile.fullName,
          phone: result.profile.phone,
        });
      }
    })();
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!selectedClient) return;
    setWsForm({
      name: selectedClient.name || "",
      currency: selectedClient.currency || "usd",
      industry: selectedClient.industry || "marketing",
    });
  }, [selectedClient?.id, selectedClient?.name, selectedClient?.currency, selectedClient?.industry]);

  useEffect(() => {
    if (!clientId) { setCounts(null); return; }
    let alive = true;
    (async () => {
      const result = await getClientCounts(clientId);
      if (alive && result.ok) setCounts(result.counts);
    })();
    return () => { alive = false; };
  }, [clientId]);

  const loadTeam = useCallback(async () => {
    if (!clientId) { setMembers([]); return; }
    setTeamLoading(true);
    const result = await getClientTeam(clientId);
    setTeamLoading(false);
    if (result.ok) setMembers(result.members);
    else setTeamMsg({ error: result.error, success: "" });
  }, [clientId]);

  useEffect(() => { loadTeam(); }, [loadTeam]);

  const refreshMfa = useCallback(async () => {
    const result = await listMfaFactors();
    if (result.ok) setMfaFactors(result.factors);
  }, []);

  useEffect(() => { refreshMfa(); }, [refreshMfa]);

  const loadRecentSessions = useCallback(async () => {
    if (!orgId) return;
    await recordSessionActivity(orgId);
    const result = await getRecentSessions();
    if (result.ok) setRecentSessions(result.sessions || []);
  }, [orgId]);
  useEffect(() => { loadRecentSessions(); }, [loadRecentSessions]);

  useEffect(() => {
    if (!orgId || !profile?.id) return;
    let alive = true;
    setCanEditNotificationPrefs(isClientAdmin);
    (async () => {
      const result = await getNotificationPrefs(orgId, profile.id, clientId);
      if (alive && result.ok) {
        setPrefs(result.prefs);
        setSavedPrefs(result.prefs);
        setCanEditNotificationPrefs(Boolean(result.can_edit || isClientAdmin));
      }
    })();
    return () => { alive = false; };
  }, [orgId, profile?.id, clientId, isClientAdmin]);

  /* ---------------- derived ---------------- */

  const displayName = profile?.fullName || profile?.email?.split("@")[0] || "";
  const initial = (displayName || "A").trim()[0]?.toUpperCase() || "A";
  // The role shown here is for the SELECTED CLIENT specifically (Owner for
  // one of your own clients, or whatever role was assigned when a different
  // agency shared a client with you) - never the viewer's unrelated org
  // role when looking at a shared client.
  const myRole = clientRole ? clientRole[0].toUpperCase() + clientRole.slice(1) : "";

  const profileDirty = Boolean(profileForm && profile && (profileForm.fullName !== profile.fullName || profileForm.phone !== profile.phone));
  const workspaceDirty = Boolean(wsForm && selectedClient && (wsForm.name !== (selectedClient.name || "") || wsForm.currency !== (selectedClient.currency || "usd") || wsForm.industry !== (selectedClient.industry || "marketing")));
  const prefsDirty = JSON.stringify(prefs) !== JSON.stringify(savedPrefs);
  const securityDirty = Boolean(pwd.current || pwd.next || pwd.confirm);
  const hasUnsavedChanges = profileDirty || workspaceDirty || prefsDirty || securityDirty;
  const { registerChanges, clearChanges, confirmNavigation } = useUnsavedChanges();

  useEffect(() => {
    if (!active) { clearChanges(); return undefined; }
    registerChanges({
      isDirty: hasUnsavedChanges,
      onSave: async () => {
        const profileOk = !profileDirty || await saveProfile();
        const workspaceOk = !workspaceDirty || await saveWorkspace();
        const prefsOk = !prefsDirty || await savePrefs();
        const passwordOk = !securityDirty || await changePassword();
        return profileOk && workspaceOk && prefsOk && passwordOk;
      },
      onDiscard: () => {
        if (profile) setProfileForm({ fullName: profile.fullName, phone: profile.phone });
        if (selectedClient) setWsForm({ name: selectedClient.name || "", currency: selectedClient.currency || "usd", industry: selectedClient.industry || "marketing" });
        setPrefs(savedPrefs);
        setPwd({ current: "", next: "", confirm: "" });
      },
    });
    return () => clearChanges();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, hasUnsavedChanges, registerChanges, clearChanges]);
  const requestTabChange = (nextTab) => {
    if (nextTab === tab) return;
    confirmNavigation(() => setTab(nextTab));
  };
  /* ---------------- handlers ---------------- */

  const saveProfile = async () => {
    setProfileSaving(true);
    setProfileMsg({ error: "", success: "" });
    const result = await updateProfile(profileForm);
    setProfileSaving(false);
    if (!result.ok) { setProfileMsg({ error: result.error, success: "" }); return false; }
    setProfile((prev) => ({ ...prev, ...profileForm }));
    notifyAccountSecurityEvent(orgId, "profile_updated");
    setProfileMsg({ error: "", success: "Profile saved." });
    return true;
  };

  const uploadProfilePhoto = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file || demoMode) return;
    setPhotoUploading(true);
    const result = await compressImageToSquareDataUrl(file);
    if (!result.ok) {
      setProfileMsg({ error: result.error, success: '' });
      setPhotoUploading(false);
      return;
    }
    const saved = await updateProfilePhoto(result.dataUrl);
    setPhotoUploading(false);
    if (!saved.ok) {
      setProfileMsg({ error: saved.error, success: '' });
      return;
    }
    setProfile((prev) => (prev ? { ...prev, avatarUrl: result.dataUrl } : prev));
    notifyAccountSecurityEvent(orgId, "profile_updated");
    setProfileMsg({ error: '', success: 'Profile photo updated.' });
  };

  const saveWorkspace = async () => {
    setWsSaving(true);
    setWsMsg({ error: "", success: "" });
    const result = await updateClientWorkspace(clientId, wsForm);
    setWsSaving(false);
    if (!result.ok) { setWsMsg({ error: result.error, success: "" }); return false; }
    onClientUpdated?.(result.client);
    setWsMsg({ error: "", success: "Workspace saved." });
    return true;
  };

  const openDeleteModal = () => {
    setDeleteConfirm("");
    setDeleteError("");
    setDeleteModalOpen(true);
  };
  const closeDeleteModal = () => {
    if (deleting) return;
    setDeleteModalOpen(false);
    setDeleteConfirm("");
    setDeleteError("");
  };

  useEffect(() => {
    if (!deleteModalOpen) return undefined;
    const focusTimer = window.setTimeout(() => deleteModalRef.current?.querySelector("input")?.focus(), 0);
    const handleKeyDown = (event) => {
      if (event.key === "Escape") { event.preventDefault(); closeDeleteModal(); return; }
      if (event.key !== "Tab") return;
      const focusable = Array.from(deleteModalRef.current?.querySelectorAll("button, input") || [])
        .filter((element) => !element.disabled);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", handleKeyDown, true);
    return () => { window.clearTimeout(focusTimer); document.removeEventListener("keydown", handleKeyDown, true); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deleteModalOpen, deleting]);

  const confirmDeleteWorkspace = async () => {
    if (deleteConfirm !== selectedClient?.name || deleting) return;
    setDeleting(true);
    setDeleteError("");
    const result = await deleteClientWorkspace(clientId);
    setDeleting(false);
    if (!result.ok) { setDeleteError(result.error); return; }
    setDeleteModalOpen(false);
    onClientDeleted?.(clientId);
  };

  // Only "shared" rows (client_memberships) are editable from here - a
  // member with access "owner" is a real member of the client's owning
  // organization, whose role applies uniformly across every client that
  // org owns, not just this one; managing that belongs to a future Agency
  // Settings area, not this client-scoped Team tab.
  const changeRole = async (member, role) => {
    setTeamMsg({ error: "", success: "" });
    const result = await changeClientMemberRole(clientId, member.userId, role);
    if (!result.ok) { setTeamMsg({ error: result.error, success: "" }); return false; }
    setMembers((prev) => prev.map((m) => (m.userId === member.userId ? { ...m, role } : m)));
    setTeamMsg({ error: "", success: "Role updated." });
  };

  const kickMember = async (member) => {
    setTeamMsg({ error: "", success: "" });
    const result = await removeClientMember(clientId, member.userId);
    if (!result.ok) { setTeamMsg({ error: result.error, success: "" }); return; }
    setMembers((prev) => prev.filter((m) => m.userId !== member.userId));
    setTeamMsg({ error: "", success: `${member.name} removed from this client.` });
  };

  const changePassword = async () => {
    setPwdMsg({ error: "", success: "" });
    if (pwd.next !== pwd.confirm) { setPwdMsg({ error: "New passwords do not match", success: "" }); return false; }

    setPwdSaving(true);
    const reauth = await reauthenticate(profile?.email, pwd.current);
    if (!reauth.ok) { setPwdSaving(false); setPwdMsg({ error: reauth.error, success: "" }); return false; }

    const result = await updatePassword(pwd.next);
    setPwdSaving(false);
    if (!result.ok) { setPwdMsg({ error: result.error, success: "" }); return false; }
    setPwd({ current: "", next: "", confirm: "" });
    notifyAccountSecurityEvent(orgId, "password_changed");
    setPwdMsg({ error: "", success: "Password updated." });
    return true;
  };

  const beginMfa = async () => {
    setMfaBusy(true);
    setMfaMsg({ error: "", success: "" });
    const result = await startMfaEnrollment();
    setMfaBusy(false);
    if (!result.ok) { setMfaMsg({ error: result.error, success: "" }); return; }
    setEnrollment(result);
  };

  const finishMfa = async () => {
    setMfaBusy(true);
    setMfaMsg({ error: "", success: "" });
    const result = await confirmMfaEnrollment(enrollment.factorId, mfaCode);
    setMfaBusy(false);
    if (!result.ok) { setMfaMsg({ error: result.error, success: "" }); return; }
    setEnrollment(null);
    setMfaCode("");
    notifyAccountSecurityEvent(orgId, "mfa_enabled");
    setMfaMsg({ error: "", success: "Two-factor authentication is on." });
    refreshMfa();
  };

  const turnOffMfa = async () => {
    setMfaBusy(true);
    setMfaMsg({ error: "", success: "" });
    const result = await disableMfa(mfaFactors[0].id);
    setMfaBusy(false);
    if (!result.ok) { setMfaMsg({ error: result.error, success: "" }); return; }
    notifyAccountSecurityEvent(orgId, "mfa_disabled");
    setMfaMsg({ error: "", success: "Two-factor authentication turned off." });
    refreshMfa();
  };

  const revokeOtherSessions = async () => {
    setSessionsBusy(true);
    setMfaMsg({ error: "", success: "" });
    const result = await signOutOtherSessions();
    setSessionsBusy(false);
    if (!result.ok) { setMfaMsg({ error: result.error, success: "" }); return; }
    notifyAccountSecurityEvent(orgId, "sessions_revoked");
    setMfaMsg({ error: "", success: "Signed out of all other sessions." });
    loadRecentSessions();
  };

  const toggleCategoryInApp = (categoryId) => {
    if (!canEditNotificationPrefs) {
      setPrefsMsg({ error: "You don't have permission to update notification preferences for this client.", success: "" });
      return;
    }
    setPrefs((prev) => {
      const current = prev.categories?.[categoryId] || { inApp: true, email: false };
      return { ...prev, categories: { ...prev.categories, [categoryId]: { ...current, inApp: !current.inApp } } };
    });
    setPrefsMsg({ error: "", success: "" });
  };

  const toggleCategoryEmail = (categoryId) => {
    if (!canEditNotificationPrefs) {
      setPrefsMsg({ error: "You don't have permission to update notification preferences for this client.", success: "" });
      return;
    }
    setPrefs((prev) => {
      const current = prev.categories?.[categoryId] || { inApp: true, email: false };
      return { ...prev, categories: { ...prev.categories, [categoryId]: { ...current, email: !current.email } } };
    });
    setPrefsMsg({ error: "", success: "" });
  };

  const savePrefs = async () => {
    if (!canEditNotificationPrefs) {
      setPrefsMsg({ error: "You don't have permission to update notification preferences for this client.", success: "" });
      return false;
    }
    setPrefsSaving(true);
    setPrefsMsg({ error: "", success: "" });
    const result = await saveNotificationPrefs(orgId, profile?.id, clientId, prefs);
    setPrefsSaving(false);
    if (!result.ok) { setPrefsMsg({ error: result.error, success: "" }); return false; }
    setSavedPrefs(prefs);
    setPrefsMsg({ error: "", success: "Notification preferences saved." });
    return true;
  };

  // Full-page takeover (sidebar/topbar untouched, Settings' own nav/tabs
  // hidden) rather than a nested modal - see TeamInviteFlow.jsx.
  if (addMemberOpen) {
    return (
      <TeamInviteFlow
        client={selectedClient}
        onClose={() => setAddMemberOpen(false)}
        onInvited={() => { setAddMemberOpen(false); loadTeam(); }}
      />
    );
  }

  const baseTabs = adminSection ? ADMIN_TABS : TABS;
  const visibleTabs = baseTabs.filter((t) => {
    if (t.id === "team") return canViewTeamSection;
    if (t.id === "billing") return canViewBillingSection;
    return true;
  });

  return (
    <div className="page settings-page">
      {readOnly && (
        <div className="inline-error">
          <i className="ti ti-alert-triangle" />
          {demoMode
            ? "You're in demo mode — sign in to change any of these settings."
            : "No workspace is linked to this account, so these settings can't be saved."}
        </div>
      )}

      {!adminSection && <SettingsNav tabs={visibleTabs} active={tab} onChange={requestTabChange} />}

      {/* ---------------- Account ---------------- */}
      {tab === "account" && (
        <div id="settings-panel-account" role="tabpanel" aria-labelledby="settings-tab-account" className="studio-col">
          <section className="studio-card">
            <div className="studio-card-head has-meta">
              <span className="studio-icn"><i className="ti ti-id-badge-2" /></span>
              <div><h3>Personal details</h3><p>Your name and how the team reaches you</p></div>
              {(currentMember?.joinedAt || profile?.createdAt) && (
                <span className="studio-tag tag-meta">Member since · {formatDate(currentMember?.joinedAt || profile?.createdAt)}</span>
              )}
            </div>

            <div className="profile-photo-row">
              <div className="profile-photo">
                <div className={`profile-photo-mark${profile?.avatarUrl ? " has-image" : ""}`}>
                  {profile?.avatarUrl ? <img src={profile.avatarUrl} alt="" /> : initial}
                </div>
                <label className={`profile-photo-btn${photoUploading ? " is-uploading" : ""}`} title={photoUploading ? "Uploading..." : "Change profile photo"}>
                  <i className={`ti ${photoUploading ? "ti-loader-2" : "ti-camera"}`} />
                  <input type="file" accept="image/png,image/jpeg,image/webp" onChange={uploadProfilePhoto} disabled={demoMode || photoUploading} />
                </label>
              </div>
              <div>
                <div className="profile-photo-name">{displayName || "Your name"}</div>
                <div className="profile-photo-sub">{myRole ? `${myRole} · ${selectedClient?.name || ""}` : profile?.email}</div>
              </div>
            </div>

            <Banner error={profileMsg.error} success={profileMsg.success} />

            {profileForm && (
              <>
                <div className="studio-field-row">
                  <label className="studio-field">
                    <span>Full name</span>
                    <input
                      value={profileForm.fullName}
                      placeholder="Your name"
                      onChange={(e) => setProfileForm((f) => ({ ...f, fullName: e.target.value }))}
                    />
                  </label>
                  <label className="studio-field">
                    <span>Work email</span>
                    <input value={profile?.email || ""} disabled title="Contact support to change your email" />
                  </label>
                </div>
                <div className="studio-field-row">
                  <label className="studio-field">
                    <span>Phone</span>
                    <input
                      value={profileForm.phone}
                      placeholder="+92 300 1234567"
                      onChange={(e) => setProfileForm((f) => ({ ...f, phone: e.target.value }))}
                    />
                  </label>
                </div>
                <button className="primary-btn" onClick={saveProfile} disabled={profileSaving || demoMode}>
                  {profileSaving ? "Saving..." : "Save changes"}
                </button>
              </>
            )}
          </section>

          <section className="studio-card">
            <div className="studio-card-head">
              <span className="studio-icn"><i className="ti ti-palette" /></span>
              <div><h3>Appearance</h3><p>Saved on this device, not your account</p></div>
            </div>
            <div className="theme-row">
              {THEME_OPTIONS.map((option) => (
                <button
                  key={option.id}
                  className={`theme-card${themePref === option.id ? " active" : ""}`}
                  onClick={() => onThemeChange?.(option.id)}
                  aria-pressed={themePref === option.id}
                >
                  <span className={`theme-swatch ${option.id}`} aria-hidden="true">
                    <span className="tsw-bar" /><span className="tsw-body" />
                  </span>
                  <span className="theme-label"><i className={`ti ${option.icon}`} />{option.label}</span>
                </button>
              ))}
            </div>
            {themePref === "system" && (
              <div className="studio-hint" style={{ marginBottom: 0, marginTop: 12 }}>
                Following your device — currently <b>{resolvedTheme}</b>.
              </div>
            )}
          </section>
        </div>
      )}

      {/* ---------------- Workspace ---------------- */}
      {tab === "workspace" && (
        <div id="settings-panel-workspace" role="tabpanel" aria-labelledby="settings-tab-workspace" className="studio-col">
          <section className="studio-card">
            <div className="studio-card-head">
              <span className="studio-icn"><i className="ti ti-building-store" /></span>
              <div><h3>Workspace details</h3><p>The basics for {selectedClient?.name || "this client workspace"}</p></div>
            </div>

            <Banner error={wsMsg.error} success={wsMsg.success} />

            {wsForm ? (
              <>
                <div className="studio-field-row">
                  <label className="studio-field">
                    <span>Workspace name</span>
                    <input value={wsForm.name} disabled={!isClientAdmin || readOnly} onChange={(e) => setWsForm((f) => ({ ...f, name: e.target.value }))} />
                  </label>
                  <label className="studio-field">
                    <span>Default currency</span>
                    <select value={wsForm.currency} disabled={!isClientAdmin || readOnly} onChange={(e) => setWsForm((f) => ({ ...f, currency: e.target.value }))}>
                      {CURRENCIES.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                    </select>
                  </label>
                </div>
                <div className="studio-field-row">
                  <label className="studio-field">
                    <span>Industry</span>
                    <select value={wsForm.industry} disabled={!isClientAdmin || readOnly} onChange={(e) => setWsForm((f) => ({ ...f, industry: e.target.value }))}>
                      {INDUSTRIES.map((i) => <option key={i.id} value={i.id}>{i.label}</option>)}
                    </select>
                  </label>
                </div>
                <button className="primary-btn" onClick={saveWorkspace} disabled={wsSaving || readOnly || !isClientAdmin}>
                  {wsSaving ? "Saving..." : "Save workspace"}
                </button>
                {!isClientAdmin && !readOnly && (
                  <div className="studio-hint">Only owners and admins can change workspace details.</div>
                )}
              </>
            ) : (
              <div className="studio-hint">{demoMode ? "Sign in to load your workspace." : "Select a client workspace to manage its settings."}</div>
            )}
          </section>

          <div className="settings-lower-grid">
            <section className="studio-card">
              <div className="studio-card-head">
                <span className="studio-icn alt"><i className="ti ti-chart-donut" /></span>
                <div><h3>Workspace usage</h3><p>What's actually built inside this client workspace</p></div>
              </div>
              {counts ? (
                <div className="ws-stat-grid">
                  <div className="ws-stat"><i className="ti ti-file-analytics" /><div><b>{counts.reports}</b><span>Reports</span></div></div>
                  <div className="ws-stat"><i className="ti ti-plug" /><div><b>{counts.connectors}</b><span>Connected sources</span></div></div>
                </div>
              ) : (
                <div className="studio-hint" style={{ marginBottom: 0 }}>Sign in to see live workspace counts.</div>
              )}
            </section>

            {isClientAdmin && selectedClient?.access !== "shared" && (
              <section className="studio-card danger-zone">
                <div className="studio-card-head">
                  <span className="studio-icn danger"><i className="ti ti-alert-triangle" /></span>
                  <div><h3>Danger zone</h3><p>Permanently deletes this client workspace and its dashboards/reports</p></div>
                </div>
                <div className="danger-row">
                  <div>
                    <div className="dz-title">Delete this client workspace</div>
                    <div className="dz-sub">This cannot be undone.</div>
                  </div>
                  <button type="button" className="danger-btn" onClick={openDeleteModal} disabled={readOnly}>
                    Delete client workspace
                  </button>
                </div>
              </section>
            )}
          </div>
        </div>
      )}

      {/* ---------------- Team ---------------- */}
      {tab === "team" && canViewTeamSection && (
        <div id="settings-panel-team" role="tabpanel" aria-labelledby="settings-tab-team">
          <section className="studio-card">
            <div className="studio-card-head">
              <span className="studio-icn alt"><i className="ti ti-users" /></span>
              <div>
                <h3>Team members</h3>
                <p>{teamLoading ? "Loading..." : `${members.length} ${members.length === 1 ? "person has" : "people have"} access to ${selectedClient?.name || "this client"}`}</p>
              </div>
              {isClientAdmin && (
                <button className="primary-btn" style={{ marginLeft: "auto" }} onClick={() => setAddMemberOpen(true)} disabled={readOnly}>
                  <i className="ti ti-user-plus" /> Add Team Member
                </button>
              )}
            </div>

            <Banner error={teamMsg.error} success={teamMsg.success} />

            {!teamLoading && members.length === 0 && (
              <div className="studio-hint">Sign in to load the members of this client.</div>
            )}

            {members.length > 0 && (
              <div className="member-list">
                {members.map((member) => {
                  const isMe = member.userId === profile?.id;
                  const isShared = member.access === "shared";
                  const canEdit = isClientAdmin && !isMe && isShared;
                  return (
                    <div key={member.userId} className="member-row">
                      <div className="member-avatar">
                        {member.avatarUrl
                          ? <img src={member.avatarUrl} alt="" />
                          : (member.name || "?")[0].toUpperCase()}
                      </div>
                      <div className="member-info">
                        <div className="member-name">
                          {member.name}{isMe && <span className="member-you">You</span>}
                        </div>
                        <div className="member-meta">{member.email}</div>
                      </div>
                      <span className={`ov-badge${isShared ? " ov-custom" : " ov-inherit"}`}>
                        {isShared ? "Shared" : "Owner"}
                      </span>
                      <div className="member-last">
                        <span>Last active</span>
                        <b>{formatRelative(member.lastSignInAt)}</b>
                      </div>
                      {canEdit ? (
                        <select
                          className="invite-role"
                          value={member.role}
                          onChange={(e) => changeRole(member, e.target.value)}
                        >
                          {["owner", "admin", "editor", "viewer"].map((r) => (
                            <option key={r} value={r}>{r[0].toUpperCase() + r.slice(1)}</option>
                          ))}
                        </select>
                      ) : (
                        <span className={`role-badge role-${member.role}`}>
                          {member.role[0].toUpperCase() + member.role.slice(1)}
                        </span>
                      )}
                      {canEdit && (
                        <button
                          className="icon-only-btn danger"
                          title={`Remove ${member.name}`}
                          aria-label={`Remove ${member.name}`}
                          onClick={() => kickMember(member)}
                        >
                          <i className="ti ti-user-minus" />
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        </div>
      )}

      {/* ---------------- Billing ---------------- */}
      {tab === "billing" && canViewBillingSection && (
        <div id="settings-panel-billing" role="tabpanel" aria-labelledby="settings-tab-billing">
          <section className="studio-card">
            <div className="studio-card-head">
              <span className="studio-icn"><i className="ti ti-credit-card" /></span>
              <div><h3>Billing</h3><p>Plans, invoices and usage limits</p></div>
            </div>
            <div className="empty-state">
              <i className="ti ti-credit-card" />
              <div className="empty-title">Billing isn't set up yet</div>
              <div className="empty-sub">
                This workspace has no payment provider connected, so there are no plans, invoices or
                usage limits to show. Once billing is wired up, your subscription, payment method and
                invoice history will appear here.
              </div>
            </div>
          </section>
        </div>
      )}

      {/* ---------------- Security ---------------- */}
      {tab === "security" && (
        <div id="settings-panel-security" role="tabpanel" aria-labelledby="settings-tab-security" className="studio-grid settings-2col">
          <div className="studio-col">
            <section className="studio-card">
              <div className="studio-card-head">
                <span className="studio-icn"><i className="ti ti-key" /></span>
                <div><h3>Password</h3><p>Use at least 8 characters with a mix of letters and numbers</p></div>
              </div>

              <Banner error={pwdMsg.error} success={pwdMsg.success} />

              <label className="studio-field">
                <span>Current password</span>
                <div className="password-field">
                  <input
                    type={showPwd.current ? "text" : "password"}
                    value={pwd.current}
                    placeholder="Enter your current password"
                    onChange={(e) => setPwd((p) => ({ ...p, current: e.target.value }))}
                  />
                  <button type="button" className="password-toggle" onClick={() => setShowPwd((p) => ({ ...p, current: !p.current }))} aria-label={showPwd.current ? "Hide password" : "Show password"} aria-pressed={showPwd.current}>
                    <i className={`ti ${showPwd.current ? "ti-eye-off" : "ti-eye"}`} aria-hidden="true" />
                  </button>
                </div>
              </label>
              <label className="studio-field">
                <span>New password</span>
                <div className="password-field">
                  <input
                    type={showPwd.next ? "text" : "password"}
                    value={pwd.next}
                    placeholder="Enter your new password"
                    onChange={(e) => setPwd((p) => ({ ...p, next: e.target.value }))}
                  />
                  <button type="button" className="password-toggle" onClick={() => setShowPwd((p) => ({ ...p, next: !p.next }))} aria-label={showPwd.next ? "Hide password" : "Show password"} aria-pressed={showPwd.next}>
                    <i className={`ti ${showPwd.next ? "ti-eye-off" : "ti-eye"}`} aria-hidden="true" />
                  </button>
                </div>
              </label>
              <label className="studio-field">
                <span>Confirm password</span>
                <div className="password-field">
                  <input
                    type={showPwd.confirm ? "text" : "password"}
                    value={pwd.confirm}
                    placeholder="Confirm your new password"
                    onChange={(e) => setPwd((p) => ({ ...p, confirm: e.target.value }))}
                  />
                  <button type="button" className="password-toggle" onClick={() => setShowPwd((p) => ({ ...p, confirm: !p.confirm }))} aria-label={showPwd.confirm ? "Hide password" : "Show password"} aria-pressed={showPwd.confirm}>
                    <i className={`ti ${showPwd.confirm ? "ti-eye-off" : "ti-eye"}`} aria-hidden="true" />
                  </button>
                </div>
              </label>
              <button
                className="primary-btn"
                onClick={changePassword}
                disabled={pwdSaving || demoMode || !pwd.current || !pwd.next}
              >
                {pwdSaving ? "Updating..." : "Update password"}
              </button>
            </section>
          </div>

          <div className="studio-col">
            <section className="studio-card">
              <div className="studio-card-head">
                <span className="studio-icn"><i className="ti ti-shield-lock" /></span>
                <div><h3>Two-factor authentication</h3><p>Add an authenticator app for an additional layer of account protection.</p></div>
                {!enrollment && (
                  <span className={`studio-tag${twoFactorOn ? " tag-done" : " tag-pending"}`}>{twoFactorOn ? "Enabled" : "Not enabled"}</span>
                )}
              </div>

              <Banner error={mfaMsg.error} success={mfaMsg.success} />

              {enrollment ? (
                <div className="mfa-enroll">
                  {enrollment.qrCode?.trim().startsWith("<") ? (
                    <div className="mfa-qr" dangerouslySetInnerHTML={{ __html: enrollment.qrCode }} />
                  ) : (
                    <img className="mfa-qr-img" src={enrollment.qrCode} alt="Scan this QR code with your authenticator app" />
                  )}
                  <div className="mfa-secret">
                    <span>Can't scan? Enter this key manually:</span>
                    <code>{enrollment.secret}</code>
                  </div>
                  <label className="studio-field">
                    <span>6-digit code from your app</span>
                    <input value={mfaCode} inputMode="numeric" maxLength={6} placeholder="123456"
                      onChange={(e) => setMfaCode(e.target.value)} />
                  </label>
                  <div className="mfa-actions">
                    <button className="primary-btn" onClick={finishMfa} disabled={mfaBusy || mfaCode.length < 6}>
                      {mfaBusy ? "Verifying..." : "Verify &amp; enable"}
                    </button>
                    <button className="ghost-btn" onClick={() => { setEnrollment(null); setMfaCode(""); }}>Cancel</button>
                  </div>
                </div>
              ) : (
                <div className="twofa-row">
                  <div className="pref-sub">
                    {twoFactorOn
                      ? "You'll be asked for a code from your authenticator app when signing in."
                      : "Add an authenticator app for a second layer of protection."}
                  </div>
                  {twoFactorOn ? (
                    <button className="ghost-btn" onClick={turnOffMfa} disabled={mfaBusy}>Turn off 2FA</button>
                  ) : (
                    <button className="primary-btn" onClick={beginMfa} disabled={mfaBusy || demoMode}>
                      {mfaBusy ? "Starting..." : "Enable 2FA"}
                    </button>
                  )}
                </div>
              )}
            </section>

            <section className="studio-card">
              <div className="studio-card-head">
                <span className="studio-icn alt"><i className="ti ti-devices" /></span>
                <div><h3>Sessions</h3><p>Sign out everywhere except this browser</p></div>
              </div>
              <div className="session-list">{recentSessions.length ? recentSessions.map((item) => <div className="session-row" key={item.id}>
                <i className={`ti ${isMobileSession(item.user_agent) ? "ti-device-mobile" : "ti-device-laptop"}`} />
                <div className="session-info"><b>{item.current ? "Current device" : "Other device"}</b><span>{sessionDeviceLabel(item.user_agent)} · {item.current ? "Active now" : formatRelative(item.last_seen_at)}</span></div>
                {item.current && <em>Current</em>}
              </div>) : <div className="studio-hint">Your current device will appear after this page refreshes once.</div>}</div>
              <div className="twofa-row">
                <div className="pref-sub">Sign out of every other device at once.</div>
                <button className="ghost-btn" onClick={revokeOtherSessions} disabled={sessionsBusy || demoMode}>
                  {sessionsBusy ? "Signing out..." : "Sign out others"}
                </button>
              </div>
            </section>
          </div>
        </div>
      )}

      {/* ---------------- Notifications ---------------- */}
      {tab === "notifications" && (
        <div id="settings-panel-notifications" role="tabpanel" aria-labelledby="settings-tab-notifications">
          <section className="studio-card">
            <div className="studio-card-head">
              <span className="studio-icn alt"><i className="ti ti-category" /></span>
              <div><h3>Notifications</h3><p>{selectedClient?.name ? `In-app and email alerts for ${selectedClient.name}` : "Choose what you hear about, by category"}</p></div>
              <button className="primary-btn" style={{ marginLeft: "auto" }} onClick={savePrefs} disabled={prefsSaving || readOnly || !canEditNotificationPrefs}>
                {prefsSaving ? "Saving..." : "Save preferences"}
              </button>
            </div>

            <Banner error={prefsMsg.error} success={prefsMsg.success} />
            {!canEditNotificationPrefs && (
              <div className="inline-error">
                <i className="ti ti-lock" /> You don't have permission to update notification preferences for this client.
              </div>
            )}

            <div className="notif-table">
              <div className="notif-category-head"><span /><span>In-app</span><span>Email</span></div>
              {NOTIFICATION_CATEGORIES.map((category) => {
                const current = prefs.categories?.[category.id] || { inApp: true, email: false };
                return (
                  <div
                    key={category.id}
                    className="notif-category-row"
                    onClick={() => {
                      if (!canEditNotificationPrefs) {
                        setPrefsMsg({ error: "You don't have permission to update notification preferences for this client.", success: "" });
                      }
                    }}
                  >
                    <div className="notif-category-main">
                      <span className="notif-category-icn"><i className={`ti ${category.icon}`} /></span>
                      <div>
                        <div className="notif-category-title">{category.label}</div>
                        <div className="notif-category-desc">{category.desc}</div>
                      </div>
                    </div>
                    {category.lockedInApp ? (
                      <div className="notif-locked-cell">
                        <button type="button" className="bd-switch small on" disabled aria-checked="true" role="switch" aria-label={`In-app alerts for ${category.label} (always on)`}><span /></button>
                        <div className="notif-locked-tag">Always on</div>
                      </div>
                    ) : (
                      <button
                        type="button" role="switch" aria-checked={current.inApp}
                        aria-label={`In-app alerts for ${category.label}`}
                        className={`bd-switch small${current.inApp ? " on" : ""}`}
                        disabled={!canEditNotificationPrefs}
                        onClick={() => toggleCategoryInApp(category.id)}
                      ><span /></button>
                    )}
                    {category.emailAvailable ? (
                      <button
                        type="button" role="switch" aria-checked={current.email}
                        aria-label={`Email alerts for ${category.label}`}
                        className={`bd-switch small${current.email ? " on" : ""}`}
                        disabled={!canEditNotificationPrefs}
                        onClick={() => toggleCategoryEmail(category.id)}
                      ><span /></button>
                    ) : (
                      <span className="notif-email-soon">Soon</span>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        </div>
      )}

      {deleteModalOpen && (
        <div className="modal-bg" role="dialog" aria-modal="true" aria-labelledby="delete-ws-title">
          <div className="modal" ref={deleteModalRef}>
            <div className="modal-h">
              <span className="ico" style={{ background: "var(--bad-soft)", color: "var(--bad)" }}><i className="ti ti-trash" /></span>
              <div><div className="t" id="delete-ws-title">Delete client workspace?</div><div className="s">This action cannot be undone.</div></div>
            </div>
            <div className="modal-b">
              <p className="unsaved-copy" style={{ marginBottom: 16 }}>
                This will permanently remove <b>{selectedClient?.name}</b> and every dashboard, report and connection inside it.
              </p>
              <div className="field">
                <label htmlFor="delete-ws-confirm">Type <b>{selectedClient?.name}</b> to confirm</label>
                <input
                  id="delete-ws-confirm"
                  value={deleteConfirm}
                  placeholder={selectedClient?.name}
                  disabled={deleting}
                  onChange={(e) => setDeleteConfirm(e.target.value)}
                />
              </div>
              {deleteError && <div className="modal-error"><i className="ti ti-alert-triangle" /> {deleteError}</div>}
            </div>
            <div className="modal-foot">
              <button className="btn-ghost" onClick={closeDeleteModal} disabled={deleting}>Cancel</button>
              <button
                className="btn-solid danger"
                onClick={confirmDeleteWorkspace}
                disabled={deleteConfirm !== selectedClient?.name || deleting}
              >
                {deleting ? "Deleting..." : "Delete client workspace"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
