# 0002 — Pydantic AI for the investigation stage machine

**Date:** 2026-08-24 · **Status:** Accepted

## Problem

The investigation workflow (extract → timeline → entities → claims →
hypotheses → falsification → revision → causal graph → assessment → lock)
must be resumable, replayable, auditable, and constrained — the spec forbids a
theatrical agent swarm and in-memory conversation state.

## Decision

Explicit functional stages, each a Pydantic AI agent with a typed result
model and a constrained tool set, driven by deterministic stage-runner code.
Investigation state lives in Postgres and checkpoints after every stage
transition (and per work-item inside long stages). A Postgres job queue
(`FOR UPDATE SKIP LOCKED`) drives workers; resume re-dispatches from the
latest checkpoint. Stages may use different models via per-stage config.

## Alternatives considered

- **LangGraph** — mature graph orchestration with checkpointing, but adds a
  second state model beside our Postgres investigation state, which is the
  actual product. Rejected: duplication of the thing we must own.
- **Hand-rolled loop with raw SDK calls** — full control, but we would rebuild
  structured-output retries, tool-call plumbing, and provider abstraction that
  Pydantic AI already gives typed and tested.
- **Temporal / DBOS durable execution** — strong resumability, but an
  orchestration server (or framework runtime) is disproportionate for a
  solo-built system whose workflow is sequential stages over one case.

## Consequences

Structured-output validation and retry-on-schema-failure are framework
behavior, recorded as spans. Resumability is ours to implement and test
(crash-resume tests are in the verification loop). If a future need appears
that Pydantic AI genuinely cannot express, the stage-runner boundary — not the
whole system — is the swap point.
