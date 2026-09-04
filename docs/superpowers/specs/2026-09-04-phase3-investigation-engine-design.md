# Phase 3 Investigation Engine Design

**Date:** 2026-09-04  
**Status:** Approved in conversation; pending implementation plan  
**Source of product truth:** `product-spec.md` §§12–17, 26–30, 38 and Phase 3
(local-only; never commit it)  
**Related decisions:** ADR 0002 stage machine, ADR 0005 falsification, ADR 0007
temporal blindness, ADR 0008 Postgres observability  
**Scope:** Canonical claims/entities/timeline, typed evidence tools, hybrid
retrieval baseline and embedding probe, competing hypotheses, dedicated
falsification, deterministic confidence revision, persisted jobs/events, crash
resume, and deterministic replay

## 1. Purpose

Phase 3 turns the active Phase 1 evidence graph into a real persisted
investigation. The engine must construct canonical state, generate genuinely
competing explanations, search separately for support and contradiction, execute
a dedicated critic/falsification stage, revise confidence with a deterministic
rule, and replay every accepted mutation without calling a model again.

The engine runs entirely inside the Phase 2 blind boundary. It cannot acquire
new sources, call Context.dev or the web, access raw Storage, read official
analysis/final findings, lock the persistent case, or reveal evaluation material.

Phase 3 stops before causal graph construction, missing-evidence second pass,
`FinalAssessment`, lock integration, and official-result grading. Those remain
Phases 4–6.

## 2. Accepted decisions

- Use normalized relational state plus an immutable event log; do not store one
  opaque investigation JSON snapshot.
- Use explicit deterministic stage runners with typed Pydantic AI calls; do not
  use a conversational agent loop or a second orchestration framework.
- Execute through persisted Postgres jobs claimed with `FOR UPDATE SKIP LOCKED`.
- Preserve Phase 2 dedicated `casezero_blind` role-scoped connections and
  capability absence.
- Promote Phase 1 candidates into canonical state; candidates do not silently
  become facts.
- Generate at least three materially distinct hypotheses when evidence permits.
- Search support and contradictions independently and persist retrieval intent.
- Use the POPPER-style sequential falsification structure from ADR 0005.
- Apply fixed strength-weighted confidence deltas; confidence remains explicitly
  “model confidence,” not a calibrated probability.
- Build metadata/entity/time/Postgres FTS retrieval first. Enable pgvector only
  if a blind relevance probe improves mean Recall@10 by at least 10 percentage
  points and improves at least two query categories.
- Probe only a Hyperfusion-compatible embedding route. Do not add another model
  provider for Phase 3.
- Run one bounded falsification/revision cycle in Phase 3.
- Keep CEN22FA375 `BLIND` and unlocked after the real-case exit run.

## 3. Package boundaries

### 3.1 `packages/investigation`

Owns canonical investigation models, stage/job contracts, typed tool functions,
hypothesis/critique/test/revision schemas, deterministic confidence rules,
stage runners, event models, and replay reducers. It may depend on
`casezero-evidence`, `casezero-retrieval`, `casezero-observability`, Pydantic,
and Pydantic AI.

It must not depend on NTSB acquisition, Context.dev, generic HTTP clients,
Supabase Storage clients, evaluation official-source access, or UI code.

### 3.2 `packages/retrieval`

Owns query contracts, metadata/entity/time filtering, Postgres FTS ranking,
optional embedding probe, stable score composition, and persisted retrieval
records. Retrieval returns active `EvidenceItem` IDs and component scores; it
never produces claims, hypotheses, or conclusions.

### 3.3 `packages/evidence`

Remains the owner of source/EvidenceItem contracts and Phase 2 blind access.
Phase 3 reaches evidence only through `BlindAccessService` and repository
interfaces whose SQL runs under `casezero_blind`.

### 3.4 `packages/observability`

Records model/tool/retrieval spans, prompt-template hashes, token usage, retries,
latency, and constrained network/access events. A stage cannot complete if its
required spans fail to persist.

### 3.5 `apps/api`

Composes settings, the dedicated blind connection, repositories, retriever,
model router, stage registry, worker, and CLI. It contains no investigation
domain rules.

## 4. Investigation lifecycle

### 4.1 Investigation record

Create `public.investigations`:

