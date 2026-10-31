-- Settings page: real team roster + per-user notification preferences.

------------------------------------------------------------
-- 1. Per-member notification preferences
-- Preferences are per user *per organization*, so memberships is the
-- natural home for them rather than a global profile row.
------------------------------------------------------------

alter table public.memberships
  add column if not exists notification_prefs jsonb not null default '{}'::jsonb;

-- Members may update their own preference blob. The existing policies only
-- covered select/insert/delete, so updates were previously blocked entirely.
drop policy if exists "membership_update_self" on public.memberships;
create policy "membership_update_self" on public.memberships
  for update using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- Admins may change any member's role inside their own organization.
drop policy if exists "membership_update_admin" on public.memberships;
create policy "membership_update_admin" on public.memberships
  for update using (public.is_org_admin(organization_id))
  with check (public.is_org_admin(organization_id));

------------------------------------------------------------
-- 2. Team roster
-- auth.users is not readable from the browser, so expose exactly the columns
-- the Settings page needs through a security-definer function that re-checks
-- membership before returning anything.
------------------------------------------------------------

create or replace function public.org_members(org_id uuid)
returns table (
  user_id         uuid,
  email           text,
  display_name    text,
  role            public.org_role,
  joined_at       timestamptz,
  last_sign_in_at timestamptz
)
language sql
security definer
stable
set search_path = public
as $$
  select
    m.user_id,
    u.email::text,
    coalesce(
      nullif(u.raw_user_meta_data->>'full_name', ''),
      split_part(u.email::text, '@', 1)
    ) as display_name,
    m.role,
    m.created_at,
    u.last_sign_in_at
  from public.memberships m
  join auth.users u on u.id = m.user_id
  where m.organization_id = org_id
    and public.is_org_member(org_id)
  order by m.created_at asc;
$$;

revoke all on function public.org_members(uuid) from public;
grant execute on function public.org_members(uuid) to authenticated;

------------------------------------------------------------
-- 3. Workspace counters
-- Dashboards/reports/connectors live one join away from the organization, so
-- roll them up in one round trip instead of three client-side count queries.
------------------------------------------------------------

create or replace function public.org_counts(org_id uuid)
returns table (
  clients     bigint,
  dashboards  bigint,
  reports     bigint,
  connectors  bigint
)
language sql
security definer
stable
set search_path = public
as $$
  select
    (select count(*) from public.clients c
      where c.organization_id = org_id),
    (select count(*) from public.pages p
      join public.clients c on c.id = p.client_id
      where c.organization_id = org_id and p.type = 'dashboard'),
    (select count(*) from public.pages p
      join public.clients c on c.id = p.client_id
      where c.organization_id = org_id and p.type = 'report'),
    (select count(*) from public.data_sources d
      join public.clients c on c.id = d.client_id
      where c.organization_id = org_id and d.status in ('connected','syncing'))
  where public.is_org_member(org_id);
$$;

revoke all on function public.org_counts(uuid) from public;
grant execute on function public.org_counts(uuid) to authenticated;
