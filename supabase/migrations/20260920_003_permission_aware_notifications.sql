-- Permission-aware notification system.
-- Preferences are scoped to the exact recipient + client workspace, including
-- shared client members who do not belong to the owning agency organization.

create table if not exists public.notification_preferences (
  id          uuid primary key default gen_random_uuid(),
  client_id   uuid not null references public.clients(id) on delete cascade,
  user_id     uuid not null references auth.users(id) on delete cascade,
  preferences jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (client_id, user_id)
);

create index if not exists notification_preferences_user_client_idx
  on public.notification_preferences(user_id, client_id);

alter table public.notification_preferences enable row level security;

drop policy if exists "notification_preferences_read_self" on public.notification_preferences;
create policy "notification_preferences_read_self"
  on public.notification_preferences
  for select
  using (user_id = auth.uid() and public.has_client_access(client_id, 'viewer'));

drop policy if exists "notification_preferences_admin_write_self" on public.notification_preferences;
create policy "notification_preferences_admin_write_self"
  on public.notification_preferences
  for all
  using (user_id = auth.uid() and public.has_client_access(client_id, 'admin'))
  with check (user_id = auth.uid() and public.has_client_access(client_id, 'admin'));

alter table public.user_notifications
  add column if not exists module text,
  add column if not exists required_permission text;

comment on table public.notification_preferences is
  'Per client workspace + agency user notification preferences. Writes are Admin+ only; reads are visible to any member with client access.';

comment on column public.user_notifications.required_permission is
  'Permission required when the notification was created, e.g. integrations.view or branding.edit.';