- `id uuid` primary key;
- `case_id uuid` referencing `cases`;
- `status`: `PENDING | RUNNING | SUCCEEDED | FAILED`;
- `current_stage` from the Phase 3 stage enum;
- `configuration_hash` SHA-256;
- non-empty `model_versions` and `prompt_versions` JSON objects;
- database `created_at`, nullable `started_at`, nullable `completed_at`;
- nullable `failure_code` from `PROVIDER_EXHAUSTED | SCHEMA_INVALID |
  REFERENCE_INVALID | DIVERSITY_INVALID | LEASE_EXPIRED | REPLAY_MISMATCH |
  PERSISTENCE_FAILED`; no free-form provider/exception message is stored.

`configuration_hash` uses `postgres-investigation-config-v1` over JSONB
containing the ordered stage list/version, stage-to-model mapping,
stage-to-prompt-template hash and git SHA mapping, retrieval rule version,
confidence rule version, job lease seconds (`1800`), maximum job attempts (`3`),
and cumulative model-request budget (`3`). It is serialized and hashed using the
Postgres JSONB-text rule established in Phase 2.

A partial unique index permits one `PENDING` or `RUNNING` investigation per case
and configuration hash. Completed investigations are retained for comparison.
No investigation write is permitted unless the parent case is `BLIND`.

### 4.2 Fixed Phase 3 stage order

1. `PROMOTE_TIMELINE`
2. `RESOLVE_ENTITIES`
3. `PROMOTE_CLAIMS`
4. `GENERATE_HYPOTHESES`
5. `SEARCH_SUPPORT`
6. `SEARCH_CONTRADICTIONS`
7. `DESIGN_FALSIFICATION_TESTS`
8. `EXECUTE_FALSIFICATION_TESTS`
9. `REVISE_CONFIDENCE`
10. `VERIFY_REPLAY`
11. `COMPLETE`

A new investigation starts `PENDING` with `current_stage=PROMOTE_TIMELINE` and
one pending promotion job. Claiming that job changes the investigation to
`RUNNING`. A successful stage transaction sets `current_stage` to the next enum
value and enqueues its work. A failed job leaves `current_stage` unchanged;
retryable work is reclaimed/requeued under the rules below, while a terminal
failure sets the investigation `FAILED`. Completing `VERIFY_REPLAY` enqueues one
deterministic `COMPLETE` job; that job appends the completion event and sets
`status=SUCCEEDED`, `current_stage=COMPLETE`, and `completed_at` atomically.

The stage order is data, not model discretion. A stage runner can enqueue only
the next declared stage. Phase 4 extends the enum/version with causal stages;
it does not reorder Phase 3 history.

### 4.3 Jobs and leases

Create `public.investigation_jobs`:

- investigation ID, stage, and deterministic `work_key`;
- status `PENDING | RUNNING | SUCCEEDED | FAILED`;
- input-state hash;
- attempt count, retryable flag, and nullable fixed `failure_code` from the
  investigation failure enum;
- worker ID, claimed timestamp, lease expiration;
- completed timestamp;
- cumulative reserved model-request count;
- unique `(investigation_id, stage, work_key, input_state_hash)`.

Create `investigation_job_attempts` with one immutable row per claim: job ID,
attempt number, worker ID, database start/end timestamps, outcome, fixed failure
code, and model-request count. Create `model_request_attempts` with job/attempt
ID, cumulative request ordinal, status, fixed failure code, and sanitized
validation issues as a closed array of `{path, code}`; it stores no rejected
value, prompt, response, or arbitrary message. Thus the job row is the current
checkpoint while same-input job/model attempt history and retry context are never
overwritten.

A security-definer claim function first changes expired `RUNNING` jobs back to
`PENDING`, closes their open attempt as `LEASE_EXPIRED`, then selects one
eligible job with `FOR UPDATE SKIP LOCKED`. It marks that job running, increments
attempt count, inserts the attempt row, and grants a fixed 30-minute lease using
database time. The worker polls this claim operation; no background reclaimer,
heartbeat, external queue, Redis, or orchestration server exists.

