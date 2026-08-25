do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'casezero_processor') then
    create role casezero_processor nologin nobypassrls;
  end if;
end
$$;

grant casezero_processor to postgres;
grant usage on schema public to casezero_processor;

create table public.docket_items (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.cases(id) on delete restrict,
  title text not null,
  source_url text not null,
  document_type text,
  file_type text,
  page_count integer check (page_count is null or page_count > 0),
  published_at timestamptz,
  rights_status text not null check (rights_status in (
    'NTSB_AUTHORED', 'THIRD_PARTY_PERMISSION_CONFIRMED',
    'THIRD_PARTY_UNCLEAR', 'UNKNOWN'
  )),
  processing_disposition text not null check (processing_disposition in (
    'AI_ALLOWED', 'LOCAL_ONLY', 'LINK_ONLY', 'EXCLUDED'
  )),
  attribution text not null,
  review_note text not null,
  reviewed_at timestamptz not null,
  expected_checksum text check (
    expected_checksum is null or expected_checksum ~ '^[0-9a-f]{64}$'
  ),
  created_at timestamptz not null default now(),
  unique (case_id, source_url),
  check (
    rights_status not in ('THIRD_PARTY_UNCLEAR', 'UNKNOWN')
    or processing_disposition in ('LINK_ONLY', 'EXCLUDED')
  )
);

create table public.source_blobs (
  checksum text primary key check (checksum ~ '^[0-9a-f]{64}$'),
  storage_path text not null unique,
  byte_size bigint not null check (byte_size >= 0),
  created_at timestamptz not null default now()
);

insert into public.source_blobs (checksum, storage_path, byte_size)
select checksum, min(storage_path), 0
from public.source_documents
group by checksum
on conflict (checksum) do nothing;

alter table public.source_documents
  add column docket_item_id uuid references public.docket_items(id) on delete restrict,
  add column blob_checksum text references public.source_blobs(checksum) on delete restrict;
update public.source_documents set blob_checksum = checksum where blob_checksum is null;
alter table public.source_documents
  drop constraint if exists source_documents_storage_path_key;

create table public.processing_runs (
  id uuid primary key default gen_random_uuid(),
  source_document_id uuid not null references public.source_documents(id) on delete restrict,
  source_checksum text not null check (source_checksum ~ '^[0-9a-f]{64}$'),
  processor_name text not null,
  processor_version text not null,
  configuration_hash text not null check (configuration_hash ~ '^[0-9a-f]{64}$'),
  status text not null check (status in (
    'PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'UNSUPPORTED', 'SKIPPED_RIGHTS'
  )),
  error_type text,
  error_message text,
  retryable boolean not null default false,
  attempt_count integer not null default 1 check (attempt_count > 0),
  started_at timestamptz not null,
  completed_at timestamptz,
  unique (source_checksum, processor_name, processor_version, configuration_hash)
);

create table public.derived_artifacts (
  id uuid primary key default gen_random_uuid(),
  processing_run_id uuid not null references public.processing_runs(id) on delete restrict,
  source_document_id uuid not null references public.source_documents(id) on delete restrict,
  kind text not null check (kind in (
    'DOCUMENT_STRUCTURE', 'OCR_TRANSCRIPT', 'TABLE_PROFILE',
    'TIME_SERIES_PROFILE', 'IMAGE_PREPARATION'
  )),
  checksum text not null check (checksum ~ '^[0-9a-f]{64}$'),
  storage_path text not null,
  media_type text not null,
  byte_size bigint not null check (byte_size >= 0),
  tool_metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null,
  unique (processing_run_id, checksum)
);

create table public.structural_units (
  id uuid primary key,
  derived_artifact_id uuid not null references public.derived_artifacts(id) on delete restrict,
  source_document_id uuid not null references public.source_documents(id) on delete restrict,
  kind text not null check (kind in (
    'TEXT_BLOCK', 'TABLE', 'TABLE_ROW_GROUP', 'TIME_SERIES_WINDOW', 'IMAGE'
  )),
  ordinal integer not null check (ordinal >= 0),
  content_checksum text not null check (content_checksum ~ '^[0-9a-f]{64}$'),
  locator jsonb not null,
  payload jsonb not null,
  unique (derived_artifact_id, ordinal)
);

create table public.model_runs (
  id uuid primary key,
  case_id uuid not null references public.cases(id) on delete restrict,
  parent_run_id uuid references public.model_runs(id) on delete restrict,
  stage text not null,
  provider text not null,
  model text not null,
  prompt_hash text not null check (prompt_hash ~ '^[0-9a-f]{64}$'),
  structural_unit_ids uuid[] not null default '{}',
  input_tokens integer,
  output_tokens integer,
  latency_ms integer not null check (latency_ms >= 0),
  retry_count integer not null check (retry_count >= 0),
  schema_failure_count integer not null check (schema_failure_count >= 0),
  status text not null,
  created_at timestamptz not null
);

create table public.evidence_items (
  id uuid primary key,
  case_id uuid not null references public.cases(id) on delete restrict,
  source_document_id uuid not null references public.source_documents(id) on delete restrict,
  structural_unit_id uuid not null references public.structural_units(id) on delete restrict,
  model_run_id uuid references public.model_runs(id) on delete restrict,
  item jsonb not null,
  review_status text not null check (review_status in (
    'NOT_REQUIRED', 'PENDING', 'ACCEPTED', 'REJECTED'
  )),
  created_at timestamptz not null
);

create table public.claim_candidates (
  id uuid primary key,
  case_id uuid not null references public.cases(id) on delete restrict,
  model_run_id uuid not null references public.model_runs(id) on delete restrict,
  evidence_ids uuid[] not null,
  candidate jsonb not null,
  created_at timestamptz not null
);

