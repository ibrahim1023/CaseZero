alter table public.model_runs
  add column investigation_id uuid references public.investigations(id),
  add column job_id uuid references public.investigation_jobs(id),
  add column parent_span_id uuid,
  add column prompt_git_sha text,
  add unique (id, case_id),
  add unique (id, investigation_id, case_id),
  add constraint model_runs_phase3_context check (
    (investigation_id is null and job_id is null and parent_span_id is null and prompt_git_sha is null)
    or (investigation_id is not null and job_id is not null and parent_span_id is not null
        and prompt_git_sha is not null and prompt_git_sha ~ '^[0-9a-f]{7,40}$')
  ),
  add foreign key (job_id, investigation_id, case_id)
    references public.investigation_jobs(id, investigation_id, case_id);
create table public.investigation_spans (
  id uuid primary key default gen_random_uuid(),
  investigation_id uuid not null,
  case_id uuid not null,
  job_id uuid not null,
  parent_span_id uuid references public.investigation_spans(id),
  kind text not null check (kind in ('AGENT','TOOL','RETRIEVAL')),
  operation text not null check (length(operation) between 1 and 100),
  operation_version text not null check (length(operation_version) between 1 and 100),
  status text not null check (status in ('RUNNING','SUCCEEDED','FAILED')),
  started_at timestamptz not null default clock_timestamp(),
  completed_at timestamptz,
  duration_ms bigint check (duration_ms >= 0),
  foreign key (job_id, investigation_id, case_id)
    references public.investigation_jobs(id, investigation_id, case_id),
  unique (id, investigation_id, case_id)
);
alter table public.model_runs add foreign key (parent_span_id, investigation_id, case_id)
  references public.investigation_spans(id, investigation_id, case_id);

alter table public.confidence_revisions drop constraint confidence_revisions_delta_check;
alter table public.confidence_revisions alter column delta type numeric;
alter table public.confidence_revisions add check (
  delta not in ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric) and scale(delta) <= 4
);
alter table public.hypothesis_tests add foreign key (job_id, investigation_id, case_id)
 references public.investigation_jobs(id, investigation_id, case_id);

do $$ declare name text; begin
 foreach name in array array['claims','investigation_entities','timeline_events','hypotheses','hypothesis_critiques','hypothesis_tests','confidence_revisions','unresolved_questions'] loop
   execute format('alter table public.%I add unique(id,investigation_id,case_id)', name);
   execute format('alter table public.%I add foreign key(investigation_id,case_id) references public.investigations(id,case_id)',name);
 end loop;
 foreach name in array array['claims','investigation_entities','timeline_events','hypotheses','hypothesis_critiques','hypothesis_tests'] loop
   execute format('alter table public.%I add foreign key(model_run_id,case_id) references public.model_runs(id,case_id)',name);
 end loop;
end $$;

alter table public.claim_candidate_links add foreign key(claim_id,investigation_id,case_id) references public.claims(id,investigation_id,case_id);
alter table public.claim_evidence_links add foreign key(claim_id,investigation_id,case_id) references public.claims(id,investigation_id,case_id);
alter table public.entity_candidate_links add foreign key(entity_id,investigation_id,case_id) references public.investigation_entities(id,investigation_id,case_id);
alter table public.entity_evidence_links add foreign key(entity_id,investigation_id,case_id) references public.investigation_entities(id,investigation_id,case_id);
alter table public.timeline_candidate_links add foreign key(timeline_event_id,investigation_id,case_id) references public.timeline_events(id,investigation_id,case_id);
alter table public.timeline_evidence_links add foreign key(timeline_event_id,investigation_id,case_id) references public.timeline_events(id,investigation_id,case_id);
alter table public.hypothesis_claim_links
 add foreign key(hypothesis_id,investigation_id,case_id) references public.hypotheses(id,investigation_id,case_id),
 add foreign key(claim_id,investigation_id,case_id) references public.claims(id,investigation_id,case_id);
alter table public.hypothesis_critiques add foreign key(hypothesis_id,investigation_id,case_id) references public.hypotheses(id,investigation_id,case_id);
alter table public.hypothesis_tests
 add foreign key(hypothesis_id,investigation_id,case_id) references public.hypotheses(id,investigation_id,case_id),
 add foreign key(critique_id,investigation_id,case_id) references public.hypothesis_critiques(id,investigation_id,case_id);
