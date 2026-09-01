# Phase 2 Blindness Infrastructure Design

**Date:** 2026-09-01  
**Status:** Approved in conversation; pending implementation plan  
**Source of product truth:** `product-spec.md` §31.8 and Phase 2 (local-only;
never commit it)  
**Scope:** Evidence visibility, temporal cutoff, official-result blocking,
immutable case locking, capability enforcement, and deterministic leakage audit

## 1. Purpose

Phase 2 turns CaseZero's existing visibility labels and partial RLS policies into
a complete access-control boundary. A blind investigation must be unable to read
later official analysis or final findings even when application query code is
wrong or a prompt is manipulated. Stored visibility, disposition, title/type,
and cutoff metadata are checked independently; semantic misclassification that
is not detectable from those reviewed inputs remains a curation risk rather
than an RLS guarantee. Official material becomes readable to evaluation only after an immutable investigation lock exists.

This phase does not build the investigation engine, hypotheses, causal graph,
final assessment schema, benchmark grader suite, or user interface. It builds the
security and state-transition contracts those phases must use.

## 2. Accepted decisions

- Use one hosted Supabase Postgres database and the existing private,
  content-addressed Storage buckets.
- Enforce visibility and lifecycle in Postgres RLS and atomic database
  functions, not application filters or prompt instructions.
- Use dedicated, non-pooled role-scoped connections with allowlisted `SET ROLE`
  for processor, blind, and evaluation operations. Runtime role names are fixed
  enums, never user input.
- Implement the immutable lock contract now against a versioned canonical
  assessment snapshot. Phase 5 supplies the typed `FinalAssessment` without
  changing the lock boundary.
- Enforce network restrictions through absent capabilities plus append-only
  audit. OS-level network sandboxing is outside Phase 2.
- Do not lock the real CEN22FA375 development case during Phase 2. Lock tests use
  rollback-isolated fixtures.

## 3. Trust zones and roles

### 3.1 Acquisition

Acquisition is deterministic and privileged. It may call approved NTSB and
Context.dev clients, classify officially public source metadata, write immutable
source objects, and create source ledger rows. It cannot create or alter an
investigation lock and is not exposed to the investigation agent.

Transitioning a case from `ACQUIRING` to `BLIND` requires an explicit UTC
`evidence_cutoff`. The transition and cutoff assignment are atomic. Repeating
that operation is idempotent only when the supplied cutoff is identical.

### 3.2 Evidence processing

The existing `casezero_processor` role may read and write processing state only
while its case is `BLIND`, only for `AI_ALLOWED` docket items, and only through
lineage rooted in `INVESTIGATION_EVIDENCE` whose non-null `published_at` is at or
before the case cutoff. After lock, source, artifact, model, evidence, completion,
and candidate writes are denied.

The hosted Python connection may authenticate as the backend Postgres user, but
processor operations use a dedicated non-pooled connection that applies
`SET ROLE casezero_processor` before constructing the repository. Short reads
and independently committed unit/batch transactions therefore all execute under
RLS. The context resets the role and closes the connection on exit; no processor
repository method may rely on the backend user's RLS bypass.

### 3.3 Blind investigation

The `casezero_blind` role is `NOLOGIN NOBYPASSRLS`. It may read the case and the
active investigation evidence graph but never official source rows. Its source
policy requires all of:

- case state is `BLIND` or `LOCKED` for read-only replay;
- source visibility is `INVESTIGATION_EVIDENCE`;
- source `published_at` is non-null and no later than `cases.evidence_cutoff`;
- the linked docket item is `AI_ALLOWED`;
- a SQL eligibility function independently applies the conservative final-report
  and official-analysis title/type patterns to stored immutable metadata.

The deterministic auditor recomputes the same eligibility projection without
trusting the stored `visibility` value and emits `CLASSIFICATION_MISMATCH` when
they disagree. This catches mislabeled rows detectable from metadata; it cannot
infer that deceptively titled content is official without reviewed metadata.

Blind writes introduced in later phases must additionally require case state
`BLIND`. The blind runtime receives typed evidence/model capabilities. It does
not receive NTSB, Context.dev, arbitrary HTTP, web-search, raw Supabase Storage,
or Supabase secret-key clients.

### 3.4 Evaluation

The `casezero_eval` role is `NOLOGIN NOBYPASSRLS`. It can read investigation and
official source lineage only when the case is `LOCKED` or `REVEALED` and exactly
one immutable lock row exists. It cannot update the lock, assessment snapshot,
active evidence state, or case cutoff. Reveal-specific grading remains a later
phase; Phase 2 proves the access transition.

### 3.5 Public roles

`anon`, `authenticated`, and `PUBLIC` retain no CaseZero table, sequence,
function, or private-object access. New Phase 2 objects follow the revocation
and default-privilege policy established in Phase 1.

