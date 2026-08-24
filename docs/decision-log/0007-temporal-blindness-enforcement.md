# 0007 — Temporal blindness as an access-control boundary

**Date:** 2026-08-24 · **Status:** Accepted

## Problem

Historical evaluation is meaningless if the model sees the official finding,
later news, or retrospective analysis. The spec requires enforcement "as an
access-control boundary", forbids prompt-based rules, and sets leakage
incidents = 0 as a hard requirement.

## Decision

Three independent layers, each insufficient alone:

1. **Database layer.** `source_documents.visibility` is enforced by Postgres
   row-level security. Blind investigation stages connect as a role whose
   policy admits only `INVESTIGATION_EVIDENCE`; `OFFICIAL_ANALYSIS` and
   `FINAL_FINDING` rows do not exist for that role. The evaluation role gains
   access only after `LOCK`.
2. **Capability layer.** The investigation tool layer contains no web-search
   or Context.dev tool — not a disabled one, an absent one. Context.dev runs
   only in acquisition, before blind mode begins.
3. **Audit layer.** Every evidence and tool access is logged; a deterministic
   leakage auditor (docs/evaluation.md) scans spans for blocked-document IDs,
   post-cutoff publication dates, and network egress during blind stages. Any
   hit fails the benchmark run.

## Alternatives considered

- **Prompt instruction ("do not look at the answer")** — explicitly prohibited
  by the spec; listed here only to record the rejection.
- **Separate physical databases** for blind vs official material — stronger
  still, but operationally awkward for one Postgres; RLS + role separation
  reaches the same guarantee inside Supabase. Revisit if RLS ever shows a
  bypass.

## Consequences

Phase 2 (blindness infrastructure) is tested aggressively before any benchmark
runs, with dedicated leakage fixtures and attack tests (e.g. a tool call
attempting to fetch a `FINAL_FINDING` document must fail at the DB layer).
The five-case gate includes "no final-answer leakage".
