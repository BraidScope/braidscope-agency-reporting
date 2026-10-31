-- branding_published notification, generated at the database layer.
--
-- Branding.jsx currently saves straight from the browser to
-- organizations.update() (see web/src/lib/auth.js updateOrganizationBranding).
-- Per notification_improvements.md, a persistent notification must only be
-- created after a *confirmed, meaningful* backend save — never from the
-- frontend click handler alone. Moving the whole publish flow behind a new
-- Python/RPC endpoint just to get that guarantee would be a much bigger,
-- riskier change for this phase, and Workspace Settings' own "Save
-- workspace" already writes into this same branding_config jsonb column
-- (only to stash currency/industry — see updateWorkspace in
-- web/src/lib/settings.js) and must NOT trigger a "Branding published"
-- notification.
--
-- A trigger gets both properties for free, with zero frontend changes: it
-- only fires on a real, committed UPDATE (backend-confirmed by definition),
-- and it excludes the currency/industry keys before comparing old vs new so
-- a plain workspace-details save is correctly treated as not meaningful.

create or replace function public.notify_branding_published()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  actor uuid;
  meaningful boolean;
begin
  actor := auth.uid();
  if actor is null then
    -- No attributable session (e.g. a service-role/administrative update) —
    -- nothing to notify.
    return new;
  end if;

  meaningful :=
    (coalesce(old.branding_config, '{}'::jsonb) - 'currency' - 'industry')
      is distinct from
    (coalesce(new.branding_config, '{}'::jsonb) - 'currency' - 'industry')
    or old.accent_color is distinct from new.accent_color
    or old.logo_url is distinct from new.logo_url;

  if not meaningful then
    return new;
  end if;

  begin
    insert into public.user_notifications
      (user_id, organization_id, category, event_type, title, body, link, action_label, priority)
    values
      (actor, new.id, 'branding', 'branding_published',
       'Branding published',
       'Your Braidscope branding changes are now live.',
       '/branding', 'View Branding', 'normal');
  exception when others then
    -- A notification problem must never roll back the actual branding save.
    raise warning 'notify_branding_published: could not insert notification: %', sqlerrm;
  end;

  return new;
end;
$$;

drop trigger if exists organizations_notify_branding_published on public.organizations;
create trigger organizations_notify_branding_published
  after update of branding_config, accent_color, logo_url on public.organizations
  for each row execute function public.notify_branding_published();
