# Phase 2 Blindness Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Repository override:** `AGENTS.md` prohibits those execution skills. Execute
> directly, test-first, with one verified commit per task.

**Goal:** Enforce temporal blindness, official-result blocking, immutable case
locking, capability absence, and deterministic leakage auditing as database and
typed-runtime boundaries.

**Architecture:** Hosted Postgres owns cutoff/state transitions, visibility RLS,
and atomic lock hashes. Dedicated non-pooled connections apply an allowlisted
runtime role for their full lifetime so independent Phase 1 checkpoints continue
to commit under RLS. Typed access/audit contracts feed an evaluation-owned pure
leakage auditor; blind composition contains no acquisition, web, Context.dev, or
raw Storage capability.

**Tech Stack:** Python 3.12, strict Pydantic 2, psycopg 3, PostgreSQL 17,
Supabase Free/Postgres RLS, pgcrypto, pgTAP, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-01-phase2-blindness-infrastructure-design.md`

## Global Constraints

- Default tests make no live model, NTSB, Context.dev, Supabase, or other network
  calls. Hosted tests require explicit environment flags.
- `anon`, `authenticated`, and `PUBLIC` receive no CaseZero data or function
  access.
- Runtime role names are closed enums and SQL identifiers, never caller strings.
- Role-scoped connections are dedicated and non-pooled; always reset role and
  close after use.
- Blind source eligibility requires `AI_ALLOWED`,
  `INVESTIGATION_EVIDENCE`, a non-null publication date at or before the stored
  case cutoff, and conservative metadata eligibility.
- The persistent CEN22FA375 case remains `BLIND` and unlocked.
- Lock and evidence hashes use `postgres-jsonb-text-v1`; no Python alternative
  silently substitutes for the database algorithm.
- Access audit rows contain identifiers, enums, hostnames, and stable reason
  codes only—never source content, prompts, model output, URLs with query
  strings, credentials, or arbitrary exception text.
- Work test-first and commit each task independently after focused verification.
- Do not add a queue, cache, new service process, OS firewall, user interface, or
  Phase 3 investigation behavior.

---

### Task 1: Blindness and Lock Boundary Contracts

**Files:**
- Create: `packages/evidence/src/casezero_evidence/blindness.py`
- Modify: `packages/evidence/src/casezero_evidence/__init__.py`
- Create: `packages/evidence/tests/test_blindness_models.py`

**Interfaces:**
- Produces: `WorkflowStage`, `RuntimeActor`, `AccessCapability`,
  `AccessOperation`, `AuditReasonCode`, `AssessmentSnapshot`,
  `InvestigationLock`, and `AccessAuditEvent`.
- `AssessmentSnapshot` fields: `schema_version: str`, `assessment_kind: str`,
  `payload: JsonValue`.
- `InvestigationLock` fields mirror `public.investigation_locks` and require
  lowercase SHA-256 values, aware UTC `locked_at`, non-empty version mappings,
  and `hash_algorithm == "postgres-jsonb-text-v1"`.
- `AccessAuditEvent` validates stage/capability context and exposes no free-form
  payload field.

- [ ] **Step 1: Write failing strict-model tests**

```python
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from casezero_evidence import (
    AccessAuditEvent,
    AccessCapability,
    AccessOperation,
    AssessmentSnapshot,
    InvestigationLock,
    RuntimeActor,
    WorkflowStage,
)


