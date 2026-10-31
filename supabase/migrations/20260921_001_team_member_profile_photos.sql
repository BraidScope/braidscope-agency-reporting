-- Include Supabase Auth profile photos in the client Team response.
drop function if exists public.client_team_members(uuid);

create function public.client_team_members(p_client_id uuid)
returns table (
  user_id uuid,
  email text,
  display_name text,
  avatar_url text,
  role public.org_role,
  access text,
  joined_at timestamptz,
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
    nullif(u.raw_user_meta_data->>'avatar_url', '') as avatar_url,
    m.role,
    'owner'::text as access,
    m.created_at as joined_at,
    u.last_sign_in_at
  from public.memberships m
  join auth.users u on u.id = m.user_id
  join public.clients c on c.organization_id = m.organization_id
  where c.id = p_client_id

  union all

  select
    cm.user_id,
    u.email::text,
    coalesce(nullif(u.raw_user_meta_data->>'full_name', ''), split_part(u.email::text, '@', 1)) as display_name,
    nullif(u.raw_user_meta_data->>'avatar_url', '') as avatar_url,
    cm.role,
    'shared'::text as access,
    cm.created_at as joined_at,
    u.last_sign_in_at
  from public.client_memberships cm
  join auth.users u on u.id = cm.user_id
  where cm.client_id = p_client_id

  order by access asc, joined_at asc;
$$;

revoke all on function public.client_team_members(uuid) from public;
grant execute on function public.client_team_members(uuid) to authenticated;
