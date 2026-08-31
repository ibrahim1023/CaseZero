create table public.semantic_unit_completions (
  structural_unit_id uuid primary key references public.structural_units(id) on delete restrict,
  model_run_id uuid not null references public.model_runs(id) on delete restrict,
  evidence_count integer not null check (evidence_count >= 0),
  completed_at timestamptz not null
);

alter table public.semantic_unit_completions enable row level security;
alter table public.semantic_unit_completions force row level security;

create policy processor_semantic_completions on public.semantic_unit_completions
  for all to casezero_processor
  using (exists (
    select 1 from public.structural_units unit
    join public.source_documents source on source.id = unit.source_document_id
    where unit.id = semantic_unit_completions.structural_unit_id
      and source.visibility = 'INVESTIGATION_EVIDENCE'
  ))
  with check (exists (
    select 1 from public.structural_units unit
    join public.source_documents source on source.id = unit.source_document_id
    where unit.id = semantic_unit_completions.structural_unit_id
      and source.visibility = 'INVESTIGATION_EVIDENCE'
  ));

grant select, insert, update on public.semantic_unit_completions to casezero_processor;
