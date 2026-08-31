# Hosted Supabase Development Validation

**Date:** 2026-08-27 · **Environment:** development · **Tier:** Supabase Free

## Configuration checks

- Supabase CLI authenticated and linked to the project matching `SUPABASE_URL`.
- Current publishable, secret, and JWKS settings are configured locally and
  gitignored.
- Secret-key REST and Storage authentication returned HTTP 200.
- Publishable-key Auth settings returned HTTP 200.
- Hosted psycopg connection returned `select 1` successfully.
- Hyperfusion authentication and configured text/vision model discovery passed.
- Context.dev key format validation passed; no credit-consuming call was made.

No secret values or project identifiers are recorded here.

## Migration deployment

The first dry run listed exactly migrations 0001–0004 and no reset/drop
operation. They were applied successfully. The hosted verifier then detected
that `source_blobs` lacked forced RLS, so migration 0005 enabled/forced RLS and
revoked anon/authenticated privileges. A second dry run listed only 0005 before
it was applied. A later live access audit found Supabase's default table grants;
migration 0006 revoked table, sequence, and function access from public client
roles and hardened matching default privileges. Migration 0007 added explicit
semantic-unit completion checkpoints so valid zero-observation results are
idempotent without fabricating evidence.

Applied migrations:

```text
0001_cases_and_sources.sql
0002_evidence_processing.sql
0003_processing_skip_audit.sql
0004_hosted_environment_and_buckets.sql
0005_source_blob_rls.sql
0006_revoke_public_access.sql
0007_semantic_unit_completions.sql
```

## Hosted verification

```text
PASS hosted environment marker
PASS expected schema and RLS
PASS private source and derived buckets
PASS processor final-finding boundary
```

Both `casezero-sources` and `casezero-derived` exist as private buckets. No
`anon` or `authenticated` public-schema table grants remain. Live requests with
the publishable key returned no REST rows or Storage listings, and known private
object reads were denied. The database environment marker is `development`.
This project is not approved for production traffic.

## Remaining gate

Phase 1 still requires fresh hosted ingestion, Hyperfusion-only processing,
and a no-change idempotency rerun. Temporary local database rows are not copied.
