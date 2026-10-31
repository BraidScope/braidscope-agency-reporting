-- Recovery path for an authenticated user with zero memberships.
--
-- Every normal signup gets an organization for free via handle_new_user(),
-- so an authenticated session with no membership at all was never expected
-- to happen — until Settings' Danger Zone started actually deleting
-- workspaces (see 20260917_001). Deleting a workspace intentionally leaves
-- the person's auth.users row alone (so they can still sign back in with the
-- same credentials), but it cascades their only membership away with it,
-- landing them in that same "authenticated, zero orgs" gap on next login.
--
-- ensure_own_organization() closes that gap: it's the signup trigger's own
-- provisioning logic, callable on demand instead of only at signup. Calling
-- it for a user who already has a membership is a safe no-op that just
-- returns their existing organization.

create or replace function public.ensure_own_organization()
returns table (
  id              uuid,
  name            text,
  slug            text,
  accent_color    text,
  logo_url        text,
  branding_config jsonb,
  role            public.org_role
)
language plpgsql
security definer
set search_path = public
as $$
declare
  current_email text;
  current_meta  jsonb;
  org_name      text;
  org_accent    text;
  org_slug      text;
  new_org_id    uuid;
begin
  if auth.uid() is null then
    raise exception 'Not signed in';
  end if;

  -- Already belongs somewhere — hand back that membership's organization
  -- rather than ever creating a second one for the same user.
  return query
    select o.id, o.name, o.slug, o.accent_color, o.logo_url, o.branding_config, m.role
    from public.memberships m
    join public.organizations o on o.id = m.organization_id
    where m.user_id = auth.uid()
    order by m.created_at asc
    limit 1;
  if found then
    return;
  end if;

  select u.email, u.raw_user_meta_data into current_email, current_meta
  from auth.users u where u.id = auth.uid();

  org_name   := coalesce(nullif(current_meta->>'agency_name', ''), split_part(current_email, '@', 1));
  org_accent := coalesce(nullif(current_meta->>'accent_color', ''), '#111111');
  org_slug   := regexp_replace(
                  lower(org_name) || '-' || substr(auth.uid()::text, 1, 8),
                  '[^a-z0-9-]', '-', 'g');

  insert into public.organizations (name, slug, accent_color)
  values (org_name, org_slug, org_accent)
  returning organizations.id into new_org_id;

  insert into public.memberships (user_id, organization_id, role)
  values (auth.uid(), new_org_id, 'owner');

  return query
    select o.id, o.name, o.slug, o.accent_color, o.logo_url, o.branding_config, 'owner'::public.org_role
    from public.organizations o where o.id = new_org_id;
end;
$$;

revoke all on function public.ensure_own_organization() from public;
grant execute on function public.ensure_own_organization() to authenticated;
