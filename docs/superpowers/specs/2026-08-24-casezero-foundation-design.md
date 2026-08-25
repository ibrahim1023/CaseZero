# CaseZero Foundation Design

**Date:** 2026-08-24 · **Status:** Approved-in-principle by owner (stack decisions
confirmed); final review requested before implementation begins.
**Source of product truth:** `product-spec.md` (local-only, never committed).

## Scope of this design

Phases 0–2 of the spec roadmap plus the seams later phases plug into:

- **Phase 0 — Case acquisition:** NTSB metadata + docket manifests + source
  downloader + immutable source store + visibility classification.
- **Phase 1 — Evidence extraction:** modality processors → typed
  `EvidenceItems` with working `SourceLocators`; claims/entities/timeline
  candidate extraction.
- **Phase 2 — Blindness infrastructure:** visibility enforcement (RLS),
  temporal cutoff, official-result blocking, case locking.

Phases 3+ (investigation engine, causal graph, assessment, reveal, UI) get
their own specs after Phase 2 lands; this design fixes only the contracts
they depend on (data models, store interfaces, tool-layer shape, spans).

## Architecture

See `docs/architecture.md` for the system view and layer ownership. Stack
decisions: ADRs 0001–0008 in `docs/decision-log/`. Summary: Python 3.12 /
FastAPI / Pydantic AI stage machine / Hyperfusion (OpenAI-compatible,
open-weight) behind a `ReasoningModel` interface / Supabase Postgres +
pgvector + Storage / Postgres `SKIP LOCKED` job queue / spans in Postgres /
DeepEval CI evals.

## Core data contracts (packages/evidence)

Python Pydantic models mirroring the spec's TypeScript interfaces
(§10–15, 19, 32). All ids are UUIDs; all datetimes are aware UTC; money is
not in domain; confidences are `float` in [0,1] labeled model confidence.

- `SourceDocument { id, case_id, title, source_url, published_at?,
  evidence_date?, retrieved_at, document_type, visibility, checksum }`
  — `visibility ∈ {INVESTIGATION_EVIDENCE, OFFICIAL_ANALYSIS, FINAL_FINDING}`
- `EvidenceItem { id, case_id, source_document_id, type ∈ {TEXT, TABLE,
  TIME_SERIES, IMAGE, METADATA}, subtype?, observation, source_locator,
  occurred_at?, entities[], extraction_method ∈ {DETERMINISTIC, AI, HUMAN},
  confidence? }`
- `SourceLocator` — discriminated union: `PdfLocator{page, paragraph?}`,
  `TableLocator{row, columns[]}`, `ImageLocator{region?}`,
  `AudioLocator{start_ms, end_ms}`, `TextLocator{start, end}`.
  Rule: a locator must resolve to the original artifact bytes.
- `Claim { id, case_id, text, status ∈ {OBSERVED, INFERRED, DISPUTED,
  UNKNOWN}, supporting_evidence_ids[], contradicting_evidence_ids[],
  confidence, created_by (model run ref), created_at }`
- `InvestigationEntity { id, case_id, type, canonical_name, aliases[],
  evidence_ids[] }`
- `TimelineEvent { id, case_id, occurred_at?, time_precision ∈ {EXACT,
  APPROXIMATE, RELATIVE, UNKNOWN}, description, evidence_ids[], confidence }`
- `Hypothesis { id, title, description, supporting_claim_ids[],
  contradicting_claim_ids[], unresolved_questions[], confidence, status ∈
  {ACTIVE, WEAKENED, REJECTED, LEADING} }` + persisted `ConfidenceRevision
  { from, to, rationale, test_refs[], at }`
- `HypothesisCritique { hypothesis_id, strongest_contradiction?,
  missing_evidence[], alternative_explanation?, critique_confidence }`
- `CausalEdge { from, to, relation ∈ {CAUSES, CONTRIBUTES_TO, PRECEDES,
  ENABLES, CONTRADICTS}, evidence_ids[] (non-empty), confidence }`
