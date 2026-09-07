create index evidence_observation_fts_idx on public.evidence_items using gin
 (to_tsvector('english'::regconfig, coalesce(item->>'observation', '')))
 where review_status in ('NOT_REQUIRED','ACCEPTED');

create table public.retrieval_queries (
 id uuid primary key,
 investigation_id uuid not null,
 case_id uuid not null,
 hypothesis_id uuid,
 job_id uuid not null,
 model_run_id uuid references public.model_runs(id),
 intent text not null check(intent in ('SUPPORT','CONTRADICT','NEUTRAL')),
 query_text text not null check(length(query_text) between 1 and 4096),
 evidence_types text[] not null default '{}',
 document_types text[] not null default '{}',
 entity_ids uuid[] not null default '{}',
 start_at timestamptz,
 end_at timestamptz,
 result_limit integer not null check(result_limit between 1 and 50),
 config_version text not null check(config_version='fts-v1'),
 query_hash text not null check(query_hash ~ '^[0-9a-f]{64}$'),
 created_at timestamptz not null,
 unique(id,investigation_id,case_id),
 foreign key(job_id,investigation_id,case_id) references public.investigation_jobs(id,investigation_id,case_id),
 foreign key(hypothesis_id,investigation_id,case_id) references public.hypotheses(id,investigation_id,case_id),
 check(start_at is null or end_at is null or end_at >= start_at)
);
create table public.retrieval_results (
 investigation_id uuid not null,
 case_id uuid not null,
 query_id uuid not null,
 evidence_id uuid not null,
 rank integer not null check(rank between 1 and 50),
 total_score numeric not null check(total_score between 0 and 1),
 fts_score numeric not null check(fts_score between 0 and 1),
 entity_score numeric not null check(entity_score between 0 and 1),
 time_score numeric not null check(time_score between 0 and 1),
 type_score numeric not null check(type_score between 0 and 1),
 matched_filters text[] not null default '{}',
 primary key(query_id,evidence_id),
 unique(query_id,rank),
 foreign key(query_id,investigation_id,case_id) references public.retrieval_queries(id,investigation_id,case_id),
 foreign key(investigation_id,case_id,evidence_id) references public.investigation_evidence(investigation_id,case_id,evidence_id)
);

do $$ declare name text; begin
 foreach name in array array['retrieval_queries','retrieval_results'] loop
  execute format('alter table public.%I enable row level security',name);
  execute format('alter table public.%I force row level security',name);
  execute format('revoke all on public.%I from public,anon,authenticated,casezero_processor,casezero_eval,casezero_blind',name);
  execute format('grant select,insert on public.%I to casezero_blind',name);
  execute format('grant select on public.%I to casezero_eval',name);
  execute format('create policy %I on public.%I for select to casezero_blind using(public.phase3_blind_can_read(case_id))',name||'_blind_read',name);
  execute format('create policy %I on public.%I for insert to casezero_blind with check(public.phase3_blind_can_write(case_id))',name||'_blind_insert',name);
  execute format('create policy %I on public.%I for select to casezero_eval using(public.phase3_eval_can_read(case_id))',name||'_eval_read',name);
  execute format('create trigger %I before insert or update on public.%I for each row execute function public.phase3_guard_canonical_write()',name||'_write_guard',name);
  execute format('create trigger %I before update or delete on public.%I for each row execute function public.reject_access_audit_mutation()',name||'_immutable',name);
 end loop;
end $$;

alter table public.hypothesis_critiques drop constraint hypothesis_critiques_strongest_contradiction_id_fkey;
create or replace function public.phase3_validate_critique_refs()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
begin
 if new.strongest_contradiction_id is not null and not exists (
  select 1 from public.investigation_evidence p where p.investigation_id=new.investigation_id
   and p.case_id=new.case_id and p.evidence_id=new.strongest_contradiction_id
 ) and not exists (
  select 1 from public.claims c where c.investigation_id=new.investigation_id
   and c.case_id=new.case_id and c.id=new.strongest_contradiction_id
 ) then raise exception 'critique reference outside investigation' using errcode='23514'; end if;
 return new;