Before each provider request, `reserve_model_request(job_id, attempt_id)` locks
the job and increments its cumulative count only when it is below three. Phase 3
configures its Pydantic AI agent with internal retries disabled and runs the
three-attempt loop in the deterministic stage adapter: each iteration reserves
one request, performs one structured call, and, after a schema failure, includes
only the sanitized validation `{path, code}` entries in the next prompt. A
reclaimed attempt reconstructs this context from completed
`model_request_attempts` ordered by request ordinal; an open request is marked
`LEASE_EXPIRED` and contributes no invented validation issue. Existing Phase 1
adapters retain their current behavior. This makes the database budget match
actual Phase 3 requests across reclaimed attempts. Provider/schema exhaustion
and deterministic validation failure are terminal for unchanged input. Only
worker/process interruption and lease expiry are reclaimable, up to three job
attempts; a changed input-state hash creates a new job with a fresh request
budget.

### 4.4 Input-state hashing

Every job hash contains canonical ordered representations of:

- upstream canonical row IDs and stored JSON/hash values;
- active EvidenceItem IDs and selected semantic model-run IDs;
- stage implementation version;
- prompt-template hash;
- model name and structured parameters;
- retrieval configuration/rule version.

Job input hashing uses `postgres-investigation-input-v1`: build one JSONB object
with the fields above, sort every row/ID array before construction, serialize
with PostgreSQL `jsonb::text`, encode as UTF-8, and apply SHA-256 with pgcrypto.
JSON arrays remain order-sensitive; object key order and insignificant input
whitespace do not. The PostgreSQL major version is part of system configuration,
and a serialization change requires a new algorithm identifier. A matching
successful job is reused. A changed input creates a distinct job; old attempts
remain audit history.

## 5. Canonical investigation state

All canonical tables include `investigation_id`, `case_id`, creating model run
where applicable, and database timestamps. Foreign keys and repository
validation prevent references outside the same investigation/case. Phase 1
candidate confidence remains a float input for compatibility; promotion converts
it with `Decimal(str(value)).quantize(Decimal("0.0001"))`. Canonical confidence
columns use PostgreSQL `numeric(5,4)` with `[0,1]` checks, and Phase 3 Pydantic
models use bounded `Decimal` fields.

### 5.1 Claims

`public.claims` stores:

- text;
- status `OBSERVED | INFERRED | DISPUTED | UNKNOWN`;
- confidence in `[0, 1]`;
- source candidate IDs;
- creating model-run ID.

Separate `claim_evidence_links` rows use polarity `SUPPORTING | CONTRADICTING`
and reference active EvidenceItems. A claim must have at least one supporting or
contradicting evidence link; UNKNOWN may use contradicting/missing context but
cannot be ungrounded prose.

### 5.2 Entities

`public.investigation_entities` stores fixed type, canonical name, aliases,
source candidate IDs, and creating model run. `entity_evidence_links` references
active EvidenceItems. Alias normalization is casefolded and whitespace-normalized
for duplicate detection, while display values preserve source spelling.

### 5.3 Timeline

`public.timeline_events` stores optional aware UTC `occurred_at`, precision
`EXACT | APPROXIMATE | RELATIVE | UNKNOWN`, description, confidence, source
candidate IDs, and creating model run. `timeline_evidence_links` references
active EvidenceItems. Deterministically parseable times are normalized before a
model sees them; ambiguous narrative relations remain `INFERRED`.

### 5.4 Candidate promotion

Phase 1 candidate rows are immutable inputs. Promotion stages may merge genuine
duplicates, retain conflicting candidates, normalize aliases/times, and reject
invented references. Each canonical row records every contributing candidate ID
and exact active EvidenceItem links. A candidate never changes status merely
because another candidate is more confident.

Model output does not write canonical tables. The runner validates a complete
typed stage result, then commits canonical rows, links, event rows, job success,
and the next job in one transaction.

## 6. Hypothesis state

### 6.1 Hypotheses

`public.hypotheses` stores:

- title and description;
- status `ACTIVE | WEAKENED | REJECTED | LEADING`;
- initial and current model confidence;
- distinguishing prediction;
- weakening evidence description;
- creating model-run ID.

`hypothesis_claim_links` references canonical claims with polarity
`SUPPORTING | CONTRADICTING`. `unresolved_questions` stores each question as a
separate row linked to its hypothesis and creation/revision event.

Initial model confidence is constrained to `0.05–0.85`. It is not interpreted as
calibrated probability.

### 6.2 Competition and diversity

