// SPDX-License-Identifier: AGPL-3.0-only
// Branding is persisted in organizations.branding_config in Supabase.
// Keep logo payloads small enough for the JSONB organization record.
export const MAX_LOGO_BYTES = 512 * 1024;

export const LOGO_SLOTS = [
  { id: "primary", label: "Primary logo", hint: "PNG or SVG · light backgrounds" },
  { id: "dark", label: "Dark-mode logo", hint: "PNG or SVG · dark surfaces" },
  { id: "icon", label: "Icon / mark", hint: "Square, 512×512 min" },
  { id: "favicon", label: "Favicon", hint: "PNG or ICO, 64×64" },
];

export const FONT_PAIRINGS = [
  {
    id: "jakarta",
    name: "Plus Jakarta Sans",
    body: "+ Jakarta body",
    stack: "'Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,sans-serif",
  },
  {
    id: "serif",
    name: "Georgia",
    body: "+ Jakarta body",
    stack: "Georgia,'Times New Roman',serif",
  },
  {
    id: "mono",
    name: "IBM Plex Mono",
    body: "+ Jakarta body",
    stack: "'IBM Plex Mono',ui-monospace,monospace",
  },
];

export function emptyBranding() {
  return {
    tagline: "",
    supportEmail: "",
    fontPairing: FONT_PAIRINGS[0].id,
    logos: { primary: null, dark: null, icon: null, favicon: null },
    overrides: {},
  };
}

export function mergeBranding(base, override) {
  const fallback = emptyBranding();
  return {
    ...fallback,
    ...(base || {}),
    ...(override || {}),
    logos: { ...fallback.logos, ...(base?.logos || {}), ...(override?.logos || {}) },
    overrides: { ...(base?.overrides || {}), ...(override?.overrides || {}) },
  };
}

export function readLogoFile(file, maxBytes = MAX_LOGO_BYTES) {
  return new Promise((resolve) => {
    if (!file) return resolve({ ok: false, error: "No file selected" });
    if (!file.type.startsWith("image/")) {
      return resolve({ ok: false, error: "Image must be a PNG, JPG, WEBP or SVG file" });
    }
    if (file.size > maxBytes) {
      return resolve({ ok: false, error: `Image must be under ${Math.round(maxBytes / 1024)}KB` });
    }

    const reader = new FileReader();
    reader.onload = () => resolve({ ok: true, dataUrl: String(reader.result), name: file.name });
    reader.onerror = () => resolve({ ok: false, error: "Could not read that file" });
    reader.readAsDataURL(file);
  });
}

// Downscales+recompresses an image into a small square JPEG data URL, for any
// image that ends up stored in Supabase Auth user_metadata (see
// MAX_STORED_AVATAR_BYTES in settings.js) rather than a free-standing table
// column. user_metadata rides inside the JWT on every request, so an
// uncompressed upload here (previously allowed up to 96KB raw / ~128KB
// base64) was blowing past the dev server's ~16KB header-size limit and
// causing every request to fail with 431 Request Header Fields Too Large.
// Compressing down to a small thumbnail keeps this permanently well under
// that limit regardless of what the user uploads.
export function compressImageToSquareDataUrl(file, { maxDim = 128, maxBytes = 12 * 1024 } = {}) {
  return new Promise((resolve) => {
    if (!file) return resolve({ ok: false, error: "No file selected" });
    if (!file.type.startsWith("image/")) {
      return resolve({ ok: false, error: "Image must be a PNG, JPG or WEBP file" });
    }

    const reader = new FileReader();
    reader.onerror = () => resolve({ ok: false, error: "Could not read that file" });
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => resolve({ ok: false, error: "Could not read that image" });
      img.onload = () => {
        const side = Math.min(img.naturalWidth, img.naturalHeight) || maxDim;
        const sx = (img.naturalWidth - side) / 2;
        const sy = (img.naturalHeight - side) / 2;

        const canvas = document.createElement("canvas");
        canvas.width = maxDim;
        canvas.height = maxDim;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, sx, sy, side, side, 0, 0, maxDim, maxDim);

        let quality = 0.8;
        let dataUrl = canvas.toDataURL("image/jpeg", quality);
        while (dataUrl.length > maxBytes && quality > 0.2) {
          quality -= 0.15;
          dataUrl = canvas.toDataURL("image/jpeg", quality);
        }

        if (dataUrl.length > maxBytes) {
          resolve({ ok: false, error: "Image is too complex to compress small enough — try a simpler photo" });
          return;
        }
        resolve({ ok: true, dataUrl });
      };
      img.src = String(reader.result);
    };
    reader.readAsDataURL(file);
  });
}

/* ---------------- colour maths ---------------- */

function toRgb(hex) {
  const clean = String(hex || "").replace("#", "");
  if (clean.length !== 6) return null;
  const value = parseInt(clean, 16);
  if (Number.isNaN(value)) return null;
  return { r: (value >> 16) & 255, g: (value >> 8) & 255, b: value & 255 };
}

function toHex({ r, g, b }) {
  const part = (n) => Math.round(Math.min(255, Math.max(0, n))).toString(16).padStart(2, "0");
  return `#${part(r)}${part(g)}${part(b)}`;
}

function mix(hex, target, amount) {
  const from = toRgb(hex);
  const to = toRgb(target);
  if (!from || !to) return hex;
  return toHex({
    r: from.r + (to.r - from.r) * amount,
    g: from.g + (to.g - from.g) * amount,
    b: from.b + (to.b - from.b) * amount,
  });
}

const RAMP_STEPS = [0.94, 0.86, 0.7, 0.48, 0.24, 0, -0.22, -0.44, -0.64];

