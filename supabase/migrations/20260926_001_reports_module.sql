-- Reports module: turns `pages` rows with type = 'report' into real,
-- client-scoped report entities.
--
-- Why extend `pages` instead of adding a table: pages already has the
-- client_id foreign key (cascade on client delete), the shared-client RLS
-- (20260924_001) and - importantly - every existing usage count
-- (org_counts, client_counts, admin_workspace.py) already counts
-- pages.type = 'report'. Extending it keeps all of those correct with no
-- edits to shared functions. Every new column is nullable/defaulted, so
-- existing rows and existing readers are unaffected.
--
-- Writes to report rows go through the Python API (service role) which
-- enforces the reports.* permission matrix (view/edit/manage/delete). The
-- browser (authenticated role) may still READ reports through the existing
-- select policy, but may no longer write them directly - see the policy
-- change at the bottom.

alter table public.pages
  add column if not exists organization_id uuid references public.organizations(id) on delete cascade,
  add column if not exists status text,
  add column if not exists template text,
  add column if not exists period_start date,
  add column if not exists period_end date,
  add column if not exists content jsonb not null default '{}'::jsonb,
  add column if not exists page_count integer,
  add column if not exists created_by uuid references auth.users(id) on delete set null,
  add column if not exists updated_by uuid references auth.users(id) on delete set null,
  add column if not exists updated_at timestamptz not null default now(),
  add column if not exists pdf_path text,
  add column if not exists pdf_version integer not null default 0,
  add column if not exists pdf_generated_at timestamptz,
  add column if not exists pdf_size_bytes integer;

alter table public.pages
  drop constraint if exists pages_report_status_check;
alter table public.pages
  add constraint pages_report_status_check
  check (status is null or status in ('draft', 'final', 'sent', 'archived'));

alter table public.pages
  drop constraint if exists pages_report_period_check;
alter table public.pages
  add constraint pages_report_period_check
  check (period_start is null or period_end is null or period_start <= period_end);

-- Library listing: newest-updated first, per client, reports only.
create index if not exists pages_reports_client_updated_idx
  on public.pages (client_id, updated_at desc)
  where type = 'report';

comment on column public.pages.config is
  'For type=report: structure only - { sources: [...], sections: [{id, enabled}], periodDays }. Narratives and the data snapshot live in `content`.';
comment on column public.pages.content is
  'For type=report: { narratives: {sectionId: text}, snapshot: {...dashboard data used by the report...}, snapshotAt }.';
comment on column public.pages.pdf_path is
  'Object path inside the PRIVATE report-files bucket: {client_id}/{report_id}/v{pdf_version}.pdf. Never exposed as a public URL; downloads are streamed by the API after an authorization check.';

-- Private bucket for generated PDFs. No storage.objects policies are created
-- on purpose: only the service role (the Python API) can read or write it.
insert into storage.buckets (id, name, public)
values ('report-files', 'report-files', false)
on conflict (id) do update set public = false;

-- Reports are written only through the API. Dashboards rows (type <> 'report')
-- keep the exact previous write rule; report rows are excluded from direct
-- browser writes so the role matrix (e.g. delete = owner only) cannot be
-- bypassed with a raw Supabase call.
drop policy if exists "pages_write_members" on public.pages;
create policy "pages_write_members" on public.pages
  for all using (
    type <> 'report'
    and exists (select 1 from public.clients c
                where c.id = pages.client_id
                  and (public.is_org_editor(c.organization_id) or public.has_client_access(c.id, 'editor')))
  )
  with check (
    type <> 'report'
    and exists (select 1 from public.clients c
                where c.id = pages.client_id
                  and (public.is_org_editor(c.organization_id) or public.has_client_access(c.id, 'editor')))
  );

-- Make PostgREST pick up the new columns immediately.
notify pgrst, 'reload schema';
