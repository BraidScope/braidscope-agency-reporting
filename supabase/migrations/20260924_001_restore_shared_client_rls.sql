-- Restore shared-client read/write access in RLS.
--
-- Symptom: a user with a client_memberships row (e.g. role "owner" on a client
-- owned by another agency) is told "Client workspace is not accessible" on the
-- Integrations page and sees 0 data sources, even though
--   effective_client_role(client, user) = 'owner'  and
--   has_client_access(client, 'viewer')  = true.
-- `select ... from public.clients where id = <shared client>` returns 0 rows
-- for that user, i.e. the live clients_select_members policy does not contain
-- the has_client_access() clause that 20260919_003_client_sharing.sql (section
-- 3) defines - most likely because an earlier migration that recreates the
-- members-only policy (e.g. 20260604_000_init.sql) was re-run afterwards.
--
-- This file re-applies ONLY section 3 of 20260919_003_client_sharing.sql,
-- verbatim. It is idempotent (drop policy if exists + create policy), so it is
-- safe to run whether or not the policies are already correct. Nothing else is
-- changed: clients_insert_editor and clients_delete_admin are deliberately left
-- alone (deleting a client stays restricted to its owning organization).
--
-- To undo, re-run the members-only policies from 20260604_000_init.sql /
-- 20260811_001_enforce_workspace_roles.sql / 20260918_001_client_workspace_settings.sql.

drop policy if exists "clients_select_members" on public.clients;
create policy "clients_select_members" on public.clients
  for select using (public.is_org_member(organization_id) or public.has_client_access(id, 'viewer'));

drop policy if exists "clients_update_editor" on public.clients;
create policy "clients_update_editor" on public.clients
  for update using (public.is_org_editor(organization_id) or public.has_client_access(id, 'editor'))
  with check (public.is_org_editor(organization_id) or public.has_client_access(id, 'editor'));

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
