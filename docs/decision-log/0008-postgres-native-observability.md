# 0008 — Postgres-native observability; DeepEval for CI evals

**Date:** 2026-08-24 · **Status:** Accepted

## Problem

The spec requires every model operation recorded (model, task, prompt version,
tokens, latency, cost, tool calls, retries, schema failures, status), every
claim traceable to model run + evidence, full replay, and a benchmark harness
with measured — never invented — results.

## Decision

**Observability:** spans, model runs, and prompt versions are first-class
Postgres tables (`packages/observability`), written synchronously by the
`ReasoningModel` wrapper and stage runners. Span fields follow the emerging
OTel GenAI conventions (agent/model/tool span kinds) without committing to an
unstable schema version, so export to Langfuse/OTel later is a mapper, not a
migration. Replay and trace inspection are SQL over our own tables — replay is
a product feature, so owning this data is not optional. Details:
`docs/observability.md`.

**Evaluation:** DeepEval (pytest-native) for CI-regressable metrics plus
custom deterministic graders for the spec's metric set (cause agreement,
factor recall, citation precision, unsupported-claim rate, contradiction
discovery, calibration, abstention, leakage). LLM judges use a different model
than the generator, a written rubric, and are calibrated against a small
human-labeled set before their scores gate anything. Details:
`docs/evaluation.md`.

**Prompt versions:** prompts live in the repo as versioned modules; the
rendered hash + git SHA is recorded on every model run. A registry UI is
deferred until more than one person edits prompts.

## Alternatives considered

- **Langfuse self-hosted** — real capability, but a 5-service stack
  (ClickHouse, Redis, MinIO included) to observe 5–50 investigations;
  rejected as disproportionate now, with a documented export path.
- **LangSmith / hosted SaaS** — external dependency, data leaves the box,
  weak fit with the local-first benchmark harness. Rejected.
- **promptfoo** — JS-centric; DeepEval fits the pytest workflow directly.

## Consequences

Observability is not optional plumbing: a stage that runs without spans is a
bug the verification loop catches. Benchmark reports (`benchmark/reports/`)
cite only measured runs with evidence-set and prompt-version hashes.