end $$;
create trigger hypothesis_critique_refs before insert or update on public.hypothesis_critiques
 for each row execute function public.phase3_validate_critique_refs();
revoke all on function public.phase3_validate_critique_refs() from public,anon,authenticated;

alter table public.access_audit_events drop constraint access_audit_events_capability_check;
alter table public.access_audit_events add constraint access_audit_events_capability_check check(capability in (
 'NTSB_ACQUISITION','CONTEXT_DEV','WEB_SEARCH','RAW_STORAGE','SUPABASE_DATABASE','MODEL_INFERENCE','EVIDENCE_READ','LOCK',
 'INVESTIGATION_TOOL','RETRIEVAL'
));

create or replace function public.phase3_enqueue_stage(target_investigation_id uuid, target_stage text)
returns void language plpgsql security definer set search_path=pg_catalog,public as $$
declare inv public.investigations; key text; keys text[]; input_hash text; candidate_kind text;
begin
 select * into strict inv from public.investigations where id=target_investigation_id;
 if target_stage in ('PROMOTE_TIMELINE','RESOLVE_ENTITIES','PROMOTE_CLAIMS') then
  candidate_kind := case target_stage when 'PROMOTE_TIMELINE' then 'timeline' when 'RESOLVE_ENTITIES' then 'entity' else 'claim' end;
  select array_agg(batch::text order by batch) into keys from (
    select distinct ((row_number() over(order by candidate_id))-1)/20 as batch
    from public.investigation_candidates where investigation_id=inv.id and kind=candidate_kind
  ) batches;
 elsif target_stage in ('SEARCH_SUPPORT','SEARCH_CONTRADICTIONS','DESIGN_FALSIFICATION_TESTS') then
  select array_agg(id::text order by id) into keys from public.hypotheses where investigation_id=inv.id;
 elsif target_stage='EXECUTE_FALSIFICATION_TESTS' then
  select array_agg(id::text order by id) into keys from public.hypothesis_tests where investigation_id=inv.id;
 end if;
 keys := coalesce(keys,array['case']);
 foreach key in array keys loop
  input_hash := encode(extensions.digest(convert_to(jsonb_build_object(
   'algorithm','postgres-investigation-input-v1','configuration',inv.configuration,
   'evidence_set_hash',inv.evidence_set_hash,'stage',target_stage,'work_key',key,
   'upstream_event_hash',inv.event_head_hash)::text,'UTF8'),'sha256'),'hex');
  insert into public.investigation_jobs(investigation_id,case_id,stage,work_key,input_state_hash)
  values(inv.id,inv.case_id,target_stage,key,input_hash)
  on conflict(investigation_id,stage,work_key,input_state_hash) do nothing;
 end loop;
end $$;

create or replace function public.phase3_guard_event_history()
returns trigger language plpgsql set search_path=pg_catalog,public as $$
begin
 if tg_op='DELETE' or tg_table_name='investigation_events' then
  raise exception 'investigation history is immutable' using errcode='55000';
 end if;
 if tg_table_name='investigation_job_attempts' then
  if old.outcome<>'RUNNING' then
   raise exception 'finished job attempts are immutable' using errcode='55000';
  end if;
 elsif tg_table_name='model_request_attempts' then
  if old.status not in ('RESERVED','RUNNING') then
   raise exception 'finished model requests are immutable' using errcode='55000';
  end if;
 end if;
 return new;
end $$;
create trigger investigation_event_history before update or delete on public.investigation_events
 for each row execute function public.phase3_guard_event_history();
create trigger investigation_attempt_history before update or delete on public.investigation_job_attempts
 for each row execute function public.phase3_guard_event_history();
create trigger investigation_request_history before update or delete on public.model_request_attempts
 for each row execute function public.phase3_guard_event_history();
revoke all on function public.phase3_guard_event_history() from public,anon,authenticated;