When evidence permits, generation must produce at least three hypotheses. The
runner rejects invented claim IDs and computes pairwise normalized token Jaccard
over concatenated title/description. Overlap above `0.75` is a near duplicate
and triggers a bounded regeneration attempt. The model cannot satisfy the count
by renaming the same explanation.

After three schema/diversity attempts, the stage fails explicitly. It does not
pad output with generic alternatives. The real CEN22FA375 Phase 3 gate requires
three accepted hypotheses; a future sparse case may record explicit
`INSUFFICIENT_EVIDENCE` when the Phase 5 assessment contract exists.

## 7. Typed tool layer

Phase 3 tools wrap blind access, retrieval, and investigation repositories:

- `search_evidence`
- `get_evidence`
- `get_source_document`
- `get_timeline`
- `find_entity`
- `get_claim`
- `list_claims`
- `create_hypothesis`
- `update_hypothesis`
- `find_supporting_evidence`
- `find_contradicting_evidence`
- `get_unresolved_questions`

Causal graph tools remain Phase 4.

Tools use strict Pydantic inputs/outputs and case/investigation IDs supplied by
the runner, not model-selectable cross-case IDs. Mutation tools return proposed
typed state to the runner; they do not commit independently. Phase 3 extends the
closed access capability enum/check constraint with `INVESTIGATION_TOOL` and
`RETRIEVAL`; this does not alter the forbidden acquisition/web/raw-storage set.
Every call writes a `tool` or `retrieval` span and a matching access event before
stage completion. `get_source_document` is the tool name; it maps directly to
Phase 2 `BlindAccessService.get_source_metadata`. An `INVESTIGATION_TOOL` event
records the tool invocation without arguments. A `RETRIEVAL` event is emitted
for each distinct returned source-document ID, while query text and ordered
EvidenceItem IDs remain in the constrained retrieval record/span. The leakage
auditor treats `RETRIEVAL` and `EVIDENCE_READ` as document-targeting capabilities
for visibility/cutoff checks.

No tool constructor or registry contains NTSB, Context.dev, arbitrary HTTP, web
search, raw SQL, raw Storage, Supabase secret key, evaluation repository, or an
unrestricted `EvidenceRepository`.

## 8. Retrieval baseline

### 8.1 Query contract

`EvidenceSearchQuery` includes:

- investigation/case IDs injected by the runner;
- intent `SUPPORT | CONTRADICT | NEUTRAL`;
- query text;
- optional EvidenceType/document-type filters;
- optional entity IDs;
- optional UTC interval or relative-time token;
- limit constrained to `1–50`;
- retrieval configuration version.

`EvidenceSearchResult` contains EvidenceItem ID, rank, total score, FTS score,
entity score, time score, type score, and matched filter reasons. It does not
contain source bytes or official metadata.

### 8.2 FTS and structured scoring

Create a Postgres full-text index over active EvidenceItem observation text.
Candidate rows, official source content, and superseded EvidenceItems do not
enter the active index query.

Each component is normalized to `[0, 1]` and combined:

```text
total = 0.55 * fts
      + 0.20 * entity_match
      + 0.15 * time_match
      + 0.10 * type_match
```

Absent optional filters contribute zero rather than redistributing weight.
Results sort by total descending, component scores descending in the displayed
order, then EvidenceItem UUID ascending. Query intent and ordered results are
persisted; support and contradiction searches cannot share a cache key.

For the D2 probe only, the vector candidate uses a separately versioned score:

```text
hybrid = 0.35 * vector_similarity
       + 0.30 * fts
       + 0.15 * entity_match
       + 0.12 * time_match
       + 0.08 * type_match
```

The baseline and vector candidate each produce independent ranked lists from the
same query/filter inputs; D2 compares their Recall@10 rather than mixing vector
scores into the baseline.

### 8.3 Retrieval records

`retrieval_queries` stores query hash, intent, typed filters, configuration
version, creating job/model run, and timestamp. `retrieval_results` stores
ordered EvidenceItem IDs and component scores. A query/result set is immutable
and forms part of falsification provenance.

### 8.4 Embedding decision D2

