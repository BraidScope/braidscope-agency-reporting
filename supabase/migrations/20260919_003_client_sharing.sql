-- Shared Client Workspace Access (Team Access & Permission Architecture).
--
-- Adds a second, independent access grain alongside the existing org-wide
-- `memberships` table: a specific user can be granted a role on ONE client
-- workspace without joining that client's owning organization at all. This
-- migration is purely additive - every existing owned-client RLS policy
-- keeps its original clause verbatim, with an `OR has_client_access(...)`
-- added alongside it, so nothing about today's owned-client behavior
-- changes for any existing account.

------------------------------------------------------------
-- 1. Tables
------------------------------------------------------------

create table if not exists public.client_memberships (
  id          uuid primary key default gen_random_uuid(),
  client_id   uuid not null references public.clients(id) on delete cascade,
  user_id     uuid not null references auth.users(id) on delete cascade,
  role        public.org_role not null,
  invited_by  uuid references auth.users(id) on delete set null,
  created_at  timestamptz not null default now(),
  unique (client_id, user_id)
);
create index if not exists client_memberships_client_idx on public.client_memberships(client_id);
create index if not exists client_memberships_user_idx on public.client_memberships(user_id);

create table if not exists public.client_invitations (
  id              uuid primary key default gen_random_uuid(),
  client_id       uuid not null references public.clients(id) on delete cascade,
  organization_id uuid not null references public.organizations(id) on delete cascade,
  email           text not null,
  role            public.org_role not null,
  -- Two concatenated UUIDs (256 bits) rather than gen_random_bytes(), which
  -- needs the pgcrypto extension - gen_random_uuid() is natively available
  -- on modern Postgres with no extension dependency to verify.
  token           text not null unique default (replace(gen_random_uuid()::text, '-', '') || replace(gen_random_uuid()::text, '-', '')),
  status          text not null default 'pending' check (status in ('pending','accepted','expired','cancelled')),
  invited_by      uuid references auth.users(id) on delete set null,
  expires_at      timestamptz not null default (now() + interval '7 days'),
  accepted_at     timestamptz,
  created_at      timestamptz not null default now()
);
create index if not exists client_invitations_client_idx on public.client_invitations(client_id);
create index if not exists client_invitations_token_idx on public.client_invitations(token);
create index if not exists client_invitations_email_idx on public.client_invitations(lower(email));

------------------------------------------------------------
-- 2. Effective role resolution
------------------------------------------------------------

-- A user's role on a client is their OWN organization's role when they own
-- the client (via their own org membership - unchanged, existing behavior),
-- otherwise their client_memberships row if one exists (shared access),
-- otherwise no access.
create or replace function public.effective_client_role(p_client_id uuid, p_user_id uuid)
returns public.org_role
language sql
security definer
stable
set search_path = public
as $$
  select coalesce(
    (
      select m.role
      from public.clients c
      join public.memberships m
        on m.organization_id = c.organization_id and m.user_id = p_user_id
      where c.id = p_client_id
    ),
    (
      select cm.role
      from public.client_memberships cm
      where cm.client_id = p_client_id and cm.user_id = p_user_id
    )
  );
$$;

create or replace function public.has_client_access(p_client_id uuid, p_min_role public.org_role default 'viewer')
returns boolean
language sql
security definer
stable
set search_path = public
as $$
  select case public.effective_client_role(p_client_id, auth.uid())
    when 'owner'  then true
    when 'admin'  then p_min_role in ('admin','editor','viewer')
    when 'editor' then p_min_role in ('editor','viewer')
    when 'viewer' then p_min_role = 'viewer'
    else false
  end;
$$;

------------------------------------------------------------
-- 3. Extend existing clients/data_sources/pages RLS (additive OR, not a
-- replacement - every original clause is preserved verbatim)
------------------------------------------------------------

drop policy if exists "clients_select_members" on public.clients;
create policy "clients_select_members" on public.clients
  for select using (public.is_org_member(organization_id) or public.has_client_access(id, 'viewer'));

drop policy if exists "clients_update_editor" on public.clients;
create policy "clients_update_editor" on public.clients
  for update using (public.is_org_editor(organization_id) or public.has_client_access(id, 'editor'))
  with check (public.is_org_editor(organization_id) or public.has_client_access(id, 'editor'));

-- clients_insert_editor (creating a brand-new client) and clients_delete_admin
-- (deleting one) are intentionally left untouched: creating a client is an
-- org-level action, and deletion must always stay restricted to the
-- client's true owning organization - an invited "Owner" role must never
-- be able to delete another agency's client. See TEAM_ACCESS_ARCHITECTURE_PLAN.md.

drop policy if exists "ds_select_members" on public.data_sources;
create policy "ds_select_members" on public.data_sources
  for select using (
    exists (select 1 from public.clients c
            where c.id = data_sources.client_id
              and (public.is_org_member(c.organization_id) or public.has_client_access(c.id, 'viewer')))
  );
