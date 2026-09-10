create or replace function public.phase3_enqueue_stage(target_investigation_id uuid, target_stage text)
returns void language plpgsql security definer set search_path=pg_catalog,public as $$
declare inv public.investigations; key text; keys text[]; input_hash text; candidate_kind text;
begin
 select * into strict inv from public.investigations where id=target_investigation_id;
 if target_stage in ('PROMOTE_TIMELINE','RESOLVE_ENTITIES','PROMOTE_CLAIMS') then
  candidate_kind := case target_stage when 'PROMOTE_TIMELINE' then 'timeline' when 'RESOLVE_ENTITIES' then 'entity' else 'claim' end;
  select array_agg(batch::text order by batch) into keys from (
    select distinct ((row_number() over(order by candidate_id))-1)/5 as batch
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