Build a blind relevance set at
`benchmark/retrieval/cen22fa375-v1.json` with at least 12 queries across four
categories: component/entity aliases, temporal sequence, technical symptom, and
contradiction-oriented search. Each row records query text, typed filters,
category, expected active EvidenceItem IDs, short relevance rationale, active
evidence-set hash, human reviewer identifier, and review timestamp. The file
contains no source payload. Labels are created and reviewed through the blind
Evidence Explorer/SQL verification workflow using investigation evidence only;
o model generates labels and official findings cannot define queries or
relevance.

Measure metadata+FTS Recall@10. Probe an available Hyperfusion-compatible
embedding route with dimension at most 2000. Enable pgvector only when hybrid
retrieval:

- improves mean Recall@10 by at least 0.10 absolute;
- improves at least two query categories;
- does not regress any category by more than 0.05;
- preserves active-evidence IDs, role scoping, and access/model audit.

If the route is unavailable or the threshold fails, record D2 as “vector not
justified” and ship metadata+FTS. No alternative provider is added.

## 9. Support and contradiction search

Each hypothesis creates separate persisted SUPPORT and CONTRADICT queries. The
query generator may use the hypothesis description, distinguishing prediction,
weakening evidence description, linked claims, and unresolved questions. It
cannot read official material.

The runner validates that contradiction intent is present in the retrieval
record and that contradiction results are not copied from a support query/cache
key. No contradiction result is recorded as support without an explicit,
separate relation and rationale. An empty contradiction retrieval is not itself
a falsification-test result. It adds an unresolved question and causes the
critic to propose an `EVIDENCE_PRESENCE` test. If that test names evidence
expected under the hypothesis and none exists, the deterministic executor
returns `EXPECTED_EVIDENCE_MISSING`; otherwise it returns `INCONCLUSIVE`.
Neither empty retrieval nor INCONCLUSIVE increases confidence.

## 10. Dedicated critic and falsification tests

### 10.1 Critique schema

`HypothesisCritique` contains:

- hypothesis ID;
- strongest contradiction Claim/Evidence IDs where present;
- missing expected evidence descriptions;
- optional alternative explanation;
- critique confidence;
- proposed falsification tests.

The schema has no supporting-evidence field. Unknown fields are forbidden, so a
critic attempt to restate support fails validation. Proposed tests are typed
`HypothesisTestDraft` values nested only in the model response; after whole-batch
validation the runner persists each as a normalized `hypothesis_tests` row with
a foreign key to the critique.

### 10.2 Test types

`public.hypothesis_tests` stores one of:

- `EVIDENCE_PRESENCE`
- `TEMPORAL_CONSISTENCY`
- `CLAIM_CONTRADICTION`
- `SEMANTIC_COMPARISON`

Each test records hypothesis, typed parameters, expected observation, strength
`LOW | MEDIUM | HIGH`, execution kind `DETERMINISTIC | AI`, status, result,
ordered EvidenceItem/Claim IDs, creating critique, and model run only for AI
execution.

Evidence-presence, temporal-consistency, and claim-contradiction tests execute as
code over active persisted state. Semantic comparison executes as a separate
strict model call and its result is labeled `INFERRED`. ADR 0005's
`CAUSAL_CHAIN_VALIDATION` test is intentionally deferred to Phase 4 because no
causal graph exists in Phase 3; Phase 4 extends the closed test-type enum rather
than simulating a graph test now.

### 10.3 Outcomes

- `CONTRADICTED`
- `SURVIVED`
- `EXPECTED_EVIDENCE_MISSING`
- `INCONCLUSIVE`

A deterministic executor cannot convert missing data into SURVIVED. Execution
errors remain FAILED jobs/tests and do not affect confidence.

## 11. Confidence revision

### 11.1 Rule `weighted-delta-v1`

| Outcome | LOW | MEDIUM | HIGH |
|---|---:|---:|---:|
| `CONTRADICTED` | -0.08 | -0.18 | -0.35 |
| `EXPECTED_EVIDENCE_MISSING` | -0.03 | -0.08 | -0.15 |
| `SURVIVED` | +0.02 | +0.04 | +0.06 |
| `INCONCLUSIVE` | 0.00 | 0.00 | 0.00 |

For tests ordered by UUID:

```text
delta = sum(weight(outcome, strength))
after = clamp(0.05, 0.95, before + delta)
```

Use `Decimal` quantized to `0.0001` for calculation and persistence; binary
floating-point cannot determine state. The database stores numeric values with
an explicit range check.

