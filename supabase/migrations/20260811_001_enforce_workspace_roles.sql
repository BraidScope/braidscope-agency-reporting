-- Enforce read-only viewers at the database boundary.
create or replace function public.is_org_editor(org_id uuid)
returns boolean language sql security definer stable as $$
  select exists (
    select 1 from public.memberships
    where organization_id = org_id
      and user_id = auth.uid()
      and role in ('owner','admin','editor')
  );
$$;

drop policy if exists "clients_write_members" on public.clients;
create policy "clients_write_members" on public.clients
  for all using (public.is_org_editor(organization_id))
  with check (public.is_org_editor(organization_id));

drop policy if exists "ds_write_members" on public.data_sources;
create policy "ds_write_members" on public.data_sources
  for all using (
    exists (select 1 from public.clients c
            where c.id = data_sources.client_id
              and public.is_org_editor(c.organization_id))
  )
  with check (
    exists (select 1 from public.clients c
            where c.id = data_sources.client_id
              and public.is_org_editor(c.organization_id))
  );

drop policy if exists "pages_write_members" on public.pages;
create policy "pages_write_members" on public.pages
  for all using (
    exists (select 1 from public.clients c
            where c.id = pages.client_id
              and public.is_org_editor(c.organization_id))
  )
  with check (
    exists (select 1 from public.clients c
            where c.id = pages.client_id
              and public.is_org_editor(c.organization_id))
  );
