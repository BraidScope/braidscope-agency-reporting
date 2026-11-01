-- Baseline for seven tables that exist in the live Supabase project and are
-- used by py/app (dashboards write to them), but were created by hand and
-- never committed. Copied from the live schema on 1 Nov 2026 so a fresh
-- self-hosted install has them. Idempotent on the live project.
--
-- These tables are written only by the backend with the service role, which
-- bypasses RLS. RLS stays enabled with no policies, so the browser (anon or
-- authenticated) can never read them. Matches the live project.

create table if not exists public.analytics_ga4 (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid not null references public.organizations(id) on delete cascade,
  property_id     text not null,
  date            date not null,
  sessions        bigint default 0,
  new_users       bigint default 0,
  active_users    bigint default 0,
  organic_traffic bigint default 0,
  conversions     bigint default 0,
  conv_rate       numeric default 0,
  synced_at       timestamptz default now(),
  constraint analytics_ga4_org_id_property_id_date_key unique (org_id, property_id, date)
);
create index if not exists idx_ga4_org_date on public.analytics_ga4 (org_id, date desc);
alter table public.analytics_ga4 enable row level security;

create table if not exists public.analytics_google_ads (
  id          uuid primary key default gen_random_uuid(),
  org_id      uuid not null references public.organizations(id) on delete cascade,
  account_id  text not null,
  date        date not null,
  clicks      bigint default 0,
  impressions bigint default 0,
  spend       numeric default 0,
  conversions numeric default 0,
  cpc         numeric default 0,
  cpm         numeric default 0,
  cpa         numeric default 0,
  revenue     numeric default 0,
  synced_at   timestamptz default now(),
  constraint analytics_google_ads_org_id_account_id_date_key unique (org_id, account_id, date)
);
create index if not exists idx_gads_org_date on public.analytics_google_ads (org_id, date desc);
alter table public.analytics_google_ads enable row level security;

create table if not exists public.analytics_ga4_cache (
  id          uuid primary key default gen_random_uuid(),
  org_id      uuid not null,
  property_id text not null,
  days        integer not null default 30,
  data_json   jsonb not null,
  synced_at   timestamptz not null default now(),
  constraint analytics_ga4_cache_org_id_property_id_days_key unique (org_id, property_id, days)
);
create index if not exists idx_ga4_cache_lookup on public.analytics_ga4_cache (org_id, property_id, days, synced_at desc);
alter table public.analytics_ga4_cache enable row level security;

create table if not exists public.analytics_google_ads_cache (
  id          uuid primary key default gen_random_uuid(),
  org_id      uuid not null,
  customer_id text not null,
  days        integer not null default 30,
  data_json   jsonb not null,
  synced_at   timestamptz not null default now(),
  constraint analytics_google_ads_cache_org_id_customer_id_days_key unique (org_id, customer_id, days)
);
create index if not exists idx_google_ads_cache_lookup on public.analytics_google_ads_cache (org_id, customer_id, days, synced_at desc);
alter table public.analytics_google_ads_cache enable row level security;

create table if not exists public.analytics_meta_cache (
  id         uuid primary key default gen_random_uuid(),
  org_id     uuid not null,
  account_id text not null,
  days       integer not null default 30,
  data_json  jsonb not null,
  synced_at  timestamptz not null default now(),
  constraint analytics_meta_cache_org_id_account_id_days_key unique (org_id, account_id, days)
);
create index if not exists idx_meta_cache_lookup on public.analytics_meta_cache (org_id, account_id, days, synced_at desc);
alter table public.analytics_meta_cache enable row level security;

create table if not exists public.analytics_hubspot_cache (
  id        uuid primary key default gen_random_uuid(),
  org_id    uuid not null,
  days      integer not null default 30,
  data_json jsonb not null,
  synced_at timestamptz not null default now(),
  constraint analytics_hubspot_cache_org_id_days_key unique (org_id, days)
);
create index if not exists idx_hubspot_cache_lookup on public.analytics_hubspot_cache (org_id, days, synced_at desc);
alter table public.analytics_hubspot_cache enable row level security;

-- Chart requests from the dashboard builder (backend only, like the tables above).
create table if not exists public.chart_requests (
  id         uuid primary key default gen_random_uuid(),
  org_id     uuid not null references public.organizations(id) on delete cascade,
  tab        text not null,
  label      text not null,
  created_at timestamptz default now()
);
alter table public.chart_requests enable row level security;
