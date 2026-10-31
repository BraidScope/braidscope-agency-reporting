-- Restore the client role RPC required by backend authorization and
-- notification visibility checks.
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
        on m.organization_id = c.organization_id
       and m.user_id = p_user_id
      where c.id = p_client_id
    ),
    (
      select cm.role
      from public.client_memberships cm
      where cm.client_id = p_client_id
        and cm.user_id = p_user_id
    )
  );
$$;

revoke all on function public.effective_client_role(uuid, uuid) from public;
grant execute on function public.effective_client_role(uuid, uuid) to service_role;
grant execute on function public.effective_client_role(uuid, uuid) to authenticated;
