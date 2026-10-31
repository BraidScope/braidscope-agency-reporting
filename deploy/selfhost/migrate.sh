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
for file in $(ls /migrations/*.sql | sort); do
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
