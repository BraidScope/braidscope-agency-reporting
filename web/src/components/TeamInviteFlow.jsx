// SPDX-License-Identifier: AGPL-3.0-only
import { useState } from "react";
import { inviteToClient, resendClientInvitation } from "../lib/settings";
import TeamInviteStepOne, { isValidInviteEmail } from "./TeamInviteStepOne";
import TeamInviteStepTwo from "./TeamInviteStepTwo";
import TeamInviteStepThree from "./TeamInviteStepThree";

const STEP_COUNT = 3;

// Full-page "Add Team Member" flow - renders as a genuine page (like
// Workspace/Dashboards/etc.), replacing Settings' own content entirely
// while the sidebar and topbar stay exactly as they are (App.jsx never
// re-renders around this - see Settings.jsx's top-level early return).
// Not a centered modal/popup over a dimmed backdrop.
//
// One shared immersive shell (a slim top row with the "Team · Client" label
// on the left and a minimal step-number marker on the right, then
// scrollable content that ends in its own action row) owns navigation for
// all three steps - no boxed header/footer bars, no separate labeled
// progress section, no separate Close button (Cancel on Step 1 and
// Back-to-Cancel from any step are the only exits needed -
// TEAM_ONBOARDING_REDESIGN.md Round 4). The step components below are
// content-only, so button sizing/position can never drift between steps.
//
// Purely a presentation layer over the same invite call this flow has
// always made (inviteToClient -> POST /py/clients/{id}/invitations) - no
// backend/permission-model changes.
export default function TeamInviteFlow({ client, onClose, onInvited }) {
  const [step, setStep] = useState(0);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("viewer");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState(null);
  const [pendingId, setPendingId] = useState(null);

  const goNext = () => {
    setError("");
    if (!isValidInviteEmail(email)) { setError("Enter a valid email address."); return; }
    setStep(1);
  };

  const sendInvitation = async () => {
    if (saving) return;
    setError("");
    setSaving(true);
    const res = pendingId
      ? await resendClientInvitation(pendingId)
      : await inviteToClient(client.id, email.trim(), role);
    setSaving(false);
    if (!res.ok) {
      // An active invitation already exists: keep the form, offer Resend.
      if (res.code === "invitation_pending") setPendingId(res.invitationId);
      setError(res.error || "Could not send invitation");
      return;
    }
    setPendingId(null);
    setResult(res);
    setStep(2);
  };

  const goBack = () => {
    setError("");
    setPendingId(null);
    if (step > 0) setStep(step - 1);
    else onClose?.();
  };

  const resetForAnother = () => {
    setError("");
    setResult(null);
    setPendingId(null);
    setEmail("");
    setRole("viewer");
    setStep(0);
  };

  const footerConfig = [
    { secondary: { label: "Cancel", onClick: onClose }, primary: { label: "Next", icon: "ti-arrow-right", onClick: goNext, disabled: !email.trim() } },
    { secondary: { label: "Back", icon: "ti-arrow-left", onClick: goBack, disabled: saving }, primary: { label: saving ? "Sending..." : pendingId ? "Resend invitation" : "Send invitation", onClick: sendInvitation, disabled: saving } },
    { secondary: { label: "Invite another teammate", icon: "ti-user-plus", onClick: resetForAnother }, primary: { label: "Done", onClick: onInvited } },
  ][step];

  return (
    <div className="page team-invite-page">
      <div className="team-onboarding-shell">
        <div className="team-onboarding-topline">
          <div className="page-eyebrow">Team &middot; {client?.name || "Client"}</div>
          <div className="onboarding-steps-mini" aria-label={`Step ${step + 1} of ${STEP_COUNT}`}>
            {Array.from({ length: STEP_COUNT }, (_, i) => (
              <span key={i} className={`onboarding-step-mini${i === step ? " active" : ""}${i < step ? " done" : ""}`} aria-hidden="true">
                {i < step ? <i className="ti ti-check" /> : i + 1}
              </span>
            ))}
          </div>
        </div>

        <div className="team-onboarding-content" key={step}>
          <div className="team-onboarding-inner">
            {step === 0 && (
              <TeamInviteStepOne email={email} setEmail={setEmail} error={error} checking={false} />
            )}
            {step === 1 && (
              <TeamInviteStepTwo email={email} clientName={client?.name} role={role} setRole={setRole} error={error} />
            )}
            {step === 2 && (
              <TeamInviteStepThree result={result} clientName={client?.name} role={role} />
            )}

            <div className="team-onboarding-actions">
              <button className="ob-btn ob-btn-secondary" type="button" onClick={footerConfig.secondary.onClick} disabled={footerConfig.secondary.disabled}>
                {footerConfig.secondary.icon && <i className={`ti ${footerConfig.secondary.icon}`} aria-hidden="true" />}
                {footerConfig.secondary.label}
              </button>
              <button className="ob-btn ob-btn-primary" type="button" onClick={footerConfig.primary.onClick} disabled={footerConfig.primary.disabled}>
                {footerConfig.primary.label}
                {footerConfig.primary.icon && <i className={`ti ${footerConfig.primary.icon}`} aria-hidden="true" />}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
