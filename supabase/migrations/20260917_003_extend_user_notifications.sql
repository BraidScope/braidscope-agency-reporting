-- Phase 1 of the notification system rework (see notification_improvements.md).
-- Extends the existing user_notifications table minimally instead of building
-- a separate notification platform. Existing columns (id, user_id,
-- organization_id, event_type, title, body, link, created_at, read_at) and
-- their data are untouched.

alter table public.user_notifications
  add column if not exists client_id     uuid references public.clients(id) on delete cascade,
  add column if not exists category      text not null default 'general',
  add column if not exists priority      text not null default 'normal',
  add column if not exists metadata      jsonb not null default '{}'::jsonb,
  add column if not exists action_label  text,
  add column if not exists dedupe_key    text;

-- Only existing row shape so far is the ad-hoc 'test' event_type from manual
-- testing; nothing else to backfill.
update public.user_notifications
  set category = 'workspace'
  where event_type = 'team_invite' and category = 'general';

comment on column public.user_notifications.category is
  'Coarse grouping used by Settings preferences and the bell UI: integrations, branding, account_security, workspace, reports, billing, reminders, general.';
comment on column public.user_notifications.priority is
  'normal | high — high surfaces integration-health/security events more prominently.';
comment on column public.user_notifications.dedupe_key is
  'Optional idempotency key for noisy/recurring producers (reminders, repeated sync failures). NULL for one-off events, which never collide with each other under the unique index below (SQL treats NULL <> NULL for uniqueness).';

-- Unread bell query: WHERE user_id = ? AND read_at IS NULL ORDER BY created_at DESC.
create index if not exists user_notifications_unread_idx
  on public.user_notifications (user_id, read_at, created_at desc);

-- Org-scoped admin/audit lookups (e.g. a future "workspace activity" view).
create index if not exists user_notifications_org_created_idx
  on public.user_notifications (organization_id, created_at desc);

-- Per-client integration history (e.g. "has this client's Shopify connection
-- flapped recently").
create index if not exists user_notifications_client_event_idx
  on public.user_notifications (client_id, event_type, created_at desc);

-- Idempotency: a producer can safely retry/upsert with the same dedupe_key
-- for the same user+org without ever creating a duplicate row. Not partial —
-- a plain unique index already lets unlimited NULL dedupe_keys coexist per
-- standard SQL null-distinctness, so one-off notifications are unaffected.
create unique index if not exists user_notifications_dedupe_uidx
  on public.user_notifications (user_id, organization_id, dedupe_key);
