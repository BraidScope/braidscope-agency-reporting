-- Server-side connector OAuth state and credential storage.
-- These tables are accessed by backend services with the Supabase service role.
-- No browser/client policies are added because credentials must never be exposed.

create table if not exists public.connector_oauth_states (
  id          uuid primary key default gen_random_uuid(),
  state_hash  text not null unique,
  user_id     uuid references auth.users(id) on delete cascade not null,
  client_id   uuid references public.clients(id) on delete cascade not null,
  type        text not null,
  shop_domain text,
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null,
  consumed_at timestamptz
);
create index if not exists connector_oauth_states_client_idx
  on public.connector_oauth_states(client_id);
create index if not exists connector_oauth_states_expires_idx
  on public.connector_oauth_states(expires_at);

create table if not exists public.integration_credentials (
  id                     uuid primary key default gen_random_uuid(),
  client_id              uuid references public.clients(id) on delete cascade not null,
  type                   text not null,
  external_account_id    text,
  shop_domain            text,
  access_token_encrypted text not null,
  scopes                 text[] not null default '{}',
  status                 text not null default 'connected',
  last_sync_at           timestamptz,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  unique (client_id, type, external_account_id)
);
create index if not exists integration_credentials_client_idx
  on public.integration_credentials(client_id);
create index if not exists integration_credentials_type_idx
  on public.integration_credentials(type);

alter table public.connector_oauth_states enable row level security;
alter table public.integration_credentials enable row level security;

create table if not exists public.analytics_shopify_orders (
  id                   uuid primary key default gen_random_uuid(),
  client_id            uuid references public.clients(id) on delete cascade not null,
  shop_domain          text not null,
  order_id             text not null,
  order_name           text,
  processed_at         timestamptz not null,
  currency_code        text,
  total_price          numeric(12, 2) not null default 0,
  subtotal_price       numeric(12, 2) not null default 0,
  total_refunded       numeric(12, 2) not null default 0,
  net_revenue          numeric(12, 2) not null default 0,
  customer_id          text,
  customer_email_hash  text,
  line_item_count      integer not null default 0,
  raw                  jsonb not null default '{}'::jsonb,
  synced_at            timestamptz not null default now(),
  unique (client_id, order_id)
);
create index if not exists analytics_shopify_orders_client_date_idx
  on public.analytics_shopify_orders(client_id, processed_at);

create table if not exists public.analytics_shopify_daily_metrics (
  id              uuid primary key default gen_random_uuid(),
  client_id       uuid references public.clients(id) on delete cascade not null,
  shop_domain     text not null,
  metric_date     date not null,
  currency_code   text,
  orders          integer not null default 0,
  gross_revenue   numeric(12, 2) not null default 0,
  refunds         numeric(12, 2) not null default 0,
  net_revenue     numeric(12, 2) not null default 0,
  average_order_value numeric(12, 2) not null default 0,
  customers       integer not null default 0,
  synced_at       timestamptz not null default now(),
  unique (client_id, metric_date)
);
create index if not exists analytics_shopify_daily_client_date_idx
  on public.analytics_shopify_daily_metrics(client_id, metric_date);

create table if not exists public.analytics_shopify_product_daily_metrics (
  id              uuid primary key default gen_random_uuid(),
  client_id       uuid references public.clients(id) on delete cascade not null,
  shop_domain     text not null,
  metric_date     date not null,
  product_id      text not null,
  product_title   text,
  currency_code   text,
  quantity        integer not null default 0,
  gross_revenue   numeric(12, 2) not null default 0,
  synced_at       timestamptz not null default now(),
  unique (client_id, metric_date, product_id)
);
create index if not exists analytics_shopify_product_daily_client_date_idx
  on public.analytics_shopify_product_daily_metrics(client_id, metric_date);

alter table public.analytics_shopify_orders enable row level security;
alter table public.analytics_shopify_daily_metrics enable row level security;
alter table public.analytics_shopify_product_daily_metrics enable row level security;
