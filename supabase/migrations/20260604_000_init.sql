-- Braidscope Phase 2 — initial schema
-- Multi-tenant: every row except auth.users belongs to an organization.

------------------------------------------------------------
-- 1. Tables
------------------------------------------------------------

create table if not exists public.organizations (
  id            uuid primary key default gen_random_uuid(),
  name          text not null,
  slug          text unique not null,
  accent_color  text default '#111111',
  logo_url      text,
  created_at    timestamptz default now()
);

do $$ begin
  create type public.org_role as enum ('owner','admin','editor','viewer');
exception when duplicate_object then null; end $$;

create table if not exists public.memberships (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid references auth.users(id) on delete cascade not null,
  organization_id uuid references public.organizations(id) on delete cascade not null,
  role            public.org_role not null default 'owner',
  created_at      timestamptz default now(),
  unique (user_id, organization_id)
);
create index if not exists memberships_user_idx on public.memberships(user_id);
create index if not exists memberships_org_idx  on public.memberships(organization_id);

create table if not exists public.clients (
  id              uuid primary key default gen_random_uuid(),
  organization_id uuid references public.organizations(id) on delete cascade not null,
  name            text not null,
  logo_url        text,
  created_at      timestamptz default now()
);
create index if not exists clients_org_idx on public.clients(organization_id);

create table if not exists public.data_sources (
  id           uuid primary key default gen_random_uuid(),
  client_id    uuid references public.clients(id) on delete cascade not null,
  type         text not null,                  -- 'google_ads','meta_ads','shopify',...
  status       text not null default 'connected',
  config       jsonb not null default '{}'::jsonb,
  last_sync_at timestamptz,
  created_at   timestamptz default now()
);
create index if not exists data_sources_client_idx on public.data_sources(client_id);

create table if not exists public.pages (
  id         uuid primary key default gen_random_uuid(),
  client_id  uuid references public.clients(id) on delete cascade not null,
  name       text not null,
  type       text not null default 'dashboard', -- 'dashboard' | 'report'
  config     jsonb not null default '{}'::jsonb,
  created_at timestamptz default now()
);
create index if not exists pages_client_idx on public.pages(client_id);

------------------------------------------------------------
-- 2. RLS
------------------------------------------------------------

alter table public.organizations enable row level security;
alter table public.memberships   enable row level security;
alter table public.clients       enable row level security;
alter table public.data_sources  enable row level security;
alter table public.pages         enable row level security;

-- Helper: is current user a member of this org?
create or replace function public.is_org_member(org_id uuid)
returns boolean language sql security definer stable as $$
  select exists (
    select 1 from public.memberships
    where organization_id = org_id and user_id = auth.uid()
  );
$$;

-- Helper: is current user owner/admin of this org?
create or replace function public.is_org_admin(org_id uuid)
returns boolean language sql security definer stable as $$
  select exists (
    select 1 from public.memberships
    where organization_id = org_id
      and user_id = auth.uid()
      and role in ('owner','admin')
  );
$$;

-- organizations
drop policy if exists "org_select_members" on public.organizations;
create policy "org_select_members" on public.organizations
  for select using (public.is_org_member(id));

drop policy if exists "org_update_admins" on public.organizations;
create policy "org_update_admins" on public.organizations
  for update using (public.is_org_admin(id));

-- memberships
drop policy if exists "membership_select_self" on public.memberships;
create policy "membership_select_self" on public.memberships
  for select using (user_id = auth.uid() or public.is_org_member(organization_id));

drop policy if exists "membership_insert_admin" on public.memberships;
create policy "membership_insert_admin" on public.memberships
  for insert with check (public.is_org_admin(organization_id));

drop policy if exists "membership_delete_admin" on public.memberships;
create policy "membership_delete_admin" on public.memberships
  for delete using (public.is_org_admin(organization_id));

-- clients
drop policy if exists "clients_select_members" on public.clients;
create policy "clients_select_members" on public.clients
  for select using (public.is_org_member(organization_id));

drop policy if exists "clients_write_members" on public.clients;
create policy "clients_write_members" on public.clients
  for all using (public.is_org_member(organization_id))
  with check (public.is_org_member(organization_id));

-- data_sources: piggyback via parent client
drop policy if exists "ds_select_members" on public.data_sources;
create policy "ds_select_members" on public.data_sources
  for select using (
    exists (select 1 from public.clients c
            where c.id = data_sources.client_id
              and public.is_org_member(c.organization_id))
  );
drop policy if exists "ds_write_members" on public.data_sources;
create policy "ds_write_members" on public.data_sources
  for all using (
    exists (select 1 from public.clients c
            where c.id = data_sources.client_id
              and public.is_org_member(c.organization_id))
  )
  with check (
    exists (select 1 from public.clients c
            where c.id = data_sources.client_id
              and public.is_org_member(c.organization_id))
  );

-- pages: same pattern
drop policy if exists "pages_select_members" on public.pages;
create policy "pages_select_members" on public.pages
  for select using (
    exists (select 1 from public.clients c
            where c.id = pages.client_id
              and public.is_org_member(c.organization_id))
  );
drop policy if exists "pages_write_members" on public.pages;
create policy "pages_write_members" on public.pages
  for all using (
    exists (select 1 from public.clients c
            where c.id = pages.client_id
              and public.is_org_member(c.organization_id))
  )
  with check (
    exists (select 1 from public.clients c
            where c.id = pages.client_id
              and public.is_org_member(c.organization_id))
  );

------------------------------------------------------------
-- 3. Auto-create organization on signup
-- When a user signs up, drop an org + owner membership for them.
-- agency_name + accent_color come from raw_user_meta_data.
------------------------------------------------------------

create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
declare
  org_name   text;
  org_accent text;
  org_slug   text;
  new_org_id uuid;
begin
  org_name   := coalesce(new.raw_user_meta_data->>'agency_name',
                         split_part(new.email, '@', 1));
  org_accent := coalesce(new.raw_user_meta_data->>'accent_color', '#111111');
  org_slug   := regexp_replace(
                  lower(org_name) || '-' || substr(new.id::text, 1, 8),
                  '[^a-z0-9-]', '-', 'g');

  insert into public.organizations (name, slug, accent_color)
  values (org_name, org_slug, org_accent)
  returning id into new_org_id;

  insert into public.memberships (user_id, organization_id, role)
  values (new.id, new_org_id, 'owner');

  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
