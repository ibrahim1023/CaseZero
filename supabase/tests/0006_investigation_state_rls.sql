begin;

create extension if not exists pgtap with schema extensions;
select plan(8);

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

select * from extensions.finish();
rollback;
