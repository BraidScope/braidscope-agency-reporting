// SPDX-License-Identifier: AGPL-3.0-only
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { STATUS_META } from "./reportModel";

export function StatusBadge({ status }) {
  const meta = STATUS_META[status] || STATUS_META.draft;
  return <span className={`rp-status rp-status--${meta.tone}`}>{meta.label}</span>;
}

// A real switch: a button with role="switch", reachable and toggleable by
// keyboard, with an accessible name.
export function Switch({ checked, onChange, label, disabled = false }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={`rp-switch${checked ? " on" : ""}`}
      onClick={() => onChange(!checked)}
    />
  );
}

export function Spinner() {
  return <span className="rp-spinner" aria-hidden="true" />;
}

// Single-choice chips (period picker). Arrow keys move between options.
export function Chips({ value, options, onChange, label, disabled = false }) {
  const onKey = (event, index) => {
    if (disabled || (event.key !== "ArrowRight" && event.key !== "ArrowLeft")) return;
    event.preventDefault();
    const next = options[(index + (event.key === "ArrowRight" ? 1 : options.length - 1)) % options.length];
    onChange(next.value);
    event.currentTarget.parentElement.querySelector(`[data-value="${next.value}"]`)?.focus();
  };
  return (
    <div className="rp-chips" role="radiogroup" aria-label={label}>
      {options.map((option, index) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          data-value={option.value}
          aria-checked={value === option.value}
          tabIndex={value === option.value ? 0 : -1}
          disabled={disabled}
          className={`rp-chip${value === option.value ? " on" : ""}`}
          onClick={() => onChange(option.value)}
          onKeyDown={(event) => onKey(event, index)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

// Tab strip with roving keyboard support.
export function Tabs({ tabs, value, onChange, label }) {
  const onKey = (event, index) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const next = tabs[(index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    onChange(next.id);
    event.currentTarget.parentElement.querySelector(`[data-tab="${next.id}"]`)?.focus();
  };
  return (
    <div className="rp-tabs" role="tablist" aria-label={label}>
      {tabs.map((tab, index) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          id={`rp-tab-${tab.id}`}
          data-tab={tab.id}
          aria-selected={value === tab.id}
          aria-controls={`rp-tabpanel-${tab.id}`}
          tabIndex={value === tab.id ? 0 : -1}
          className={`rp-tab${value === tab.id ? " on" : ""}`}
          onClick={() => onChange(tab.id)}
          onKeyDown={(event) => onKey(event, index)}
        >
          <i className={`ti ${tab.icon}`} aria-hidden="true" /> {tab.label}
          {tab.count !== undefined && <span className="rp-tab-count">{tab.count}</span>}
        </button>
      ))}
    </div>
  );
}

// Overflow menu. The popup is rendered in a portal with fixed positioning, so
// no table, card or scroll container can clip it; it flips upward near the
// bottom of the viewport. Escape / outside click / scroll close it and arrow
// keys move between items.
export function RowMenu({ label, items }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  const buttonRef = useRef(null);
  const menuRef = useRef(null);

  const place = useCallback(() => {
    const button = buttonRef.current;
    const menu = menuRef.current;
    if (!button || !menu) return;
    const rect = button.getBoundingClientRect();
    const width = menu.offsetWidth;
    const height = menu.offsetHeight;
    const below = window.innerHeight - rect.bottom - 8;
    const top = below >= height || rect.top < height + 8 ? rect.bottom + 6 : rect.top - height - 6;
    const left = Math.min(Math.max(8, rect.right - width), window.innerWidth - width - 8);
    setPos({ top: Math.max(8, top), left });
  }, []);

  useLayoutEffect(() => {
    if (open) place();
    else setPos(null);
  }, [open, place]);

  useEffect(() => {
    if (!open) return undefined;
    const close = () => setOpen(false);
    const onPointer = (event) => {
      if (!menuRef.current?.contains(event.target) && !buttonRef.current?.contains(event.target)) close();
    };
    const onKey = (event) => {
      if (event.key === "Escape") { close(); buttonRef.current?.focus(); }
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [open]);

  useEffect(() => {
    if (open && pos) menuRef.current?.querySelector("button:not([disabled])")?.focus();
  }, [open, pos === null]); // eslint-disable-line react-hooks/exhaustive-deps

  const onMenuKey = (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const buttons = [...menuRef.current.querySelectorAll("button:not([disabled])")];
    const index = buttons.indexOf(document.activeElement);
    const next = event.key === "ArrowDown" ? (index + 1) % buttons.length : (index - 1 + buttons.length) % buttons.length;
    buttons[next]?.focus();
  };

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className={`rp-icon-btn${open ? " active" : ""}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        title="More actions"
        onClick={() => setOpen((value) => !value)}
      >
        <i className="ti ti-dots" aria-hidden="true" />
      </button>
      {open && createPortal(
        <div
          className="rp-menu-list"
          role="menu"
          aria-label={label}
          ref={menuRef}
          onKeyDown={onMenuKey}
          style={pos ? { top: pos.top, left: pos.left } : { top: 0, left: 0, visibility: "hidden" }}
        >
          {items.filter(Boolean).map((item) => (
            <div key={item.key} className="rp-menu-row">
              {item.separator && <div className="rp-menu-sep" role="separator" />}
              <button
                type="button"
                role="menuitem"
                disabled={item.disabled}
                title={item.title}
                className={item.danger ? "danger" : undefined}
                onClick={() => { setOpen(false); item.onSelect?.(); }}
              >
                <i className={`ti ${item.icon}`} aria-hidden="true" />
                <span>{item.label}</span>
              </button>
            </div>
          ))}
        </div>,
        document.body,
      )}
    </>
  );
}

// Open dialogs, topmost last: Escape only closes the one on top (the custom
// date-range dialog opens over the New report dialog).
const modalStack = [];

// Dialog with a header (icon, title, close), scrolling body and a footer that
// stays visible. Focus moves in on open and returns on close; Escape closes.
export function Modal({ title, description, icon = "ti-file-analytics", onClose, children, footer, busy = false, wide = false, tone }) {
  const ref = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    ref.current?.querySelector("[data-autofocus], input, select, textarea")?.focus();
    const token = {};
    modalStack.push(token);
    const onKey = (event) => {
      if (event.key === "Escape" && !busy && modalStack[modalStack.length - 1] === token) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      const at = modalStack.indexOf(token);
      if (at >= 0) modalStack.splice(at, 1);
      previous?.focus?.();
    };
  }, [onClose, busy]);
  return createPortal(
    <div className="rp-modal-bg" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <div className={`rp-modal${wide ? " wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby="rp-modal-title" aria-describedby={description ? "rp-modal-desc" : undefined} ref={ref}>
        <div className="rp-modal-head">
          <span className={`rp-modal-icon${tone ? ` ${tone}` : ""}`} aria-hidden="true"><i className={`ti ${icon}`} /></span>
          <div className="rp-modal-headtext">
            <h2 id="rp-modal-title" className="rp-modal-title">{title}</h2>
            {description && <p id="rp-modal-desc" className="rp-modal-desc">{description}</p>}
          </div>
          <button type="button" className="rp-icon-btn" onClick={onClose} disabled={busy} aria-label="Close dialog"><i className="ti ti-x" aria-hidden="true" /></button>
        </div>
        {children && <div className="rp-modal-body">{children}</div>}
        {footer && <div className="rp-modal-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function Banner({ tone = "info", icon, children, actions }) {
  return (
    <div className={`rp-banner rp-banner--${tone}`} role={tone === "error" ? "alert" : "status"}>
      <i className={`ti ${icon || (tone === "error" ? "ti-alert-circle" : tone === "warn" ? "ti-alert-triangle" : "ti-info-circle")}`} aria-hidden="true" />
      <div className="rp-banner-text">{children}</div>
      {actions && <div className="rp-banner-actions">{actions}</div>}
    </div>
  );
}
