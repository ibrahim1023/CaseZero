begin;

create extension if not exists pgtap with schema extensions;
select plan(3);

insert into public.cases (ntsb_number, title, state)
values ('TEST00AA000', 'Fixture case', 'BLIND');

insert into public.source_documents (
  case_id,
  title,
  source_url,
  retrieved_at,
  document_type,
  visibility,
  checksum,
  storage_path
)
select id, 'Factual report', 'https://example.test/factual.pdf', now(),
       'FACTUAL_REPORT', 'INVESTIGATION_EVIDENCE', repeat('a', 64), 'fixture/factual.pdf'
from public.cases
where ntsb_number = 'TEST00AA000';

insert into public.source_documents (
  case_id,
  title,
  source_url,
  retrieved_at,
  document_type,
  visibility,
  checksum,
  storage_path
)
select id, 'Final report', 'https://example.test/final.pdf', now(),
       'FINAL_REPORT', 'FINAL_FINDING', repeat('b', 64), 'fixture/final.pdf'
from public.cases
where ntsb_number = 'TEST00AA000';

create temporary table observed_visibility (
  document_count integer not null,
  visibility text not null
);
grant insert on observed_visibility to casezero_blind;

set local role casezero_blind;
insert into observed_visibility
select count(*)::integer, min(visibility)
from public.source_documents;
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
