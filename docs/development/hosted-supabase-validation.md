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
idempotent without fabricating evidence. Migration 0008 applies the same
explicit completion contract to candidate batches, keyed by their exact
evidence sets, including valid zero-candidate outputs. Migration 0009 separates
visibility-blocked audit records from rights-based skips. Phase 2 migrations
0010–0017 add the stored cutoff, forced-RLS lock/audit schema, guarded blind
transition, hardened role policies, database-computed eligible-evidence lock
hashes, post-lock immutability triggers, role-bound append-only audit, and a
checksum/eligibility-guarded source-link function. Each migration was dry-run
before deployment; no reset, truncate, drop-table, or data deletion was applied.

Applied migrations:

```text
0001_cases_and_sources.sql
0002_evidence_processing.sql
0003_processing_skip_audit.sql
0004_hosted_environment_and_buckets.sql
0005_source_blob_rls.sql
0006_revoke_public_access.sql
0007_semantic_unit_completions.sql
0008_candidate_batch_completions.sql
0009_processing_skip_statuses.sql
0010_phase2_boundary_schema.sql
0011_enter_blind.sql
0012_blindness_rls.sql
0013_investigation_lock.sql
0014_post_lock_immutability.sql
0015_access_audit.sql
0016_lock_eligible_evidence_only.sql
0017_link_processable_source.sql
```

## Hosted verification

```text
PASS hosted environment marker
PASS expected schema and RLS
PASS private source and derived buckets
PASS processor final-finding boundary
PASS reference case cutoff and unlocked state
```

Both `casezero-sources` and `casezero-derived` exist as private buckets. No
`anon` or `authenticated` public-schema table grants remain. Live requests with
the publishable key returned no REST rows or Storage listings; Phase 2 RPC calls
were also denied. Seven rollback-isolated hosted repository tests passed, and all
five pgTAP files reported only `ok` assertions after fixture queries were scoped
to their temporary cases.

CEN22FA375 received its cutoff only from the committed curated manifest. It
remains `BLIND`, has no investigation lock, and completed a role-scoped no-change
processing run with 3 structural, 180 semantic, and 11 candidate checkpoints
reused and no model usage. The database environment marker is `development`.
This project is not approved for production traffic.

## Remaining gate

Phase 2 still requires the final offline/hosted verification record and mandatory
phase explanation. Temporary lock fixtures were rolled back; no official result
was revealed.
