---
name: blind-benchmark-run
description: Run a blind benchmark case without leaking the official finding. Use before and during any `casezero benchmark run` or manual blind investigation of a curated case.
---

# Blind Benchmark Run

Temporal leakage incidents must be 0. This skill is the human/agent checklist
that complements the architectural enforcement (RLS + capability absence +
leakage auditor). Architectural enforcement is the guarantee; this checklist
catches process mistakes architecture cannot (e.g. reading the finding
yourself and then "helping" the model).

## Before the run

1. Confirm the case manifest classifies every document's `visibility` and the
   classification rules in `packages/ntsb` (ADR 0007) were applied, not
   hand-waved. Ambiguous documents must be blocked.
2. Confirm the case cutoff date and that it precedes the official report's
   publication date.
3. Recompute the evidence-set hash and record it in the run manifest.
4. Run the blindness attack tests: `uv run pytest tests/blindness/ -v` — all green.
5. Do not open the adopted report or any post-cutoff coverage of the case in
   this session. If you already know the outcome, say so in the run notes —
   prior knowledge is a bias source worth recording, not hiding.

## During the run

- The investigation tool layer must contain no web-search or Context.dev
  tool. If you see one available to a blind stage, stop — that is a P0 defect,
  not a prompt fix.
- Blind workers run with network egress disabled. Any logged egress attempt
  fails the run.
- LOCK must precede any evaluation-role read. Check the lock row:
  assessment hash, evidence-set hash, model + prompt versions, timestamp.

## After the run

- Run the leakage auditor (deterministic; see `docs/evaluation.md`). Any
  incident → the run is invalid, file an ADR-worthy postmortem entry in
  `docs/decision-log/`.
- Only then reveal and grade. Record measured results in
  `benchmark/reports/` with all hashes.
