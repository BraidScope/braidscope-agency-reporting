// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useRef, useState } from "react";
import { useUnsavedChanges } from "../lib/UnsavedChangesContext.jsx";

const BENEFITS = [
  { icon: "ti-plug-connected", label: "Connect client data sources" },
  { icon: "ti-chart-bar", label: "Build dashboards and reports" },
  { icon: "ti-folder-check", label: "Keep client insights organized" },
];

const COPY = {
  first: {
    title: "Create your first client workspace",
    subtitle: "Create a workspace to organize a client's sources, dashboards, reports, and insights in one place.",
  },
  add: {
    title: "Add a client workspace",
    subtitle: "Create a workspace to connect sources and view dashboards.",
  },
};

// Single reusable client-creation modal for both the mandatory first
// workspace (mode="first", not dismissible - there is nothing to fall back
// to yet) and every later "Add client workspace" action (mode="add",
// dismissible with an unsaved-changes guard). Both paths share the same
// design, validation, creation mutation, and error handling - only the
// copy and dismissibility differ.
export default function ClientWorkspaceModal({
  open,
  mode = "add",
  onCreate,
  onClose,
  saving,
  error,
}) {
  const dismissible = mode !== "first";
  const [name, setName] = useState("");
  const modalRef = useRef(null);
  const dirty = Boolean(dismissible && open && name.trim());
  const { registerChanges, clearChanges, confirmNavigation } = useUnsavedChanges();

  useEffect(() => {
    if (open) setName("");
  }, [open]);

  useEffect(() => {
    if (!dismissible || !open) { clearChanges(); return undefined; }
    registerChanges({
      isDirty: dirty,
      onSave: async () => (await onCreate(name)) !== false,
      onDiscard: () => {},
    });
    return () => clearChanges();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dismissible, open, dirty, name, registerChanges, clearChanges]);

  useEffect(() => {
    if (!open) return undefined;

    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (dismissible) requestClose();
        return;
      }

      if (event.key !== "Tab") return;
      const focusable = Array.from(modalRef.current?.querySelectorAll("input, button") || [])
        .filter((element) => !element.disabled && element.offsetParent !== null);
      if (!focusable.length) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown, true);
    return () => document.removeEventListener("keydown", handleKeyDown, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, dismissible, dirty]);

  if (!open) return null;

  const copy = COPY[mode] || COPY.add;

  const submit = async (event) => {
    event.preventDefault();
    await onCreate(name);
  };

  const requestClose = () => {
    if (!dismissible) return;
    confirmNavigation(() => onClose?.());
  };

  return (
    <div
      className="onboard-modal-bg"
      role="dialog"
      aria-modal="true"
      aria-labelledby="client-workspace-modal-title"
      onClick={dismissible ? (e) => { if (e.target === e.currentTarget) requestClose(); } : undefined}
    >
      <form className="onboard-modal" onSubmit={submit} ref={modalRef}>
        {dismissible && <i className="ti ti-x onboard-modal-close" onClick={requestClose} />}
        <div className="onboard-modal-icon"><i className="ti ti-building-store" aria-hidden="true" /></div>
        <div className="onboard-modal-copy">
          <h2 id="client-workspace-modal-title" className="onboard-modal-title">{copy.title}</h2>
          <p className="onboard-modal-sub">{copy.subtitle}</p>
        </div>
        <div className="onboard-benefits" aria-label="What you can do in a client workspace">
          {BENEFITS.map((benefit) => (
            <div className="onboard-benefit" key={benefit.label}>
              <i className={`ti ${benefit.icon}`} aria-hidden="true" />
              <span>{benefit.label}</span>
            </div>
          ))}
        </div>
        <div className="field onboard-modal-field">
          <label htmlFor="client-workspace-name">Client workspace name</label>
          <input
            id="client-workspace-name"
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Example: Acme Clothing Co."
            disabled={saving}
          />
        </div>
        {error && <div className="modal-error">{error}</div>}
        <div className={dismissible ? "onboard-modal-actions" : undefined}>
          {dismissible && <button className="btn-ghost" type="button" onClick={requestClose}>Cancel</button>}
          <button className="onboard-modal-submit" type="submit" disabled={saving || !name.trim()}>
            {saving ? "Creating..." : "Create client workspace"}
          </button>
        </div>
      </form>
    </div>
  );
}
