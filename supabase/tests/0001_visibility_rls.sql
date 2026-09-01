begin;

create extension if not exists pgtap with schema extensions;
select plan(3);

insert into public.cases (id, ntsb_number, title, state, evidence_cutoff)
values (
  '30000000-0000-0000-0000-000000000001',
  'TEST00AA000',
  'Fixture case',
  'BLIND',
  '2025-05-01 00:00:00+00'
);

insert into public.docket_items (
  id, case_id, title, source_url, document_type, rights_status,
  processing_disposition, attribution, review_note, reviewed_at
) values
  ('31000000-0000-0000-0000-000000000001', '30000000-0000-0000-0000-000000000001', 'Factual report', 'https://example.test/factual.pdf', 'FACTUAL_REPORT', 'NTSB_AUTHORED', 'AI_ALLOWED', 'Source: National Transportation Safety Board', 'fixture', now()),
  ('31000000-0000-0000-0000-000000000002', '30000000-0000-0000-0000-000000000001', 'Final report', 'https://example.test/final.pdf', 'FINAL_REPORT', 'NTSB_AUTHORED', 'AI_ALLOWED', 'Source: National Transportation Safety Board', 'fixture', now());

insert into public.source_documents (
  id, case_id, docket_item_id, title, source_url, published_at, retrieved_at,
  document_type, visibility, checksum, storage_path
) values
  ('32000000-0000-0000-0000-000000000001', '30000000-0000-0000-0000-000000000001', '31000000-0000-0000-0000-000000000001', 'Factual report', 'https://example.test/factual.pdf', '2025-05-01 00:00:00+00', now(), 'FACTUAL_REPORT', 'INVESTIGATION_EVIDENCE', repeat('a', 64), 'fixture/factual.pdf'),
  ('32000000-0000-0000-0000-000000000002', '30000000-0000-0000-0000-000000000001', '31000000-0000-0000-0000-000000000002', 'Final report', 'https://example.test/final.pdf', '2025-05-01 00:00:00+00', now(), 'FINAL_REPORT', 'FINAL_FINDING', repeat('b', 64), 'fixture/final.pdf');

create temporary table observed_visibility (
  document_count integer not null,
  visibility text not null
);
grant insert on observed_visibility to casezero_blind;

set local role casezero_blind;
insert into observed_visibility
select count(*)::integer, min(visibility)
from public.source_documents
where case_id = '30000000-0000-0000-0000-000000000001';
reset role;

select extensions.is(
  (select document_count from observed_visibility),
  1,
  'blind role sees only investigation evidence'
);
select extensions.is(
  (select visibility from observed_visibility),
  'INVESTIGATION_EVIDENCE',
  'blocked rows are invisible to the blind role'
);
select extensions.is(
  has_table_privilege('casezero_blind', 'public.source_documents', 'UPDATE'),
  false,
  'blind role cannot mutate source documents'
);

select * from extensions.finish();
rollback;
