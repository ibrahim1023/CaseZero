# Phase 2 Blindness Infrastructure Validation

**Validated:** 2026-09-03  
**Persistent reference case:** CEN22FA375  
**Environment:** hosted Supabase Free development project

Phase 2 implements temporal cutoff persistence, role-scoped RLS, official-result
blocking, atomic investigation locks, post-lock immutability, constrained access
audit events, and a deterministic leakage auditor. All real lock/reveal tests
used rollback-isolated fixtures; the persistent reference case was not locked or
revealed.

## Persistent reference-case state

```text
reference_state BLIND
reference_cutoff_configured PASS
reference_lock_count 0
reference_blind_audit_count 1
stale_transactions 0
background_processes PASS
```

The stored cutoff was supplied by the committed CEN22FA375 curated manifest
through `enter_blind`; it was not inferred from event, retrieval, publication, or
current time. The single `ENTERED_BLIND` event records lifecycle metadata only.

A role-scoped no-change processing run after RLS hardening returned:

```json
{
  "artifacts": 0,
  "candidates": 0,
  "evidence_items": 0,
  "failures": [],
  "model_usage": {},
  "reused_candidate_runs": 11,
  "reused_semantic_runs": 180,
  "reused_structural_runs": 3,
  "status_counts": {"SKIPPED_RIGHTS": 12, "SUCCEEDED": 3},
  "structural_units": 0
}
```

This confirms the dedicated `casezero_processor` connection can read only the
eligible active graph and retain Phase 1 idempotency without a model call.

## Offline verification

Executed from the repository root:

```text
uv run ruff check .
All checks passed!

uv run mypy
Success: no issues found in 49 source files

uv run pytest
168 passed, 9 deselected

uv run pytest tests/blindness -v
7 passed
```

The blindness suite was mutation-tested by temporarily removing `WEB_SEARCH`
from the forbidden-capability set. Its exact attack test failed; restoring the
rule returned all seven tests to green.

The pure deterministic auditor covers:

- blind reads of blocked visibility;
- null and post-cutoff evidence;
- evaluation access before lock;
- forbidden blind capabilities;
- unapproved blind network hosts;
- independently detected classification mismatch;
- processing mutation after lock;
- malformed case/document audit context.

No auditor test calls a model, database, network client, or current clock.

## Hosted rollback verification

Because Docker was unavailable, hosted tests used the non-production Sydney
Supavisor session pooler and force-rollback transactions:

```text
CASEZERO_DB_TEST=1 uv run pytest -m db -v
7 passed, 170 deselected
```

Measured behavior includes:

- `ACQUIRING -> BLIND` stores the explicit cutoff atomically;
- a migrated `BLIND` null cutoff can be backfilled once;
- same-cutoff entry is idempotent and conflicting cutoff fails;
- blind and processor roles see only pre-cutoff, `AI_ALLOWED`,
  `INVESTIGATION_EVIDENCE` sources that pass conservative metadata checks;
- processor cannot create a derived run for a hidden post-cutoff source;
- processor can link a pre-acquired source only through the checksum- and
  eligibility-checking function, and cannot escalate reviewed disposition;
- evaluation sees no official row before lock;
- the lock function hashes the canonical snapshot and eligible active evidence
  projection, excluding an owner-inserted active official-evidence attack row,
  then inserts one lock, changes state, and appends an event atomically;
- duplicate lock fails without replacing the lock;
- after fixture lock, blind still sees no `FINAL_FINDING`, while evaluation sees
  the fixture official row;
- lock, cutoff, docket, source, and EvidenceItem mutation fail after lock;
- audit insertion is role-bound and audit update fails.

## pgTAP RLS and mutation verification

All files under `supabase/tests/` were executed with
`psql -X -v ON_ERROR_STOP=1` against the non-production session pooler. Each file
runs inside `BEGIN ... ROLLBACK`.

| File | Assertions | Result |
|---|---:|---|
| `0001_visibility_rls.sql` | 3 | all `ok` |
| `0002_processor_rls.sql` | 5 | all `ok` |
| `0003_phase2_boundary_schema.sql` | 15 | all `ok` |
| `0004_blindness_attack_rls.sql` | 7 | all `ok` |
| `0005_post_lock_immutability.sql` | 5 | all `ok` |
| **Total** | **35** | **all `ok`** |

The SQL fixtures scope observations to their temporary case IDs so persistent
eligible reference rows cannot contaminate counts.

## Hosted environment and public-access checks

```text
uv run python scripts/verify_hosted_supabase.py
PASS hosted environment marker
PASS expected schema and RLS
PASS private source and derived buckets
PASS processor final-finding boundary
PASS reference case cutoff and unlocked state

publishable_rest_reads_blocked PASS
publishable_rpc_calls_blocked PASS
publishable_storage_lists_blocked PASS
```

The REST probe covered all CaseZero tables, including
`investigation_locks` and `access_audit_events`. RPC probes covered
`enter_blind`, `lock_investigation`, `append_access_audit_event`, and
`link_processable_source`. Storage
probes covered both private buckets. No source content, object name, credential,
assessment, or prompt was printed.

## Lock and audit boundaries

The lock uses `postgres-jsonb-text-v1`: SHA-256 of PostgreSQL `jsonb::text` UTF-8
bytes. The rollback test independently recomputed the assessment digest from a
key-reordered JSON object and matched the stored hash. The evidence-set digest
covers each active EvidenceItem JSON, selected semantic model run and prompt
hash, structural checksum, and immutable source checksum in EvidenceItem UUID
order.

`AccessAuditEvent` has a closed schema. It contains fixed stage, actor,
capability, operation, reason code, identifiers, optional hostname, decision,
and UTC timestamp fields. It has no payload, prompt, model output, credential,
header, arbitrary URL, or arbitrary exception field. The database append
function verifies that the declared actor/stage matches the active scoped role.

## Limitations and deferred work

- Capability absence plus deterministic audit is not an OS/container firewall.
  Blind services have no arbitrary HTTP, NTSB, Context.dev, web-search, or raw
  Storage dependency, but Phase 2 does not claim process-level packet filtering.
- The backend migration owner remains outside the runtime threat model and can
  administratively alter database controls. Runtime processor/blind/evaluation
  paths use dedicated role-scoped connections.
- Conservative metadata classification detects known title/type/date/disposition
  mismatches. It cannot infer that deceptively labeled content is official when
  reviewed metadata provides no such signal.
- The lock fixture uses the versioned generic Phase 2 assessment envelope.
  Phase 5 supplies the typed `FinalAssessment` producer without changing the
  lock schema.
- The persistent CEN22FA375 case contains no official-result row and was not
  revealed. Official access transition was proven only with rollback fixtures.
- Local `supabase test db` was not run because Docker was unavailable. The same
  pgTAP files were executed against non-production hosted Postgres and rolled
  back.
- Phase 3 investigation tools do not exist yet. It must wrap the Phase 2 blind
  access service rather than introduce an unrestricted repository path.
- No benchmark quality, semantic correctness, causal accuracy, or legal outcome
  claim is made by this infrastructure validation.
