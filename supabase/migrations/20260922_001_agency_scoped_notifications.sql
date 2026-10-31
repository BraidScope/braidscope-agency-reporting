-- Keep agency notifications in the shared user_notifications table while
-- making their scope explicit and queryable.
--
-- This is intentionally a standalone, idempotent migration. The repository
-- contains conflicting 20260917 migration versions, so deployments must not
-- replay that batch to obtain this schema change. Apply only this migration
-- (or its equivalent SQL) and reload PostgREST's schema cache if the column
-- was added outside the migration runner.
alter table public.user_notifications
  add column if not exists scope text not null default 'client';

alter table public.user_notifications
  drop constraint if exists user_notifications_scope_check;

alter table public.user_notifications
  add constraint user_notifications_scope_check
  check (scope in ('client', 'agency'));

create index if not exists user_notifications_agency_unread_idx
  on public.user_notifications (organization_id, scope, client_id, read_at, created_at desc);
