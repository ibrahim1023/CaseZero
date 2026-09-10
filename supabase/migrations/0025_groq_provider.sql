-- switch blind model-run audit provider from Hyperfusion to Groq

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
 values(request.id,job.case_id,job.stage,'groq',target_model,target_prompt_hash,'{}',
  target_input_tokens,target_output_tokens,target_latency_ms,0,case when target_status='SCHEMA_FAILED' then 1 else 0 end,
  case when target_status='SUCCEEDED' then 'SUCCEEDED' else 'FAILED' end,request.started_at,
  job.investigation_id,job.id,target_span_id,target_git_sha);
 update public.model_request_attempts set status=target_status,validation_issues=target_issues,
  failure_code=case target_status when 'SCHEMA_FAILED' then 'SCHEMA_INVALID' when 'PROVIDER_FAILED' then 'PROVIDER_EXHAUSTED' else null end,
  completed_at=clock_timestamp() where id=request.id;
end $$;

update public.model_runs set provider='groq' where provider='hyperfusion';
