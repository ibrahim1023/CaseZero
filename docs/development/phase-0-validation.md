# Phase 0 Validation — Case Acquisition

**Date:** 2026-08-25 · **Result:** Phase exit gate passed

## Delivered behavior

`casezero ingest <ntsb-number>` resolves real NTSB metadata, validates a docket
manifest, downloads only NTSB-hosted originals, writes immutable
content-addressed source bytes, records SHA-256 provenance and deterministic
visibility, persists the case and documents, and transitions a successful case
to `BLIND`. Retrieval failures remain explicit in the CLI summary.

## Automated verification executed

```text
uv run ruff check .
All checks passed.

uv run mypy packages/ apps/api/
Success: no issues found in 27 source files

uv run pytest
42 passed, 3 deselected

CASEZERO_DB_TEST=1 uv run pytest apps/api/tests/test_repository_db.py -m db -v
1 passed

supabase test db
Files=1, Tests=3, Result: PASS

CASEZERO_LIVE=1 uv run pytest packages/ntsb/tests/test_carol_live.py -m live -v
1 passed
```

The three default-suite deselections are the two authenticated/live tests and
the local-Postgres integration test. The Postgres test and public CAROL smoke
were executed separately above. The authenticated NTSB developer-portal live
test was not run because portal credentials and its account-specific endpoint
template are not configured; its mocked contract tests passed in the offline
suite.

## Real-case exit smoke

Only two officially public, NTSB-authored reports from investigation
`CEN22FA375` were used:

- [NTSB Examination Report](https://data.ntsb.gov/Docket/Document/docBLOB?ID=16664358&FileExtension=pdf&FileName=CEN22FA375%20NTSB%20Examination%20Report-Rel.pdf)
- [Fire Specialist's Factual Report](https://data.ntsb.gov/Docket/Document/docBLOB?ID=16658473&FileExtension=pdf&FileName=23-078%20Fire%20Factual%20Report-Rel.pdf)

Source: National Transportation Safety Board. The temporary curated manifest
was outside the repository. No third-party docket material was republished or
used for model training.

```text
casezero ingest CEN22FA375 \
  --manifest /tmp/casezero-cen22fa375-phase0-manifest.json \
  --cutoff 2024-03-20T17:00:00Z

Case: CEN22FA375
Documents fetched: 2
Bytes stored: 2000938
INVESTIGATION_EVIDENCE: 2
Retrieval errors: 0
```

A direct database check returned:

```text
('CEN22FA375', 'BLIND', 2, True, True)
```

The tuple means: expected case number, `BLIND` state, two source rows, every
checksum is 64 hexadecimal characters, and every ingested source is classified
`INVESTIGATION_EVIDENCE`.

## Hyperfusion capability result

The Phase 0 spike executed 240 Pydantic AI calls. `qwen/qwen3-32b` completed
120/120 representative text-stage structured outputs; `openai/gpt-oss-120b`
completed 113/120 and failed the 95% eligibility threshold for hypothesis
generation. The measured model table and limitations are recorded in
`docs/decision-log/0003a-hyperfusion-spike-results.md`.

## Remaining limitations

- Phase 0 proves acquisition, provenance, visibility classification, and the
  model boundary; it does not parse document contents or run an investigation.
- The real-case smoke intentionally used two NTSB-authored reports rather than
  claiming full-docket benchmark curation. Full benchmark manifests, rights
  review, checksums, and retrieval scripts are Phase 7 work.
- Context.dev extraction was contract-tested but not called live because no
  Context.dev key is configured. Curated manifests are an accepted MVP path.
- Hyperfusion vision capability remains unmeasured and must be probed before
  Phase 1 image processing selects a model.
