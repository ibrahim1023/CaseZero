# Testing

## Layers

| Layer | What | Live calls? | Runs in CI? |
|---|---|---|---|
| Unit | Pure functions, models, parsers, time normalization, hashing | Never | Yes |
| Integration | Packages against local Supabase (RLS policies, job queue, checkpoints) | Never (local services only) | Yes |
| Leakage / attack | Blind-role queries against blocked documents; tool layer capability audit | Never | Yes |
| Golden / regression | Processor outputs against curated fixture inputs with committed expected outputs | Never | Yes |
| Live acquisition | Real NTSB CAROL/docket fetch smoke tests | Yes — NTSB only | Opt-in, manual |
| Live investigation | One-case end-to-end with real models | Yes — Hyperfusion | Opt-in, manual |
| Evals | Benchmark graders + DeepEval metrics | Judge calls only in live mode | Offline subset in CI |

## Rules

- The default command `uv run pytest` must never touch the network or a model.
  Tests that need either are marked (`@pytest.mark.live`) and skipped without
  the opt-in env flag (`CASEZERO_LIVE=1`).
- Synthetic fixtures are allowed **only** in tests, per spec §2.1. Real-case
  material enters through `fixtures/real-cases/` manifests (URLs + SHA-256),
  fetched by the retrieval script, never committed.
- Processor tests are fixture-driven: one directory per scenario with the
  input artifact (synthetic), `expected.evidence.json`, and provenance
  assertions (every EvidenceItem's SourceLocator resolves).
- RLS/visibility tests are not optional plumbing tests — they are the
  blindness guarantee. A blocked document readable by the blind role is a
  P0 defect.
- Crash-resume tests kill the worker mid-stage and assert checkpoint resume
  produces identical final state (replay fold comparison).

## TDD discipline

Failing test → minimal implementation → passing focused test → broader
verification → commit. Exact test paths while iterating; full offline gate
before claiming completion (see `docs/development/verification-loops.md`).

## What we deliberately do not test

- Third-party SDK internals (Pydantic AI, Docling, Polars).
- Pixel-level UI (Phase 8 concern; Playwright smoke only).
- Statistical calibration with N < 30 cases (meaningless; see
  `docs/evaluation.md`).
