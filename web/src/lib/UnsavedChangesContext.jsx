// SPDX-License-Identifier: AGPL-3.0-only
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import "../styles/unsaved-modal.css";

// One reusable "unsaved changes" system for the whole app, replacing what
// used to be seven near-identical hand-rolled modals (App.jsx's generic
// fallback, Settings/ClientBranding/AgencyBranding/Reports, and
// ConnectModal/ClientWorkspaceModal) each with their own Cancel/Save-only
// confirm dialog. Any editable page/modal calls registerChanges() once it
// has something at stake and clearChanges() when it doesn't; anything that
// wants to navigate away calls confirmNavigation(action) instead of just
// doing the navigation directly - it either runs immediately (nothing
// dirty) or shows the one shared modal first.
//
// `genericFallback` is a second, lower-priority registration slot used
// only by App.jsx itself for pages that never call registerChanges
// explicitly (a generic DOM-based dirty check) - an explicit registration
// always wins over it while active, matching the priority the old
// per-page ref chain already had.
const UnsavedChangesContext = createContext(null);

export function UnsavedChangesProvider({ children }) {
  const registrationRef = useRef(null);
  const genericFallbackRef = useRef(null);
  const [pending, setPending] = useState(null); // { action } | null
  const [busy, setBusy] = useState(false);

  const registerChanges = useCallback((registration) => {
    registrationRef.current = registration;
  }, []);

  const clearChanges = useCallback(() => {
    registrationRef.current = null;
  }, []);

  const setGenericFallback = useCallback((registration) => {
    genericFallbackRef.current = registration;
  }, []);

  const activeRegistration = useCallback(() => registrationRef.current || genericFallbackRef.current, []);

  const isDirty = useCallback(() => {
    const reg = activeRegistration();
    if (!reg) return false;
    return typeof reg.isDirty === "function" ? Boolean(reg.isDirty()) : Boolean(reg.isDirty);
  }, [activeRegistration]);

  // action: () => void - what to do once we're clear to proceed (navigate,
  // reload, sign out, etc). Runs immediately if nothing is dirty right now.
  const confirmNavigation = useCallback((action) => {
    if (!isDirty()) { action(); return; }
    setPending({ action });
  }, [isDirty]);

  const closeOnly = useCallback(() => {
    if (!busy) setPending(null);
  }, [busy]);

  useEffect(() => {
    if (!pending) return undefined;
    const onKeyDown = (event) => {
      if (event.key === "Escape") closeOnly();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [pending, closeOnly]);

  const handleSave = async () => {
    const reg = activeRegistration();
    if (!reg?.onSave) { setPending(null); return; }
    setBusy(true);
    const ok = await reg.onSave();
    setBusy(false);
    if (!ok) return; // stay open - the page's own error message explains why
    registrationRef.current = null;
    const action = pending?.action;
    setPending(null);
    action?.();
  };

  const handleDiscard = () => {
    activeRegistration()?.onDiscard?.();
    registrationRef.current = null;
    const action = pending?.action;
    setPending(null);
    action?.();
  };

  return (
    <UnsavedChangesContext.Provider value={{ registerChanges, clearChanges, setGenericFallback, confirmNavigation, isDirty }}>
      {children}
      {pending && (
        <div className="modal-bg global-unsaved-modal" role="dialog" aria-modal="true" aria-labelledby="unsaved-changes-title" aria-describedby="unsaved-changes-copy" onClick={closeOnly}>
          <div className="modal unsaved-modal" onClick={(event) => event.stopPropagation()}>
            <div className="unsaved-modal-body">
              <span className="unsaved-modal-icon" aria-hidden="true"><i className="ti ti-alert-triangle" /></span>
              <div className="unsaved-modal-text">
                <h2 className="unsaved-modal-title" id="unsaved-changes-title">Unsaved changes</h2>
                <p className="unsaved-modal-copy" id="unsaved-changes-copy">You have changes that haven&apos;t been saved. Save them before you continue, or discard them.</p>
              </div>
            </div>
            <div className="unsaved-modal-foot">
              <button type="button" className="unsaved-btn unsaved-btn--cancel" onClick={closeOnly} disabled={busy}>Cancel</button>
              <div className="unsaved-modal-actions">
                <button type="button" className="unsaved-btn unsaved-btn--discard" onClick={handleDiscard} disabled={busy}>Discard changes</button>
                <button type="button" className="unsaved-btn unsaved-btn--save" onClick={handleSave} disabled={busy} autoFocus>{busy ? "Saving..." : "Save changes"}</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </UnsavedChangesContext.Provider>
  );
}

export function useUnsavedChanges() {
  const ctx = useContext(UnsavedChangesContext);
  if (!ctx) throw new Error("useUnsavedChanges must be used within an UnsavedChangesProvider");
  return ctx;
}
