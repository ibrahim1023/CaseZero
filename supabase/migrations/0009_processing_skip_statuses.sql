alter table public.processing_skips
  drop constraint processing_skips_status_check;

alter table public.processing_skips
  add constraint processing_skips_status_check
  check (status in ('SKIPPED_RIGHTS', 'SKIPPED_VISIBILITY'));
