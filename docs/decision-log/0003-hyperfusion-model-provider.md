# 0003 — Hyperfusion as the model provider

**Date:** 2026-08-24 · **Status:** Accepted · **Owner decision:** user-directed

## Problem

The spec demands one strong model with structured output, tool use, long
context, and multimodal analysis, behind a provider-agnostic interface.

## Decision

All model access goes through a narrow `ReasoningModel` interface (Pydantic AI
models underneath). The MVP provider is Hyperfusion's OpenAI-compatible API
serving open-weight models — reasoning stages target a strong open-weight
reasoning model (e.g. gpt-oss-120b class), image interpretation targets a
hosted VLM (e.g. Gemma 3 / Qwen-VL class). Model selection is per-stage
configuration, not hardcoded. Vision/OCR and Whisper-class speech on the same
API are a bonus, not a reason to integrate more surface.

## Risks (accepted, measured, not assumed away)

Open-weight models have weaker structured-output and tool-calling discipline
than frontier APIs. Mitigations, in order:

1. A **capability spike is Phase 0-adjacent**: measure schema-failure and
   tool-call failure rates for the exact prompts the investigation stages use,
   before the investigation engine is built on top. Results go in the spike
   report and may change the per-stage model table.
2. Every model output validates against Pydantic schemas with bounded retry
   feeding the validation error back; failures are spans and benchmark data.
3. The interface keeps a documented fallback (OpenAI or Anthropic direct) that
   is a config change, not a refactor. "Do not weaken the flagship project to
   avoid API costs" still applies — if the benchmark shows the open-weight
   tier cannot ground hypotheses in evidence, we escalate.

## Alternatives considered

- **Anthropic / OpenAI / Gemini direct** — stronger out-of-box reliability;
  kept as the configured fallback, not the default, per owner direction.
- **LiteLLM gateway** — another translation layer to debug; Pydantic AI's
  OpenAI-compatible provider already covers Hyperfusion. Rejected as
  duplication.

## Consequences

`.env` carries `HYPERFUSION_BASE_URL` + key; no model SDK is imported outside
`packages/observability`-instrumented model construction in one module.
Benchmark reports must include schema-failure rate per stage so this decision
stays honest.
