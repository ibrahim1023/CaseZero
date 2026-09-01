create table public.candidate_batch_completions (
  case_id uuid not null references public.cases(id) on delete restrict,
  batch_hash text not null check (batch_hash ~ '^[0-9a-f]{64}$'),
  model_run_id uuid not null references public.model_runs(id) on delete restrict,
  candidate_count integer not null check (candidate_count >= 0),
  completed_at timestamptz not null,
  primary key (case_id, batch_hash)
);

alter table public.candidate_batch_completions enable row level security;
alter table public.candidate_batch_completions force row level security;

create policy processor_candidate_completions on public.candidate_batch_completions
  for all to casezero_processor
  using (exists (
    select 1 from public.cases case_record
    where case_record.id = candidate_batch_completions.case_id
      and case_record.state = 'BLIND'
  ))
  with check (exists (
    select 1 from public.cases case_record
    where case_record.id = candidate_batch_completions.case_id
      and case_record.state = 'BLIND'
  ));

grant select, insert, update on public.candidate_batch_completions to casezero_processor;
