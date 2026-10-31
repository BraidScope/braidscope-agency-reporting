// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useMemo, useRef, useState } from "react";
import { getClientTeam } from "../../lib/settings";
import { Banner, Modal, Spinner } from "./ReportControls";

const roleLabel = (role) => (role ? role[0].toUpperCase() + role.slice(1) : "");
const newSendId = () => (globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID() : `send-${Date.now()}-${Math.random().toString(36).slice(2)}`);

function Avatar({ member }) {
  const initial = (member.name || member.email || "?").trim().slice(0, 1).toUpperCase();
  return (
    <span className="rp-avatar" aria-hidden="true">
      {member.avatarUrl ? <img src={member.avatarUrl} alt="" /> : initial}
    </span>
  );
}

// Lists THIS client's teammates (the same team Settings shows, loaded with the
// same helper) and emails the report PDF to the ones you tick. The server
// validates every recipient again against the client's team.
export default function SendToTeammatesModal({ clientId, clientName, report, onClose, onSend }) {
  const [members, setMembers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [selected, setSelected] = useState(() => new Set());
  const [phase, setPhase] = useState("idle"); // idle | sending | sent
  const [error, setError] = useState("");
  const [sentCount, setSentCount] = useState(0);
  const sendId = useRef(newSendId());
  const inFlight = useRef(false);
  const allRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getClientTeam(clientId).then((result) => {
      if (cancelled) return;
      setLoading(false);
      if (!result.ok) { setLoadError(result.error || "Couldn't load teammates."); return; }
      setMembers(result.members.filter((member) => member.userId && member.email));
    });
    return () => { cancelled = true; };
  }, [clientId]);

  const sorted = useMemo(() => [...members].sort((a, b) => (a.name || a.email).localeCompare(b.name || b.email)), [members]);
  const allSelected = sorted.length > 0 && selected.size === sorted.length;
  const someSelected = selected.size > 0 && !allSelected;

  useEffect(() => {
    if (allRef.current) allRef.current.indeterminate = someSelected;
  }, [someSelected]);

  const toggle = (id) => setSelected((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(sorted.map((member) => member.userId)));

  const send = async () => {
    if (inFlight.current || selected.size === 0) return;
    inFlight.current = true;
    setPhase("sending");
    setError("");
    const result = await onSend([...selected], sendId.current);
    inFlight.current = false;
    if (!result.ok) { setPhase("idle"); setError(result.error || "Could not send the report. Try again."); return; }
    setSentCount(result.data?.sent ?? selected.size);
    setPhase("sent");
  };

  const sending = phase === "sending";

  if (phase === "sent") {
    return (
      <Modal
        icon="ti-circle-check"
        title="Report sent"
        description={`${sentCount} ${sentCount === 1 ? "teammate" : "teammates"} will receive “${report.name}” as a PDF by email.`}
        onClose={onClose}
        footer={<button type="button" className="primary-btn" onClick={onClose} data-autofocus>Done</button>}
      />
    );
  }

  return (
    <Modal
      icon="ti-send"
      title="Send to teammates"
      description={`Choose who on ${clientName} should get “${report.name}” as a PDF by email.`}
      onClose={onClose}
      busy={sending}
      footer={(
        <>
          <span className="rp-foot-summary" role="status">{selected.size === 0 ? "No one selected" : `${selected.size} ${selected.size === 1 ? "teammate" : "teammates"} selected`}</span>
          <button type="button" className="ghost-btn" onClick={onClose} disabled={sending}>Cancel</button>
          <button type="button" className="primary-btn" onClick={send} disabled={sending || selected.size === 0}>
            {sending ? <><Spinner /> Sending…</> : <><i className="ti ti-send" aria-hidden="true" /> Send report</>}
          </button>
        </>
      )}
    >
      <div className="rp-send">
        {loading && <div className="rp-skel-list" aria-busy="true"><div className="rp-skel-row" /><div className="rp-skel-row" /><div className="rp-skel-row" /></div>}
        {loadError && <Banner tone="error">{loadError}</Banner>}
        {!loading && !loadError && sorted.length === 0 && <Banner tone="info">There are no other teammates with an email address on this client yet. Invite people from Settings.</Banner>}
        {!loading && sorted.length > 0 && (
          <>
            <label className="rp-recipient rp-recipient--all">
              <input ref={allRef} type="checkbox" checked={allSelected} onChange={toggleAll} disabled={sending} />
              <span className="rp-recipient-name">Select all</span>
              <span className="rp-recipient-meta">{sorted.length} {sorted.length === 1 ? "teammate" : "teammates"}</span>
            </label>
            <ul className="rp-recipients" aria-label="Teammates">
              {sorted.map((member) => (
                <li key={member.userId}>
                  <label className={`rp-recipient${selected.has(member.userId) ? " on" : ""}`}>
                    <input type="checkbox" checked={selected.has(member.userId)} onChange={() => toggle(member.userId)} disabled={sending} />
                    <Avatar member={member} />
                    <span className="rp-recipient-text">
                      <span className="rp-recipient-name">{member.name || member.email}</span>
                      <span className="rp-recipient-email">{member.email}</span>
                    </span>
                    <span className="rp-recipient-role">{roleLabel(member.role)}</span>
                  </label>
                </li>
              ))}
            </ul>
          </>
        )}
        {error && <Banner tone="error">{error}</Banner>}
      </div>
    </Modal>
  );
}
