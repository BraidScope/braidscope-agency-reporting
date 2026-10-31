-- Client Branding overrides were previously stored inside the VIEWING
-- user's own organizations.branding_config.overrides[client_id] - correct
-- before shared clients existed (every client always belonged to the
-- viewer's own org), wrong now: a shared client's branding override would
-- land in the wrong agency's organization row entirely. Moves override
-- storage onto the client row itself, which every viewer (owner-org or
-- shared) already has correctly role-gated access to via the existing
-- clients_update_editor / clients_select_members RLS policies
-- (20260919_003_client_sharing.sql) - no new policy needed, only the column.

alter table public.clients
  add column if not exists branding_override jsonb not null default '{}'::jsonb;

-- Backfill: copy each organization's existing per-client override into that
-- SAME client's new column, scoped by organization_id so no cross-tenant
-- leakage is possible even if two different orgs' branding_config.overrides
-- blobs happen to use overlapping keys.
update public.clients c
set branding_override = o.branding_config -> 'overrides' -> c.id::text
from public.organizations o
where o.id = c.organization_id
  and o.branding_config -> 'overrides' -> c.id::text is not null
  and o.branding_config -> 'overrides' -> c.id::text <> 'null'::jsonb;
