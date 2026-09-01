begin;

create extension if not exists pgtap with schema extensions;
select plan(15);

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

insert into public.cases (id, ntsb_number, title, state)
values ('10000000-0000-0000-0000-000000000001', 'TEST-PHASE2-CUTOFF', 'fixture', 'ACQUIRING');

select extensions.is(
  public.enter_blind(
    '10000000-0000-0000-0000-000000000001',
    '2025-05-01 00:00:00+00'::timestamptz
  ),
  '10000000-0000-0000-0000-000000000001'::uuid,
  'enter_blind returns the case id'
);
select extensions.is(
  (select state from public.cases where id = '10000000-0000-0000-0000-000000000001'),
  'BLIND',
  'enter_blind transitions the case atomically'
);
select extensions.is(
  (select evidence_cutoff from public.cases where id = '10000000-0000-0000-0000-000000000001'),
  '2025-05-01 00:00:00+00'::timestamptz,
  'enter_blind stores the cutoff'
);
select extensions.is(
  public.enter_blind(
    '10000000-0000-0000-0000-000000000001',
    '2025-05-01 00:00:00+00'::timestamptz
  ),
  '10000000-0000-0000-0000-000000000001'::uuid,
  'same-cutoff transition is idempotent'
);
select extensions.throws_ok(
  $$select public.enter_blind(
    '10000000-0000-0000-0000-000000000001',
    '2025-05-02 00:00:00+00'::timestamptz
  )$$,
  'P0001',
  'case state or cutoff conflicts with BLIND transition',
  'conflicting cutoff is rejected'
);
select extensions.is(
  (select count(*)::integer from public.access_audit_events
   where case_id = '10000000-0000-0000-0000-000000000001'
     and reason_code = 'ENTERED_BLIND'),
  2,
  'successful transitions append lifecycle audit events'
);

select * from extensions.finish();
rollback;
