#!/usr/bin/env bash
# Applies every file in /migrations in name order, once. Tracks what ran in
# public.braidscope_migrations so a second boot is a no-op and an upgrade only
# runs the new files. Each file runs inside one transaction.
set -euo pipefail

psql -v ON_ERROR_STOP=1 -q <<'SQL'
create table if not exists public.braidscope_migrations (
  name       text primary key,
  applied_at timestamptz not null default now()
);
SQL

applied=0
# Order by the timestamp before the first "_", compared as text, like the
# Supabase CLI. Plain sort would run 20260604000100_connector_credentials
# before 20260604_000_init ("0" < "_"); sort -V would run it last (14 digits
# compare as a bigger number than 8).
ordered() {
  for f in /migrations/*.sql; do
    n="$(basename "$f")"
    printf '%s %s\n' "${n%%_*}" "$f"
  done | LC_ALL=C sort -k1,1 -k2,2 | cut -d' ' -f2-
}
for file in $(ordered); do
  name="$(basename "$file")"
  already="$(psql -tAq -c "select 1 from public.braidscope_migrations where name = '$name'")"
  if [ "$already" = "1" ]; then
    continue
  fi
  echo "migrate: applying $name"
  psql -v ON_ERROR_STOP=1 -q --single-transaction -f "$file"
  psql -v ON_ERROR_STOP=1 -q -c "insert into public.braidscope_migrations(name) values ('$name')"
  applied=$((applied + 1))
done

echo "migrate: done, $applied new migration(s) applied"