## 4. Data model

### 4.1 Case cutoff

Add nullable `cases.evidence_cutoff timestamptz` with a constraint requiring UTC
semantics. It is nullable only for pre-boundary `ACQUIRING` rows and migration of
existing development data. Database triggers or guarded functions prevent a
change once a case enters `BLIND`.

RLS fails closed when a blind case has no cutoff. Existing BLIND development
cases therefore become invisible to blind roles until an explicit operational
backfill reads the committed curated manifest and records its `blindCutoff`.
No cutoff is inferred from event date, retrieval time, maximum publication date,
or current time.

### 4.2 Investigation lock

Create `public.investigation_locks`:

| Column | Contract |
|---|---|
| `case_id` | Primary key and FK to `cases`; exactly one lock per case |
| `assessment_snapshot` | Versioned JSONB snapshot supplied by the caller |
| `assessment_hash` | SHA-256 computed in Postgres from canonical JSONB text |
| `evidence_set_hash` | SHA-256 computed in Postgres from active evidence state |
| `hash_algorithm` | Fixed identifier `postgres-jsonb-text-v1` |
| `model_versions` | Non-empty JSONB object |
| `prompt_versions` | Non-empty JSONB object |
| `system_version` | Non-empty text |
| `locked_at` | Database transaction timestamp |

The Phase 2 snapshot boundary model contains `schema_version`,
`assessment_kind`, and a JSON-compatible `payload`. Phase 5 replaces the generic
payload producer with `FinalAssessment`; the persisted lock shape remains
unchanged.

`postgres-jsonb-text-v1` is defined exactly as SHA-256 over the UTF-8 bytes from
PostgreSQL `jsonb::text`. JSONB object keys use PostgreSQL's deterministic
ordering, insignificant input whitespace is removed, duplicate keys collapse
according to JSONB input semantics, numeric values use JSONB normalization, and
array order remains significant. Evidence hashing first constructs a
`jsonb_agg` ordered by EvidenceItem UUID, then hashes that aggregate's
`jsonb::text` bytes using the same algorithm. The PostgreSQL major version is
part of the system version; changing database serialization semantics requires
a new hash-algorithm identifier rather than silently recomputing old locks.

No runtime role receives direct `INSERT`, `UPDATE`, or `DELETE` on this table.
Insert occurs only through the lock function. A trigger rejects runtime update
or delete attempts. The trusted migration owner can bypass or disable database
controls and is explicitly outside the runtime threat model; application code
must not use owner privileges for lock-table statements.

### 4.3 Access audit events

Create `public.access_audit_events` as append-only records:

- `id`, `case_id`, `occurred_at`;
- workflow stage (`ACQUISITION`, `PROCESSING`, `BLIND`, `EVALUATION`);
- fixed actor role and capability;
- operation (`READ`, `WRITE`, `NETWORK`, `LOCK`, `DENY`);
- optional target document ID;
- optional network host;
- allowed/denied boolean;
- stable reason code.

Rows contain no source content, prompts, model output, credentials, authorization
headers, query strings, or arbitrary exception text. Runtime roles insert only
through a constrained security-definer function with a fixed `search_path` and
revoked public execution. They cannot update or delete events.

A denied blind lookup records the requested document ID. The deterministic audit
later joins that ID under an administrative verification session to determine
the target visibility without exposing it to the blind caller.

## 5. Atomic lifecycle operations

### 5.1 Enter blind mode

`enter_blind(case_id, evidence_cutoff)`:

1. Require an aware UTC cutoff.
2. Lock the case row.
3. Permit `ACQUIRING -> BLIND` and set the cutoff atomically.
4. For migrated `BLIND` rows only, permit one explicit `NULL -> cutoff`
   backfill; afterward permit `BLIND -> BLIND` only if the cutoff is identical.
5. Reject `LOCKED` and `REVEALED`.
6. Append a lifecycle audit event.

Existing acquisition composition passes its already-validated CLI cutoff to
this operation.

### 5.2 Create lock

`lock_investigation(case_id, assessment_snapshot, model_versions,
prompt_versions, system_version)` is owned by the trusted migration owner,
uses `SECURITY DEFINER`, schema-qualifies every object, and sets
`search_path = pg_catalog, public`. `PUBLIC`, acquisition, processor, and
evaluation execution are revoked; only `casezero_blind` receives `EXECUTE`.
The backend must call it through a dedicated connection already scoped with
`SET ROLE casezero_blind`.

The function executes as one database transaction:

1. Lock and require the case row in state `BLIND` with a cutoff.
2. Require at least one active EvidenceItem selected by the current semantic
   completion records.
3. Validate non-empty version metadata and the snapshot envelope.
4. Build canonical assessment JSON from the JSONB value and compute SHA-256 in
   Postgres using `pgcrypto`.
