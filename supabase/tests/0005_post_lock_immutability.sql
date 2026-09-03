begin;

create extension if not exists pgtap with schema extensions;
select plan(5);

insert into public.cases (
  id, ntsb_number, title, state, evidence_cutoff
) values (
  '50000000-0000-0000-0000-000000000001',
  'TEST-PHASE2-LOCKED',
  'Locked mutation fixture',
  'BLIND',
  '2025-05-01 00:00:00+00'
);
insert into public.docket_items (
  id, case_id, title, source_url, document_type, rights_status,
  processing_disposition, attribution, review_note, reviewed_at
) values (
  '51000000-0000-0000-0000-000000000001',
  '50000000-0000-0000-0000-000000000001',
  'Factual',
  'https://data.ntsb.gov/phase2-locked.pdf',
  'FACTUAL_REPORT',
  'NTSB_AUTHORED',
  'AI_ALLOWED',
  'Source: National Transportation Safety Board',
  'fixture',
  now()
);
insert into public.source_documents (
  id, case_id, docket_item_id, title, source_url, published_at, retrieved_at,
  document_type, visibility, checksum, storage_path
) values (
  '52000000-0000-0000-0000-000000000001',
  '50000000-0000-0000-0000-000000000001',
  '51000000-0000-0000-0000-000000000001',
  'Factual',
  'https://data.ntsb.gov/phase2-locked.pdf',
  '2025-05-01 00:00:00+00',
  now(),
  'FACTUAL_REPORT',
  'INVESTIGATION_EVIDENCE',
  repeat('a', 64),
  'phase2/locked'
);
insert into public.investigation_locks (
  case_id, assessment_snapshot, assessment_hash, evidence_set_hash,
  hash_algorithm, model_versions, prompt_versions, system_version, locked_at
) values (
  '50000000-0000-0000-0000-000000000001',
  '{"schema_version":"phase2-lock-contract-v1","assessment_kind":"fixture","payload":{}}',
  repeat('b', 64), repeat('c', 64), 'postgres-jsonb-text-v1',
  '{"evidence":"fixture"}', '{"evidence":"fixture.v1"}', 'phase2-test', now()
);
update public.cases set state = 'LOCKED'
where id = '50000000-0000-0000-0000-000000000001';

select extensions.throws_ok(
  $$update public.investigation_locks set system_version = 'changed'
    where case_id = '50000000-0000-0000-0000-000000000001'$$,
  'P0001', 'investigation locks are immutable',
  'lock rows reject updates'
);
select extensions.throws_ok(
  $$update public.cases set evidence_cutoff = '2025-05-02 00:00:00+00'
    where id = '50000000-0000-0000-0000-000000000001'$$,
  'P0001', 'evidence cutoff is immutable after blind entry',
  'locked cases reject cutoff changes'
);
select extensions.throws_ok(
  $$update public.source_documents set visibility = 'FINAL_FINDING'
    where id = '52000000-0000-0000-0000-000000000001'$$,
  'P0001', 'source inventory is immutable after lock',
  'locked cases reject source changes'
);
select extensions.throws_ok(
  $$update public.docket_items set processing_disposition = 'EXCLUDED'
    where id = '51000000-0000-0000-0000-000000000001'$$,
  'P0001', 'source inventory is immutable after lock',
  'locked cases reject docket changes'
);
select extensions.is(
  (select state from public.cases where id = '50000000-0000-0000-0000-000000000001'),
  'LOCKED',
  'failed mutations retain locked state'
);

select * from extensions.finish();
rollback;
