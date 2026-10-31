// SPDX-License-Identifier: AGPL-3.0-only
import { CLIENT_ROLE_INFO, PERMISSION_MODULES } from "../lib/settings";

const ACTION_COLUMNS = [
  { id: "view", label: "View" },
  { id: "edit", label: "Edit" },
  { id: "manage", label: "Manage" },
  { id: "delete", label: "Delete" },
];

// Content only - Back/Send invitation live in TeamInviteFlow's shared
// .team-onboarding-footer.
export default function TeamInviteStepTwo({ email, clientName, role, setRole, error }) {
  const activeRole = CLIENT_ROLE_INFO.find((r) => r.id === role) || CLIENT_ROLE_INFO[0];

  return (
    <div className="team-invite-panel team-invite-panel-wide">
      <h2 className="team-invite-title">Role &amp; permissions</h2>
      <p className="team-invite-sub">Choose what {email} can see and do on {clientName}.</p>

      <div className="team-invite-matrix-layout">
        <div className="team-invite-role-list" role="radiogroup" aria-label="Select a role">
          {CLIENT_ROLE_INFO.map((r) => (
            <button
              key={r.id}
              type="button"
              role="radio"
              aria-checked={role === r.id}
              className={`team-invite-role-row${role === r.id ? " active" : ""}`}
              onClick={() => setRole(r.id)}
            >
              <span className={`role-badge role-${r.id}`}>{r.name}</span>
              <span className="team-invite-role-row-desc">{r.desc}</span>
              {role === r.id && <i className="ti ti-chevron-right team-invite-role-row-chevron" aria-hidden="true" />}
            </button>
          ))}
        </div>

        <div className="team-invite-matrix-panel" key={activeRole.id}>
          <div className="team-invite-matrix-head">
            <span className={`role-badge role-${activeRole.id}`}>{activeRole.name}</span>
            <span className="team-invite-matrix-head-desc">{activeRole.desc}</span>
          </div>
          <div className="team-invite-matrix-table" role="table" aria-label={`${activeRole.name} permissions`}>
            <div className="team-invite-matrix-row team-invite-matrix-row-head" role="row">
              <span role="columnheader">Module</span>
              {ACTION_COLUMNS.map((col) => <span key={col.id} role="columnheader">{col.label}</span>)}
            </div>
            {PERMISSION_MODULES.map((moduleName) => {
              const access = activeRole.matrix[moduleName];
              return (
                <div className="team-invite-matrix-row" role="row" key={moduleName}>
                  <span className="team-invite-matrix-module" role="cell">{moduleName}</span>
                  {ACTION_COLUMNS.map((col) => (
                    <span key={col.id} role="cell" className={`team-invite-matrix-cell${access[col.id] ? " on" : ""}`}>
                      <i className={`ti ${access[col.id] ? "ti-check" : "ti-circle"}`} aria-hidden="true" />
                    </span>
                  ))}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {error && <div className="modal-error">{error}</div>}
    </div>
  );
}
