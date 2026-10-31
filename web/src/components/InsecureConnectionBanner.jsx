// SPDX-License-Identifier: AGPL-3.0-only
// Shown when the app is served over plain http from anywhere but this machine.
// Passwords and tokens travel in clear text on such a connection. Self-host
// installs get HTTPS automatically once DOMAIN is a real domain (Caddy).
export default function InsecureConnectionBanner() {
  if (typeof window === "undefined") return null;
  const { protocol, hostname } = window.location;
  const local = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
  if (protocol === "https:" || local) return null;
  return (
    <div
      role="alert"
      style={{
        background: "#b42318",
        color: "#fff",
        padding: "8px 16px",
        fontSize: 13,
        textAlign: "center",
        fontWeight: 600,
      }}
    >
      Not secure: this Braidscope instance is served over plain http. Set DOMAIN to a real domain so
      HTTPS turns on, or only use it on a private network.
    </div>
  );
}
