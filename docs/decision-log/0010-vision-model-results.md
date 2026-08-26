# 0010 — Phase 1 vision model route

**Date:** 2026-08-25 · **Status:** Superseded by hosted Hyperfusion-only design (2026-08-26)

## Method

The probe rendered page 1 of two approved NTSB-authored CEN22FA375 reports and
requested one conservative structured observation with a fixed source id and a
0–1 confidence value. Ten calls per model alternated between the pages. Reports
stored status, attempts, latency, provider/model, and source id only—never image
bytes, prompts, or generated observations.

## Results

| Route | Model | Schema/source success | Mean latency | Attempts |
|---|---|---:|---:|---:|
| Hyperfusion | `google/gemma-4-31b-it` | 10/10 | 1.823 s | 10 first-attempt |
| Ollama | `qwen2.5vl:7b` | 10/10 | 2.297 s | 10 first-attempt |

Two representative outputs (one per model) were manually inspected against the
visible report cover. Both described visible titles/date/header content and
used the required source id. This is a small capability check, not a forensic
quality benchmark.

## Decision

The original decision used Hyperfusion `google/gemma-4-31b-it` as primary and
local Ollama `qwen2.5vl:7b` as fallback. The 2026-08-26 hosted-services design
supersedes the fallback: Hyperfusion remains the only runtime provider and
LOCAL_ONLY is audited-skipped. Every
production observation remains INFERRED and reviewable. Re-evaluate with a
larger image set before making quality or calibration claims.
