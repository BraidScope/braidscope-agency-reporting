// SPDX-License-Identifier: AGPL-3.0-only
// Content only - Invite another/Done live in TeamInviteFlow's shared
// .team-onboarding-footer.
export default function TeamInviteStepThree({ result, clientName, role }) {
  const roleLabel = role ? role[0].toUpperCase() + role.slice(1) : "";

  return (
    <div className="team-invite-panel team-invite-panel-narrow">
      <div className="team-invite-success-icon" aria-hidden="true">
        <svg viewBox="0 0 64 64" width="64" height="64">
          <circle className="team-invite-success-ring" cx="32" cy="32" r="28" fill="none" strokeWidth="4" />
          <path className="team-invite-success-check" d="M20 33 L28 41 L45 23" fill="none" strokeWidth="4.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
      <h2 className="team-invite-title">Invitation sent successfully</h2>
      <p className="team-invite-sub">They&rsquo;ll receive an email with instructions to join this workspace.</p>

      <div className="invite-success-details">
        <div><span>Email</span><b>{result?.email}</b></div>
        <div><span>Client</span><b>{result?.client_name || clientName}</b></div>
        <div><span>Role</span><b>{roleLabel}</b></div>
      </div>
    </div>
  );
}
