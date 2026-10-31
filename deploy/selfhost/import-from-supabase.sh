#!/usr/bin/env bash
# Move an existing Braidscope from Supabase cloud into this self-hosted stack.
#
# Copies: every user (with their password hashes and two-factor factors, so
# nobody needs to reset anything), all workspaces and app data, and the
# report PDF files. Run once, after install.sh, while the cloud project is
# still running. Safe to re-run; it starts from the current self-host data
# and replaces it.
#
# Needs two values from the Supabase dashboard (Project Settings):
#   SUPABASE_DB_URL        Database > Connection string (URI, "session" mode)
#   SUPABASE_SERVICE_KEY   API > service_role key
#   SUPABASE_URL           API > Project URL
# Pass them as environment variables. They are used once and not stored.
set -euo pipefail
cd "$(dirname "$0")"

: "${SUPABASE_DB_URL:?set SUPABASE_DB_URL}"
: "${SUPABASE_SERVICE_KEY:?set SUPABASE_SERVICE_KEY}"
: "${SUPABASE_URL:?set SUPABASE_URL}"

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
PG="docker compose exec -T db"
DUMP="docker run --rm -i --network host supabase/postgres:17.6.1.136"

say "Backing up the current self-host data first"
bash backup.sh

say "Dumping data from Supabase cloud (schema is already here from migrations)"
# auth: users and everything hanging off them. public: all app tables.
# storage: bucket and object rows (files are copied below).
$DUMP pg_dump "$SUPABASE_DB_URL" --data-only --no-owner --no-privileges \
  --disable-triggers \
  -t 'auth.users' -t 'auth.identities' -t 'auth.mfa_factors' -t 'auth.mfa_challenges' \
  -t 'public.*' -t 'storage.buckets' -t 'storage.objects' \
  > /tmp/braidscope-cloud-data.sql

say "Clearing self-host tables that will be replaced"
$PG psql -U postgres -v ON_ERROR_STOP=1 -q <<'SQL'
begin;
set session_replication_role = replica;
do $$
declare t record;
begin
  for t in select tablename from pg_tables where schemaname = 'public' and tablename <> 'braidscope_migrations' loop
    execute format('truncate table public.%I cascade', t.tablename);
  end loop;
end $$;
truncate storage.objects, storage.buckets cascade;
truncate auth.mfa_challenges, auth.mfa_factors, auth.identities, auth.users cascade;
commit;
SQL

say "Loading data"
$PG psql -U postgres -v ON_ERROR_STOP=1 -q -f - < /tmp/braidscope-cloud-data.sql
rm -f /tmp/braidscope-cloud-data.sql

say "Copying report PDF files"
DOMAIN_VALUE="$(sed -n 's/^DOMAIN=//p' .env)"
SERVICE_ROLE_KEY="$(sed -n 's/^SERVICE_ROLE_KEY=//p' .env)"
if [ "$DOMAIN_VALUE" = "localhost" ]; then LOCAL_BASE="http://localhost"; else LOCAL_BASE="https://localhost"; fi
objects="$($PG psql -U postgres -tAq -c "select bucket_id || '/' || name from storage.objects where name is not null")"
count=0
while IFS= read -r path; do
  [ -z "$path" ] && continue
  bucket="${path%%/*}"; name="${path#*/}"
  tmp="/tmp/braidscope-object"
  if curl -fsS -o "$tmp" -H "Authorization: Bearer $SUPABASE_SERVICE_KEY" "$SUPABASE_URL/storage/v1/object/$bucket/$name"; then
    curl -fsSk -o /dev/null -X POST -H "Host: $DOMAIN_VALUE" -H "Authorization: Bearer $SERVICE_ROLE_KEY" -H "x-upsert: true" \
      --data-binary @"$tmp" "$LOCAL_BASE/storage/v1/object/$bucket/$name" \
      || echo "   could not upload $path (re-run later)"
    count=$((count + 1))
  else
    echo "   could not download $path"
  fi
done <<< "$objects"
rm -f /tmp/braidscope-object
say "Copied $count file(s)"

say "Restarting the app so caches reload"
docker compose restart py >/dev/null

say "Done. Sign in with the same email and password you used on the cloud."
echo "   Two-factor codes keep working."
echo "   Connector tokens were copied, but they are encrypted with the cloud key."
echo "   To keep them: put the cloud CONNECTOR_TOKEN_ENCRYPTION_KEY into .env and"
echo "   run: docker compose up -d. Otherwise reconnect sources from Integrations."
