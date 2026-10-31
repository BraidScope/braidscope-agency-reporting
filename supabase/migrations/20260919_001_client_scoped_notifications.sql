-- Client-scoped notification architecture, phase 1 (DB).
--
-- 1. Drop the old branding-publish trigger. It fired on every organizations
--    branding_config/accent_color/logo_url update with no client_id
--    attribution and no preference/dedupe/email handling - and since
--    ClientBranding.jsx now saves a client's override through the exact same
--    organizations.update() call as AgencyBranding.jsx, every client
--    branding save was ALSO firing this same generic, unattributed
--    "Branding published" notification (the wall of identical rows the user
--    saw). Replaced by an explicit backend call
--    (py/app/main.py:/py/branding/notify-published) that knows exactly
--    which entity was saved and can route through create_user_notification
--    (client attribution, preference check, dedupe, email).
drop trigger if exists organizations_notify_branding_published on public.organizations;
drop function if exists public.notify_branding_published();

-- 2. Account & Security producer: one real, low-risk, backend-confirmed
-- event (a role actually changing is a committed DB row, exactly like the
-- branding trigger's own definition of "confirmed"). Global by nature (no
-- client_id) - a membership is organization-scoped, not client-scoped, per
-- the architecture already established for Settings -> Account.
create or replace function public.notify_membership_role_changed()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.role is distinct from old.role then
    begin
      insert into public.user_notifications
        (user_id, organization_id, client_id, category, event_type, title, body, link, action_label, priority)
      values
        (new.user_id, new.organization_id, null, 'account_security', 'membership_role_changed',
         'Your role was changed',
         'Your role is now ' || new.role || '.',
         '/settings', 'View Settings', 'normal');
    exception when others then
      raise warning 'notify_membership_role_changed: could not insert notification: %', sqlerrm;
    end;
  end if;
  return new;
end;
$$;

drop trigger if exists memberships_notify_role_changed on public.memberships;
create trigger memberships_notify_role_changed
  after update of role on public.memberships
  for each row execute function public.notify_membership_role_changed();
