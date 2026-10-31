#!/usr/bin/env bash
# Braidscope one-command install.
#
#   curl -fsSL https://raw.githubusercontent.com/BraidScope/braidscope-agency-reporting/main/deploy/selfhost/install.sh | bash
#
# Optional environment variables before running:
#   BRAIDSCOPE_DOMAIN=app.example.com   (default: localhost, plain http)
#   BRAIDSCOPE_EMAIL=you@example.com    (for Let's Encrypt and the first owner login)
#   BRAIDSCOPE_DIR=/opt/braidscope      (default: ./braidscope)
#   BRAIDSCOPE_REF=main                 (git branch or tag to install)
#
# What it does: checks Docker, downloads the code, writes .env with random
# secrets, starts the stack, creates the first owner account, prints the URL.
# It never asks for a cloud account and never sends anything anywhere.
set -euo pipefail

REPO_OWNER="${BRAIDSCOPE_REPO_OWNER:-BraidScope}"
REPO_NAME="${BRAIDSCOPE_REPO_NAME:-braidscope-agency-reporting}"
REF="${BRAIDSCOPE_REF:-main}"
DOMAIN="${BRAIDSCOPE_DOMAIN:-localhost}"
EMAIL="${BRAIDSCOPE_EMAIL:-}"
DIR="${BRAIDSCOPE_DIR:-$PWD/braidscope}"

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail "Docker is not installed. See https://docs.docker.com/engine/install/"
# Refuse to run next to a Braidscope cloud deployment: same machine, same ports.
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qE '^braidscope-(caddy|py|web)-1$'; then
  fail "A Braidscope cloud deployment is already running on this machine. Use a fresh server for self-host."
fi
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is missing (docker compose). Update Docker."
docker info >/dev/null 2>&1 || fail "Docker is installed but not running, or you need to be in the docker group."
command -v curl >/dev/null 2>&1 || fail "curl is required."
command -v tar >/dev/null 2>&1 || fail "tar is required."

if [ "$DOMAIN" = "localhost" ]; then
  PUBLIC_URL="http://localhost"
  CADDY_SITE="http://localhost"
else
  PUBLIC_URL="https://$DOMAIN"
  CADDY_SITE="$DOMAIN"
fi

# 1. Code
if [ ! -d "$DIR/deploy/selfhost" ]; then
  say "Downloading Braidscope ($REF) into $DIR"
  mkdir -p "$DIR"
  curl -fsSL "https://github.com/$REPO_OWNER/$REPO_NAME/archive/refs/heads/$REF.tar.gz" -o /tmp/braidscope.tar.gz \
    || curl -fsSL "https://github.com/$REPO_OWNER/$REPO_NAME/archive/refs/tags/$REF.tar.gz" -o /tmp/braidscope.tar.gz \
    || fail "Could not download $REPO_OWNER/$REPO_NAME at $REF"
  tar -xzf /tmp/braidscope.tar.gz -C "$DIR" --strip-components=1
  rm -f /tmp/braidscope.tar.gz
else
  say "Using existing checkout in $DIR"
fi
cd "$DIR/deploy/selfhost"

# 2. Secrets. Minted inside a throwaway Python container so the host needs
#    nothing but Docker. ANON_KEY and SERVICE_ROLE_KEY are JWTs signed with
#    JWT_SECRET, exactly like Supabase does it.
if [ ! -f .env ]; then
  say "Generating secrets"
  SECRETS="$(docker run --rm -i python:3.12-slim python - <<'PY'
import base64, hmac, hashlib, json, secrets, time
def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
def jwt(secret, payload):
    h = b64(json.dumps({"alg":"HS256","typ":"JWT"},separators=(",",":")).encode())
    p = b64(json.dumps(payload,separators=(",",":")).encode())
    s = b64(hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest())
    return f"{h}.{p}.{s}"