- `FinalAssessment { status ∈ {CONCLUSION_REACHED, INSUFFICIENT_EVIDENCE},
  probable_cause?, contributing_factors[], evidence_ids[], confidence,
  unresolved_questions[], generated_at }`
- `DocketManifest { case_id, documents: [{title, document_type?, source_url,
  file_type?, page_count?, published_at?}] }` (Context.dev extraction schema)

## Acquisition layer (packages/ntsb)

Interfaces only; implementations per source:

- `CaseCatalog` — CAROL FileExport API (public, ZIP of JSON) for discovery;
  developer API `GetCase`/`GetCasesByDateRangeV2` behind key; monthly MDB
  import is a stopgap with a hard migration date (2027-04-05 deprecation).
- `DocketDiscovery` — Context.dev extraction → `DocketManifest`, validated.
- `SourceDownloader` — validates URLs, downloads originals, SHA-256, writes
  immutable `SourceStore` records. 5 s minimum delay between NTSB requests,
  exponential backoff, aggressive local caching.
- Visibility classification happens at ingestion from document type +
  publication metadata; ambiguous documents default to blocked until curated.

First 5–20 cases may use curated manifests (`fixtures/real-cases/`) when
scraping is fragile — recorded as a manifest field, never hidden.

## Ingestion layer (packages/ingestion)

One interface: `Processor.process(source: StoredSource) -> list[EvidenceItem]`.
Processors: `pdf` (Docling; per-page scan detection routes to VLM OCR),
`table` (Polars + schema inference, UTC normalization, derived event
windows), `image` (VLM structured observation), `audio` (ElevenLabs Scribe,
only when present), `text/html` (deterministic parse). Deterministic-first;
AI only for semantic interpretation. Derived artifacts record extraction
model + version.

## Blindness boundary (Phase 2)

RLS policies on `source_documents` and all derived tables; blind-stage DB
role sees only `INVESTIGATION_EVIDENCE`. Investigation tool layer has no
web/Context.dev capability. `LOCK` writes `investigation_locks
{assessment_hash, evidence_set_hash, model_versions, prompt_versions,
system_version, locked_at}` and flips case state; evaluation role unlocks
official material only after. Leakage auditor is a deterministic grader
(docs/evaluation.md).

## Tool layer shape (fixed now, implemented in Phase 3)

`search_evidence, get_evidence, get_source_document, get_timeline,
find_entity, get_claim, list_claims, create_hypothesis, update_hypothesis,
find_supporting_evidence, find_contradicting_evidence, get_causal_graph,
add_causal_edge, get_unresolved_questions` — typed inputs/outputs, each call
a span, none able to see blocked documents.

## Error handling

- Schema failures from models: bounded retry (≤3) with validation error fed
  back; then stage failure recorded, investigation resumable from checkpoint.
- Acquisition failures: recorded on the manifest (`retrieval_errors[]`);
  missing originals are explicit limitations, not silent gaps.
- OCR/VLM low confidence: item created with reduced confidence and
  `extraction_method=AI`, flagged for review; never dropped silently.

## Testing strategy

`docs/testing.md`. Highlights for these phases: fixture-driven processor
tests with provenance assertions; RLS attack tests; crash-resume tests;
checksum round-trip tests against synthetic binaries; live NTSB smoke tests
behind `CASEZERO_LIVE=1`.

## Decisions and remaining input

- **D1 — resolved:** Supabase CLI local stack for development parity with
  hosted RLS and Storage.
- **D2 — open:** Hyperfusion-hosted open embedding vs OpenAI
  `text-embedding-3-small` (≤1536 dimensions). Decide in the retrieval phase
  after a recall probe on real evidence.
- **D3 — partially resolved:** `qwen/qwen3-32b` is the initial text-stage model
  based on the Phase 0 capability spike. Vision remains a Phase 1 probe.
- **D4 — resolved:** manually curate the initial benchmark manifests first so
  they provide a reviewed reference. After curation, validate live Context.dev
  against at least one reference docket before the Phase 7 go/no-go. Context
  output performs discovery only; CaseZero still downloads originals and
  owns checksums, provenance, visibility, and blind-mode exclusion.
