# CaseZero Architecture

Status: foundational. Normative requirements come from `product-spec.md`
(local-only); this document records how the tracked design satisfies them.
Decisions with real alternatives are in `docs/decision-log/`.

## System view

```text
                         ACQUISITION (deterministic)
        NTSB developer API / CAROL FileExport / docket pages
                               │
                  Context.dev → DocketManifest (discovery only)
                               │
                       Source Downloader ──► validate URLs, fetch originals
                               │
                    Immutable Source Store (SHA-256, Supabase Storage)

===================  VISIBILITY BOUNDARY (deterministic, enforced in code)

        INVESTIGATION_EVIDENCE        │ OFFICIAL_ANALYSIS / FINAL_FINDING
        visible to blind stages       │ invisible until after LOCK

=====================================================================

                  EVIDENCE PROCESSING (one-time, per case)
        PDF → Docling (PreOCR routes scans → OCR)      ─┐
        CSV/XLSX → Polars (schema detect, UTC normalize) ├─ typed
        Images → VLM structured observation              │ EvidenceItems
        Audio → ElevenLabs Scribe (if present)          ─┘
                               │
              Claims + Entities + TimelineEvents (persisted)

=====================================================================

                INVESTIGATION (Pydantic AI stage machine)
        evidence → claims → ≥3 competing hypotheses
          → support search → falsification (critic) → confidence revision
          → causal graph (every edge cites evidence) → second pass
          → FinalAssessment → LOCK (hashes + model/prompt versions)

        No unrestricted live-web or Context.dev access in this zone.

=====================================================================

                     EVALUATION (deterministic math + judges)
        reveal official finding → cause agreement, factor recall,
        citation precision, unsupported-claim rate, contradiction
        discovery, calibration, abstention, leakage audit (= 0)

=====================================================================

                        INTERFACE (Phase 8+)
        Next.js investigation workspace (state first, chatbot secondary)
        ElevenLabs voice later, over the same constrained tool layer
```

## Layers and ownership

| Package | Owns | Must not do |
|---|---|---|
| `packages/ntsb` | Case metadata (developer API, CAROL FileExport), docket manifests, downloader, checksums | Interpret content; call models |
| `packages/ingestion` | Modality processors producing `EvidenceItems` with `SourceLocators` | Mutate sources; drop provenance |
| `packages/evidence` | Typed models (evidence, claims, entities, timeline), provenance, visibility policy | Call models; know about stages |
| `packages/retrieval` | Hybrid search over typed evidence (metadata filters + FTS + pgvector) | Define the investigation flow |
| `packages/investigation` | Stage machine, tool layer, hypotheses, falsification, causal graph, lock | Bypass visibility policy; access raw store except via evidence tools |
| `packages/evaluation` | Reveal, graders, benchmark metrics, reports | Change locked state |
| `packages/observability` | Model-run spans, prompt versions, cost, replay log | Be optional — every model call emits a span |
| `apps/api` | FastAPI surface over packages | Contain domain logic |
| `apps/web` | Investigation UI (Phase 8) | Exist before the engine proves out |

## Deterministic / AI split

Deterministic: file acquisition, SHA-256, provenance, timestamps, CSV/XLSX
parsing, time normalization where mathematically possible, visibility rules,
locking, graph persistence, dedup, caching, workflow state, evaluation
calculations.

AI: document understanding, image/audio interpretation, claim and entity
extraction from narrative, hypothesis generation, falsification critique,
contradiction analysis, causal reasoning, uncertainty, explanations.

The boundary rule: if conventional software can do it reliably, a model must
not be asked to do it.

## Blindness enforcement

`SourceDocument.visibility` ∈ {`INVESTIGATION_EVIDENCE`, `OFFICIAL_ANALYSIS`,
`FINAL_FINDING`} is set at ingestion (manifest classification + date rules)
and enforced by the evidence store query layer: blind-stage connections
physically cannot read blocked rows (separate DB role / row-level security,
not a `WHERE` clause the agent's tools could forget). The investigation tool
layer has no web-search or Context.dev tool to call — absence of capability,
not instruction. After `LOCK`, the evaluation layer gains read access to
official material. See `docs/decision-log/0007-temporal-blindness-enforcement.md`.

## Legal and public-output boundary

Official source records and CaseZero-generated records remain separate at
storage, API, and component levels; a response must never overload one field
with both. Public DTOs expose distinct `official` and `casezero_generated`
sections. Every generated section carries a persistent “AI-Generated — Not an
Official Finding” or “CaseZero Experimental Hypothesis” label, while official
material carries a direct source link and “Official NTSB Finding” label. UI
components and export templates must not imitate government reports, notices,
seals, or determinations.

Acquisition accepts only officially public NTSB URLs and never confidential,
leaked, restricted, sealed, unpublished, or user-submitted evidence. Materials
with unclear third-party reuse rights remain links/checksums rather than
republished payloads and are excluded from model-training corpora. Generated
copy preserves uncertainty and may not assert blame, unlawful conduct,
negligence, fault, or liability for living people or companies. Any public
interface must include a channel to report, correct, or remove inaccurate or
harmful generated content. Publication requires Loop 5 in
`docs/development/verification-loops.md`; README.md lists cases that require
qualified legal review.

## Workflow state and resumability

The investigation is an explicit stage machine (Pydantic AI agents inside
deterministic stage drivers). After each stage transition — and within long
stages after each unit of work — state checkpoints to Postgres. A Postgres job
queue (`SELECT … FOR UPDATE SKIP LOCKED`) drives execution; workers resume
from the latest checkpoint. No orchestration server, no Redis. See
`docs/decision-log/0002-pydantic-ai-stage-machine.md` and
`0004-supabase-postgres-platform.md`.

## Replay

Every state mutation (evidence extracted, hypothesis created, confidence
revised with rationale, edge added, lock) appends to an investigation event
log. Replay is a deterministic fold over that log — it is a query, not a
re-run. Model replays use recorded model-run spans, never fresh calls.

## Failure modes (owned at design time)

- **Schema failure from open-weight models** — bounded retries with the failed
  validation error fed back; failures recorded in spans and surfaced per model
  in the benchmark report. See `0003-hyperfusion-model-provider.md`.
- **Docket scraping fragility** — Context.dev manifests validated, originals
  downloaded by us; first 5–20 cases allow curated manifests.
- **MDB deprecation (April 2027)** — bulk catalog migrates to the developer
  API `GetCasesByDateRangeV2`; acquisition layer isolates this behind
  `packages/ntsb` interfaces.
- **Confidence treated as calibrated probability** — UI labels it
  "model confidence"; calibration is measured, not assumed.
- **Critic restating support** — critique schema requires strongest
  contradiction, missing evidence, and alternative explanation; graders check.
