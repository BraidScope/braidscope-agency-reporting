-- Replaces the memberships_notify_role_changed DB trigger (added in
-- 20260919_001_client_scoped_notifications.sql) with an explicit backend
-- call, for the exact same reason the branding-publish trigger was replaced
-- in that same migration: a trigger can only ever INSERT a row directly -
-- it cannot check the per-client/global email preference split cleanly nor
-- call Resend. py/app/main.py's new /py/account/notify-role-changed route
-- (called right after a successful role update) now owns this notification,
-- routed through create_user_notification for real preference/email
-- support. Leaving both active would double-notify every role change.
drop trigger if exists memberships_notify_role_changed on public.memberships;
drop function if exists public.notify_membership_role_changed();
