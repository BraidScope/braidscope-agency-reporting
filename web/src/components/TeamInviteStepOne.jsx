// SPDX-License-Identifier: AGPL-3.0-only
// Content only - Cancel/Next live in TeamInviteFlow's shared action row so
// every step's buttons stay identically sized and positioned instead of
// each step rendering its own. No icon here (removed per
// TEAM_ONBOARDING_REDESIGN.md Round 3 - one less element competing for the
// step's already-tight vertical space).
export default function TeamInviteStepOne({ email, setEmail, error, checking }) {
  return (
    <div className="team-invite-panel team-invite-panel-narrow">
      <h2 className="team-invite-title">Give client access to your teammate</h2>
      <p className="team-invite-sub">Invite someone to collaborate on this client workspace with controlled permissions.</p>

      <div className={`team-invite-input-field${error ? " has-error" : ""}`}>
        <label htmlFor="team-invite-email">Email address</label>
        <div className="team-invite-input-wrap">
          <i className="ti ti-mail" aria-hidden="true" />
          <input
            id="team-invite-email"
            type="email"
            autoFocus
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="teammate@company.com"
          />
          {checking && <i className="ti ti-loader-2 team-invite-input-spinner" aria-hidden="true" />}
        </div>
        {error && <div className="modal-error">{error}</div>}
      </div>
    </div>
  );
}

export function isValidInviteEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test((email || "").trim());
}
