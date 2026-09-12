begin;

create extension if not exists pgtap with schema extensions;
select plan(11);

select extensions.has_table(
  'public', 'investigations',
  'investigations table exists'
);
select extensions.has_table(
  'public', 'claims',
  'canonical claims table exists'
);
select extensions.has_table(
  'public', 'hypotheses',
  'hypotheses table exists'
);
select extensions.has_table(
  'public', 'confidence_revisions',
  'confidence revisions table exists'
);
select extensions.ok(
  (select relrowsecurity and relforcerowsecurity
   from pg_class where oid = 'public.investigations'::regclass),
  'investigations has forced RLS'
);
select extensions.ok(
  (select relrowsecurity and relforcerowsecurity
   from pg_class where oid = 'public.hypotheses'::regclass),
  'hypotheses has forced RLS'
);
select extensions.ok(
  not has_table_privilege('anon', 'public.claims', 'select'),
  'anon cannot read claims'
);
select extensions.ok(
  not has_table_privilege('authenticated', 'public.investigation_entities', 'select'),
  'authenticated cannot read investigation entities'
);

select extensions.ok(
  public.phase3_candidate_is_material('claim', '{"text":"The flight-control cable was fractured."}'::jsonb),
  'material claim candidate is eligible'
);
select extensions.ok(
  not public.phase3_candidate_is_material('timeline', '{"description":"Observation in Row 437, Column 5: 0.63"}'::jsonb),
  'locator-restatement timeline candidate is ineligible'
);
select extensions.ok(
  not public.phase3_candidate_is_material('entity', '{"proposed_canonical_name":"0.63"}'::jsonb),
  'isolated-scalar entity candidate is ineligible'
);

select * from extensions.finish();
rollback;
