# Phase 1 Evidence Extraction Validation

**Validated:** 2026-09-01  
**Reference case:** CEN22FA375  
**Runtime:** hosted Supabase Free development project and Hyperfusion
`qwen/qwen3-32b`

This record measures the active prompt-versioned checkpoints. Superseded model
runs, evidence rows, and candidates from interrupted or earlier development
runs remain in hosted Postgres as audit history; they are excluded from active
counts by `semantic_unit_completions` and `candidate_batch_completions`.

## Reference-case boundary

The curated manifest contains 15 reviewed docket items:

| Disposition | Count | Runtime behavior |
|---|---:|---|
| `AI_ALLOWED` | 3 | Downloaded, checksum-verified, stored privately, processed |
| `LINK_ONLY` | 11 | Metadata/link retained; bytes not acquired by the Phase 1 retrieval or processor |
| `LOCAL_ONLY` | 1 | Not sent to hosted models and not materialized in hosted source storage |

The live retrieval command reported three checksum matches and twelve skips:

```text
CASEZERO_LIVE=1 uv run python fixtures/real-cases/cen22fa375/retrieval.py
3 OK; 11 LINK_ONLY skipped; 1 LOCAL_ONLY skipped
```

The process report independently recorded `SKIPPED_RIGHTS: 12` and
`SUCCEEDED: 3`. Only three `INVESTIGATION_EVIDENCE` source documents exist in
the hosted database. No active model run uses a provider other than
`hyperfusion`.

## Active extraction state

| Measure | Result |
|---|---:|
| Reused structural document runs | 3 |
| Active semantic-unit checkpoints | 180 |
| Active EvidenceItems | 951 |
| PDF-located EvidenceItems | 200 |
| Table-located EvidenceItems | 751 |
| Active candidate-batch checkpoints | 11 |
| Active provisional candidates | 171 |
| EvidenceItems pending review | 3 |
| Active model runs | 191 |
| Active input tokens | 399,723 |
| Active output tokens | 260,917 |
| Final-run failures | 0 |

The candidate count is the sum recorded by the 11 active batch completions.
Candidates are provisional typed outputs, not official findings or a locked
assessment.

## Locator validation

Every active EvidenceItem was resolved against bytes read back from the private
hosted source bucket using the production modality adapters:

```text
active_evidence_checked 951
resolved_by_locator {'pdf': 200, 'table': 751}
resolution_failures 0
failure_types {}
```

The two PDF sources were converted with `DoclingPdfAdapter`; each `PdfLocator`
resolved to its page/reading-order text block. Each `TableLocator` resolved to
the selected source CSV rows and columns. No active image or audio evidence was
produced by this case.

## Idempotency result

After all prompt-versioned semantic and exact-evidence candidate checkpoints
were persisted, an immediate no-change hosted run returned:

```json
{
  "artifacts": 0,
  "candidates": 0,
  "evidence_items": 0,
  "failures": [],
  "model_usage": {},
  "reused_candidate_runs": 11,
  "reused_semantic_runs": 180,
  "reused_structural_runs": 3,
  "status_counts": {"SKIPPED_RIGHTS": 12, "SUCCEEDED": 3},
  "structural_units": 0
}
```

Thus the measured no-change run made no model call and created no duplicate
active state.

## Automated verification

Executed successfully from the repository root:

```text
uv run ruff check .
All checks passed!

uv run mypy
Success: no issues found in 42 source files

uv run pytest
135 passed, 4 deselected

CASEZERO_DB_TEST=1 uv run pytest -m db -v
2 passed, 137 deselected

uv run python scripts/verify_hosted_supabase.py
PASS hosted environment marker
PASS expected schema and RLS
PASS private source and derived buckets
PASS processor final-finding boundary
```

Live publishable-key probes returned no PostgREST rows and no Storage object
listings. Both known private-bucket object reads and unauthenticated public URLs
were denied in the preceding hosted access audit. The local `.env` is
untracked.

## Limitations and unexecuted checks

- `supabase start` and `supabase test db` were not run in this final loop. The
  approved hosted-first migration replaced the local Docker gate with the
  hosted verifier and rollback-isolated database tests.
- Context.dev live validation was not run. The approved Phase 1 baseline remains
  the curated manifest; Context.dev validation is deferred to the later
  multi-case gate.
- The configured vision route was previously probed, but this reference case
  produced no active image evidence, so the final hosted extraction did not
  exercise a vision call.
- OCR and audio transcription were not exercised by the three approved source
  artifacts.
- Locator correctness was measured mechanically. Semantic extraction quality
  and the 171 provisional candidates have not received a complete expert
  aviation review; no quality claim beyond schema grounding and locator
  resolution is made.
- Three active EvidenceItems remain `PENDING` review and must not be silently
  promoted to accepted facts.
