// SPDX-License-Identifier: AGPL-3.0-only
import { supabase, isSupabaseConfigured } from "./supabase";

export async function signIn(email, password) {
  if (!isSupabaseConfigured()) return { ok: false, error: "Auth not configured" };
  const { data, error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) return { ok: false, error: error.message };
  return { ok: true, session: data.session };
}

export async function signUp(email, password, agencyName) {
  if (!isSupabaseConfigured()) return { ok: false, error: "Auth not configured" };
  const { data, error } = await supabase.auth.signUp({
    email,
    password,
    options: {
      emailRedirectTo: getAuthRedirectUrl(),
      data: { agency_name: agencyName },
    },
  });
  if (error) return { ok: false, error: error.message };
  if (data?.user && Array.isArray(data.user.identities) && data.user.identities.length === 0) {
    return { ok: false, error: "An account with this email already exists. Please sign in or reset your password." };
  }
  return { ok: true, session: data.session, user: data.user };
}

export async function requestPasswordReset(email) {
  if (!isSupabaseConfigured()) return { ok: false, error: "Auth not configured" };
  const { error } = await supabase.auth.resetPasswordForEmail(email, { redirectTo: `${window.location.origin}/reset-password` });
  return error ? { ok: false, error: error.message } : { ok: true };
}

export async function setRecoveryPassword(password) {
  if (!isSupabaseConfigured()) return { ok: false, error: "Auth not configured" };
  const { error } = await supabase.auth.updateUser({ password });
  return error ? { ok: false, error: error.message } : { ok: true };
}

// A recovery link lands here one of two ways: Supabase already exchanged it
// for a session before this page finished loading (the normal case), or the
// link was invalid/expired/already used, in which case Supabase redirects
// back with an error on the URL instead of a session. Check both so the UI
// can tell a bad link apart from a good one before showing the reset form.
export async function checkRecoveryLink() {
  const query = new URLSearchParams(window.location.search);
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const linkError = query.get("error_description") || hash.get("error_description") || query.get("error") || hash.get("error");
  if (linkError) return { ok: false, error: decodeURIComponent(linkError.replace(/\+/g, " ")) };
  if (!isSupabaseConfigured()) return { ok: false, error: "Auth not configured" };
  const { data, error } = await supabase.auth.getSession();
  if (error || !data.session) return { ok: false, error: "This reset link is invalid or has expired." };
  return { ok: true };
}

function getAuthRedirectUrl() {
  if (typeof window === "undefined") return undefined;
  return window.location.origin;
}

export async function signOut() {
  if (!isSupabaseConfigured()) return;
  await supabase.auth.signOut();
}

// If the stored access token is close to expiry, supabase-js's own
// getSession() silently makes a real network call to Supabase's auth
// server to refresh it first - a call with no timeout of its own, made
// before any of the 20+ call sites across this app even build their own
// request. If that refresh call is slow or the network briefly drops (seen
// directly on this machine), every one of those callers hangs completely
// silently for however long it takes - no error, no log, nothing - since
// their own fetch/AbortController never even gets a chance to start.
// Racing it against a bounded timeout here fixes that everywhere at once:
// every caller already treats a null/tokenless session as "not signed in"
// and shows its own sensible message, so resolving to null on timeout
// (not throwing) degrades exactly the same way a real signed-out session
// already does, instead of introducing a new uncaught-rejection path.
const SESSION_TIMEOUT_MS = 15000;

export async function getSession() {
  if (!isSupabaseConfigured()) return null;
  try {
    const { data } = await Promise.race([
      supabase.auth.getSession(),
      new Promise((_, reject) => window.setTimeout(() => reject(new Error("session timeout")), SESSION_TIMEOUT_MS)),
    ]);
    return data.session;
  } catch {
    return null;
  }
}

// The cached session is only a token on disk. supabase-js hands it back
// without asking the server, so a token signed by a stack that no longer
// exists (or a revoked one) still looks signed in until the first data call
// fails. Ask the auth server once at boot; on a definite rejection, clear
// the cache so the app shows the login screen. Network trouble is not a
// rejection: keep the session and let the callers' own errors show.
export async function verifySession(session) {
  if (!session || !isSupabaseConfigured()) return session;
  try {
    const { error } = await Promise.race([
      supabase.auth.getUser(session.access_token),
      new Promise((_, reject) => window.setTimeout(() => reject(new Error("verify timeout")), SESSION_TIMEOUT_MS)),
    ]);
    if (error && isAuthRejection(error)) {
      await supabase.auth.signOut({ scope: "local" });
      return null;
    }
    return session;
  } catch {
    return session;
  }
}

