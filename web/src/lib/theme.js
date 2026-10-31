// SPDX-License-Identifier: AGPL-3.0-only
export const THEME_OPTIONS = [
  { id: "light", label: "Light", icon: "ti-sun" },
  { id: "dark", label: "Dark", icon: "ti-moon" },
  { id: "system", label: "System", icon: "ti-device-laptop" },
];

export function prefersDark() {
  return typeof window !== "undefined"
    && window.matchMedia?.("(prefers-color-scheme: dark)").matches;
}

// "system" is a preference, not a value — resolve it to the concrete theme that
// should actually be painted right now.
export function resolveTheme(preference) {
  if (preference === "dark") return "dark";
  if (preference === "light") return "light";
  return prefersDark() ? "dark" : "light";
}

export function applyTheme(resolved) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.setAttribute("data-theme", resolved);
  root.classList.toggle("dark", resolved === "dark");
}

// Fires whenever the OS theme flips, but only matters while the preference is
// "system" — the caller re-resolves and decides.
export function watchSystemTheme(callback) {
  if (typeof window === "undefined" || !window.matchMedia) return () => {};
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  const handler = () => callback(query.matches ? "dark" : "light");
  query.addEventListener("change", handler);
  return () => query.removeEventListener("change", handler);
}

/* ---------------- accent tinting ---------------- */

// The brand accent is user-chosen, so its "soft" companion has to be mixed
// toward whichever surface it will sit on — white on light, near-black on dark.
export function accentSoft(hex, resolvedTheme) {
  const clean = String(hex || "").replace("#", "");
  if (clean.length !== 6) return null;
  const value = parseInt(clean, 16);
  if (Number.isNaN(value)) return null;

  const r = (value >> 16) & 255;
  const g = (value >> 8) & 255;
  const b = value & 255;

  if (resolvedTheme === "dark") {
    // Keep the hue but let the dark canvas show through.
    return `rgba(${r},${g},${b},0.16)`;
  }
  const mix = 0.9;
  return `rgb(${Math.round(r + (255 - r) * mix)},${Math.round(g + (255 - g) * mix)},${Math.round(b + (255 - b) * mix)})`;
}

// On a dark canvas a near-black brand accent disappears, so lift it just enough
// to stay visible while keeping the hue the agency picked.
export function accentForTheme(hex, resolvedTheme) {
  const clean = String(hex || "").replace("#", "");
  if (clean.length !== 6) return hex;
  const value = parseInt(clean, 16);
  if (Number.isNaN(value)) return hex;

  const r = (value >> 16) & 255;
  const g = (value >> 8) & 255;
  const b = value & 255;
  if (resolvedTheme !== "dark") return hex;

  const luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  if (luminance > 0.32) return hex;

  const lift = 0.55;
  const part = (n) => Math.round(n + (255 - n) * lift).toString(16).padStart(2, "0");
  return `#${part(r)}${part(g)}${part(b)}`;
}