drop policy if exists "ds_write_members" on public.data_sources;
create policy "ds_write_members" on public.data_sources
  for all using (
    exists (select 1 from public.clients c
            where c.id = data_sources.client_id
              and (public.is_org_editor(c.organization_id) or public.has_client_access(c.id, 'editor')))
  )
  with check (
    exists (select 1 from public.clients c
            where c.id = data_sources.client_id
              and (public.is_org_editor(c.organization_id) or public.has_client_access(c.id, 'editor')))
  );

drop policy if exists "pages_select_members" on public.pages;
create policy "pages_select_members" on public.pages
  for select using (
    exists (select 1 from public.clients c
            where c.id = pages.client_id
              and (public.is_org_member(c.organization_id) or public.has_client_access(c.id, 'viewer')))
  );
drop policy if exists "pages_write_members" on public.pages;
create policy "pages_write_members" on public.pages
  for all using (
    exists (select 1 from public.clients c
            where c.id = pages.client_id
              and (public.is_org_editor(c.organization_id) or public.has_client_access(c.id, 'editor')))
  )
  with check (
    exists (select 1 from public.clients c
            where c.id = pages.client_id
              and (public.is_org_editor(c.organization_id) or public.has_client_access(c.id, 'editor')))
  );

------------------------------------------------------------
-- 4. RLS on the two new tables
------------------------------------------------------------

alter table public.client_memberships enable row level security;
alter table public.client_invitations enable row level security;

-- A user can always see their own shared-access row; managing the roster
-- (insert/update/delete, including seeing everyone else's row) requires
-- being admin+ of the owning org OR admin+ via client_memberships itself
-- (an invited Admin/Owner can manage this client's team, per the spec's own
-- per-role capability list - "Team" is listed under both Owner and Admin).
create policy "client_memberships_select" on public.client_memberships
  for select using (
    user_id = auth.uid()
    or exists (select 1 from public.clients c where c.id = client_memberships.client_id and public.is_org_admin(c.organization_id))
    or public.has_client_access(client_id, 'admin')
  );

create policy "client_memberships_manage" on public.client_memberships
  for all using (
    exists (select 1 from public.clients c where c.id = client_memberships.client_id and public.is_org_admin(c.organization_id))
    or public.has_client_access(client_id, 'admin')
  )
  with check (
    exists (select 1 from public.clients c where c.id = client_memberships.client_id and public.is_org_admin(c.organization_id))
    or public.has_client_access(client_id, 'admin')
  );

-- Invitations are never readable/listable via direct PostgREST select by
-- token (a token lookup always goes through a backend endpoint using the
-- service role) - this policy only ever lets an owning-org/client admin see
-- the invitations THEY manage, for the roster UI.
create policy "client_invitations_select" on public.client_invitations
  for select using (
    exists (select 1 from public.clients c where c.id = client_invitations.client_id and public.is_org_admin(c.organization_id))
    or public.has_client_access(client_id, 'admin')
  );

create policy "client_invitations_manage" on public.client_invitations
  for all using (
    exists (select 1 from public.clients c where c.id = client_invitations.client_id and public.is_org_admin(c.organization_id))
    or public.has_client_access(client_id, 'admin')
  )
  with check (
    exists (select 1 from public.clients c where c.id = client_invitations.client_id and public.is_org_admin(c.organization_id))
    or public.has_client_access(client_id, 'admin')
  );

------------------------------------------------------------
-- 5. Client-aware team roster (owning org's own members UNION shared access)
------------------------------------------------------------

create or replace function public.client_team_members(p_client_id uuid)
returns table (
  user_id         uuid,
  email           text,
  display_name    text,
  role            public.org_role,
  access          text,
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
    coalesce(nullif(u.raw_user_meta_data->>'full_name', ''), split_part(u.email::text, '@', 1)) as display_name,
    m.role,
    'owner'::text as access,
    m.created_at as joined_at,
    u.last_sign_in_at
  from public.memberships m
  join auth.users u on u.id = m.user_id
  join public.clients c on c.organization_id = m.organization_id
  where c.id = p_client_id
    and public.has_client_access(p_client_id, 'viewer')

  union all

  select
    cm.user_id,
    u.email::text,
    coalesce(nullif(u.raw_user_meta_data->>'full_name', ''), split_part(u.email::text, '@', 1)) as display_name,
    cm.role,
    'shared'::text as access,
    cm.created_at as joined_at,
    u.last_sign_in_at
  from public.client_memberships cm
  join auth.users u on u.id = cm.user_id
  where cm.client_id = p_client_id
    and public.has_client_access(p_client_id, 'viewer')

  order by access asc, joined_at asc;
$$;

revoke all on function public.client_team_members(uuid) from public;
grant execute on function public.client_team_members(uuid) to authenticated;
