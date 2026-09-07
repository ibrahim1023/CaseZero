alter table public.investigations
  add column configuration jsonb not null default '{}'::jsonb,
  add column evidence_set_hash text,
  add column event_sequence bigint not null default 0,
  add column event_head_hash text;
alter table public.investigations add unique (id, case_id);

create table public.investigation_evidence (
  investigation_id uuid not null,
  case_id uuid not null,
  evidence_id uuid not null references public.evidence_items(id) on delete restrict,
  model_run_id uuid not null references public.model_runs(id) on delete restrict,
  primary key (investigation_id, evidence_id),
  unique (investigation_id, case_id, evidence_id),
  foreign key (investigation_id, case_id) references public.investigations(id, case_id)
);
create table public.investigation_candidates (
  investigation_id uuid not null,
  case_id uuid not null,
  candidate_id uuid not null,
  kind text not null check (kind in ('claim', 'entity', 'timeline')),
  model_run_id uuid not null references public.model_runs(id) on delete restrict,
  primary key (investigation_id, candidate_id),
  foreign key (investigation_id, case_id) references public.investigations(id, case_id)
);
create table public.investigation_jobs (
  id uuid primary key default gen_random_uuid(),
  investigation_id uuid not null,
  case_id uuid not null,
  stage text not null,
  work_key text not null check (length(work_key) between 1 and 128),
  input_state_hash text not null check (input_state_hash ~ '^[0-9a-f]{64}$'),
  status text not null default 'PENDING' check (status in ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')),
  attempt_count integer not null default 0 check (attempt_count between 0 and 3),
  model_request_count integer not null default 0 check (model_request_count between 0 and 3),
  retryable boolean not null default true,
  failure_code text,
  worker_id text,
  claimed_at timestamptz,
  lease_expires_at timestamptz,
  created_at timestamptz not null default clock_timestamp(),
  completed_at timestamptz,
  foreign key (investigation_id, case_id) references public.investigations(id, case_id),
  unique (id, investigation_id, case_id),
  unique (investigation_id, stage, work_key, input_state_hash),
  check ((status = 'RUNNING') = (worker_id is not null and claimed_at is not null and lease_expires_at is not null)),
  check (lease_expires_at is null or lease_expires_at > claimed_at)
);
create index investigation_jobs_claim_idx on public.investigation_jobs(investigation_id, stage, created_at)
  where status in ('PENDING', 'RUNNING');
create table public.investigation_job_attempts (
  job_id uuid not null references public.investigation_jobs(id),
  investigation_id uuid not null,
  case_id uuid not null,
  attempt_number integer not null check (attempt_number between 1 and 3),
  worker_id text not null,
  started_at timestamptz not null,
  completed_at timestamptz,
  outcome text not null check (outcome in ('RUNNING', 'SUCCEEDED', 'FAILED', 'LEASE_EXPIRED')),
  failure_code text,
  model_request_count integer not null default 0 check (model_request_count between 0 and 3),
  primary key (job_id, attempt_number),
  foreign key (job_id, investigation_id, case_id) references public.investigation_jobs(id, investigation_id, case_id)
);
create table public.model_request_attempts (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null,
  investigation_id uuid not null,
  case_id uuid not null,
  job_attempt_number integer not null,
  request_ordinal integer not null check (request_ordinal between 1 and 3),
  status text not null check (status in ('RESERVED', 'RUNNING', 'SUCCEEDED', 'SCHEMA_FAILED', 'PROVIDER_FAILED', 'LEASE_EXPIRED')),
  failure_code text,
  validation_issues jsonb not null default '[]'::jsonb check (jsonb_typeof(validation_issues) = 'array'),
  started_at timestamptz not null default clock_timestamp(),
  completed_at timestamptz,
  unique (job_id, request_ordinal),
  foreign key (job_id, job_attempt_number) references public.investigation_job_attempts(job_id, attempt_number),
  foreign key (job_id, investigation_id, case_id) references public.investigation_jobs(id, investigation_id, case_id)
);
create table public.investigation_events (
  id uuid primary key default gen_random_uuid(),
  investigation_id uuid not null,
  case_id uuid not null,
  sequence bigint not null check (sequence > 0),
  event_type text not null,
  target_type text not null,
  target_id uuid not null,
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  model_run_id uuid references public.model_runs(id),
  previous_event_hash text,
  event_hash text not null check (event_hash ~ '^[0-9a-f]{64}$'),
  hash_algorithm text not null default 'postgres-investigation-event-v1'
    check (hash_algorithm = 'postgres-investigation-event-v1'),
  created_at timestamptz not null default clock_timestamp(),
  foreign key (investigation_id, case_id) references public.investigations(id, case_id),
  unique (investigation_id, sequence),
  check ((sequence = 1 and previous_event_hash is null)
      or (sequence > 1 and previous_event_hash ~ '^[0-9a-f]{64}$'))
);

create or replace function public.phase3_assert_blind()
returns void language plpgsql security definer set search_path = pg_catalog, public as $$
begin
  if current_setting('role', true) is distinct from 'casezero_blind' then
    raise exception 'blind role required' using errcode = '42501';
  end if;
end
$$;

create or replace function public.phase3_assert_lease(target_job_id uuid, target_worker text, target_attempt integer)
returns public.investigation_jobs language plpgsql security definer set search_path = pg_catalog, public as $$
declare result public.investigation_jobs;
begin
  perform public.phase3_assert_blind();
  select * into result from public.investigation_jobs where id = target_job_id for update;
  if not found or result.status <> 'RUNNING' or result.worker_id is distinct from target_worker
     or result.attempt_count is distinct from target_attempt or result.lease_expires_at <= clock_timestamp() then
    raise exception 'job lease is not owned' using errcode = '55000';
  end if;
  perform 1 from public.cases where id = result.case_id and state = 'BLIND' and evidence_cutoff is not null for share;
  if not found then
    raise exception 'case is not blind' using errcode = '42501';
  end if;
  return result;
end
$$;

create or replace function public.append_investigation_event(
  target_investigation_id uuid, target_event_type text, target_type text,
  target_id uuid, target_payload jsonb, target_model_run_id uuid default null
) returns public.investigation_events language plpgsql security definer set search_path = pg_catalog, public as $$
declare inv public.investigations; result public.investigation_events; envelope jsonb;
begin
  perform public.phase3_assert_blind();
  select * into inv from public.investigations where id = target_investigation_id for update;
  if not found or inv.status not in ('PENDING', 'RUNNING') then
    raise exception 'investigation is not writable' using errcode = '55000';
  end if;
  perform 1 from public.cases where id = inv.case_id and state = 'BLIND' and evidence_cutoff is not null for share;
  if not found then raise exception 'case is not blind' using errcode = '42501'; end if;
  if target_payload->>'kind' is distinct from target_event_type then
    raise exception 'event payload type mismatch' using errcode = '22023';
  end if;
  if target_model_run_id is not null and not exists (
    select 1 from public.model_runs where id = target_model_run_id and case_id = inv.case_id
  ) then raise exception 'event model run mismatch' using errcode = '23514'; end if;
  envelope := jsonb_build_object(
    'previous_hash', inv.event_head_hash, 'investigation_id', inv.id, 'case_id', inv.case_id,
    'sequence', inv.event_sequence + 1, 'event_type', target_event_type,
    'target_type', target_type, 'target_id', target_id, 'payload', target_payload,
    'model_run_id', target_model_run_id
  );
  insert into public.investigation_events(
    investigation_id, case_id, sequence, event_type, target_type, target_id, payload,
    model_run_id, previous_event_hash, event_hash
  ) values (
    inv.id, inv.case_id, inv.event_sequence + 1, target_event_type, target_type, target_id,
    target_payload, target_model_run_id, inv.event_head_hash,
    encode(extensions.digest(convert_to(envelope::text, 'UTF8'), 'sha256'), 'hex')
  ) returning * into result;
  update public.investigations set event_sequence = result.sequence, event_head_hash = result.event_hash
    where id = inv.id;
  return result;
end
$$;

create or replace function public.phase3_enqueue_stage(target_investigation_id uuid, target_stage text)
returns void language plpgsql security definer set search_path = pg_catalog, public as $$
declare inv public.investigations; key text; keys text[]; input_hash text;
begin
  select * into strict inv from public.investigations where id = target_investigation_id;
  if target_stage in ('SEARCH_SUPPORT', 'SEARCH_CONTRADICTIONS', 'DESIGN_FALSIFICATION_TESTS') then
    select array_agg(id::text order by id) into keys from public.hypotheses where investigation_id = inv.id;
  elsif target_stage = 'EXECUTE_FALSIFICATION_TESTS' then
    select array_agg(id::text order by id) into keys from public.hypothesis_tests where investigation_id = inv.id;
  end if;
  keys := coalesce(keys, array['case']);
  foreach key in array keys loop
    input_hash := encode(extensions.digest(convert_to(jsonb_build_object(
      'algorithm', 'postgres-investigation-input-v1', 'configuration', inv.configuration,
      'evidence_set_hash', inv.evidence_set_hash, 'stage', target_stage,
      'work_key', key, 'upstream_event_hash', inv.event_head_hash
    )::text, 'UTF8'), 'sha256'), 'hex');
    insert into public.investigation_jobs(investigation_id, case_id, stage, work_key, input_state_hash)
    values (inv.id, inv.case_id, target_stage, key, input_hash)
    on conflict (investigation_id, stage, work_key, input_state_hash) do nothing;
  end loop;
end
$$;

create or replace function public.create_investigation(target_case_id uuid, target_configuration jsonb)
returns public.investigations language plpgsql security definer set search_path = pg_catalog, public as $$
declare result public.investigations; snapshot jsonb; snapshot_hash text; config_hash text;
begin
  perform public.phase3_assert_blind();
  perform 1 from public.cases where id = target_case_id and state = 'BLIND'
    and evidence_cutoff is not null for update;
  if not found then raise exception 'case must be blind with cutoff' using errcode = '42501'; end if;
  if target_configuration is null or jsonb_typeof(target_configuration) <> 'object' or target_configuration = '{}' then
    raise exception 'configuration is required' using errcode = '22023';
  end if;
  select jsonb_agg(jsonb_build_object('evidence_id', e.id, 'model_run_id', e.model_run_id,
    'item', e.item, 'review_status', e.review_status, 'source_checksum', s.checksum) order by e.id)
  into snapshot
  from public.evidence_items e
  join public.semantic_unit_completions c on c.structural_unit_id = e.structural_unit_id and c.model_run_id = e.model_run_id
  join public.source_documents s on s.id = e.source_document_id and s.case_id = e.case_id
  join public.structural_units u on u.id = e.structural_unit_id and u.source_document_id = s.id
  join public.model_runs m on m.id = e.model_run_id and m.case_id = e.case_id and m.status = 'SUCCEEDED'
  join public.docket_items d on d.id = s.docket_item_id and d.case_id = s.case_id
  join public.cases cr on cr.id = s.case_id
  where e.case_id = target_case_id and e.review_status in ('NOT_REQUIRED', 'ACCEPTED')
    and public.is_blind_metadata_eligible(s.title, s.document_type, s.visibility, s.published_at, cr.evidence_cutoff, d.processing_disposition);
  if snapshot is null then raise exception 'eligible reviewed evidence required' using errcode = '55000'; end if;
  snapshot_hash := encode(extensions.digest(convert_to(snapshot::text, 'UTF8'), 'sha256'), 'hex');
  config_hash := encode(extensions.digest(convert_to(jsonb_build_object(
    'algorithm', 'postgres-investigation-config-v1', 'configuration', target_configuration,
    'evidence_set_hash', snapshot_hash)::text, 'UTF8'), 'sha256'), 'hex');
  select * into result from public.investigations where case_id = target_case_id
    and configuration_hash = config_hash order by created_at desc limit 1;
  if found then return result; end if;
  insert into public.investigations(case_id, status, current_stage, configuration_hash, configuration,
    evidence_set_hash, model_versions, prompt_versions, created_at)
  values (target_case_id, 'PENDING', 'PROMOTE_TIMELINE', config_hash, target_configuration,
    snapshot_hash, target_configuration->'model_versions',
    target_configuration->'prompt_versions', clock_timestamp())
  returning * into result;
  insert into public.investigation_evidence(investigation_id, case_id, evidence_id, model_run_id)
  select result.id, target_case_id, (entry->>'evidence_id')::uuid, (entry->>'model_run_id')::uuid
    from jsonb_array_elements(snapshot) entry;
  insert into public.investigation_candidates(investigation_id, case_id, candidate_id, kind, model_run_id)
  select result.id, target_case_id, c.id, c.kind, c.model_run_id from (
    select id, case_id, model_run_id, evidence_ids, 'claim'::text kind from public.claim_candidates
    union all select id, case_id, model_run_id, evidence_ids, 'entity' from public.entity_candidates
    union all select id, case_id, model_run_id, evidence_ids, 'timeline' from public.timeline_candidates
  ) c where c.case_id = target_case_id and cardinality(c.evidence_ids) > 0
    and exists (select 1 from public.candidate_batch_completions b where b.case_id = target_case_id and b.model_run_id = c.model_run_id)
    and not exists (select 1 from unnest(c.evidence_ids) eid where not exists (
      select 1 from public.investigation_evidence p where p.investigation_id = result.id and p.evidence_id = eid
    ));
  perform public.append_investigation_event(result.id, 'INVESTIGATION_STARTED', 'investigation', result.id,
    jsonb_build_object('kind','INVESTIGATION_STARTED', 'configuration_hash',config_hash,
      'current_stage','PROMOTE_TIMELINE','status','PENDING'));
  perform public.phase3_enqueue_stage(result.id, 'PROMOTE_TIMELINE');
  select * into result from public.investigations where id = result.id;
  return result;
end
$$;

create or replace function public.claim_investigation_job(target_investigation_id uuid, target_worker text)
returns setof public.investigation_jobs language plpgsql security definer set search_path = pg_catalog, public as $$
declare inv public.investigations; expired public.investigation_jobs; result public.investigation_jobs;
begin
  perform public.phase3_assert_blind();
  if target_worker is null or length(target_worker) not between 1 and 128 then
    raise exception 'worker required' using errcode = '22023';
  end if;
  select * into inv from public.investigations where id = target_investigation_id;
  if not found or inv.status not in ('PENDING', 'RUNNING') then return; end if;
  perform 1 from public.cases where id = inv.case_id and state = 'BLIND' and evidence_cutoff is not null for share;
  if not found then raise exception 'case is not blind' using errcode = '42501'; end if;
  for expired in select * from public.investigation_jobs where investigation_id = inv.id
    and status = 'RUNNING' and lease_expires_at <= clock_timestamp() for update skip locked
  loop
    update public.investigation_job_attempts set outcome='LEASE_EXPIRED', failure_code='LEASE_EXPIRED',
      completed_at=clock_timestamp() where job_id=expired.id and attempt_number=expired.attempt_count;
    update public.model_request_attempts set status='LEASE_EXPIRED', failure_code='LEASE_EXPIRED', completed_at=clock_timestamp()
      where job_id=expired.id and status in ('RESERVED','RUNNING');
    update public.investigation_jobs set status=case when attempt_count >= 3 or model_request_count >= 3 then 'FAILED' else 'PENDING' end,
      worker_id=null, claimed_at=null, lease_expires_at=null, failure_code='LEASE_EXPIRED',
      completed_at=case when attempt_count >= 3 or model_request_count >= 3 then clock_timestamp() else null end,
      retryable=(attempt_count < 3 and model_request_count < 3) where id=expired.id;
  end loop;
  if exists(select 1 from public.investigation_jobs where investigation_id=inv.id and status='FAILED') then
    update public.investigations set status='FAILED', failure_code='LEASE_EXPIRED', completed_at=clock_timestamp() where id=inv.id;
    return;
  end if;
  select * into result from public.investigation_jobs where investigation_id=inv.id and stage=inv.current_stage
    and status='PENDING' and attempt_count < 3 order by created_at, work_key, id for update skip locked limit 1;
  if not found then return; end if;
  update public.investigation_jobs set status='RUNNING', attempt_count=attempt_count+1,
    worker_id=target_worker, claimed_at=clock_timestamp(), lease_expires_at=clock_timestamp()+interval '30 minutes',
    failure_code=null, completed_at=null where id=result.id returning * into result;
  insert into public.investigation_job_attempts(job_id,investigation_id,case_id,attempt_number,worker_id,started_at,outcome)
  values(result.id,inv.id,inv.case_id,result.attempt_count,target_worker,result.claimed_at,'RUNNING');
  update public.investigations set status='RUNNING', started_at=coalesce(started_at,clock_timestamp()) where id=inv.id;
  return next result;
end
$$;

create or replace function public.reserve_model_request(target_job_id uuid, target_worker text, target_attempt integer)
returns public.model_request_attempts language plpgsql security definer set search_path = pg_catalog, public as $$
declare job public.investigation_jobs; result public.model_request_attempts;
begin
  job := public.phase3_assert_lease(target_job_id, target_worker, target_attempt);
  if job.model_request_count >= 3 then raise exception 'model request budget exhausted' using errcode='54000'; end if;
  update public.investigation_jobs set model_request_count=model_request_count+1 where id=job.id;
  update public.investigation_job_attempts set model_request_count=model_request_count+1
    where job_id=job.id and attempt_number=target_attempt;
  insert into public.model_request_attempts(job_id,investigation_id,case_id,job_attempt_number,request_ordinal,status)
  values(job.id,job.investigation_id,job.case_id,target_attempt,job.model_request_count+1,'RESERVED') returning * into result;
  return result;
end
$$;

create or replace function public.complete_stage_job(target_job_id uuid, target_worker text, target_attempt integer)
returns void language plpgsql security definer set search_path = pg_catalog, public as $$
declare job public.investigation_jobs; next_stage text; stages text[] := array[
 'PROMOTE_TIMELINE','RESOLVE_ENTITIES','PROMOTE_CLAIMS','GENERATE_HYPOTHESES','SEARCH_SUPPORT','SEARCH_CONTRADICTIONS',
 'DESIGN_FALSIFICATION_TESTS','EXECUTE_FALSIFICATION_TESTS','REVISE_CONFIDENCE','VERIFY_REPLAY','COMPLETE'];
begin
  job := public.phase3_assert_lease(target_job_id,target_worker,target_attempt);
  perform 1 from public.investigations where id=job.investigation_id and current_stage=job.stage and status='RUNNING' for update;
  if not found then raise exception 'stage is not running' using errcode='55000'; end if;
  update public.investigation_job_attempts set outcome='SUCCEEDED', completed_at=clock_timestamp()
    where job_id=job.id and attempt_number=target_attempt;
  update public.investigation_jobs set status='SUCCEEDED', completed_at=clock_timestamp(),retryable=false,
    worker_id=null,claimed_at=null,lease_expires_at=null where id=job.id;
  if exists(select 1 from public.investigation_jobs where investigation_id=job.investigation_id and stage=job.stage and status<>'SUCCEEDED') then return; end if;
  next_stage := stages[array_position(stages,job.stage)+1];
  if next_stage is null then
    update public.investigations set status='SUCCEEDED',completed_at=clock_timestamp() where id=job.investigation_id;
  else
    perform public.append_investigation_event(job.investigation_id,'STAGE_TRANSITION','investigation',job.investigation_id,
      jsonb_build_object('kind','STAGE_TRANSITION','previous_stage',job.stage,'next_stage',next_stage));
    update public.investigations set current_stage=next_stage where id=job.investigation_id;
    perform public.phase3_enqueue_stage(job.investigation_id,next_stage);
  end if;
end
$$;

create or replace function public.fail_stage_job(target_job_id uuid, target_worker text, target_attempt integer, target_failure text)
returns void language plpgsql security definer set search_path = pg_catalog, public as $$
declare job public.investigation_jobs;
begin
  job := public.phase3_assert_lease(target_job_id,target_worker,target_attempt);
  if target_failure not in ('PROVIDER_EXHAUSTED','SCHEMA_INVALID','REFERENCE_INVALID','DIVERSITY_INVALID','LEASE_EXPIRED','REPLAY_MISMATCH','PERSISTENCE_FAILED') then
    raise exception 'unknown failure code' using errcode='22023';
  end if;
  update public.investigation_job_attempts set outcome='FAILED', failure_code=target_failure,completed_at=clock_timestamp()
    where job_id=job.id and attempt_number=target_attempt;
  update public.investigation_jobs set status='FAILED',failure_code=target_failure,retryable=false,completed_at=clock_timestamp(),
    worker_id=null,claimed_at=null,lease_expires_at=null where id=job.id;
  update public.investigations set status='FAILED',failure_code=target_failure,completed_at=clock_timestamp() where id=job.investigation_id;
end
$$;

do $$
declare relation_name text; fn record;
begin
  foreach relation_name in array array['investigation_evidence','investigation_candidates','investigation_jobs',
    'investigation_job_attempts','model_request_attempts','investigation_events'] loop
    execute format('alter table public.%I enable row level security', relation_name);
    execute format('alter table public.%I force row level security', relation_name);
    execute format('revoke all on public.%I from public,anon,authenticated,casezero_blind,casezero_eval,casezero_processor', relation_name);
    execute format('grant select on public.%I to casezero_blind,casezero_eval', relation_name);
    execute format('create policy %I on public.%I for select to casezero_blind using(public.phase3_blind_can_read(case_id))',relation_name||'_blind_read',relation_name);
    execute format('create policy %I on public.%I for select to casezero_eval using(public.phase3_eval_can_read(case_id))',relation_name||'_eval_read',relation_name);
    execute format('create trigger %I before insert or update or delete on public.%I for each row execute function public.require_blind_case_row()',relation_name||'_lock_guard',relation_name);
  end loop;
  for fn in select p.oid::regprocedure signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname in ('phase3_assert_blind','phase3_assert_lease','phase3_enqueue_stage',
    'create_investigation','claim_investigation_job','reserve_model_request','append_investigation_event','complete_stage_job','fail_stage_job') loop
    execute format('revoke all on function %s from public,anon,authenticated,casezero_processor,casezero_eval,casezero_blind',fn.signature);
  end loop;
end $$;
grant execute on function public.create_investigation(uuid,jsonb), public.claim_investigation_job(uuid,text),
  public.reserve_model_request(uuid,text,integer), public.append_investigation_event(uuid,text,text,uuid,jsonb,uuid),
  public.complete_stage_job(uuid,text,integer), public.fail_stage_job(uuid,text,integer,text),
  public.phase3_assert_lease(uuid,text,integer) to casezero_blind;
revoke insert,update,delete on public.investigations from casezero_blind;

grant select on public.evidence_items, public.structural_units, public.semantic_unit_completions,
 public.claim_candidates, public.entity_candidates, public.timeline_candidates, public.candidate_batch_completions,
 public.model_runs to casezero_blind;
create policy blind_evidence_items on public.evidence_items for select to casezero_blind
 using(exists(select 1 from public.source_documents s where s.id=evidence_items.source_document_id));
create policy blind_structural_units on public.structural_units for select to casezero_blind
 using(exists(select 1 from public.source_documents s where s.id=structural_units.source_document_id));
create policy blind_semantic_completions on public.semantic_unit_completions for select to casezero_blind
 using(exists(select 1 from public.structural_units u where u.id=semantic_unit_completions.structural_unit_id));
create policy blind_model_runs on public.model_runs for select to casezero_blind
 using(public.phase3_blind_can_read(case_id));
create policy blind_candidate_completions on public.candidate_batch_completions for select to casezero_blind
 using(public.phase3_blind_can_read(case_id));
do $$ declare relation_name text; begin
 foreach relation_name in array array['claim_candidates','entity_candidates','timeline_candidates'] loop
   execute format('create policy %I on public.%I for select to casezero_blind using(public.phase3_blind_can_read(case_id) and not exists(select 1 from unnest(evidence_ids) eid where not exists(select 1 from public.evidence_items e where e.id=eid)))',relation_name||'_blind_read',relation_name);
 end loop;
end $$;
