#!/bin/sh
# Writes /config.js from environment at container start, so one prebuilt
# image serves any domain. Values here are public by design (the anon key is
# shipped to every browser anyway). Empty values fall back to the build-time
# VITE_* settings baked into the bundle.
set -e
escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
cat > /usr/share/nginx/html/config.js <<EOF
window.__BRAIDSCOPE_CONFIG__ = {
  supabaseUrl: "$(escape "${SUPABASE_URL_PUBLIC:-}")",
  supabaseAnonKey: "$(escape "${SUPABASE_ANON_KEY:-}")"
};
EOF