alter table public.unresolved_questions add foreign key(hypothesis_id,investigation_id,case_id) references public.hypotheses(id,investigation_id,case_id);
alter table public.confidence_revisions add foreign key(hypothesis_id,investigation_id,case_id) references public.hypotheses(id,investigation_id,case_id);
alter table public.confidence_revision_test_deltas
 add foreign key(revision_id,investigation_id,case_id) references public.confidence_revisions(id,investigation_id,case_id),
 add foreign key(test_id,investigation_id,case_id) references public.hypothesis_tests(id,investigation_id,case_id);

do $$ declare name text; begin
 foreach name in array array['claim_evidence_links','entity_evidence_links','timeline_evidence_links'] loop
   execute format('alter table public.%I add foreign key(investigation_id,case_id,evidence_id) references public.investigation_evidence(investigation_id,case_id,evidence_id)',name);
 end loop;
end $$;
alter table public.investigation_candidates add unique(investigation_id,case_id,candidate_id);
alter table public.claim_candidate_links add foreign key(investigation_id,case_id,candidate_id) references public.investigation_candidates(investigation_id,case_id,candidate_id);
alter table public.entity_candidate_links add foreign key(investigation_id,case_id,candidate_id) references public.investigation_candidates(investigation_id,case_id,candidate_id);
alter table public.timeline_candidate_links add foreign key(investigation_id,case_id,candidate_id) references public.investigation_candidates(investigation_id,case_id,candidate_id);

create or replace function public.phase3_blind_can_read(target_case_id uuid)
returns boolean language sql stable security definer set search_path=pg_catalog,public as $$
 select exists(select 1 from public.cases where id=target_case_id and state in ('BLIND','LOCKED') and evidence_cutoff is not null)
$$;
create or replace function public.phase3_blind_can_write(target_case_id uuid)
returns boolean language sql stable security definer set search_path=pg_catalog,public as $$
 select exists(select 1 from public.cases where id=target_case_id and state='BLIND' and evidence_cutoff is not null)
$$;

create or replace function public.phase3_guard_canonical_write()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
declare inv public.investigations;
begin
 if tg_op = 'UPDATE' and (old.investigation_id <> new.investigation_id or old.case_id <> new.case_id) then
   raise exception 'canonical context is immutable' using errcode='23514';
 end if;
 perform 1 from public.cases where id=new.case_id and state='BLIND' and evidence_cutoff is not null for share;
 if not found then raise exception 'case is not blind' using errcode='42501'; end if;
 select * into inv from public.investigations where id=new.investigation_id and case_id=new.case_id for share;
 if not found or inv.status not in ('PENDING','RUNNING') then
   raise exception 'investigation is not writable' using errcode='55000';
 end if;
 return new;
end
$$;

do $$ declare name text; begin
 foreach name in array array['claims','claim_candidate_links','claim_evidence_links','investigation_entities','entity_candidate_links',
  'entity_evidence_links','timeline_events','timeline_candidate_links','timeline_evidence_links','hypotheses','hypothesis_claim_links',
  'unresolved_questions','hypothesis_critiques','hypothesis_tests','confidence_revisions','confidence_revision_test_deltas'] loop
  execute format('create trigger %I before insert or update on public.%I for each row execute function public.phase3_guard_canonical_write()',name||'_context_guard',name);
 end loop;
end $$;

create or replace function public.create_investigation_span(
 target_job_id uuid, target_worker text, target_attempt integer,
 target_kind text, target_operation text, target_version text, target_parent uuid default null
) returns uuid language plpgsql security definer set search_path=pg_catalog,public as $$
declare job public.investigation_jobs; result uuid;
begin
 job := public.phase3_assert_lease(target_job_id,target_worker,target_attempt);
 if target_parent is not null and not exists(select 1 from public.investigation_spans
  where id=target_parent and investigation_id=job.investigation_id and job_id=job.id) then
   raise exception 'span parent context mismatch' using errcode='23514';
 end if;
 insert into public.investigation_spans(investigation_id,case_id,job_id,parent_span_id,kind,operation,operation_version,status)
 values(job.investigation_id,job.case_id,job.id,target_parent,target_kind,target_operation,target_version,'RUNNING') returning id into result;
 return result;
end $$;

