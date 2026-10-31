-- Private OAuth token storage for backend-managed connectors.
-- Browser clients must not read this table. The Python API accesses it with the
-- Supabase service-role key after verifying the user's client access.

create table if not exists public.connector_oauth_tokens (
  id                uuid primary key default gen_random_uuid(),
  client_id          uuid references public.clients(id) on delete cascade not null,
  source_type        text not null,
  token_payload      jsonb not null,
  oauth_client_id    text,
  expires_at         timestamptz,
  scope              text,
  last_connected_by  uuid references auth.users(id) on delete set null,
  created_at         timestamptz default now(),
  updated_at         timestamptz default now(),
  unique (client_id, source_type)
);

create index if not exists connector_oauth_tokens_client_idx
  on public.connector_oauth_tokens(client_id);

delete from public.data_sources ds
using (
  select ctid
  from (
    select
      ctid,
      row_number() over (
        partition by client_id, type
        order by coalesce(last_sync_at, created_at) desc nulls last, created_at desc
      ) as row_number
    from public.data_sources
  ) ranked
  where ranked.row_number > 1
) duplicates
where ds.ctid = duplicates.ctid;

create unique index if not exists data_sources_client_type_unique
  on public.data_sources(client_id, type);

alter table public.connector_oauth_tokens enable row level security;

-- Intentionally no authenticated-user policies:
-- service_role bypasses RLS; anon/authenticated browser clients cannot read tokens.

create or replace function public.touch_connector_oauth_tokens_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists connector_oauth_tokens_updated_at on public.connector_oauth_tokens;
create trigger connector_oauth_tokens_updated_at
  before update on public.connector_oauth_tokens
  for each row execute function public.touch_connector_oauth_tokens_updated_at();
