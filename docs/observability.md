# Observability

Owned in `packages/observability`. Every model operation emits a span; every
claim is traceable to a model run and its evidence. Spans are ordinary
Postgres tables — queryable, exportable, and the substrate for replay
(ADR 0008).

## Data model

**`prompt_versions`** — one row per prompt module version: template id,
rendered hash, git SHA, model, parameters. Written on first use of a new hash.

**`model_runs`** — one row per model call: run id, investigation id, stage,
span parent, model, prompt version hash, input/output tokens, latency, cost,
tool calls (JSONB), retry count, schema-failure count, result status,
timestamps.

**`investigation_events`** — append-only event log per case: evidence
extracted, claim created, hypothesis created, confidence revised (from, to,
rationale, test refs), critique recorded, causal edge added, locked, revealed.
This log is the replay substrate.

## Rules

- Span emission is synchronous and mandatory. A stage completing without
  spans is a defect the verification loop checks for.
- Cost is computed from recorded token counts and a versioned price table,
  not from provider invoices.
- Prompt text is versioned in the repo; only the hash travels with runs.
  Raw prompt/response payloads are stored for benchmark cases only (they
  feed replay and judge audits), never for ad-hoc interactive use by default.
- Span kinds follow OTel GenAI conventions (`agent` / `model` / `tool` /
  `retrieval`) at the vocabulary level so a future Langfuse/OTel export is a
  mapper. We do not run an OTel collector for 5–50 cases.

## Replay

Replay folds `investigation_events` for a case into the state at any point:
`E14 extracted → H1 created at 0.34 → E22 strengthens H1 → H1 0.34 → 0.51 →
…`. Replay never re-calls models; model outputs come from recorded runs.
Replay correctness is tested: fold(events) == persisted final state.

## What we do not build

- A tracing UI. SQL + the Phase 8 replay screen are the interface.
- Online/production monitoring, alert pipelines, SLOs — this is a benchmarked
  investigation system, not a high-traffic service.
- A prompt registry UI. One editor (the repo), hash versioning, git history.
