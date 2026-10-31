// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { acceptInvitation, lookupInvitation } from "../lib/settings";
import Login from "./Login";

// Handles /accept-invitation?token=... for BOTH auth states at once (see
// App.jsx: this route takes priority over the generic auth-gating branch,
// so this one component stays mounted across the login/signup transition
// instead of needing to stash the token somewhere and pick it back up
// after a redirect).
export default function AcceptInvitation({ isAuthed, agencyName, brandLogo, ini, onAccepted }) {
  const location = useLocation();
  const token = new URLSearchParams(location.search).get("token") || "";

  const [lookup, setLookup] = useState(null);
  const [lookupError, setLookupError] = useState("");
  const [accepting, setAccepting] = useState(false);
  const [acceptError, setAcceptError] = useState("");
  const acceptedRef = useRef(false);

  useEffect(() => {
    let alive = true;
    if (!token) { setLookupError("This invitation link is missing its token."); return undefined; }
    (async () => {
      const result = await lookupInvitation(token);
      if (!alive) return;
      if (!result.ok) { setLookupError(result.error || "This invitation could not be found."); return; }
      setLookup(result);
    })();
    return () => { alive = false; };
  }, [token]);

  // Once the lookup says this invite is pending AND the user is
  // authenticated (whether they already had a session, just logged in, or
  // just signed up), accept it exactly once and hand off.
  useEffect(() => {
    if (!isAuthed || !lookup || lookup.status !== "pending" || acceptedRef.current) return;
    acceptedRef.current = true;
    (async () => {
      setAccepting(true);
      const result = await acceptInvitation(token);
      setAccepting(false);
      if (!result.ok) { acceptedRef.current = false; setAcceptError(result.error || "Could not accept this invitation."); return; }
      onAccepted?.(result.client_id);
    })();
  }, [isAuthed, lookup, token, onAccepted]);

  const shell = (children) => (
    <main className="reset-shell">
      <div className="reset-shell-inner">
        <section className="login-card">
          <div className="lm">{brandLogo ? <img className="login-brand-image" src={brandLogo} alt="" /> : (ini || "B")}</div>
          {children}
        </section>
      </div>
    </main>
  );

  if (lookupError) {
    return shell(
      <>
        <h2>Invitation not found</h2>
        <p className="p">{lookupError}</p>
      </>,
    );
  }

  if (!lookup) {
    return shell(
      <>
        <h2>Checking your invitation…</h2>
        <p className="p">Hang on a moment.</p>
      </>,
    );
  }

  if (lookup.status === "expired") {
    return shell(
      <>
        <h2>This invitation has expired</h2>
        <p className="p">Ask {lookup.agency_name} to send you a new invitation to {lookup.client_name}.</p>
      </>,
    );
  }
  if (lookup.status === "cancelled") {
    return shell(
      <>
        <h2>This invitation was cancelled</h2>
        <p className="p">Ask {lookup.agency_name} to send you a new invitation if you still need access.</p>
      </>,
    );
  }
  if (lookup.status === "accepted" && !isAuthed) {
    return shell(
      <>
        <h2>Already accepted</h2>
        <p className="p">This invitation has already been accepted. Sign in to access {lookup.client_name}.</p>
      </>,
    );
  }

  if (isAuthed) {
    return shell(
      <>
        <h2>{accepting ? "Setting up your access…" : acceptError ? "Something went wrong" : "You're in"}</h2>
        <p className="p">
          {acceptError || `Connecting your account to ${lookup.client_name}.`}
        </p>
      </>,
    );
  }

  // Not authenticated yet: an existing account must log in (never a signup
  // form for an email that already has one); a brand-new email gets the
  // normal signup flow with the email prefilled and locked so it can't be
  // changed out from under the invitation. Both render through Login's own
  // full split-screen shell (marketing panel + card), same as the regular
  // /login page, with the invite context surfaced as a banner above the
  // card instead of a bespoke standalone layout.
  const inviteBanner = (
    <div className="brand-context-banner">
      <i className="ti ti-info-circle" />
      <span><b>{lookup.agency_name}</b> has invited you to access <b>{lookup.client_name}</b> as {lookup.role}.</span>
    </div>
  );

  if (lookup.account_exists) {
    return (
      <Login
        ini={ini}
        agencyName={agencyName}
        brandLogo={brandLogo}
        initialMode="signin"
        lockedEmail={lookup.email}
        onAuthed={() => {}}
        topBanner={inviteBanner}
      />
    );
  }

  return (
    <Login
      ini={ini}
      agencyName={agencyName}
      brandLogo={brandLogo}
      initialMode="signup"
      lockedEmail={lookup.email}
      onAuthed={() => {}}
      topBanner={inviteBanner}
    />
  );
}
