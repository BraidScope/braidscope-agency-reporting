alter table public.memberships
  add column if not exists ui_preferences jsonb not null default '{}'::jsonb;