// 401/403 from the auth server, or PostgREST's "JWT invalid/expired" code.
export function isAuthRejection(error) {
  if (!error) return false;
  if (error.status === 401 || error.status === 403) return true;
  if (error.code === "PGRST301" || error.code === "PGRST302") return true;
  const msg = String(error.message || "").toLowerCase();
  return msg.includes("invalid jwt") || msg.includes("jwt expired") || msg.includes("invalid claim") || msg.includes("session_not_found");
}

export async function signOutLocal() {
  if (!isSupabaseConfigured()) return;
  await supabase.auth.signOut({ scope: "local" });
}

export function onAuthChange(callback) {
  if (!isSupabaseConfigured()) return () => {};
  const { data } = supabase.auth.onAuthStateChange((_event, session) => callback(session));
  return () => data.subscription.unsubscribe();
}

// Fetch the signed-in user's primary organization (first membership)
export async function getMyOrganization() {
  if (!isSupabaseConfigured()) return null;
  const { data, error } = await supabase
    .from("memberships")
    .select("role, organization:organizations(id, name, slug, accent_color, logo_url, branding_config)")
    .limit(1)
    .maybeSingle();
  if (error) return { __error: error.message, __unauthorized: isAuthRejection(error) };
  if (!data?.organization) return null;
  return { role: data.role, ...data.organization };
}

// Recovery for an authenticated user with zero memberships — most likely
// because their only workspace was just deleted via Settings (that cascades
// the membership away but deliberately leaves the auth user intact so they
// can sign back in). Provisions a fresh organization exactly like signup
// does, so the account lands back in the normal "zero clients" onboarding
// state instead of staying orgless. Safe to call anytime: a user who already
// belongs to an organization just gets that same membership back.
export async function ensureOwnOrganization() {
  if (!isSupabaseConfigured()) return null;
  const { data, error } = await supabase.rpc("ensure_own_organization");
  if (error) return { __error: error.message };
  const row = Array.isArray(data) ? data[0] : data;
  if (!row) return null;
  const { role, ...organization } = row;
  return { role, ...organization };
}

export async function updateOrganizationBranding(organizationId, { name, accentColor, brandingConfig }) {
  if (!isSupabaseConfigured()) return { ok: false, error: "Auth not configured" };
  if (!organizationId) return { ok: false, error: "Organization not loaded yet" };

  const { data, error } = await supabase
    .from("organizations")
    .update({
      name: name.trim(),
      accent_color: accentColor,
      branding_config: brandingConfig || {},
    })
    .eq("id", organizationId)
    .select("id, name, slug, accent_color, logo_url, branding_config")
    .single();

  if (error) return { ok: false, error: error.message };
  return { ok: true, organization: data };
}

export async function getMfaRequirement() {
  if (!isSupabaseConfigured()) return { ok: true, required: false };
  const { data, error } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
  if (error) return { ok: false, error: error.message, required: false };
  return { ok: true, required: data?.nextLevel === "aal2" && data?.currentLevel !== "aal2" };
}

export async function getMfaFactorsForLogin() {
  if (!isSupabaseConfigured()) return { ok: false, error: "Auth not configured" };
  const { data, error } = await supabase.auth.mfa.listFactors();
  if (error) return { ok: false, error: error.message };
  return { ok: true, factors: (data?.totp || []).filter((factor) => factor.status === "verified") };
}

export async function verifyMfaLogin(factorId, code) {
  if (!isSupabaseConfigured()) return { ok: false, error: "Auth not configured" };
  const { data: challenge, error: challengeError } = await supabase.auth.mfa.challenge({ factorId });
  if (challengeError) return { ok: false, error: challengeError.message };
  const { error } = await supabase.auth.mfa.verify({ factorId, challengeId: challenge.id, code });
  return error ? { ok: false, error: error.message } : { ok: true };
}
