-- Discovered while implementing SHARED_CLIENT_CONTEXT_FIX.md: organizations
-- RLS only ever allowed a user to SELECT their OWN organization
-- (org_select_members: is_org_member(id)). A shared client viewer therefore
-- could never read the owning agency's name/logo/accent/branding_config at
-- all - the exact identity the header, switcher and branding resolution
-- need to correctly represent "whose workspace is this" for a shared
-- client. Extends the existing policy (does not replace the owner-org path)
-- the same way clients_select_members/clients_update_editor were already
-- extended in 20260919_003_client_sharing.sql.
drop policy if exists "org_select_members" on public.organizations;
create policy "org_select_members" on public.organizations
  for select using (
    public.is_org_member(id)
    or exists (
      select 1 from public.clients c
      where c.organization_id = organizations.id
        and public.has_client_access(c.id, 'viewer')
    )
  );
