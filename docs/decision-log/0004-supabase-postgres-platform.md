# 0004 — Supabase Postgres as the platform (DB + storage + observability tables)

**Date:** 2026-08-24 · **Status:** Accepted · **Owner decision:** user-directed

## Problem

The spec wants PostgreSQL (JSONB + FTS + relational), optional pgvector,
S3-compatible object storage in production with local filesystem in dev, a
lightweight durable job queue, and first-class observability storage.

## Decision

Supabase is the single platform: Postgres (with pgvector) for all
investigation state, Supabase Storage for immutable source artifacts,
row-level security as part of the blindness boundary. Local development uses
the Supabase CLI local stack (`supabase start`) so migrations, RLS policies,
and storage behave identically in dev and hosted. The job queue is a Postgres
table with `FOR UPDATE SKIP LOCKED` — no Redis, no Kafka. Observability spans,
model runs, and prompt versions are ordinary tables in the same database.

## Alternatives considered

- **Self-managed Postgres + MinIO + filesystem** — fewer moving parts to
  understand, more to operate; loses hosted parity. Rejected.
- **Langfuse self-hosted for observability** — adds Postgres + ClickHouse +
  Redis + MinIO + two services to trace 5–50 investigations. Rejected as
  disproportionate; the span schema follows OTel GenAI conventions closely
  enough to export later if a UI becomes worth it (see 0008).

## Consequences

Migrations and RLS policies are code-reviewed SQL in `supabase/migrations/`.
Storage access always goes through a `SourceStore` interface with a
local-filesystem implementation for offline tests. pgvector is enabled but
embedding dimensions stay ≤ 2000 (HNSW limit); the embedding model choice is
deferred to the retrieval phase (open decision D4).