jwt_secret = secrets.token_urlsafe(48)
now = int(time.time()); exp = now + 10*365*24*3600
print("POSTGRES_PASSWORD=" + secrets.token_urlsafe(32).replace("-", "x"))
print("JWT_SECRET=" + jwt_secret)
print("ANON_KEY=" + jwt(jwt_secret, {"role":"anon","iss":"supabase","iat":now,"exp":exp}))
print("SERVICE_ROLE_KEY=" + jwt(jwt_secret, {"role":"service_role","iss":"supabase","iat":now,"exp":exp}))
print("CONNECTOR_TOKEN_ENCRYPTION_KEY=" + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
print("OWNER_PASSWORD=" + secrets.token_urlsafe(14))
PY
)"
  # Never write an .env with empty secrets (an earlier version did, when the
  # container's stdin was not attached). Fail loudly instead.
  for name in POSTGRES_PASSWORD JWT_SECRET ANON_KEY SERVICE_ROLE_KEY CONNECTOR_TOKEN_ENCRYPTION_KEY OWNER_PASSWORD; do
    echo "$SECRETS" | grep -qE "^$name=.{8,}" || fail "Secret generation failed ($name is empty). Is Docker able to run containers and reach Docker Hub?"
  done
  OWNER_PASSWORD="$(echo "$SECRETS" | sed -n 's/^OWNER_PASSWORD=//p')"
  {
    echo "DOMAIN=$DOMAIN"
    echo "PUBLIC_URL=$PUBLIC_URL"
    echo "CADDY_SITE=$CADDY_SITE"
    echo "ACME_EMAIL=$EMAIL"
    echo "$SECRETS" | grep -v '^OWNER_PASSWORD='
    echo "JWT_EXPIRY=3600"
    echo "DISABLE_SIGNUP=false"
    echo "ENABLE_EMAIL_AUTOCONFIRM=true"
    echo "GIT_COMMIT=$REF"
  } > .env
  chmod 600 .env
  echo "OWNER_PASSWORD=$OWNER_PASSWORD" > .first-owner
  chmod 600 .first-owner
else
  say ".env already exists, keeping it"
fi

# 3. Start
say "Building and starting (first run takes a few minutes)"
docker compose up -d --build

say "Waiting for the app"
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost/py/health" >/dev/null 2>&1 || curl -fsSk "https://localhost/py/health" -H "Host: $DOMAIN" >/dev/null 2>&1; then
    break
  fi
  sleep 3
done

# 4. First owner
if [ -f .first-owner ]; then
  OWNER_EMAIL="${EMAIL:-owner@$DOMAIN}"
  OWNER_PASSWORD="$(sed -n 's/^OWNER_PASSWORD=//p' .first-owner)"
  SERVICE_ROLE_KEY="$(sed -n 's/^SERVICE_ROLE_KEY=//p' .env)"
  say "Creating the first owner account: $OWNER_EMAIL"
  docker compose exec -T auth wget -q -O- --header="Authorization: Bearer $SERVICE_ROLE_KEY" \
    --header="Content-Type: application/json" \
    --post-data="{\"email\":\"$OWNER_EMAIL\",\"password\":\"$OWNER_PASSWORD\",\"email_confirm\":true}" \
    http://localhost:9999/admin/users >/dev/null \
    && rm -f .first-owner \
    || say "Could not create the owner yet. Run: bash install.sh again after the stack is healthy."
  echo
  say "Braidscope is running"
  echo "   URL:      $PUBLIC_URL"
  echo "   Email:    $OWNER_EMAIL"
  echo "   Password: $OWNER_PASSWORD"
  echo "   Change the password after the first login (Settings > Security)."
else
  say "Braidscope is running at $PUBLIC_URL"
fi
echo
echo "Logs:    cd $DIR/deploy/selfhost && docker compose logs -f"
echo "Upgrade: cd $DIR/deploy/selfhost && bash upgrade.sh"
echo "Backup:  cd $DIR/deploy/selfhost && bash backup.sh"