create or replace function public.finish_investigation_span(
 target_span_id uuid, target_worker text, target_attempt integer, target_status text
) returns void language plpgsql security definer set search_path=pg_catalog,public as $$
declare span public.investigation_spans;
begin
 select * into strict span from public.investigation_spans where id=target_span_id for update;
 perform public.phase3_assert_lease(span.job_id,target_worker,target_attempt);
 if span.status <> 'RUNNING' or target_status not in ('SUCCEEDED','FAILED') then
   raise exception 'invalid span transition' using errcode='55000';
 end if;
 update public.investigation_spans set status=target_status,completed_at=clock_timestamp(),
  duration_ms=greatest(0,floor(extract(epoch from clock_timestamp()-started_at)*1000)) where id=span.id;
end $$;

create or replace function public.finish_model_request(
 target_request_id uuid, target_worker text, target_attempt integer,
 target_status text, target_issues jsonb, target_model text, target_prompt_hash text,
 target_git_sha text, target_span_id uuid, target_input_tokens integer,
 target_output_tokens integer, target_latency_ms integer
) returns void language plpgsql security definer set search_path=pg_catalog,public as $$
declare request public.model_request_attempts; job public.investigation_jobs; issue jsonb;
begin
 select * into strict request from public.model_request_attempts where id=target_request_id for update;
 job := public.phase3_assert_lease(request.job_id,target_worker,target_attempt);
 if request.status not in ('RESERVED','RUNNING') or request.job_attempt_number <> target_attempt
  or target_status not in ('SUCCEEDED','SCHEMA_FAILED','PROVIDER_FAILED') then
   raise exception 'invalid model request transition' using errcode='55000';
 end if;
 if target_issues is null or jsonb_typeof(target_issues) <> 'array' then
  raise exception 'invalid validation issues' using errcode='22023';
 end if;
 for issue in select * from jsonb_array_elements(target_issues) loop
  if jsonb_typeof(issue) <> 'object' or not(issue ?& array['path','code'])
    or issue - 'path' - 'code' <> '{}' or jsonb_typeof(issue->'path') <> 'array'
    or jsonb_typeof(issue->'code') <> 'string' then
    raise exception 'validation issues must contain only path and code' using errcode='22023';
  end if;
 end loop;
 if target_status <> 'SCHEMA_FAILED' and target_issues <> '[]' then
  raise exception 'unexpected validation context' using errcode='22023';
 end if;
 insert into public.model_runs(id,case_id,stage,provider,model,prompt_hash,structural_unit_ids,
  input_tokens,output_tokens,latency_ms,retry_count,schema_failure_count,status,created_at,
  investigation_id,job_id,parent_span_id,prompt_git_sha)
 values(request.id,job.case_id,job.stage,'hyperfusion',target_model,target_prompt_hash,'{}',
  target_input_tokens,target_output_tokens,target_latency_ms,0,case when target_status='SCHEMA_FAILED' then 1 else 0 end,
  case when target_status='SUCCEEDED' then 'SUCCEEDED' else 'FAILED' end,request.started_at,
  job.investigation_id,job.id,target_span_id,target_git_sha);
 update public.model_request_attempts set status=target_status,validation_issues=target_issues,
  failure_code=case target_status when 'SCHEMA_FAILED' then 'SCHEMA_INVALID' when 'PROVIDER_FAILED' then 'PROVIDER_EXHAUSTED' else null end,
  completed_at=clock_timestamp() where id=request.id;
end $$;

alter table public.investigation_spans enable row level security;
alter table public.investigation_spans force row level security;
revoke all on public.investigation_spans from public,anon,authenticated,casezero_blind,casezero_eval;
grant select on public.investigation_spans to casezero_blind,casezero_eval;
create policy investigation_spans_blind_read on public.investigation_spans for select to casezero_blind using(public.phase3_blind_can_read(case_id));
create policy investigation_spans_eval_read on public.investigation_spans for select to casezero_eval using(public.phase3_eval_can_read(case_id));
create trigger investigation_spans_lock_guard before insert or update or delete on public.investigation_spans
 for each row execute function public.require_blind_case_row();

revoke all on function public.phase3_guard_canonical_write() from public,anon,authenticated;
revoke all on function public.create_investigation_span(uuid,text,integer,text,text,text,uuid),
 public.finish_investigation_span(uuid,text,integer,text),
 public.finish_model_request(uuid,text,integer,text,jsonb,text,text,text,uuid,integer,integer,integer)
 from public,anon,authenticated,casezero_eval,casezero_processor;
grant execute on function public.create_investigation_span(uuid,text,integer,text,text,text,uuid),
 public.finish_investigation_span(uuid,text,integer,text),
 public.finish_model_request(uuid,text,integer,text,jsonb,text,text,text,uuid,integer,integer,integer)
 to casezero_blind;
