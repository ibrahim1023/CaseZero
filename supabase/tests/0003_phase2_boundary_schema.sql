begin;

create extension if not exists pgtap with schema extensions;
select plan(9);

select extensions.has_column(
  'public', 'cases', 'evidence_cutoff',
  'cases records the blind evidence cutoff'
);
select extensions.has_table(
  'public', 'investigation_locks',
  'investigation locks table exists'
);
select extensions.has_table(
  'public', 'access_audit_events',
  'access audit events table exists'
);
select extensions.ok(
  (select relrowsecurity from pg_class where oid = 'public.investigation_locks'::regclass),
  'investigation locks has RLS enabled'
);
select extensions.ok(
  (select relforcerowsecurity from pg_class where oid = 'public.investigation_locks'::regclass),
  'investigation locks has RLS forced'
);
select extensions.ok(
  (select relrowsecurity from pg_class where oid = 'public.access_audit_events'::regclass),
  'access audit events has RLS enabled'
);
select extensions.ok(
  (select relforcerowsecurity from pg_class where oid = 'public.access_audit_events'::regclass),
  'access audit events has RLS forced'
);
select extensions.ok(
  not has_table_privilege('anon', 'public.investigation_locks', 'select'),
  'anon cannot read investigation locks'
);
select extensions.ok(
  not has_table_privilege('authenticated', 'public.access_audit_events', 'select'),
  'authenticated cannot read access audit events'
);

select * from extensions.finish();
rollback;
