begin;

create extension if not exists pgtap with schema extensions;
select plan(7);

insert into public.cases (
  id, ntsb_number, title, state, evidence_cutoff
) values (
  '20000000-0000-0000-0000-000000000001',
  'TEST-PHASE2-RLS',
  'Phase 2 RLS fixture',
  'BLIND',
  '2025-05-01 00:00:00+00'
);

insert into public.docket_items (
  id, case_id, title, source_url, document_type, rights_status,
  processing_disposition, attribution, review_note, reviewed_at
) values
  ('21000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'Eligible factual', 'https://data.ntsb.gov/phase2-eligible.pdf', 'FACTUAL_REPORT', 'NTSB_AUTHORED', 'AI_ALLOWED', 'Source: National Transportation Safety Board', 'fixture', now()),
  ('21000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000001', 'Post cutoff factual', 'https://data.ntsb.gov/phase2-post.pdf', 'FACTUAL_REPORT', 'NTSB_AUTHORED', 'AI_ALLOWED', 'Source: National Transportation Safety Board', 'fixture', now()),
  ('21000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000001', 'Link only factual', 'https://data.ntsb.gov/phase2-link.pdf', 'FACTUAL_REPORT', 'NTSB_AUTHORED', 'LINK_ONLY', 'Source: National Transportation Safety Board', 'fixture', now()),
  ('21000000-0000-0000-0000-000000000004', '20000000-0000-0000-0000-000000000001', 'Final Report', 'https://data.ntsb.gov/phase2-final.pdf', 'FACTUAL_REPORT', 'NTSB_AUTHORED', 'AI_ALLOWED', 'Source: National Transportation Safety Board', 'fixture', now());

insert into public.source_documents (
  id, case_id, docket_item_id, title, source_url, published_at, retrieved_at,
  document_type, visibility, checksum, storage_path
) values
  ('22000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', '21000000-0000-0000-0000-000000000001', 'Eligible factual', 'https://data.ntsb.gov/phase2-eligible.pdf', '2025-05-01 00:00:00+00', now(), 'FACTUAL_REPORT', 'INVESTIGATION_EVIDENCE', repeat('a', 64), 'phase2/a'),
  ('22000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000001', '21000000-0000-0000-0000-000000000002', 'Post cutoff factual', 'https://data.ntsb.gov/phase2-post.pdf', '2025-05-02 00:00:00+00', now(), 'FACTUAL_REPORT', 'INVESTIGATION_EVIDENCE', repeat('b', 64), 'phase2/b'),
  ('22000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000001', '21000000-0000-0000-0000-000000000003', 'Link only factual', 'https://data.ntsb.gov/phase2-link.pdf', '2025-05-01 00:00:00+00', now(), 'FACTUAL_REPORT', 'INVESTIGATION_EVIDENCE', repeat('c', 64), 'phase2/c'),
  ('22000000-0000-0000-0000-000000000004', '20000000-0000-0000-0000-000000000001', '21000000-0000-0000-0000-000000000004', 'Final Report', 'https://data.ntsb.gov/phase2-final.pdf', '2025-05-01 00:00:00+00', now(), 'FACTUAL_REPORT', 'INVESTIGATION_EVIDENCE', repeat('d', 64), 'phase2/d');

create temporary table role_observation (
  actor text primary key,
  source_count integer not null
);
grant insert on role_observation to casezero_blind, casezero_processor, casezero_eval;

set local role casezero_blind;
insert into role_observation
values ('blind', (select count(*)::integer from public.source_documents
 where case_id = '20000000-0000-0000-0000-000000000001'));
reset role;

set local role casezero_processor;
insert into role_observation
values ('processor', (select count(*)::integer from public.source_documents
 where case_id = '20000000-0000-0000-0000-000000000001'));
reset role;

set local role casezero_eval;
insert into role_observation
values ('evaluation', (select count(*)::integer from public.source_documents
 where case_id = '20000000-0000-0000-0000-000000000001'));
reset role;

select extensions.is(
  (select source_count from role_observation where actor = 'blind'),
  1,
  'blind role sees only eligible pre-cutoff evidence'
);
select extensions.is(
  (select source_count from role_observation where actor = 'processor'),
  1,
  'processor role sees only eligible pre-cutoff evidence'
);
select extensions.is(
  (select source_count from role_observation where actor = 'evaluation'),
  0,
  'evaluation role sees no source before lock'
);

select extensions.ok(
  public.is_blind_metadata_eligible(
    'Eligible factual', 'FACTUAL_REPORT', 'INVESTIGATION_EVIDENCE',
    '2025-05-01 00:00:00+00', '2025-05-01 00:00:00+00', 'AI_ALLOWED'
  ),
  'metadata eligibility admits reviewed factual evidence'
);
select extensions.ok(
  not public.is_blind_metadata_eligible(
    'Final Report', 'FACTUAL_REPORT', 'INVESTIGATION_EVIDENCE',
    '2025-05-01 00:00:00+00', '2025-05-01 00:00:00+00', 'AI_ALLOWED'
  ),
  'metadata eligibility rejects final-report title mismatch'
);
select extensions.ok(
  has_function_privilege(
    'casezero_blind',
    'public.lock_investigation(uuid,jsonb,jsonb,jsonb,text)',
    'EXECUTE'
  ),
  'blind role can invoke the atomic lock function'
);
select extensions.ok(
  not has_function_privilege(
    'casezero_eval',
    'public.lock_investigation(uuid,jsonb,jsonb,jsonb,text)',
    'EXECUTE'
  ),
  'evaluation role cannot invoke the lock function'
);

select * from extensions.finish();
rollback;
