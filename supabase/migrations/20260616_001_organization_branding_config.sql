-- Persist complete workspace branding.
 alter table public.organizations add column if not exists branding_config jsonb not null default '{}'::jsonb;
