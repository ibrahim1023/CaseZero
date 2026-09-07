create or replace function public.phase3_record_initial_running_transition()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
begin
  perform public.append_investigation_event(new.id,'STAGE_TRANSITION','investigation',new.id,
    jsonb_build_object('kind','STAGE_TRANSITION','previous_stage','PROMOTE_TIMELINE',
      'next_stage','PROMOTE_TIMELINE'));
  return new;
end
$$;

create trigger investigation_initial_running_transition
  after update of status on public.investigations
  for each row when (old.status='PENDING' and new.status='RUNNING'
    and old.current_stage='PROMOTE_TIMELINE' and new.current_stage='PROMOTE_TIMELINE')
  execute function public.phase3_record_initial_running_transition();

revoke all on function public.phase3_record_initial_running_transition()
  from public,anon,authenticated,casezero_blind,casezero_eval,casezero_processor;
