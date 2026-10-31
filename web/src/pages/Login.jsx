// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useState } from "react";
import { checkRecoveryLink, getMfaFactorsForLogin, requestPasswordReset, setRecoveryPassword, signIn, signOut, signUp, verifyMfaLogin } from "../lib/auth";

// Rotating marketing headlines for the left panel. Kept local to the auth
// experience on purpose — this is a small, fixed set of strings, not a
// candidate for a global content system.
const AUTH_HEADLINES = [
  "Marketing intelligence, without the manual reporting.",
  "Turn scattered marketing data into clear decisions.",
  "Bring every client's marketing data into one view.",
  "See what changed and know what to do next.",
  "Give every client a clearer view of performance.",
  "Understand performance without manual data work.",
  "Automate reporting and focus on better decisions.",
  "All your marketing data, finally working together.",
];

const AUTH_BENEFITS = [
  "Unified multi-channel data",
  "AI-powered performance answers",
  "Automated client reporting",
];

const HEADLINE_INTERVAL_MS = 10000;
const HEADLINE_FADE_MS = 400;

// Rotates through `messages` on an interval, exposing a `visible` flag so the
// caller can crossfade: text swaps while opacity is at 0, so there's never a
// visible jump. Timers are cleared on unmount.
function useRotatingMessage(messages, intervalMs, fadeMs) {
  const [index, setIndex] = useState(0);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    if (messages.length < 2) return undefined;
    let fadeTimer;
    const rotateTimer = setInterval(() => {
      setVisible(false);
      fadeTimer = setTimeout(() => {
        setIndex((current) => (current + 1) % messages.length);
        setVisible(true);
      }, fadeMs);
    }, intervalMs);
    return () => {
      clearInterval(rotateTimer);
      clearTimeout(fadeTimer);
    };
  }, [messages, intervalMs, fadeMs]);

  return [messages[index], visible];
}