// Nine tonal stops generated from the accent — light tints first, base in the
// middle, shades last.
export function buildAccentRamp(accent) {
  return RAMP_STEPS.map((step) => {
    if (step === 0) return accent;
    return step > 0 ? mix(accent, "#ffffff", step) : mix(accent, "#000000", -step);
  });
}

function channelLuminance(channel) {
  const c = channel / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function luminance(hex) {
  const rgb = toRgb(hex);
  if (!rgb) return 0;
  return (
    0.2126 * channelLuminance(rgb.r) +
    0.7152 * channelLuminance(rgb.g) +
    0.0722 * channelLuminance(rgb.b)
  );
}

// Contrast of the accent against white text — the accent's main job is as a
// button/badge background.
export function contrastOnWhite(accent) {
  const light = luminance("#ffffff");
  const dark = luminance(accent);
  const ratio = (Math.max(light, dark) + 0.05) / (Math.min(light, dark) + 0.05);
  const rounded = Math.round(ratio * 10) / 10;
  const grade = ratio >= 7 ? "AAA" : ratio >= 4.5 ? "AA" : ratio >= 3 ? "AA Large" : "Fails";
  return { ratio: rounded, grade, passes: ratio >= 4.5 };
}

export function isValidHex(hex) {
  return /^#[0-9a-fA-F]{6}$/.test(String(hex || ""));
}

/* ---------------- readiness ---------------- */

// Drives the ring in the Brand Studio hero. Each item is something the agency
// can actually complete on this page.
export function computeReadiness({ agencyName, accent, branding, overrideCount }) {
  const checks = [
    { id: "name", label: "Agency name set", done: Boolean(agencyName?.trim()) },
    { id: "accent", label: "Accent colour set", done: isValidHex(accent) },
    { id: "tagline", label: "Tagline written", done: Boolean(branding.tagline?.trim()) },
    { id: "email", label: "Support email added", done: Boolean(branding.supportEmail?.trim()) },
    { id: "primary", label: "Primary logo uploaded", done: Boolean(branding.logos?.primary) },
    { id: "dark", label: "Dark-mode logo uploaded", done: Boolean(branding.logos?.dark) },
    { id: "icon", label: "Icon / mark uploaded", done: Boolean(branding.logos?.icon) },
    { id: "favicon", label: "Favicon uploaded", done: Boolean(branding.logos?.favicon) },
    { id: "overrides", label: "A client override configured", done: overrideCount > 0 },
  ];
  const done = checks.filter((check) => check.done).length;
  return {
    checks,
    done,
    total: checks.length,
    percent: Math.round((done / checks.length) * 100),
  };
}

/* ---------------- effective (client) branding ---------------- */

// Single source of truth for "what does this client's branding actually
// look like right now" - the OWNING agency's defaults merged with this
// client's own override (only when that override is enabled). Used both by
// App.jsx's --accent/--font-core CSS effect (drives Dashboards/Reports/
// Topbar app-wide) and by the Client Branding page's own rendering, so the
// inheritance rule is never duplicated/out of sync between the two.
//
// The override now lives on the client row itself (client.branding_override)
// rather than inside either agency's organizations.branding_config blob - a
// shared client's override must never be resolved against the VIEWING
// user's own agency, only its true owner's (see SHARED_CLIENT_CONTEXT_FIX.md).
// ownerAgencyName/ownerAccent/ownerBranding are explicitly "the owning
// agency's own branding" - for an owned client that's the viewer's own
// agency (unchanged); for a shared client it's the fetched owner org's.
export function resolveClientBranding({ ownerAgencyName, ownerAccent, ownerBranding, client }) {
  const override = client?.branding_override || null;
  const enabled = Boolean(override?.enabled);
  const fontPairing = (enabled && override.fontPairing) || ownerBranding?.fontPairing || FONT_PAIRINGS[0].id;
  const pairing = FONT_PAIRINGS.find((f) => f.id === fontPairing) || FONT_PAIRINGS[0];
  return {
    enabled,
    name: (enabled ? override.name : null) ?? client?.name ?? ownerAgencyName,
    tagline: (enabled && override.tagline) || ownerBranding?.tagline || "",
    supportEmail: (enabled && override.supportEmail) || ownerBranding?.supportEmail || "",
    fontPairing,
    fontStack: pairing.stack,
    accent: enabled && isValidHex(override.accent) ? override.accent : ownerAccent,
    logo: (enabled && override.logo) || ownerBranding?.logos?.icon || ownerBranding?.logos?.primary || null,
  };
}

// Client Branding's own readiness, distinct from agency readiness (Phase 13
// of the spec): every check is evaluated against the EFFECTIVE (resolved)
// value, so a client that simply inherits valid agency defaults already
// reads as "ready" - only the "Custom branding enabled" check requires an
// actual override on this client specifically.
export function computeClientReadiness({ effective, enabled }) {
  const checks = [
    { id: "name", label: "Display name set", done: Boolean(effective.name?.trim()) },
    { id: "accent", label: "Accent colour set", done: isValidHex(effective.accent) },
    { id: "tagline", label: "Tagline written", done: Boolean(effective.tagline?.trim()) },
    { id: "email", label: "Support email added", done: Boolean(effective.supportEmail?.trim()) },
    { id: "logo", label: "Logo available", done: Boolean(effective.logo) },
    { id: "custom", label: "Custom branding turned on", done: Boolean(enabled) },
  ];
  const done = checks.filter((check) => check.done).length;
  return {
    checks,
    done,
    total: checks.length,
    percent: Math.round((done / checks.length) * 100),
  };
}