### 11.2 Revision rows

`confidence_revisions` stores hypothesis ID, before, each test ID/delta, summed
delta, after, rule version, deterministic rationale, and timestamp. The
rationale is generated from ordered stable outcome/strength codes and test IDs;
a model does not invent the numeric explanation.

INCONCLUSIVE and EXPECTED_EVIDENCE_MISSING tests add persisted unresolved
questions where the critique supplied one.

### 11.3 Status assignment

After all revisions, evaluate these branches in order:

- first, confidence `<= 0.15` becomes `REJECTED`;
- otherwise, confidence `< 0.35` becomes `WEAKENED`;
- otherwise remains `ACTIVE` initially;
- the unique highest confidence at `>= 0.50` becomes `LEADING`;
- a tie for highest leaves every tied hypothesis `ACTIVE`.

Status updates and events commit atomically with revision rows. Phase 3 runs one
complete falsification/revision cycle.

## 12. Investigation event log

### 12.1 Event shape

Create append-only `investigation_events` with:

- investigation and case IDs;
- monotonically increasing sequence;
- event type and target type/ID;
- versioned typed payload JSONB;
- optional model-run ID;
- previous event hash;
- event hash and fixed `hash_algorithm='postgres-investigation-event-v1'`;
- database timestamp;
- unique `(investigation_id, sequence)`.

Event types cover investigation/stage lifecycle, canonical
claim/entity/timeline creation, hypothesis/link/question creation, retrieval
completion, critique/test creation, test result, confidence revision, hypothesis
status change, and investigation completion. Canonical claims/entities/timeline
are immutable after promotion in Phase 3, so they have CREATE but no UPDATE
event. Later phases must add a versioned replacement event rather than mutate
history.

Each payload has `schema_version` plus one closed shape:

- lifecycle/job: stage, prior/new status, input-state hash, and job/attempt IDs;
- canonical CREATE: the full canonical row plus ordered candidate/evidence link
  rows;
- hypothesis CREATE: full hypothesis, ordered claim links, and unresolved
  questions;
- retrieval COMPLETE: full query contract and every ordered result with component
  scores;
- critique/test CREATE: full critique and normalized test rows;
- test RESULT: before status, outcome, execution kind, and ordered cited IDs;
- confidence REVISION: full revision row, ordered test deltas, prior/new
  hypothesis confidence and status;
- investigation COMPLETE: final stage, canonical projection hash, and event-head
  hash.

The Python discriminated union validates event type/version against exactly one
payload model before SQL append. Unknown or extra fields fail validation, giving
the pure reducer all state it needs without SQL lookups. Array order is
significant only for ranked retrieval results and ordered per-test confidence
deltas. Every set-like candidate, evidence, claim, alias, question, and link
array is sorted by its normalized value or UUID before event construction and
hashing.

### 12.2 Atomic append

A security-definer append function locks the investigation's event head,
allocates the next sequence, validates required identifiers, and builds
`{"previous_hash", "investigation_id", "case_id", "sequence", "event_type",
"target_type", "target_id", "payload", "model_run_id"}`. The
`postgres-investigation-event-v1` hash is SHA-256 of that object's PostgreSQL
`jsonb::text` UTF-8 bytes. The function inserts one row with the algorithm ID.
Runtime roles receive no direct event update/delete access.

Canonical state mutation, event append, job success, and next-job creation occur
inside one transaction. An event cannot describe a mutation that rolled back,
and a committed mutation cannot lack its event.

## 13. Replay

A pure reducer folds typed events from sequence 1 over an empty
`InvestigationProjection`. It performs no SQL, current-time read, retrieval, or
model call.

The projection contains canonical claims/entities/timeline, hypotheses and
links, unresolved questions, critiques/tests/results, confidence revisions,
full retrieval queries and ranked results, statuses, and current stage. Unknown
event type/version, sequence gap, previous-hash mismatch, duplicate creation, or
update-before-create fails replay.

`VERIFY_REPLAY` loads events and normalized canonical relational state under the
blind role. It compares retrieval queries/results as first-class projection
state. Database rows and set-like links normalize by primary UUID/value;
retrieval result rank and confidence-delta order remain significant. Database
timestamps are excluded from projection equality but remain in event-chain
verification. Any mismatch fails the job and prevents COMPLETE.

