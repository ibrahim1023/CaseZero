# 0001 — Monorepo: Python backend + Next.js web

**Date:** 2026-08-24 · **Status:** Accepted

## Problem

The spec writes its models in TypeScript but recommends Python/FastAPI for the
backend (document, data, and AI tooling) and Next.js for the UI. We needed one
repository layout that keeps the five-case blind pipeline cheap to build while
leaving room for the Phase 8 product UI.

## Decision

Monorepo with `apps/api` (Python 3.12+, FastAPI, uv workspace),
`apps/web` (Next.js + TypeScript + Tailwind + React Flow, created at Phase 8),
and domain logic in `packages/*` as Python packages (`ntsb`, `ingestion`,
`evidence`, `retrieval`, `investigation`, `evaluation`, `observability`).
The spec's TypeScript interfaces become Pydantic models in
`packages/evidence`; TypeScript types for the UI are generated from the API
schema when `apps/web` exists, never maintained by hand in two places.

## Alternatives considered

- **Python-only repo, UI bolted on later** — simplest to the five-case gate,
  but the spec's layout and the portfolio UI are both mandatory eventually;
  the monorepo skeleton costs nothing now because `apps/web` is a placeholder.
- **Full TypeScript stack** — matches the spec's interface syntax but abandons
  the Python document/data ecosystem (Docling, Polars, Pydantic AI) that the
  workload actually needs.

## Consequences

Two toolchains (uv, pnpm) exist in one repo; only uv is active until Phase 8.
Package boundaries in `docs/architecture.md` are the contract: domain logic
never lives in `apps/`.
