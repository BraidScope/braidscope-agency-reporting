// SPDX-License-Identifier: AGPL-3.0-only
// Turns whatever an API put in its error slot into one readable sentence.
//
// FastAPI sends `detail` as a plain string for HTTPException, but as a LIST of
// `{type, loc, msg, input, ctx}` objects for request-validation 422s. The
// dashboards used to hand that list straight to JSX, which React refuses to
// render ("Objects are not valid as a React child") and the whole page fell
// over. Everything user-facing should go through here so only strings reach
// the UI, whatever shape the backend (or a proxy in front of it) returns.

const FIELD_LABELS = {
  days: "date range",
  client_id: "client",
  property_id: "property",
  customer_id: "account",
  account_id: "account",
};

function describeValidationItem(item) {
  if (!item || typeof item !== "object") return "";
  const loc = Array.isArray(item.loc) ? item.loc : [];
  const field = [...loc].reverse().find((part) => typeof part === "string" && part !== "query" && part !== "body");
  const label = field ? FIELD_LABELS[field] || field.replace(/_/g, " ") : "";
  const msg = typeof item.msg === "string" ? item.msg : "";
  if (!msg) return label ? `Invalid ${label}` : "";
  return label ? `Invalid ${label}: ${msg.charAt(0).toLowerCase()}${msg.slice(1)}` : msg;
}

export function toErrorMessage(value, fallback = "Something went wrong. Please try again.") {
  if (value == null || value === "") return fallback;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (value instanceof Error) return value.message || fallback;
  if (Array.isArray(value)) {
    const parts = [...new Set(value.map((item) => (typeof item === "string" ? item : describeValidationItem(item))).filter(Boolean))];
    return parts.length ? parts.join(" · ") : fallback;
  }
  if (typeof value === "object") {
    for (const key of ["message", "detail", "reason", "error", "msg"]) {
      if (value[key] != null && value[key] !== value) {
        const nested = toErrorMessage(value[key], "");
        if (nested) return nested;
      }
    }
    const single = describeValidationItem(value);
    if (single) return single;
  }
  return fallback;
}

// Dashboard-specific wording on top of toErrorMessage: a rejected date range
// (the backend's `days` validation) gets a sentence the user can act on
// instead of Pydantic's "Input should be greater than or equal to 7".
export function dashboardErrorMessage(detail, { label = "Dashboard", status } = {}) {
  if (status === 422 && Array.isArray(detail) && detail.some((item) => Array.isArray(item?.loc) && item.loc.includes("days"))) {
    return `${label} doesn't support this date range. Choose a longer range, such as Last 7 days.`;
  }
  return toErrorMessage(detail, `Could not load ${label} dashboard`);
}
