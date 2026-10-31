-- Notification + preference security hardening (Phase 1, section 3 of
-- notification_improvements.md).
--
-- 1. user_notifications: the app never updates this table from the browser
--    (all reads/writes go through the Python service-role endpoints), but
--    the table is still exposed to PostgREST directly with the anon/
--    authenticated roles holding full column grants, and the existing
--    `notifications_update_self` RLS policy has no column restriction of its
--    own (Postgres RLS is row-scoped, not column-scoped). That combination
--    would let a signed-in user craft a direct PostgREST call to rewrite the
--    title/body/category/link of their own notification rows. Insert stays
--    blocked (no insert policy exists, and RLS default-denies with none),
--    and this migration additionally locks UPDATE down to read_at only —
--    the one field a user legitimately owns.
revoke update on public.user_notifications from authenticated, anon;
grant update (read_at) on public.user_notifications to authenticated;

-- 2. memberships: `membership_update_self` (added for notification_prefs /
--    ui_preferences self-service) is a row policy — `using (user_id =
--    auth.uid())` — with no column restriction, so a signed-in member can
--    already reach it directly via PostgREST and rewrite their OWN role or
--    organization_id, not just their preferences. A column grant can't fix
--    this cleanly: `membership_update_admin` legitimately needs admins to be
--    able to change *role* on *other members'* rows, and Postgres column
--    grants apply to the `authenticated` role as a whole, not per-policy —
--    restricting the grant would also block that legitimate admin path.
--    A trigger that only fires when the row being changed is the caller's
--    own row is the correct fix: it leaves admin-on-someone-else updates
--    untouched while blocking a member from escalating their own row.
create or replace function public.prevent_membership_self_escalation()
returns trigger
language plpgsql
as $$
begin
  if old.user_id = auth.uid() then
    if new.role is distinct from old.role then
      raise exception 'You cannot change your own role';
    end if;
    if new.organization_id is distinct from old.organization_id then
      raise exception 'You cannot move your own membership to another organization';
    end if;
    if new.user_id is distinct from old.user_id then
      raise exception 'You cannot reassign your own membership';
    end if;
  end if;
  return new;
end;
$$;

drop trigger if exists membership_prevent_self_escalation on public.memberships;
create trigger membership_prevent_self_escalation
  before update on public.memberships
  for each row execute function public.prevent_membership_self_escalation();
