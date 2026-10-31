-- Fixes every WARN from the Supabase security and performance advisors on
-- 1 Nov 2026. Idempotent. No behaviour change for signed-in users.

------------------------------------------------------------
-- 1. Functions: fixed search_path (security advisor 0011)
------------------------------------------------------------
alter function public.is_org_member(uuid)  set search_path = public;
alter function public.is_org_admin(uuid)   set search_path = public;
alter function public.is_org_editor(uuid)  set search_path = public;
alter function public.is_org_owner(uuid)   set search_path = public;
alter function public.touch_connector_oauth_tokens_updated_at() set search_path = public;
alter function public.prevent_membership_self_escalation()    set search_path = public;

------------------------------------------------------------
-- 2. Functions: anonymous visitors cannot call SECURITY DEFINER helpers
--    through /rest/v1/rpc (security advisor 0028). Signed-in users keep
--    execute: the RLS policies and the app call these as `authenticated`.
--    Trigger functions are never called directly, so nobody but the owner
--    needs execute on them.
------------------------------------------------------------
revoke execute on function public.client_counts(uuid)                    from public, anon;
revoke execute on function public.client_team_members(uuid)              from public, anon;
revoke execute on function public.effective_client_role(uuid, uuid)      from public, anon;
revoke execute on function public.ensure_own_organization()              from public, anon;
revoke execute on function public.has_client_access(uuid, public.org_role) from public, anon;
revoke execute on function public.is_org_admin(uuid)                     from public, anon;
revoke execute on function public.is_org_editor(uuid)                    from public, anon;
revoke execute on function public.is_org_member(uuid)                    from public, anon;
revoke execute on function public.is_org_owner(uuid)                     from public, anon;
revoke execute on function public.org_counts(uuid)                       from public, anon;
revoke execute on function public.org_members(uuid)                      from public, anon;
revoke execute on function public.handle_new_user()                      from public, anon, authenticated;
revoke execute on function public.prevent_membership_self_escalation()   from public, anon, authenticated;
revoke execute on function public.touch_connector_oauth_tokens_updated_at() from public, anon, authenticated;

------------------------------------------------------------
-- 3. Indexes on foreign keys (performance advisor 0001)
------------------------------------------------------------
create index if not exists chart_requests_org_idx                  on public.chart_requests (org_id);
create index if not exists client_invitations_invited_by_idx       on public.client_invitations (invited_by);
create index if not exists client_invitations_org_idx              on public.client_invitations (organization_id);
create index if not exists client_memberships_invited_by_idx       on public.client_memberships (invited_by);
create index if not exists connector_oauth_states_user_idx         on public.connector_oauth_states (user_id);
create index if not exists connector_oauth_tokens_last_connected_by_idx on public.connector_oauth_tokens (last_connected_by);
create index if not exists pages_created_by_idx                    on public.pages (created_by);
create index if not exists pages_org_idx                           on public.pages (organization_id);
create index if not exists pages_updated_by_idx                    on public.pages (updated_by);
create index if not exists user_session_activity_org_idx           on public.user_session_activity (organization_id);
create index if not exists workspace_invitations_invited_by_idx    on public.workspace_invitations (invited_by);
create index if not exists workspace_invitations_invited_user_idx  on public.workspace_invitations (invited_user_id);

------------------------------------------------------------
-- 4. Duplicate unique indexes (performance advisor 0009). The *_key
--    constraint with the same columns stays, so upserts keep working.
------------------------------------------------------------
alter table public.analytics_ga4        drop constraint if exists analytics_ga4_unique;
alter table public.analytics_google_ads drop constraint if exists analytics_google_ads_unique;

------------------------------------------------------------
-- 5. RLS policies: evaluate auth.uid() once per query, not once per row
--    (performance advisor 0003). Same logic, wrapped in (select ...).
------------------------------------------------------------
drop policy if exists client_memberships_select on public.client_memberships;
create policy client_memberships_select on public.client_memberships
  for select using (
    user_id = (select auth.uid())
    or exists (select 1 from public.clients c where c.id = client_memberships.client_id and public.is_org_admin(c.organization_id))
    or public.has_client_access(client_id, 'admin'::public.org_role)
  );

drop policy if exists membership_select_self on public.memberships;
create policy membership_select_self on public.memberships
  for select using (user_id = (select auth.uid()) or public.is_org_member(organization_id));

drop policy if exists membership_update_self on public.memberships;
create policy membership_update_self on public.memberships
  for update using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

drop policy if exists notification_preferences_admin_write_self on public.notification_preferences;
create policy notification_preferences_admin_write_self on public.notification_preferences
  for all using (user_id = (select auth.uid()) and public.has_client_access(client_id, 'admin'::public.org_role))
  with check (user_id = (select auth.uid()) and public.has_client_access(client_id, 'admin'::public.org_role));

drop policy if exists notification_preferences_read_self on public.notification_preferences;
create policy notification_preferences_read_self on public.notification_preferences
  for select using (user_id = (select auth.uid()) and public.has_client_access(client_id, 'viewer'::public.org_role));

drop policy if exists notifications_read_self on public.user_notifications;
create policy notifications_read_self on public.user_notifications
  for select using (user_id = (select auth.uid()));

drop policy if exists notifications_update_self on public.user_notifications;
create policy notifications_update_self on public.user_notifications
  for update using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

drop policy if exists session_activity_read_self on public.user_session_activity;
create policy session_activity_read_self on public.user_session_activity
  for select using (user_id = (select auth.uid()));