## 14. Observability

Create `public.investigation_spans` for `agent`, `tool`, and `retrieval` spans.
Columns are `id`, `investigation_id`, `job_id`, nullable `parent_span_id`, kind
`AGENT | TOOL | RETRIEVAL`, operation, status `RUNNING | SUCCEEDED | FAILED`,
fixed `operation_version`, database start/end timestamps, and duration
milliseconds. There is no arbitrary attributes JSONB field.

Existing `model_runs` remains the model-span table. Add schema-nullable
`investigation_id`, `job_id`, `parent_span_id`, and `prompt_git_sha` fields for
compatibility with Phase 1 history. A database check requires either all four to
be null for legacy rows or all four to be non-null, with a non-empty git SHA, for
Phase 3 rows. The Phase 3 writer always supplies all four. `ReasoningRequest`,
`ModelRunRecorder`, and `ModelRouter` carry those fields from the claimed job and
agent span. Grant `casezero_blind` only the state-scoped select/insert permissions
needed to record Phase 3 model calls.
`ModelAuditContext` uses stage/actor `BLIND` and the approved Hyperfusion
hostname.

Prompt-template hash, model name, token usage, retries, schema failures, and
latency remain in `model_runs`. Tool/retrieval versions remain in
`investigation_spans` and their domain records. Prompts, source text, model
output prose, credentials, and URL query strings are not persisted in default
runs.

Model-run and access-attempt records persist independently so a failed provider
call or rolled-back stage cannot erase the fact that an external attempt
occurred. After a successful typed result, canonical mutation, investigation
span/event insertion, and job completion share one transaction. Stage completion
requires all records; a persistence failure rolls back canonical state while
retaining prior attempt audit.

## 15. Security and RLS

All new tables enable and force RLS. Policies root through
`investigations.case_id` to a case visible under `casezero_blind`.

- `casezero_blind` can select Phase 3 rows for parent cases in `BLIND` or
  `LOCKED`, and can insert/update only while the case is `BLIND`.
- `casezero_eval` receives select-only access only when the parent case has an
  immutable investigation lock; this supports later evaluation without a schema
  rewrite.
- Evidence/claim links validate that referenced EvidenceItems are active and
  blind-eligible for the same case.
- Processor, acquisition, evaluation, anon, authenticated, and PUBLIC roles have
  no Phase 3 mutation capability.
- The Phase 2 post-lock guard is extended to every Phase 3 state/job/event table.
- Dedicated role-scoped connections remain `autocommit=True` between operations;
  each claim or stage commit explicitly opens `connection.transaction()` so
  canonical rows, event/span rows, job success, and next-job enqueue are atomic.
- Job claim/complete/event functions verify `current_setting('role')` is
  `casezero_blind` despite using `SECURITY DEFINER`.
- Tools cannot select arbitrary case IDs; the runner injects case/investigation
  context.

## 16. Failure and resume behavior

- Invalid model IDs/references: reject the entire typed result; retry only within
  the three-attempt model/schema budget.
- Near-duplicate/fewer-than-three hypotheses: bounded regeneration, then explicit
  non-retryable stage failure for unchanged input.
- Provider timeout/failure: safe job failure with lease expiry/retry; previously
  completed sibling work items remain committed.
- Retrieval empty result: valid persisted result plus unresolved question where
  required.
- Deterministic test execution error: test/job FAILED, no confidence delta.
- Event/span/audit persistence failure: rollback canonical mutation and job
  completion.
- Worker cancellation: transaction rollback; expired lease later reclaims the
  incomplete job.
- Changed upstream state/prompt/model/retrieval rule: new input hash and job;
  prior output remains history.
- Replay mismatch or hash-chain failure: investigation cannot complete.
- Case no longer BLIND: job claim and writes fail closed.

## 17. Testing strategy

### 17.1 Pure tests

- strict canonical models and cross-ID reference validation;
- candidate promotion merge/contradiction behavior;
- normalized hypothesis token overlap and `0.75` threshold;
- all 12 outcome/strength confidence deltas, clamp edges, Decimal quantization,
  tie/no-leading behavior;
- metadata/entity/time/FTS score composition and UUID tie-breaking;
- SUPPORT versus CONTRADICT cache/hash separation;
- deterministic input-state and event hashing fixtures;
- every event reducer plus sequence/hash/type failure;
- tool registry capability absence and payload-free spans.