5. Build the evidence-set projection in deterministic order and hash it.
6. Insert the immutable lock row.
7. Change the case state to `LOCKED`.
8. Append a lock audit event.
9. Return the validated lock record.

Any failure rolls back all nine effects. A second lock attempt fails rather than
returning or replacing the previous row.

The evidence-set projection includes, for every active evidence row:

- EvidenceItem ID and canonical stored item JSON;
- active semantic model-run ID and prompt hash;
- structural-unit ID and content checksum;
- source-document ID and immutable source checksum.

Rows are ordered by EvidenceItem UUID before JSON aggregation. Candidate rows
are not part of the evidence-set hash. The assessment snapshot may cite them in
a later phase, but evidence integrity is independently locked.

### 5.3 Post-lock immutability

After lock, policies reject changes to:

- case cutoff;
- source and docket classification used by blind processing;
- processing runs, artifacts, units, model runs, EvidenceItems;
- semantic and candidate completion pointers;
- provisional candidates;
- the lock row and assessment snapshot.

Read-only blind replay remains possible. Evaluation reads become possible.
`LOCKED -> REVEALED` is reserved for the evaluation workflow and is not required
to prove Phase 2.

## 6. Repository and capability interfaces

### 6.1 Role-scoped database session

Add a small backend context manager that opens a dedicated non-pooled
connection, executes one allowlisted `SET ROLE` using a fixed SQL identifier,
and yields the typed repository. On exit it executes `RESET ROLE` when possible
and always closes the connection. Callers cannot provide an arbitrary role
string, and role-scoped connections are never returned to a pool.

This session-level role scope allows each semantic unit or candidate batch to
commit independently without reverting to backend-owner privileges between
transactions. Acquisition administration remains a separate composition path.
Blind and evaluation services cannot receive an unscoped `EvidenceRepository`.

### 6.2 Blind access service

The Phase 2 blind service exposes the minimum reads needed to prove the
boundary: get case, get source metadata by ID, list active evidence, and read
active evidence by ID. Each attempt appends a structured access event. Raw
Storage bytes remain unavailable; source resolution for reviewers stays in the
trusted processing/verification path.

Phase 3 may add investigation tools only by wrapping this service. It must not
add a second unrestricted repository path.

### 6.3 Evaluation access service

The evaluation service exposes official source metadata and lock retrieval. RLS
returns no official rows before lock. After lock, the same query returns eligible
official rows. It does not expose lock mutation.

### 6.4 Network capability audit

Instrument approved clients at their composition boundaries. Blind-stage model
calls record capability `MODEL_INFERENCE` and the configured Hyperfusion host.
Database activity records `SUPABASE_DATABASE`. Any blind-stage event for
`WEB_SEARCH`, `CONTEXT_DEV`, `NTSB_ACQUISITION`, `RAW_STORAGE`, or an unapproved
network host is a leakage incident.

The runtime enforces absence by construction: forbidden clients are not fields,
parameters, or dependencies of the blind service. Phase 2 does not claim to
provide an OS firewall.

## 7. Deterministic leakage auditor

Add an evaluation-owned pure function over typed audit rows and administrative
source/lock metadata. It returns a structured report containing zero or more
incidents with stable codes; it never asks a model to judge leakage.

Incident rules:

1. `BLIND_BLOCKED_VISIBILITY_READ`: blind access targets visibility other than
   `INVESTIGATION_EVIDENCE`.
2. `BLIND_POST_CUTOFF_READ`: blind access targets a source with null or
   post-cutoff publication time.
3. `EVALUATION_BEFORE_LOCK`: evaluation access occurs before the lock timestamp
   or without a lock.
4. `FORBIDDEN_BLIND_CAPABILITY`: blind stage records a forbidden capability.
5. `FORBIDDEN_BLIND_HOST`: blind stage records a network host outside the fixed
   Hyperfusion/Supabase allowlist.
6. `CLASSIFICATION_MISMATCH`: stored visibility/disposition disagrees with the
   independently recomputed metadata-and-cutoff eligibility result.
7. `POST_LOCK_MUTATION`: processing or investigation mutation occurs after
   `locked_at`.
8. `MALFORMED_AUDIT_EVENT`: event lacks required case/stage/target context.

Any incident fails the blindness gate regardless of investigation quality.
Reports include event/document IDs and reason codes, not source content.

## 8. Security details

- Security-definer functions set an explicit trusted `search_path`, schema-
  qualify relations, validate enums, and revoke execution from public roles.
- Hashing uses `pgcrypto.digest(..., 'sha256')` and lowercase hexadecimal.
- Lock timestamps use database transaction time, not caller time.
- RLS is enabled and forced on new tables.
- Runtime roles are `NOLOGIN`, `NOBYPASSRLS`, and receive only explicit grants.
- Policies follow source lineage rather than trusting denormalized visibility on
  derived rows.
