# 0003a — Hyperfusion structured-output capability spike

**Date:** 2026-08-25 · **Status:** Complete · **Relates to:** ADR 0003

## Question

Can the Hyperfusion-hosted open-weight models reliably satisfy the typed,
tool-mediated structured outputs required by CaseZero's text investigation
stages?

## Method

The spike used Pydantic AI 2.30.0 with `OpenAIChatModel`, Hyperfusion's
OpenAI-compatible Chat Completions endpoint, and two account-available models:

- `openai/gpt-oss-120b`
- `qwen/qwen3-32b`

Each model received 30 independent calls for each of four representative
Pydantic schemas: evidence extraction, claim extraction, exactly three
competing hypotheses, and hypothesis critique. Pydantic AI was allowed two
retries. The prompts used synthetic aviation-investigation evidence IDs and
text constructed for this capability test; they did not represent a real case
or benchmark model correctness. Calls ran with a concurrency limit of four.
The harness is `scripts/spike_hyperfusion_structured_output.py`; raw per-call
records remain in the gitignored
`benchmark/results/hyperfusion-structured-output.json`.

A success means Pydantic AI returned an instance that validated against the
requested schema. `UnexpectedModelBehavior` is counted as a structured-output
failure; `ModelHTTPError` is counted as a provider failure. Latency is elapsed
wall-clock time per call, including Pydantic AI retries.

## Measured results

| Model | Shape | Success | Structured failures | Provider failures | Mean latency |
|---|---|---:|---:|---:|---:|
| `openai/gpt-oss-120b` | Evidence extraction | 30/30 (100%) | 0 | 0 | 7.863 s |
| `openai/gpt-oss-120b` | Claim extraction | 30/30 (100%) | 0 | 0 | 23.198 s |
| `openai/gpt-oss-120b` | Hypothesis generation | 23/30 (76.67%) | 1 | 6 | 107.434 s |
| `openai/gpt-oss-120b` | Hypothesis critique | 30/30 (100%) | 0 | 0 | 24.590 s |
| `qwen/qwen3-32b` | Evidence extraction | 30/30 (100%) | 0 | 0 | 10.087 s |
| `qwen/qwen3-32b` | Claim extraction | 30/30 (100%) | 0 | 0 | 11.085 s |
| `qwen/qwen3-32b` | Hypothesis generation | 30/30 (100%) | 0 | 0 | 27.659 s |
| `qwen/qwen3-32b` | Hypothesis critique | 30/30 (100%) | 0 | 0 | 12.402 s |

Across all four shapes, `qwen/qwen3-32b` completed 120/120 calls. The GPT-OSS
model completed 113/120; all seven final failures occurred during hypothesis
generation (six `ModelHTTPError`, one `UnexpectedModelBehavior`). This spike
did not retain provider error bodies, so it cannot attribute the six HTTP
failures to one specific upstream cause.

## Decision

Use `qwen/qwen3-32b` as the initial Hyperfusion model for all text-only Phase 1
and Phase 3 structured stages:

| Stage | Initial model |
|---|---|
| Narrative evidence interpretation | `qwen/qwen3-32b` |
| Claim extraction | `qwen/qwen3-32b` |
| Hypothesis generation | `qwen/qwen3-32b` |
| Hypothesis critique / falsification design | `qwen/qwen3-32b` |

A model/shape pair must achieve at least 95% final schema-valid success in this
30-run probe to be eligible as a default. `openai/gpt-oss-120b` is therefore
not eligible for hypothesis generation in the initial model table. It remains
available for later quality comparisons on stages where it passed this probe;
it is not an automatic fallback because this spike measured conformance and
availability, not reasoning quality.

## What remains unresolved

- The spike did not measure factual correctness, citation faithfulness,
  hypothesis quality, contradiction discovery, or calibration. Phase-specific
  evals must decide whether the reliable outputs are useful.
- No image input was tested. The vision-model choice remains open until Phase 1
  runs a separate image-evidence probe against actual supported Hyperfusion
  VLM model IDs.
- No token or cost measurements were captured. Production model-run spans must
  record both when the provider reports them.
- Thirty calls per shape are enough to expose gross reliability failures, not
  to establish a production service-level objective or a precise tail-failure
  rate.