create table public.entity_candidates (
  id uuid primary key,
  case_id uuid not null references public.cases(id) on delete restrict,
  model_run_id uuid not null references public.model_runs(id) on delete restrict,
  evidence_ids uuid[] not null,
  candidate jsonb not null,
  created_at timestamptz not null
);

create table public.timeline_candidates (
  id uuid primary key,
  case_id uuid not null references public.cases(id) on delete restrict,
  model_run_id uuid not null references public.model_runs(id) on delete restrict,
  evidence_ids uuid[] not null,
  candidate jsonb not null,
  created_at timestamptz not null
);

create index docket_items_case_idx on public.docket_items(case_id);
create index processing_runs_source_idx on public.processing_runs(source_document_id);
create index structural_units_source_idx on public.structural_units(source_document_id);
create index evidence_items_case_idx on public.evidence_items(case_id);

alter table public.docket_items enable row level security;
alter table public.docket_items force row level security;
alter table public.processing_runs enable row level security;
alter table public.processing_runs force row level security;
alter table public.derived_artifacts enable row level security;
alter table public.derived_artifacts force row level security;
alter table public.structural_units enable row level security;
alter table public.structural_units force row level security;
alter table public.model_runs enable row level security;
alter table public.model_runs force row level security;
alter table public.evidence_items enable row level security;
alter table public.evidence_items force row level security;
alter table public.claim_candidates enable row level security;
alter table public.claim_candidates force row level security;
alter table public.entity_candidates enable row level security;
alter table public.entity_candidates force row level security;
alter table public.timeline_candidates enable row level security;
alter table public.timeline_candidates force row level security;

create policy processor_cases on public.cases for select to casezero_processor
  using (state = 'BLIND');
create policy processor_sources on public.source_documents for select to casezero_processor
  using (visibility = 'INVESTIGATION_EVIDENCE');
create policy processor_docket_items on public.docket_items for select to casezero_processor
  using (
    processing_disposition in ('AI_ALLOWED', 'LOCAL_ONLY')
    and exists (
      select 1 from public.source_documents s
      where s.docket_item_id = docket_items.id
        and s.visibility = 'INVESTIGATION_EVIDENCE'
    )
  );

create policy processor_runs_select on public.processing_runs for select to casezero_processor
  using (exists (
    select 1 from public.source_documents s
    where s.id = processing_runs.source_document_id
      and s.visibility = 'INVESTIGATION_EVIDENCE'
  ));
create policy processor_runs_insert on public.processing_runs for insert to casezero_processor
  with check (exists (
    select 1 from public.source_documents s
    where s.id = processing_runs.source_document_id
      and s.visibility = 'INVESTIGATION_EVIDENCE'
  ));
create policy processor_runs_update on public.processing_runs for update to casezero_processor
  using (exists (
    select 1 from public.source_documents s
    where s.id = processing_runs.source_document_id
      and s.visibility = 'INVESTIGATION_EVIDENCE'
  ));

create policy processor_artifacts on public.derived_artifacts for all to casezero_processor
  using (exists (
    select 1 from public.source_documents s
    where s.id = derived_artifacts.source_document_id
      and s.visibility = 'INVESTIGATION_EVIDENCE'
  ))
  with check (exists (
    select 1 from public.source_documents s
    where s.id = derived_artifacts.source_document_id
      and s.visibility = 'INVESTIGATION_EVIDENCE'
  ));
create policy processor_units on public.structural_units for all to casezero_processor
  using (exists (
    select 1 from public.source_documents s
    where s.id = structural_units.source_document_id
      and s.visibility = 'INVESTIGATION_EVIDENCE'
  ))
  with check (exists (
    select 1 from public.source_documents s
    where s.id = structural_units.source_document_id
      and s.visibility = 'INVESTIGATION_EVIDENCE'
  ));
create policy processor_model_runs on public.model_runs for all to casezero_processor
  using (exists (select 1 from public.cases c where c.id = model_runs.case_id and c.state = 'BLIND'))
  with check (exists (select 1 from public.cases c where c.id = model_runs.case_id and c.state = 'BLIND'));
create policy processor_evidence on public.evidence_items for all to casezero_processor
  using (exists (
    select 1 from public.source_documents s
    where s.id = evidence_items.source_document_id
      and s.visibility = 'INVESTIGATION_EVIDENCE'
  ))
  with check (exists (
    select 1 from public.source_documents s
    where s.id = evidence_items.source_document_id
      and s.visibility = 'INVESTIGATION_EVIDENCE'
  ));
create policy processor_claim_candidates on public.claim_candidates for all to casezero_processor
  using (exists (select 1 from public.cases c where c.id = claim_candidates.case_id and c.state = 'BLIND'))
  with check (exists (select 1 from public.cases c where c.id = claim_candidates.case_id and c.state = 'BLIND'));
create policy processor_entity_candidates on public.entity_candidates for all to casezero_processor
  using (exists (select 1 from public.cases c where c.id = entity_candidates.case_id and c.state = 'BLIND'))
  with check (exists (select 1 from public.cases c where c.id = entity_candidates.case_id and c.state = 'BLIND'));
create policy processor_timeline_candidates on public.timeline_candidates for all to casezero_processor
  using (exists (select 1 from public.cases c where c.id = timeline_candidates.case_id and c.state = 'BLIND'))
  with check (exists (select 1 from public.cases c where c.id = timeline_candidates.case_id and c.state = 'BLIND'));

grant select on public.cases, public.docket_items, public.source_documents to casezero_processor;
grant select, insert, update on public.processing_runs to casezero_processor;
grant select, insert, update on public.derived_artifacts, public.structural_units,
  public.model_runs, public.evidence_items, public.claim_candidates,
  public.entity_candidates, public.timeline_candidates to casezero_processor;
