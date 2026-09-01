create or replace function public.is_blind_metadata_eligible(
  source_title text,
  source_document_type text,
  source_visibility text,
  source_published_at timestamptz,
  case_cutoff timestamptz,
  docket_disposition text
) returns boolean
language sql
immutable
set search_path = pg_catalog
as $$
  select source_visibility = 'INVESTIGATION_EVIDENCE'
    and source_published_at is not null
    and case_cutoff is not null
    and source_published_at <= case_cutoff
    and docket_disposition = 'AI_ALLOWED'
    and source_document_type <> 'FINAL_REPORT'
    and source_title !~* '\m(final report|probable cause|adopted|analysis|findings?|recommendations?)\M'
$$;

revoke all on function public.is_blind_metadata_eligible(
  text, text, text, timestamptz, timestamptz, text
) from public, anon, authenticated;
grant execute on function public.is_blind_metadata_eligible(
  text, text, text, timestamptz, timestamptz, text
) to casezero_blind, casezero_eval, casezero_processor;

create policy evaluation_locks on public.investigation_locks
  for select to casezero_eval using (true);
grant select on public.investigation_locks to casezero_eval;

drop policy if exists blind_cases on public.cases;
drop policy if exists evaluation_cases on public.cases;
drop policy if exists processor_cases on public.cases;

create policy blind_cases on public.cases for select to casezero_blind
  using (state in ('BLIND', 'LOCKED') and evidence_cutoff is not null);
create policy processor_cases on public.cases for select to casezero_processor
  using (state = 'BLIND' and evidence_cutoff is not null);
create policy evaluation_cases on public.cases for select to casezero_eval
  using (
    state in ('LOCKED', 'REVEALED')
    and exists (
      select 1 from public.investigation_locks lock_record
      where lock_record.case_id = cases.id
    )
  );

drop policy if exists processor_docket_items on public.docket_items;
create policy processor_docket_items on public.docket_items
  for all to casezero_processor
  using (exists (
    select 1 from public.cases case_record
    where case_record.id = docket_items.case_id
      and case_record.state = 'BLIND'
      and case_record.evidence_cutoff is not null
  ))
  with check (exists (
    select 1 from public.cases case_record
    where case_record.id = docket_items.case_id
      and case_record.state = 'BLIND'
      and case_record.evidence_cutoff is not null
  ));
create policy blind_docket_items on public.docket_items
  for select to casezero_blind
  using (
    processing_disposition = 'AI_ALLOWED'
    and exists (
      select 1 from public.cases case_record
      where case_record.id = docket_items.case_id
        and case_record.state in ('BLIND', 'LOCKED')
        and case_record.evidence_cutoff is not null
    )
  );
create policy evaluation_docket_items on public.docket_items
  for select to casezero_eval
  using (exists (
    select 1 from public.investigation_locks lock_record
    where lock_record.case_id = docket_items.case_id
  ));

drop policy if exists blind_evidence_only on public.source_documents;
drop policy if exists evaluation_sources on public.source_documents;
drop policy if exists processor_sources on public.source_documents;

create policy blind_sources on public.source_documents
  for select to casezero_blind
  using (exists (
    select 1
    from public.cases case_record
    join public.docket_items docket on docket.case_id = case_record.id
    where case_record.id = source_documents.case_id
      and docket.id = source_documents.docket_item_id
      and case_record.state in ('BLIND', 'LOCKED')
      and public.is_blind_metadata_eligible(
        source_documents.title,
        source_documents.document_type,
        source_documents.visibility,
        source_documents.published_at,
        case_record.evidence_cutoff,
        docket.processing_disposition
      )
  ));
create policy processor_sources on public.source_documents
  for select to casezero_processor
  using (exists (
    select 1
    from public.cases case_record
    join public.docket_items docket on docket.case_id = case_record.id
    where case_record.id = source_documents.case_id
      and docket.id = source_documents.docket_item_id
      and case_record.state = 'BLIND'
      and public.is_blind_metadata_eligible(
        source_documents.title,
        source_documents.document_type,
        source_documents.visibility,
        source_documents.published_at,
        case_record.evidence_cutoff,
        docket.processing_disposition
      )
  ));
create policy evaluation_sources on public.source_documents
  for select to casezero_eval
  using (
    exists (
      select 1 from public.cases case_record
      where case_record.id = source_documents.case_id
        and case_record.state in ('LOCKED', 'REVEALED')
    )
    and exists (
      select 1 from public.investigation_locks lock_record
      where lock_record.case_id = source_documents.case_id
    )
  );

create policy processor_source_blobs on public.source_blobs
  for select to casezero_processor
  using (exists (
    select 1 from public.source_documents source
    where source.blob_checksum = source_blobs.checksum
  ));
create policy blind_source_blobs on public.source_blobs
  for select to casezero_blind
  using (exists (
    select 1 from public.source_documents source
    where source.blob_checksum = source_blobs.checksum
  ));
create policy evaluation_source_blobs on public.source_blobs
  for select to casezero_eval
  using (exists (
    select 1 from public.source_documents source
    where source.blob_checksum = source_blobs.checksum
  ));

revoke all privileges on public.docket_items from casezero_blind, casezero_eval;
grant select on public.docket_items to casezero_blind, casezero_eval;
grant select, insert, update on public.docket_items to casezero_processor;
grant select on public.source_blobs to casezero_blind, casezero_eval, casezero_processor;
