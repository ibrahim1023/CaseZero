create or replace function public.phase3_close_expired_spans()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
declare finished_at timestamptz;
begin
  if old.outcome='RUNNING' and new.outcome='LEASE_EXPIRED' then
    finished_at := coalesce(new.completed_at,clock_timestamp());
    update public.investigation_spans
    set status='FAILED',completed_at=finished_at,
      duration_ms=greatest(0,floor(extract(epoch from finished_at-started_at)*1000))
    where job_id=new.job_id and status='RUNNING' and started_at>=old.started_at;
  end if;
  return new;
end
$$;

create trigger investigation_attempt_expired_spans
after update of outcome on public.investigation_job_attempts
for each row execute function public.phase3_close_expired_spans();

revoke all on function public.phase3_close_expired_spans()
from public,anon,authenticated,casezero_blind,casezero_eval,casezero_processor;
