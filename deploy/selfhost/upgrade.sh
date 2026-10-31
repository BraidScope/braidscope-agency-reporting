#!/usr/bin/env bash
# Upgrade a self-hosted Braidscope: backup, fetch the new code, rebuild, migrate.
# Run from this folder: bash upgrade.sh [branch-or-tag]
set -euo pipefail
cd "$(dirname "$0")"
REF="${1:-$(sed -n 's/^GIT_COMMIT=//p' .env)}"
ROOT="$(cd ../.. && pwd)"

bash backup.sh

echo "==> Fetching $REF"
curl -fsSL "https://github.com/BraidScope/braidscope-agency-reporting/archive/refs/heads/$REF.tar.gz" -o /tmp/braidscope-upgrade.tar.gz \
  || curl -fsSL "https://github.com/BraidScope/braidscope-agency-reporting/archive/refs/tags/$REF.tar.gz" -o /tmp/braidscope-upgrade.tar.gz
tar -xzf /tmp/braidscope-upgrade.tar.gz -C "$ROOT" --strip-components=1
rm -f /tmp/braidscope-upgrade.tar.gz
sed -i "s/^GIT_COMMIT=.*/GIT_COMMIT=$REF/" .env

echo "==> Rebuilding and migrating"
docker compose up -d --build
docker compose logs migrate | tail -n 5
echo "==> Done. Check: curl -s $(sed -n 's/^PUBLIC_URL=//p' .env)/py/health"
