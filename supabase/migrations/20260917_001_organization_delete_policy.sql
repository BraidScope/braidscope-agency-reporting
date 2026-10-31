-- Workspace deletion, at the database layer.
--
-- Settings' Danger Zone requires typing the exact workspace name and is only
-- shown to the workspace's owner, but no delete policy existed on
-- organizations at all: RLS silently blocked every delete (0 rows affected,
-- no error), so deleteWorkspace() always reported success without the
-- workspace, or anything inside it, ever actually being removed.
--
-- Every child table (memberships, clients, workspace_invitations,
-- user_notifications, and everything hanging off clients) is already
-- `references public.organizations(id) on delete cascade`, so a real delete
-- here cleans up the whole workspace in one statement.

create or replace function public.is_org_owner(org_id uuid)
returns boolean language sql security definer stable as $$
  select exists (
    select 1 from public.memberships
    where organization_id = org_id
      and user_id = auth.uid()
      and role = 'owner'
  );
$$;

drop policy if exists "org_delete_owner" on public.organizations;
create policy "org_delete_owner" on public.organizations
  for delete using (public.is_org_owner(id));
