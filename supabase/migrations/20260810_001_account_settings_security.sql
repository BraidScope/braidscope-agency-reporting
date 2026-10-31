create table if not exists public.workspace_invitations (
  id uuid primary key default gen_random_uuid(), organization_id uuid not null references public.organizations(id) on delete cascade,
  email text not null, role public.org_role not null, invited_by uuid references auth.users(id) on delete set null,
  invited_user_id uuid references auth.users(id) on delete set null, status text not null default 'pending' check (status in ('pending','accepted','revoked','expired')),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(organization_id,email)
);
create table if not exists public.user_session_activity (
  id uuid primary key default gen_random_uuid(), user_id uuid not null references auth.users(id) on delete cascade,
  session_id text not null, organization_id uuid references public.organizations(id) on delete set null, user_agent text, ip_address text,
  created_at timestamptz not null default now(), last_seen_at timestamptz not null default now(), unique(user_id,session_id)
);
create table if not exists public.user_notifications (
  id uuid primary key default gen_random_uuid(), user_id uuid not null references auth.users(id) on delete cascade,
  organization_id uuid references public.organizations(id) on delete cascade, event_type text not null, title text not null, body text, link text,
  created_at timestamptz not null default now(), read_at timestamptz
);
create index if not exists user_notifications_user_created_idx on public.user_notifications(user_id,created_at desc);
create index if not exists user_session_activity_user_seen_idx on public.user_session_activity(user_id,last_seen_at desc);
alter table public.workspace_invitations enable row level security;
alter table public.user_session_activity enable row level security;
alter table public.user_notifications enable row level security;
drop policy if exists "session_activity_read_self" on public.user_session_activity;
create policy "session_activity_read_self" on public.user_session_activity for select using (user_id=auth.uid());
drop policy if exists "notifications_read_self" on public.user_notifications;
create policy "notifications_read_self" on public.user_notifications for select using (user_id=auth.uid());
drop policy if exists "notifications_update_self" on public.user_notifications;
create policy "notifications_update_self" on public.user_notifications for update using (user_id=auth.uid()) with check (user_id=auth.uid());