### 17.2 Hosted rollback tests

- investigation creation only for BLIND case;
- one active run per case/configuration;
- concurrent `SKIP LOCKED` job claims do not duplicate work;
- expired lease reclaim and three-attempt cap;
- successful job reuse and changed-input new job;
- canonical rows/links reject inactive, official, post-cutoff, cross-case IDs;
- mutation plus event plus next job is atomic;
- event update/delete and sequence/hash forgery fail;
- post-lock Phase 3 writes fail;
- replay projection equals canonical relational projection.

### 17.3 Attack tests

- model invents EvidenceItem, candidate, Claim, Hypothesis, or test IDs;
- model emits unknown critique support field;
- hypothesis variants exceed overlap threshold;
- contradiction stage tries to reuse SUPPORT query intent/hash;
- confidence does not equal `weighted-delta-v1` output;
- direct SQL/job/tool attempts target official or post-cutoff sources;
- tool registry receives web, Context.dev, NTSB, raw Storage, raw SQL, evaluation,
  or arbitrary HTTP capability;
- event sequence gap, previous-hash alteration, and payload mutation;
- worker tries to complete a job after lease loss;
- replay output differs from canonical rows.

Every default attack is offline. SQL/RLS/job-concurrency attacks use the
non-production hosted rollback gate when Docker is unavailable.

## 18. Real-case Phase 3 exit

Run CEN22FA375 only with explicit `CASEZERO_LIVE=1` through Phase 3 under a
dedicated `casezero_blind` connection. It must produce:

- canonical claims, entities, and preliminary timeline;
- at least three accepted materially distinct hypotheses;
- persisted SUPPORT and CONTRADICT retrievals per hypothesis;
- one critique and falsification-test set per hypothesis;
- deterministic test outcomes and explicit confidence revisions;
- complete required model/tool/retrieval/access records;
- successful crash-resume and immediate no-change reuse checks;
- event replay exactly equal to canonical persisted state;
- deterministic leakage report with zero incidents;
- case state still `BLIND` and investigation lock count still zero.

Record human review at `docs/development/phase-3-grounding-review.md`: reviewer,
date, active evidence/investigation hashes, at least five sampled canonical Claim
IDs, every Hypothesis ID, citation-grounding verdicts, hypothesis-distinctness verdicts, contradiction
verdicts, and concise rationale. Any unsupported sampled claim, ungrounded
hypothesis, superficial duplicate, or fabricated contradiction fails the project
Phase 3 gate. The completed investigation remains immutable for audit; correction
requires a prompt/code/retrieval version change, a new configuration hash, and a
new investigation. That run begins at `PROMOTE_TIMELINE`, reuses matching
upstream job outputs, and re-executes from the earliest changed input. The failed review
and replacement investigation IDs are both recorded. Report exact findings and
disagreements; do not claim causal accuracy.

## 19. Validation and completion gate

Phase completion requires:

- Ruff, mypy, full offline pytest;
- `tests/blindness/` and new investigation/resume/replay attacks;
- all hosted rollback DB and pgTAP tests;
- public REST/RPC/Storage denial probes;
- D2 retrieval report and explicit vector decision;
- measured real-case stage/job/state/event/span counts;
- no-change run with zero new model/tool/retrieval work;
- zero leakage incidents;
- persistent CEN22FA375 remains BLIND and unlocked;
- `docs/development/phase-3-validation.md` with limitations;
- mandatory `explain-diff-html` from the Phase 2 endpoint through the Phase 3
  validation commit.

## 20. Explicit limitations and deferred work

- Phase 3 model confidence is not calibrated probability.
- One falsification/revision cycle does not prove exhaustive investigation.
- Retrieval relevance labels are blind human judgements over investigation
  evidence, not official-result-derived ground truth.
- pgvector is not guaranteed; it is contingent on D2 measurements.
- Capability absence plus audit remains distinct from an OS firewall.
- Causal relationships, missing-evidence second pass, FinalAssessment, lock use,
  official reveal, benchmark judge metrics, and UI remain later phases.
- Phase 1 OCR/image/audio modality execution remains triggered only by a
  rights-approved benchmark artifact that requires it before the five-case gate.