export default function Login({ onAuthed, onDemo, onResetComplete, agencyName, brandLogo, initialMode = "signin", mfaRequired = false, onMfaVerified, lockedEmail = "", embedded = false, topBanner = null }) {
  const [mode, setMode] = useState(initialMode);
  const [email, setEmail] = useState(lockedEmail || "");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [agency, setAgency] = useState("");
  const [code, setCode] = useState("");
  const [factors, setFactors] = useState([]);
  const [factorId, setFactorId] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  // Tracks whether `message` is an informational notice rather than an
  // error — needed because the reset-success message must stay a notice
  // even after `mode` flips back to "signin" (mode alone can't tell the two
  // apart in that case, since a plain sign-in error also shows in "signin").
  const [notice, setNotice] = useState(false);
  // Recovery links either arrive with a session Supabase already exchanged,
  // or with an error because the link was invalid/expired/reused — "checking"
  // avoids flashing the reset form before we know which. Only relevant when
  // initialMode is "reset" (i.e. isRecoveryRoute in App.jsx).
  const [resetLinkStatus, setResetLinkStatus] = useState("checking");
  const [headline, headlineVisible] = useRotatingMessage(AUTH_HEADLINES, HEADLINE_INTERVAL_MS, HEADLINE_FADE_MS);

  useEffect(() => { setMode(initialMode); }, [initialMode]);
  useEffect(() => {
    if (initialMode !== "reset") return;
    let cancelled = false;
    checkRecoveryLink().then((result) => {
      if (!cancelled) setResetLinkStatus(result.ok ? "valid" : "invalid");
    });
    return () => { cancelled = true; };
  }, [initialMode]);
  useEffect(() => {
    if (!mfaRequired) return;
    getMfaFactorsForLogin().then((result) => {
      if (!result.ok) return setMessage(result.error || "Could not load your authenticator.");
      setFactors(result.factors || []);
      setFactorId(result.factors?.[0]?.id || "");
    });
  }, [mfaRequired]);

  const goTo = (nextMode) => { setMessage(""); setNotice(false); setMode(nextMode); };

  const submit = async (event) => {
    event.preventDefault();
    setMessage("");
    setNotice(false);
    if (mode === "reset" && password !== confirmPassword) {
      return setMessage("Passwords do not match.");
    }
    setBusy(true);
    let result;
    if (mode === "forgot") result = await requestPasswordReset(email);
    else if (mode === "reset") result = await setRecoveryPassword(password);
    else if (mode === "signup") {
      result = await signUp(email, password, agency);
      if (result.ok && !result.session) {
        const directLogin = await signIn(email, password);
        if (directLogin.ok && directLogin.session) result = directLogin;
        else {
          setBusy(false);
          return setMessage("Account created. Check your email to confirm your account, then sign in.");
        }
      }
    } else result = await signIn(email, password);
    if (!result.ok) { setBusy(false); return setMessage(result.error || "Something went wrong."); }
    if (mode === "forgot") { setBusy(false); return setMessage("Check your email for the secure password reset link."); }
    if (mode === "reset") {
      // The recovery link authenticates this browser just long enough to
      // set a new password — that session must not carry over into a real
      // login. Sign out so the user has to come back and sign in fresh with
      // the password they just chose, then leave /reset-password entirely
      // (not just switch local UI state) so a refresh can't land back here.
      await signOut();
      setBusy(false);
      setPassword("");
      setConfirmPassword("");
      setNotice(true);
      setMessage("Password updated successfully. Sign in with your new password.");
      if (onResetComplete) onResetComplete();
      else setMode("signin");
      return;
    }
    setBusy(false);
    onAuthed?.();
  };

  const verify = async (event) => {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    const result = await verifyMfaLogin(factorId, code);
    setBusy(false);
    if (!result.ok) return setMessage(result.error || "Invalid code.");
    onMfaVerified?.();
  };

  const renderShell = (content, { showDemo = false } = {}) => (
    <main className="login-shell">
      <div className="login-grid">
        {showDemo && (
          <button type="button" className="login-demo-btn" onClick={onDemo}>
            View Demo
            <i className="ti ti-arrow-right" aria-hidden="true" />
          </button>
        )}
        <aside className="login-marketing">
          <div className="lm-brand"><div className="brand-mark big">B</div><span className="brand-name big">Braidscope</span></div>
          <div className="lm-copy">
            <h1 className="lm-hero" data-fading={headlineVisible ? "false" : "true"}>{headline}</h1>
            <p className="lm-lead">Bring every client channel into one calm, clear workspace.</p>
          </div>
          <ul className="lm-benefits">
            {AUTH_BENEFITS.map((benefit) => (
              <li key={benefit}>
                <span className="lm-benefit-marker" aria-hidden="true"><span className="lm-benefit-dot" /></span>
                {benefit}
              </li>
            ))}
          </ul>
          <div className="lm-foot">Secure workspace analytics for modern agencies.</div>
        </aside>
        <section className="login-card-wrap">
          {topBanner && <div className="login-topbanner-wrap">{topBanner}</div>}
          {content}
        </section>
      </div>
    </main>
  );

  // The recovery link (/reset-password) is a focused, standalone interaction,
  // not the full split-screen product shell — no marketing panel, no View
  // Demo, no sign-in/sign-up switch. Just the small card, centered.
  const renderMinimalShell = (content) => (
    <main className="reset-shell">
      <div className="reset-shell-inner">{content}</div>
    </main>
  );
  const isRecoveryContext = initialMode === "reset";
  // Inside the recovery route, "back to sign in" must actually leave
  // /reset-password via a real navigation (not just flip local state),
  // otherwise a refresh lands back in the recovery UI instead of a normal
  // login screen.
  const backToSignIn = () => {
    if (isRecoveryContext && onResetComplete) onResetComplete();
    else goTo("signin");
  };

  if (mfaRequired) return renderShell(
    <section className="login-card">
      <div className="lm">{brandLogo ? <img className="login-brand-image" src={brandLogo} alt="" /> : "B"}</div>
      <h2>Verify your identity</h2>
      <p className="p">Enter the code from your authenticator app to continue.</p>
      <form onSubmit={verify}>
        <div className="field"><label htmlFor="mfa-code">Authenticator code</label><input id="mfa-code" value={code} onChange={(e) => setCode(e.target.value.replace(/\s/g, ""))} inputMode="numeric" autoFocus placeholder="6-digit code" /></div>
        {factors.length > 1 && <div className="field"><label htmlFor="mfa-factor">Authenticator</label><select id="mfa-factor" value={factorId} onChange={(e) => setFactorId(e.target.value)}>{factors.map((factor) => <option key={factor.id} value={factor.id}>{factor.friendly_name || "Authenticator"}</option>)}</select></div>}
        <button className="login-btn" disabled={busy || !factorId || code.length < 6}>{busy ? "Verifying..." : "Verify and continue"}</button>
        {message && <div className="login-error">{message}</div>}
      </form>
    </section>,
  );

  // A recovery link that hasn't resolved yet (or turned out invalid/expired
  // /already used) gets its own focused state instead of the reset form —
  // never the form itself, since there'd be no valid session to submit it
  // against.
  if (mode === "reset" && resetLinkStatus !== "valid") {
    return renderMinimalShell(
      <section className="login-card">
        <div className="lm">{brandLogo ? <img className="login-brand-image" src={brandLogo} alt="" /> : "B"}</div>
        <h2>{resetLinkStatus === "checking" ? "Checking your link…" : "Link expired"}</h2>
        <p className="p">
          {resetLinkStatus === "checking"
            ? "Hang on while we verify your password reset link."
            : "This password reset link is invalid or has expired. Request a new one to continue."}
        </p>
        {resetLinkStatus === "invalid" && (
          <>
            <button type="button" className="login-btn" onClick={() => goTo("forgot")}>Send a new reset link</button>
            <div className="login-actions">
              <button type="button" className="login-back-link" onClick={backToSignIn}><i className="ti ti-arrow-left" aria-hidden="true" /> Back to sign in</button>
            </div>
          </>
        )}
      </section>,
    );
  }

  const title = mode === "signup" ? "Create your workspace" : mode === "forgot" ? "Reset your password" : mode === "reset" ? "Choose a new password" : "Welcome back";
  const subtitle = mode === "forgot"
    ? "Enter the email associated with your account and we'll send you instructions to reset your password."
    : mode === "reset"
    ? "Use a strong new password for your account."
    : mode === "signup"
    ? "Set up your agency workspace in a minute."
    : "Sign in to your agency workspace.";

  const cardContent = (
    <section className="login-card" key={mode}>
      <div className="lm">{brandLogo ? <img className="login-brand-image" src={brandLogo} alt="" /> : "B"}</div>
      {!isRecoveryContext && (mode === "signin" || mode === "signup") && (
        <div className="auth-switch" role="group" aria-label="Choose sign in or create account">
          <button type="button" aria-pressed={mode === "signin"} className={`auth-switch-btn${mode === "signin" ? " active" : ""}`} onClick={() => goTo("signin")}>Sign in</button>
          <button type="button" aria-pressed={mode === "signup"} className={`auth-switch-btn${mode === "signup" ? " active" : ""}`} onClick={() => goTo("signup")}>Create account</button>
        </div>
      )}
      <h2>{title}</h2>
      <p className="p">{subtitle}</p>
      <form onSubmit={submit}>
        {mode === "signup" && <div className="field"><label htmlFor="agency">Agency name</label><input id="agency" value={agency} onChange={(e) => setAgency(e.target.value)} placeholder="e.g. Acme Marketing" required /></div>}
        {mode !== "reset" && <div className="field"><label htmlFor="email">Work email</label><input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" required readOnly={Boolean(lockedEmail)} disabled={Boolean(lockedEmail)} /></div>}
        {mode !== "forgot" && <div className="field"><label htmlFor="password">{mode === "reset" ? "New password" : "Password"}</label><div className="password-field"><input id="password" type={showPassword ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} placeholder={mode === "signup" || mode === "reset" ? "Create a password (8+ characters)" : "Enter your password"} minLength="8" required /><button type="button" className="password-toggle" onClick={() => setShowPassword((visible) => !visible)} aria-label={showPassword ? "Hide password" : "Show password"} aria-pressed={showPassword}><i className={`ti ${showPassword ? "ti-eye-off" : "ti-eye"}`} aria-hidden="true" /></button></div></div>}
        {mode === "reset" && (
          <div className="field">
            <label htmlFor="confirm-password">Confirm password</label>
            <div className="password-field">
              <input id="confirm-password" type={showPassword ? "text" : "password"} value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} placeholder="Re-enter your new password" minLength="8" required />
            </div>
            {confirmPassword.length > 0 && confirmPassword !== password && <div className="field-hint field-hint-warn">Passwords do not match.</div>}
          </div>
        )}
        <button className="login-btn" disabled={busy || (mode === "reset" && (password.length < 8 || password !== confirmPassword))}>{busy ? "Please wait..." : mode === "signup" ? "Create account" : mode === "forgot" ? "Send reset link" : mode === "reset" ? "Update password" : "Sign in"}</button>
        {message && <div className={notice || mode === "forgot" || mode === "reset" ? "login-notice" : "login-error"}>{message}</div>}
        {mode === "signin" && !isRecoveryContext && <button type="button" className="login-forgot-link" onClick={() => goTo("forgot")}>Forgot password?</button>}
      </form>
      {(mode === "forgot" || mode === "reset") && (
        <div className="login-actions">
          <button type="button" className="login-back-link" onClick={backToSignIn}><i className="ti ti-arrow-left" aria-hidden="true" /> Back to sign in</button>
        </div>
      )}
    </section>
  );

  // Embedded mode (used by AcceptInvitation) renders just the form itself -
  // the caller already provides its own page shell/context banner, so
  // wrapping it in another shell here would nest two full-page layouts.
  if (embedded) return cardContent;

  // The whole /reset-password experience (checking the link, an expired-link
  // notice, requesting a fresh one, and the reset form itself) stays inside
  // the minimal standalone shell — never the full product shell, since the
  // user was never meant to see the marketing panel or the app itself here.
  if (isRecoveryContext) return renderMinimalShell(cardContent);

  return renderShell(cardContent, { showDemo: mode === "signin" || mode === "signup" });
}