- The backend Supabase secret key and Storage client are absent from blind and
  evaluation service constructors.
- Audit reason codes are fixed enums; arbitrary text cannot become telemetry.

## 9. Migration and hosted rollout

The rollout is additive and fail-closed:

1. Enable `pgcrypto`, then add cutoff, lock, and audit schema/functions with
   forced RLS.
2. Replace existing processor/blind/evaluation policies with cutoff- and state-
   aware policies in the same migration boundary.
3. Revoke public access and grant only explicit runtime-role privileges.
4. Deploy to the non-production hosted project using a dry run first.
5. Backfill CEN22FA375's cutoff from the committed curated manifest through the
   guarded operation. Do not infer or hardcode it in SQL.
6. Verify blind reads remain unavailable until backfill and become limited to
   the three eligible sources afterward.
7. Run rollback-isolated lock/reveal attack fixtures; do not lock the persistent
   reference case.

No source, evidence, candidate, model-run, or historical audit row is deleted.

## 10. Failure behavior

- Missing/naive cutoff: reject before state transition.
- Conflicting repeated cutoff: reject and retain original value/state.
- Missing active evidence or version metadata: reject lock with no partial row.
- Duplicate lock: reject; never overwrite or silently return success.
- Blind blocked-document lookup: return not found/denied without metadata leak,
  append a denied audit event, and let the administrative auditor classify it.
- Audit insertion failure: fail the protected operation rather than perform an
  unlogged access.
- Hash mismatch during verification: fail closed and preserve the lock for
  forensic inspection.
- Hosted role/configuration mismatch: environment verifier fails before tests or
  workers run.

## 11. Test strategy

### 11.1 Pure unit tests

- Canonical snapshot validation and deterministic hashing fixtures.
- Leakage incident rules, including multiple simultaneous incidents.
- Capability allowlist and redaction guarantees.
- UTC cutoff validation and immutable transition contracts.

### 11.2 Repository integration tests

Rollback-isolated tests against hosted non-production Postgres verify:

- blind role cannot select `OFFICIAL_ANALYSIS` or `FINAL_FINDING`;
- blind role cannot select null/post-cutoff investigation rows;
- evaluation role cannot read official rows before lock;
- evaluation role can read them after lock;
- lock function computes stable hashes and transitions atomically;
- failed and duplicate locks leave state unchanged;
- lock rows reject update/delete;
- cutoff and all active processing state reject post-lock mutation;
- audit rows are append-only;
- `anon` and `authenticated` remain denied.

### 11.3 Attack fixtures

Create `tests/blindness/` fixtures that attempt:

- direct blocked-document ID lookup;
- misleading `AI_ALLOWED` final-report metadata and a stored visibility mismatch;
- post-cutoff evidence access;
- evaluation read before lock;
- forbidden web, Context.dev, NTSB, and raw-Storage capabilities;
- an unapproved network host;
- mutation after lock;
- lock replay with altered snapshot or versions.

Every attack must fail at a deterministic boundary. Tests make no live network
or model calls.

### 11.4 Verification gates

Per-task checks use focused pytest, Ruff, and mypy. Phase completion additionally
requires:

- full offline test suite;
- `tests/blindness/` and affected resume tests;
- hosted environment verifier;
- rollback-isolated hosted DB tests;
- explicit publishable-key denial probes;
- zero leakage incidents on the fixture audit;
- mandatory `explain-diff-html` comparison from the Phase 1 endpoint through the
  Phase 2 validation commit.

## 12. Acceptance criteria

Phase 2 is complete only when:

- every blind source read is enforced by role-scoped RLS with visibility and
  cutoff constraints;
- official rows are unreadable to blind sessions in every case state;
- evaluation cannot read official rows before an immutable lock and can afterward;
- locking atomically records assessment/evidence hashes, versions, timestamp,
  and state transition;
- processing and active-state mutation are denied after lock;
- blind runtime composition contains no forbidden acquisition/web/raw-storage
  capability;
- deterministic leakage fixtures report zero incidents for allowed behavior and
  stable incidents for every attack;
- no public role can read CaseZero tables or private objects;
- CEN22FA375 remains `BLIND` and unlocked in persistent development state;
- all measured results and unexecuted limitations are recorded without claiming
  an OS firewall, semantic correctness, or benchmark quality.

## 13. Deferred work

- Phase 3: investigation tools, claims/timeline/entity state, hypotheses,
  falsification, and revision.
- Phase 4: causal graph.
- Phase 5: typed `FinalAssessment` producer using this lock contract.
- Phase 6+: official-result grading, reveal workflow, calibrated judges, and
  multi-case benchmark reports.
- OS/container network sandboxing may be added later if capability absence and
  deterministic audit prove insufficient; Phase 2 makes no such claim.
