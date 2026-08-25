begin;

select plan(4);

insert into public.cases (id, ntsb_number, title, state)
values ('10000000-0000-0000-0000-000000000001', 'TEST-PHASE1', 'Fixture', 'BLIND');

insert into public.docket_items (
  id, case_id, title, source_url, rights_status, processing_disposition,
  attribution, review_note, reviewed_at
) values
  ('20000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-000000000001',
   'Factual report', 'https://data.ntsb.gov/factual.pdf', 'NTSB_AUTHORED', 'AI_ALLOWED',
   'Source: National Transportation Safety Board', 'fixture review', now()),
  ('20000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000001',
   'Third-party photo', 'https://data.ntsb.gov/photo.pdf', 'THIRD_PARTY_UNCLEAR', 'LINK_ONLY',
   'Source: National Transportation Safety Board', 'fixture review', now());

insert into public.source_blobs (checksum, storage_path, byte_size)
values (repeat('a', 64), 'aa/' || repeat('a', 64), 10),
       (repeat('b', 64), 'bb/' || repeat('b', 64), 20);

insert into public.source_documents (
  id, case_id, docket_item_id, blob_checksum, title, source_url, retrieved_at,
  document_type, visibility, checksum, storage_path
) values
  ('30000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-000000000001',
   '20000000-0000-0000-0000-000000000001', repeat('a', 64), 'Factual report',
   'https://data.ntsb.gov/factual.pdf', now(), 'FACTUAL_REPORT', 'INVESTIGATION_EVIDENCE',
   repeat('a', 64), 'aa/' || repeat('a', 64)),
  ('30000000-0000-0000-0000-000000000002', '10000000-0000-0000-0000-000000000001',
   null, repeat('b', 64), 'Final report', 'https://data.ntsb.gov/final.pdf', now(),
   'FINAL_REPORT', 'FINAL_FINDING', repeat('b', 64), 'bb/' || repeat('b', 64));

create temporary table processor_observation (
  source_count integer not null,
  docket_count integer not null,
  inserted_runs integer not null
);
grant insert on processor_observation to casezero_processor;

set local role casezero_processor;
insert into public.processing_runs (
  id, source_document_id, source_checksum, processor_name, processor_version,
  configuration_hash, status, started_at
) values (
  '40000000-0000-0000-0000-000000000001',
  '30000000-0000-0000-0000-000000000001', repeat('a', 64),
  'fixture', '1.0.0', repeat('c', 64), 'RUNNING', now()
);
insert into processor_observation
select
  (select count(*)::integer from public.source_documents),
  (select count(*)::integer from public.docket_items),
  (select count(*)::integer from public.processing_runs);
reset role;

select extensions.is(
  (select source_count from processor_observation), 1,
  'processor sees only investigation evidence sources'
);
select extensions.is(
  (select docket_count from processor_observation), 1,
  'processor sees only processable docket inventory'
);
select extensions.is(
  (select inserted_runs from processor_observation), 1,
  'processor may create a run for visible evidence'
);
select extensions.is(
  has_table_privilege('casezero_processor', 'public.source_documents', 'UPDATE'), false,
  'processor cannot mutate authoritative sources'
);

select * from extensions.finish();
rollback;
