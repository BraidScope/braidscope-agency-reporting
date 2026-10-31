-- Client Workspace architecture correction.
--
-- Settings -> Workspace currently reads/writes organizations.name and
-- organizations.branding_config.{currency,industry} - the whole agency
-- account - instead of the selected client. Give each client its own
-- currency/industry storage so Workspace settings can become genuinely
-- client-scoped, and backfill from the organization's current (wrong-entity)
-- values so nothing appears to reset for existing workspaces.

alter table public.clients
  add column if not exists currency text not null default 'usd',
  add column if not exists industry text not null default 'marketing';

update public.clients c
set
  currency = coalesce(o.branding_config->>'currency', c.currency),
  industry = coalesce(o.branding_config->>'industry', c.industry)
from public.organizations o
where o.id = c.organization_id;

-- Danger Zone is being repurposed to delete only the selected client (not
-- the whole agency account). clients_write_members ("for all") currently
-- grants delete to any editor+, which is too broad for a destructive
-- per-client delete - split it into insert/update (editor+, unchanged
-- behavior) and a separate, stricter delete policy (admin+/owner only,
-- matching the threshold already used for team invites via is_org_admin).
drop policy if exists "clients_write_members" on public.clients;

drop policy if exists "clients_insert_editor" on public.clients;
create policy "clients_insert_editor" on public.clients
  for insert with check (public.is_org_editor(organization_id));

drop policy if exists "clients_update_editor" on public.clients;
create policy "clients_update_editor" on public.clients
  for update using (public.is_org_editor(organization_id))
  with check (public.is_org_editor(organization_id));

drop policy if exists "clients_delete_admin" on public.clients;
create policy "clients_delete_admin" on public.clients
  for delete using (public.is_org_admin(organization_id));

-- Per-client usage counts (dashboards/reports/connectors), mirroring the
-- existing org_counts() shape but scoped to one client instead of joining
-- every client under an organization.
create or replace function public.client_counts(client_id uuid)
returns table (
  dashboards bigint,
  reports    bigint,
  connectors bigint
)
language sql
security definer
stable
set search_path = public
as $$
  select
    (select count(*) from public.pages p
      where p.client_id = client_counts.client_id and p.type = 'dashboard'),
    (select count(*) from public.pages p
      where p.client_id = client_counts.client_id and p.type = 'report'),
    (select count(*) from public.data_sources d
      where d.client_id = client_counts.client_id and d.status = 'connected');
$$;

revoke all on function public.client_counts(uuid) from public;
grant execute on function public.client_counts(uuid) to authenticated;