def test_lock_contract_requires_versioned_snapshot_and_hashes() -> None:
    lock = InvestigationLock(
        case_id=uuid4(),
        assessment_snapshot=AssessmentSnapshot(
            schema_version="phase2-lock-contract-v1",
            assessment_kind="fixture",
            payload={"result": "INSUFFICIENT_EVIDENCE"},
        ),
        assessment_hash="a" * 64,
        evidence_set_hash="b" * 64,
        hash_algorithm="postgres-jsonb-text-v1",
        model_versions={"evidence": "qwen/qwen3-32b"},
        prompt_versions={"evidence": "casezero.evidence.v1"},
        system_version="phase2-test",
        locked_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert lock.hash_algorithm == "postgres-jsonb-text-v1"


def test_access_event_rejects_network_payload_or_missing_host() -> None:
    with pytest.raises(ValueError, match="network_host"):
        AccessAuditEvent(
            case_id=uuid4(),
            stage=WorkflowStage.BLIND,
            actor_role=RuntimeActor.BLIND,
            capability=AccessCapability.MODEL_INFERENCE,
            operation=AccessOperation.NETWORK,
            allowed=True,
            reason_code="ALLOWED_MODEL_HOST",
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
```

- [ ] **Step 2: Run RED test**

Run:

```bash
uv run pytest packages/evidence/tests/test_blindness_models.py -v
```

Expected: collection fails because the boundary types do not exist.

- [ ] **Step 3: Implement strict enums and models**

Use `StrictModel`, `field_validator`, and `model_validator`; permit only hostname
text in `network_host`, require it only for `NETWORK`, and forbid unknown fields.
Do not add an arbitrary metadata dictionary to audit events.

- [ ] **Step 4: Run focused and package checks**

```bash
uv run pytest packages/evidence/tests/test_blindness_models.py -v
uv run ruff check packages/evidence
uv run mypy
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/evidence
git commit -m "feat(blindness): add lock and access audit contracts"
```

---

### Task 2: Phase 2 Boundary Schema

**Files:**
- Create: `supabase/migrations/0010_phase2_boundary_schema.sql`
- Create: `supabase/tests/0003_phase2_boundary_schema.sql`
- Modify: `apps/api/src/casezero_api/hosted_verify.py`
- Modify: `scripts/verify_hosted_supabase.py`
- Modify: `tests/test_verify_hosted_supabase.py`

**Interfaces:**
- Adds nullable `public.cases.evidence_cutoff timestamptz`.
- Creates `public.investigation_locks` with the exact Task 1 lock shape.
- Creates `public.access_audit_events` with enum-like SQL checks matching Task 1.
- Enables `extensions.pgcrypto` and forced RLS on both tables.
- Produces no public policies or direct mutation grants.

- [ ] **Step 1: Write failing hosted-state and pgTAP tests**

Extend `HostedState` with `cutoff_column`, `lock_table`, `audit_table`,
`lock_rls`, and `audit_rls`. Add a pure test:

```python
def test_phase2_schema_is_required() -> None:
    errors = evaluate_hosted_state(
        state(cutoff_column=False, lock_table=False, audit_table=False)
    )
    assert any("evidence_cutoff" in error for error in errors)
    assert any("investigation_locks" in error for error in errors)
    assert any("access_audit_events" in error for error in errors)
```

Create pgTAP assertions for both tables, forced RLS, the cutoff column, and no
`anon`/`authenticated` privileges.

- [ ] **Step 2: Run RED tests**

```bash
uv run pytest tests/test_verify_hosted_supabase.py -v
```

Expected: failure because Phase 2 schema state is absent.

- [ ] **Step 3: Write migration**

The migration must execute in this order:

```sql
create extension if not exists pgcrypto with schema extensions;
alter table public.cases add column evidence_cutoff timestamptz;

create table public.investigation_locks (
  case_id uuid primary key references public.cases(id) on delete restrict,
  assessment_snapshot jsonb not null,
  assessment_hash text not null check (assessment_hash ~ '^[0-9a-f]{64}$'),
  evidence_set_hash text not null check (evidence_set_hash ~ '^[0-9a-f]{64}$'),
  hash_algorithm text not null check (hash_algorithm = 'postgres-jsonb-text-v1'),
  model_versions jsonb not null check (jsonb_typeof(model_versions) = 'object' and model_versions <> '{}'::jsonb),
  prompt_versions jsonb not null check (jsonb_typeof(prompt_versions) = 'object' and prompt_versions <> '{}'::jsonb),
  system_version text not null check (length(system_version) > 0),
  locked_at timestamptz not null
);
```

Create `access_audit_events` with fixed checks for stages, actors, capabilities,
operations, and a hostname-only `network_host`. Revoke all access from public
client roles, enable and force RLS, and add no permissive policy yet.

- [ ] **Step 4: Extend hosted inspection**

Query `information_schema.columns`, `information_schema.tables`, and
`pg_class.relrowsecurity/relforcerowsecurity`; do not infer Phase 2 readiness
from migration history alone.

- [ ] **Step 5: Run offline checks**

```bash
uv run pytest tests/test_verify_hosted_supabase.py -v
uv run ruff check apps/api/src/casezero_api/hosted_verify.py scripts/verify_hosted_supabase.py tests/test_verify_hosted_supabase.py
uv run mypy
```

Expected: all pass against synthetic hosted state. Do not push the migration yet.

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/0010_phase2_boundary_schema.sql supabase/tests/0003_phase2_boundary_schema.sql apps/api/src/casezero_api/hosted_verify.py scripts/verify_hosted_supabase.py tests/test_verify_hosted_supabase.py
git commit -m "feat(db): add Phase 2 boundary schema"
```

---

### Task 3: Atomic Cutoff and Blind Transition

**Files:**
- Create: `supabase/migrations/0011_enter_blind.sql`
- Modify: `supabase/tests/0003_phase2_boundary_schema.sql`
- Modify: `apps/api/src/casezero_api/repository.py`
- Modify: `apps/api/src/casezero_api/ingest.py`
- Modify: `apps/api/tests/test_ingest_cli.py`
- Modify: `apps/api/tests/test_acquisition_repository_db.py`

**Interfaces:**
- Replaces `mark_blind(case_id)` with
  `enter_blind(case_id: UUID, evidence_cutoff: datetime) -> None`.
- SQL function:
  `public.enter_blind(target_case_id uuid, target_cutoff timestamptz) returns uuid`.
- Existing migrated `BLIND` rows may set a null cutoff once. Repeated calls then
  succeed only for the same cutoff; locked/revealed/conflicting transitions fail.

- [ ] **Step 1: Write failing service and DB tests**

Update the in-memory ingestion repository fake to record the cutoff and assert:

```python
await service.ingest("CEN22FA375", manifest, cutoff=CUTOFF)
assert repository.entered_blind == [(CASE_ID, CUTOFF)]
```

In the rollback DB test assert `ACQUIRING -> BLIND`, one-time null-cutoff
backfill for a migrated `BLIND` row, same-cutoff idempotency,
conflicting-cutoff failure, and retained original value.

- [ ] **Step 2: Run RED tests**

```bash
uv run pytest apps/api/tests/test_ingest_cli.py -v
```

Expected: failure because `enter_blind` is not in the protocol/fake.

- [ ] **Step 3: Add guarded SQL function**

Create `public.enter_blind` as `SECURITY DEFINER` with fixed search path. It
sets function-local `TimeZone = 'UTC'`, locks the case row, rejects null inputs,
and relies on the typed Python boundary to reject non-zero-offset datetimes
before SQL. It performs `ACQUIRING -> BLIND`, one migrated `BLIND` null-to-value backfill, or
same-cutoff `BLIND -> BLIND`, appends a fixed lifecycle row directly to
`access_audit_events`, and returns the case ID. Revoke execution from `PUBLIC`, `anon`, and `authenticated`. The trusted
acquisition connection invokes it as migration owner; do not grant it to blind,
eval, or processor roles.

- [ ] **Step 4: Update Python acquisition flow**

Validate aware UTC before SQL. `IngestService` passes the same cutoff used by the
downloader to `enter_blind`. Preserve `CaseStateError` for conflicting or illegal
transitions.

- [ ] **Step 5: Run checks**

```bash
uv run pytest apps/api/tests/test_ingest_cli.py -v
uv run ruff check apps/api
uv run mypy
```

The DB test remains opt-in until hosted deployment.

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/0011_enter_blind.sql supabase/tests/0003_phase2_boundary_schema.sql apps/api/src/casezero_api/repository.py apps/api/src/casezero_api/ingest.py apps/api/tests
git commit -m "feat(blindness): persist the case cutoff atomically"
```

---

### Task 4: Dedicated Role-Scoped Connections

**Files:**
- Create: `apps/api/src/casezero_api/session.py`
- Create: `apps/api/tests/test_session.py`
- Modify: `apps/api/src/casezero_api/process.py`
- Modify: `apps/api/tests/test_process_cli.py`

**Interfaces:**
- `RuntimeRole(StrEnum)`: `PROCESSOR`, `BLIND`, `EVALUATION`.
- `role_scoped_connection(database_url: str, role: RuntimeRole) -> AsyncIterator[AsyncConnection]`.
- The context opens a new non-pooled connection, applies `SET ROLE` through a
  psycopg `sql.Identifier`, yields it, attempts `RESET ROLE`, and always closes.
- `process_from_environment` uses `RuntimeRole.PROCESSOR`; acquisition remains
  on a separate trusted connection.

- [ ] **Step 1: Write failing role-session tests**

Use a fake connection factory to assert exact sequencing:

```python
async with role_scoped_connection("postgresql://fixture", RuntimeRole.BLIND, connect=factory) as connection:
    assert connection is fake
assert fake.commands == ["SET ROLE casezero_blind", "RESET ROLE"]
assert fake.closed
```

Also assert the API accepts `RuntimeRole`, not arbitrary strings.

- [ ] **Step 2: Run RED test**

```bash
uv run pytest apps/api/tests/test_session.py -v
```

Expected: import failure because `session.py` does not exist.

- [ ] **Step 3: Implement the context manager**

Use `psycopg.sql.SQL("SET ROLE {}").format(sql.Identifier(role.value))`. The
connection must use `autocommit=True` so Phase 1 per-unit transactions remain
independently committed. On failure, close even when `RESET ROLE` cannot run.
Do not add pooling.

- [ ] **Step 4: Compose processor role**

Replace the raw connection in `process_from_environment`; keep the existing
persistence lock and per-batch repository transactions unchanged.

- [ ] **Step 5: Run checks**

```bash
uv run pytest apps/api/tests/test_session.py apps/api/tests/test_process_cli.py -v
uv run ruff check apps/api
uv run mypy
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/casezero_api/session.py apps/api/src/casezero_api/process.py apps/api/tests
git commit -m "feat(blindness): scope processing connections to RLS roles"
```

---

### Task 5: Conservative Eligibility and RLS Hardening

**Files:**
- Create: `supabase/migrations/0012_blindness_rls.sql`
- Create: `supabase/tests/0004_blindness_attack_rls.sql`
- Modify: `supabase/tests/0001_visibility_rls.sql`
- Modify: `supabase/tests/0002_processor_rls.sql`

**Interfaces:**
- SQL helper `public.is_blind_eligible(source_documents) returns boolean` is
  deterministic, stable, and not public-executable.
- Blind source policy requires state, lock-independent cutoff, non-null date,
  `AI_ALLOWED`, investigation visibility, and conservative metadata eligibility.
- Processor policies require the same source lineage plus `cases.state='BLIND'`.
- Evaluation policies require `LOCKED|REVEALED` and an investigation lock.

- [ ] **Step 1: Write failing pgTAP attack fixtures**

Fixtures insert:

- one eligible factual source;
- one `FINAL_FINDING` source;
- one post-cutoff investigation source;
- one null-date investigation source;
- one `LINK_ONLY` investigation source;
- one final-report-titled row incorrectly labeled investigation evidence.

Under `set local role casezero_blind`, assert only the first row is visible.
Under `casezero_processor`, assert the same and verify writes are denied once the
case is locked. Under `casezero_eval`, assert official rows are invisible before
a lock row exists.

- [ ] **Step 2: Verify RED in an available Supabase test environment**

Preferred:

```bash
supabase test db
```

If local Docker is unavailable, run the SQL through the rollback-isolated hosted
DB test harness after migration deployment in Task 11 and record that this RED
step was inspected but not executable locally. Do not weaken policies to satisfy
the environment.

- [ ] **Step 3: Implement SQL eligibility helper and policies**

The helper must independently require:

```sql
source.visibility = 'INVESTIGATION_EVIDENCE'
and source.published_at is not null
and source.published_at <= case_record.evidence_cutoff
and docket.processing_disposition = 'AI_ALLOWED'
and source.document_type <> 'FINAL_REPORT'
and source.title !~* '\m(final report|probable cause|adopted|analysis|findings?|recommendations?)\M'
```

Drop old policies by name before creating replacements. Derived-table policies
must join through source/case lineage; candidate completion/candidate policies
must require case `BLIND`. Evaluation source policy must join
`investigation_locks`.

- [ ] **Step 4: Verify SQL privileges**

Assert no public client grants, blind/eval roles are select-only, processor has
only required select/insert/update privileges, and `LOCAL_ONLY` is removed from
the processor docket policy.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/0012_blindness_rls.sql supabase/tests
git commit -m "feat(blindness): enforce cutoff and disposition in RLS"
```

---

### Task 6: Atomic Investigation Lock

**Files:**
- Create: `supabase/migrations/0013_investigation_lock.sql`
- Modify: `supabase/tests/0004_blindness_attack_rls.sql`
- Create: `apps/api/src/casezero_api/lock.py`
- Create: `apps/api/tests/test_lock.py`

**Interfaces:**
- SQL:
  `public.lock_investigation(target_case_id uuid, snapshot jsonb, model_versions jsonb, prompt_versions jsonb, system_version text) returns public.investigation_locks`.
- Python:
  `InvestigationLockService.create_lock(case_id, snapshot, model_versions, prompt_versions, system_version) -> InvestigationLock`.
- Function is migration-owner `SECURITY DEFINER`, fixed-search-path, and executable
  only by `casezero_blind`.

- [ ] **Step 1: Write failing lock-service and SQL tests**

Use a temporary fixture case, one source, structural unit, successful semantic
run, EvidenceItem, and active completion. Assert:

- lock changes `BLIND -> LOCKED`;
- returned hashes match stored hashes;
- same JSON object with different input key order produces the same assessment
  hash in separate rollback fixtures;
- changing active evidence changes `evidence_set_hash`;
- duplicate lock fails;
- empty versions/snapshot or no active evidence rolls back completely.

- [ ] **Step 2: Run RED Python test**

```bash
uv run pytest apps/api/tests/test_lock.py -v
```

Expected: import failure because the service does not exist.

- [ ] **Step 3: Implement SQL hash projection**

Assessment input:

```sql
encode(extensions.digest(convert_to(snapshot::jsonb::text, 'UTF8'), 'sha256'), 'hex')
```

Evidence input is the UTF-8 form of this ordered JSONB aggregate:

```sql
jsonb_agg(
  jsonb_build_object(
    'evidence_id', evidence.id,
    'item', evidence.item,
    'model_run_id', semantic_run.id,
    'prompt_hash', semantic_run.prompt_hash,
    'structural_unit_id', unit.id,
    'content_checksum', unit.content_checksum,
    'source_document_id', source.id,
    'source_checksum', source.checksum
  ) order by evidence.id
)::text
```

Join `semantic_unit_completions -> model_runs -> evidence_items ->
structural_units -> source_documents` so only active evidence enters the hash.
Store `hash_algorithm='postgres-jsonb-text-v1'`. Use
`transaction_timestamp()` for `locked_at`. Insert lock, transition state, and
append an audit event inside the function; any exception rolls back all effects.

- [ ] **Step 4: Implement typed service**

Call the function only through a `RuntimeRole.BLIND` connection. Validate the
returned row with `InvestigationLock`; do not compute a competing Python hash.

- [ ] **Step 5: Run offline checks**

Use a fake repository/function response for default service tests:

```bash
uv run pytest apps/api/tests/test_lock.py -v
uv run ruff check apps/api
uv run mypy
```

SQL behavior runs in Task 11's hosted rollback gate.

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/0013_investigation_lock.sql supabase/tests/0004_blindness_attack_rls.sql apps/api/src/casezero_api/lock.py apps/api/tests/test_lock.py
git commit -m "feat(blindness): lock investigations with database hashes"
```

---

### Task 7: Post-Lock Immutability

**Files:**
- Create: `supabase/migrations/0014_post_lock_immutability.sql`
- Create: `supabase/tests/0005_post_lock_immutability.sql`

**Interfaces:**
- Trigger function `public.reject_locked_case_mutation()` rejects protected DML
  whose source case is `LOCKED` or `REVEALED`.
- Trigger function `public.reject_lock_mutation()` always rejects lock update or
  delete.
- Cutoff trigger rejects changes after entering blind mode.

- [ ] **Step 1: Write failing pgTAP mutation attacks**

After locking a rollback fixture, attempt changes to cutoff, docket disposition,
source visibility/checksum, processing/model/evidence/completion/candidate rows,
and lock JSON/hash. Capture SQLSTATE and assert every mutation fails while
read-only queries still work.

- [ ] **Step 2: Verify RED using pgTAP or hosted rollback execution**

Expected: at least source-lineage tables currently permit owner-level mutation.

- [ ] **Step 3: Add narrow triggers**

Triggers resolve case lineage by the affected row's case/source/run foreign key.
They do not block acquisition while state is `ACQUIRING`, processing while state
is `BLIND`, or trusted rollback fixture cleanup. Do not add a generic dynamic-SQL
trigger that guesses column names.

- [ ] **Step 4: Run SQL tests**

```bash
supabase test db
```

Or execute `0005_post_lock_immutability.sql` through the hosted rollback harness
in Task 11 when Docker is unavailable.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/0014_post_lock_immutability.sql supabase/tests/0005_post_lock_immutability.sql
git commit -m "feat(blindness): freeze investigation state after lock"
```

---

### Task 8: Append-Only Audit and Runtime Capabilities

**Files:**
- Create: `supabase/migrations/0015_access_audit.sql`
- Create: `packages/evidence/src/casezero_evidence/access.py`
- Modify: `packages/evidence/src/casezero_evidence/__init__.py`
- Create: `packages/evidence/tests/test_access.py`
- Modify: `packages/observability/src/casezero_observability/reasoning.py`
- Modify: `packages/observability/tests/test_reasoning.py`

**Interfaces:**
- SQL:
  `public.append_access_audit_event(case_id, stage, actor, capability, operation, target_document_id, network_host, allowed, reason_code) returns uuid`.
- Python protocol:
  `AccessAuditRecorder.record(event: AccessAuditEvent) -> None`.
- `BlindAccessService` exposes `get_source_metadata`, `list_active_evidence`, and
  `get_evidence`; constructor receives only a role-scoped repository and recorder.
- `ModelRouter` accepts an optional recorder and emits a hostname-only allowed
  `MODEL_INFERENCE` network event for a case-scoped request.

- [ ] **Step 1: Write failing capability/audit tests**

Assert the blind service constructor and attributes contain no NTSB, Context.dev,
HTTP client, Storage store, secret key, or arbitrary tool registry. Test allowed
and denied document reads append exact enum events. Test model audit contains
only the configured hostname, not URL path/query, API key, or prompt.

- [ ] **Step 2: Run RED tests**

```bash
uv run pytest packages/evidence/tests/test_access.py packages/observability/tests/test_reasoning.py -v
```

- [ ] **Step 3: Implement constrained SQL append function**

Use `SECURITY DEFINER`, fixed search path, enum/check validation, and
`transaction_timestamp()`. Revoke direct insert/update/delete from runtime
roles; grant function execution only to processor, blind, and eval. Add an
always-reject update/delete trigger for audit rows.

- [ ] **Step 4: Implement typed service and model hook**

Do not add generic event metadata. Denied reads return a single not-found/denied
error without revealing target title, visibility, date, or type.

- [ ] **Step 5: Run checks**

```bash
uv run pytest packages/evidence/tests/test_access.py packages/observability/tests/test_reasoning.py -v
uv run ruff check packages/evidence packages/observability
uv run mypy
```

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/0015_access_audit.sql packages/evidence packages/observability
git commit -m "feat(blindness): audit constrained runtime capabilities"
```

---

### Task 9: Deterministic Leakage Auditor

**Files:**
- Create: `packages/evaluation/pyproject.toml`
- Create: `packages/evaluation/src/casezero_evaluation/__init__.py`
- Create: `packages/evaluation/src/casezero_evaluation/leakage.py`
- Create: `packages/evaluation/src/casezero_evaluation/py.typed`
- Create: `packages/evaluation/tests/test_leakage.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- `LeakageIncidentCode(StrEnum)` implements the eight codes in spec §7.
- `LeakageIncident` contains code, event ID, case ID, and target document ID only.
- `LeakageContext` contains cutoff, lock timestamp, source visibility/date, and
  independently recomputed eligibility keyed by IDs.
- `audit_temporal_leakage(events, context, allowed_hosts) -> LeakageReport` is a
  pure deterministic function; `LeakageReport.passed` is true only when incidents
  are empty.

- [ ] **Step 1: Write failing pure tests**

Create one test per incident code plus an allowed fixture containing blind
evidence reads, Supabase DB access, and Hyperfusion model inference. Assert exact
incident ordering by `(occurred_at, event_id, code)` and verify event payloads
cannot influence results because no payload field exists.

- [ ] **Step 2: Run RED test**

```bash
uv run pytest packages/evaluation/tests/test_leakage.py -v
```

Expected: package import failure.

- [ ] **Step 3: Register the package**

Add `casezero-evaluation` to root dependencies, workspace members, workspace
sources, and mypy package list. Depend only on `casezero-evidence` and Pydantic;
do not add a model or HTTP dependency.

- [ ] **Step 4: Implement the pure auditor**

No database calls, current-time reads, model calls, fuzzy matching, or free-form
reasoning. The caller builds `LeakageContext` under an administrative verification
session.

- [ ] **Step 5: Run package and workspace checks**

```bash
uv sync
uv run pytest packages/evaluation/tests/test_leakage.py -v
uv run ruff check packages/evaluation pyproject.toml
uv run mypy
```

- [ ] **Step 6: Commit**

```bash
git add packages/evaluation pyproject.toml uv.lock
git commit -m "feat(evaluation): add deterministic temporal leakage audit"
```

---

### Task 10: Offline Blindness Attack Suite

**Files:**
- Create: `tests/blindness/__init__.py`
- Create: `tests/blindness/test_capability_attacks.py`
- Create: `tests/blindness/test_leakage_attacks.py`
- Modify: `docs/development/verification-loops.md`

**Interfaces:**
- Provides the default offline attack gate required whenever visibility or lock
  behavior changes.
- Contains no live DB/network/model dependency; SQL/RLS attacks remain pgTAP and
  hosted rollback tests.

- [ ] **Step 1: Write attack tests**

Cover direct blocked-document lookup, visibility mismatch, null/post-cutoff
dates, evaluation before lock, forbidden capability/host, post-lock mutation
event, malformed event, and lock replay metadata. Assert stable incident codes
and absence of sensitive text in serialized reports.

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/blindness -v
```

Expected: pass using Task 8/9 implementations. Temporarily revert one allowlist
condition to prove the relevant attack fails, then restore it and rerun.

- [ ] **Step 3: Update verification loop**

Keep `uv run pytest tests/blindness/ -v` as the exact visibility/locking command
and document that SQL attacks require the hosted DB marker when Docker is absent.

- [ ] **Step 4: Run Loop 2**

```bash
uv run ruff check .
uv run mypy
uv run pytest
```

- [ ] **Step 5: Commit**

```bash
git add tests/blindness docs/development/verification-loops.md
git commit -m "test(blindness): add offline temporal leakage attacks"
```

---

### Task 11: Hosted Deployment, Cutoff Backfill, and Boundary Verification

**Files:**
- Create: `scripts/backfill_case_cutoffs.py`
- Create: `tests/test_backfill_case_cutoffs.py`
- Modify: `scripts/verify_hosted_supabase.py`
- Modify: `docs/development/hosted-supabase-validation.md`

**Interfaces:**
- `backfill_registered_cutoffs(connection, manifests) -> int` reads cutoff values
  only from committed curated manifests and calls `enter_blind`; it never locks a
  case.
- Script refuses production and supports only registered manifests.
- Hosted verifier checks cutoff/policies/tables/forced RLS/function grants and
  asserts CEN22FA375 remains `BLIND` with no lock.

- [ ] **Step 1: Write failing backfill tests**

Use a fake repository/connection to assert the committed manifest cutoff is
passed exactly, unknown cases fail, repeated same-cutoff runs are idempotent, and
no lock function is called.

- [ ] **Step 2: Run RED test**

```bash
uv run pytest tests/test_backfill_case_cutoffs.py -v
```

- [ ] **Step 3: Implement guarded script**

Load `HostedSettings`, reject environment marker `production`, load registered
manifest paths, query their case IDs, and invoke `enter_blind`. Print case IDs and
counts only; do not print credentials or source URLs.

- [ ] **Step 4: Verify migration plan before deployment**

```bash
set -a; source .env; set +a; supabase db push --dry-run
```

Expected: exactly migrations `0010` through `0015`, no reset/drop/truncate/data
delete. Stop if the plan differs.

- [ ] **Step 5: Deploy and backfill**

```bash
set -a; source .env; set +a; supabase db push
set -a; source .env; set +a; uv run python scripts/backfill_case_cutoffs.py
set -a; source .env; set +a; uv run python scripts/verify_hosted_supabase.py
```

The policy deployment may temporarily hide the case from blind/processor roles
until the explicit cutoff backfill finishes. It must never expose official rows.

- [ ] **Step 6: Run hosted rollback tests**

```bash
set -a; source .env; set +a; CASEZERO_DB_TEST=1 uv run pytest -m db -v
```

Also execute the pgTAP SQL files transactionally against the non-production
project when local `supabase test db` is unavailable. Record exactly which path
ran.

- [ ] **Step 7: Re-run publishable-key denial and persistent-state checks**

Assert no public REST rows/private object listings, CEN state `BLIND`, stored
cutoff equals the manifest, and lock count is zero. Terminate every test process
and verify no idle transaction remains.

- [ ] **Step 8: Commit**

```bash
git add scripts/backfill_case_cutoffs.py tests/test_backfill_case_cutoffs.py scripts/verify_hosted_supabase.py docs/development/hosted-supabase-validation.md
git commit -m "chore(blindness): verify hosted Phase 2 boundary"
```

---

### Task 12: Phase 2 Validation and Explanation

**Files:**
- Create: `docs/development/phase-2-validation.md`

**Interfaces:**
- Records measured Phase 2 acceptance without locking the persistent reference
  case or claiming OS-level egress enforcement.

- [ ] **Step 1: Run final offline gate**

```bash
uv run ruff check .
uv run mypy
uv run pytest
uv run pytest tests/blindness/ -v
```

Record exact pass/skip counts.

- [ ] **Step 2: Run final hosted gate**

```bash
set -a; source .env; set +a; uv run python scripts/verify_hosted_supabase.py
set -a; source .env; set +a; CASEZERO_DB_TEST=1 uv run pytest -m db -v
```

Verify public access denial, zero leakage incidents for allowed fixtures, stable
incidents for attacks, CEN cutoff correctness, `state='BLIND'`, zero CEN locks,
and zero stale transactions.

- [ ] **Step 3: Write measured validation record**

Document commands/results, RLS attack outcomes, hash determinism, lock atomicity,
post-lock mutation denial, capability/audit coverage, CEN persistent state, and
all unexecuted checks. Explicitly state that capability absence plus audit is not
an OS firewall and that no real official finding was revealed.

- [ ] **Step 4: Commit**

```bash
git add docs/development/phase-2-validation.md
git commit -m "docs: record Phase 2 blindness validation"
```

- [ ] **Step 5: Generate required phase explanation**

Invoke `explain-diff-html` from Phase 1 endpoint commit `4c35a1c` through the
Phase 2 validation commit. Generate
`/tmp/2026-09-01-explanation-phase-2-blindness-infrastructure.html`, inspect all
links/anchors/CSS/JavaScript, verify every `<pre>` has `white-space: pre` or
`pre-wrap`, and keep the file outside the repository.

- [ ] **Step 6: Final cleanup**

Verify a clean worktree, no running CaseZero/test/http-server process, no browser
preview, and no hosted `idle in transaction` session. Only then mark Phase 2
complete.
