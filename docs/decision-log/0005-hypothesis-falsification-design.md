# 0005 — Sequential falsification with deterministic test execution

**Date:** 2026-08-24 · **Status:** Accepted

## Problem

The spec requires ≥3 competing hypotheses, a dedicated falsification stage,
and explicit explainable confidence revision. Naive implementations
(confirmation-only search, self-critique, multi-agent debate) demonstrably
fail: debate collapses to consensus or cheap talk, and self-correction often
degrades accuracy.

## Decision

POPPER-style sequential falsification, adapted to evidence investigation:

1. **Generate** ≥3 competing hypotheses (one generator agent).
2. **Design falsification tests** per hypothesis (critic agent): "if H is
   true, evidence of kind X should exist / timeline must satisfy Y." Output
   is a typed `HypothesisCritique` + test list; the critic may not restate
   supporting evidence (schema has no field for it).
3. **Execute tests deterministically** where possible — evidence-presence
   queries, temporal-consistency checks, causal-chain validation run as code
   over the evidence store. Semantic judgements that genuinely need a model
   run as separate typed calls and are labeled `INFERRED`.
4. **Revise confidences with explicit rationale records** (from → to, why,
   new unresolved questions). Revision numbers come from a documented
   deterministic update rule seeded by test outcomes; they are labeled
   "model confidence", never calibrated probability, until calibration is
   measured (docs/evaluation.md).

## Alternatives considered

- **Multi-agent debate** — rejected on the 2025–26 evidence base (consensus
  collapse, competitive cheap talk).
- **Pure LLM self-critique** — rejected; weak falsification pressure.
- **Full POPPER with statistical error control** — designed for executable
  experiments; our "experiments" are retrospective evidence queries. We adopt
  the structure, not the statistics.

## Consequences

Falsification tests become persisted artifacts (`hypothesis_tests` table), so
replay can show exactly which test moved which confidence. The benchmark
grades contradiction discovery, which is the metric this design exists to
serve.

Sources: Stanford POPPER (ICML 2025); "When and Why Does Multi-Agent Debate
Fail" (arXiv 2510.20963); ACL 2025 confidence/critique decomposition.
