# Phase 3 Investigation Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Repository override:** `AGENTS.md` prohibits those execution skills. Execute
> directly, test-first, with one verified commit per task.

**Goal:** Build a resumable, replayable blind investigation engine that promotes
canonical state, retrieves support and contradictions, generates competing
hypotheses, executes dedicated falsification, and revises confidence through a
fixed deterministic rule.

**Architecture:** New `casezero-investigation` and `casezero-retrieval` packages
own typed domain/stage and retrieval logic. Postgres owns RLS-scoped canonical
state, jobs, attempts, event hash chains, leases, and atomic stage commits;
Pydantic AI is used only inside deterministic stage runners. Event replay must
equal normalized relational state, and the real CEN22FA375 run remains blind and
unlocked.

**Tech Stack:** Python 3.12, strict Pydantic 2, Pydantic AI 2.30.0, psycopg 3,
PostgreSQL 17 FTS/RLS/pgcrypto, optional pgvector only after D2, Hyperfusion,
pytest, pgTAP, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-04-phase3-investigation-engine-design.md`

## Global Constraints

- Default tests make no network, model, NTSB, Context.dev, or hosted call.
- Live CEN execution requires `CASEZERO_LIVE=1`; hosted tests require the existing
  non-production DB opt-in.
- All Phase 3 state writes require `casezero_blind` and parent case `BLIND`.
- Tools receive injected case/investigation context and never arbitrary SQL,
  HTTP, NTSB, Context.dev, raw Storage, evaluation, or cross-case capability.
- Canonical confidence uses `Decimal` and PostgreSQL `numeric(5,4)`; Phase 1 float
  candidate input converts with `Decimal(str(value)).quantize(Decimal("0.0001"))`.
- At least three accepted hypotheses are required for CEN22FA375; overlap above
  `0.75` is rejected.
- `weighted-delta-v1` is the only confidence mutation rule.
- Job and event hashes use the exact versioned PostgreSQL JSONB-text algorithms
  in the spec.
- A job has at most three total reserved provider requests across reclaimed
  attempts. Phase 3 Pydantic AI internal retries are disabled.
- Canonical mutation, investigation event/span, job completion, and next-job
  enqueue commit atomically.
- Existing model/access attempt audit survives stage rollback.
- CEN22FA375 remains `BLIND` with zero investigation locks.
- One task, one focused RED→GREEN loop, one reviewed commit. Never stage local
  `product-spec.md`, `task.md`, `.env`, downloaded source data, or `/tmp` HTML.

---

### Task 1: Investigation Canonical Models

**Files:**
- Create: `packages/investigation/pyproject.toml`
- Create: `packages/investigation/src/casezero_investigation/__init__.py`
- Create: `packages/investigation/src/casezero_investigation/models.py`
- Create: `packages/investigation/src/casezero_investigation/py.typed`
- Create: `packages/investigation/tests/test_models.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces enums `InvestigationStatus`, `InvestigationStage`, `ClaimStatus`,
  `HypothesisStatus`, `LinkPolarity`, `TimePrecision`.
- Produces strict models `Investigation`, `Claim`, `InvestigationEntity`,
  `TimelineEvent`, `Hypothesis`, `UnresolvedQuestion`.
- Canonical confidence is `Decimal` quantized to four places and bounded `[0,1]`;
  initial hypothesis confidence is additionally bounded `[0.05,0.85]`.

- [ ] **Step 1: Write failing strict-model tests**

```python
from decimal import Decimal
from uuid import uuid4

import pytest
from casezero_investigation import Hypothesis, HypothesisStatus


def test_hypothesis_uses_quantized_model_confidence() -> None:
    hypothesis = Hypothesis(
        investigation_id=uuid4(),
        case_id=uuid4(),
        title="Fuel interruption",
        description="Fuel flow may have been interrupted.",
        initial_confidence=Decimal("0.3800"),
        current_confidence=Decimal("0.3800"),
        status=HypothesisStatus.ACTIVE,
        distinguishing_prediction="Fuel-system abnormalities should be present.",
        weakening_evidence="Normal uninterrupted fuel flow would weaken it.",
        model_run_id=uuid4(),
    )
    assert hypothesis.current_confidence == Decimal("0.3800")


def test_hypothesis_rejects_unquantized_confidence() -> None:
    with pytest.raises(ValueError, match="four decimal"):
        Hypothesis.model_validate(valid_hypothesis() | {"current_confidence": "0.33333"})
```

Also test aware UTC timeline values, non-empty source candidate/evidence links,
and strict extra-field rejection.

- [ ] **Step 2: Run RED**

```bash
uv run pytest packages/investigation/tests/test_models.py -v
```

Expected: package import failure.

- [ ] **Step 3: Register package and implement models**

Add `casezero-investigation` to root dependencies, workspace members/sources,
and mypy packages. Depend only on evidence, Pydantic, and standard library in
this task. Use one shared four-place Decimal validator.

- [ ] **Step 4: Verify GREEN**

```bash
uv sync
uv run pytest packages/investigation/tests/test_models.py -v
uv run ruff check packages/investigation pyproject.toml
uv run mypy
```

- [ ] **Step 5: Commit**

```bash
git add packages/investigation pyproject.toml uv.lock
git commit -m "feat(investigation): add canonical investigation models"
```

---

### Task 2: Hypothesis, Critique, Test, and Revision Contracts

**Files:**
- Create: `packages/investigation/src/casezero_investigation/hypotheses.py`
- Create: `packages/investigation/tests/test_hypotheses.py`
- Modify: `packages/investigation/src/casezero_investigation/__init__.py`

**Interfaces:**
- Produces `HypothesisCritique`, `HypothesisTestDraft`, `HypothesisTest`,
  `ConfidenceRevision`, `TestType`, `TestStrength`, `TestOutcome`,
  `ExecutionKind`.
- Critique schema has no supporting-evidence field.
- Test drafts use only allowlisted claim/evidence IDs supplied to the critic.

- [ ] **Step 1: Write failing tests**

```python
def test_critique_forbids_support_field() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        HypothesisCritique.model_validate(
            {
                "hypothesis_id": str(uuid4()),
                "missing_evidence": [],
                "critique_confidence": "0.7000",
                "proposed_tests": [],
                "supporting_evidence": [str(uuid4())],
            }
        )
```

Test all closed enum values, UUID references, Decimal bounds, and result rows
requiring deterministic versus model provenance correctly.

- [ ] **Step 2: Run RED**

```bash
uv run pytest packages/investigation/tests/test_hypotheses.py -v
```

- [ ] **Step 3: Implement contracts**

Use distinct draft and persisted models. `HypothesisTestDraft` nests in critic
output; `HypothesisTest` carries critique/job/model IDs and execution result.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest packages/investigation/tests/test_hypotheses.py -v
uv run ruff check packages/investigation
uv run mypy
git add packages/investigation
git commit -m "feat(investigation): add falsification state contracts"
```

---

### Task 3: Job, Attempt, and Stage Hash Contracts

**Files:**
- Create: `packages/investigation/src/casezero_investigation/jobs.py`
- Create: `packages/investigation/tests/test_jobs.py`
- Modify: `packages/investigation/src/casezero_investigation/__init__.py`

**Interfaces:**
- Produces `InvestigationJob`, `InvestigationJobAttempt`,
  `ModelRequestAttempt`, `JobStatus`, `AttemptOutcome`, `FailureCode`.
- `InvestigationConfig.canonical_payload()` contains exact spec §4.1 values.
- `job_input_payload(...)` returns sorted JSON-compatible state for DB hashing.

- [ ] **Step 1: Write RED tests**

Test initial stage, fixed lease `1800`, max attempts/requests `3`, all-or-none
model request context, closed sanitized validation issues `{path, code}`, and
stable payload ordering independent of input set order.

```python
def test_job_input_payload_sorts_set_like_ids() -> None:
    payload = job_input_payload(
        stage=InvestigationStage.GENERATE_HYPOTHESES,
        active_evidence_ids=(UUID(int=2), UUID(int=1)),
        upstream_record_ids=(UUID(int=4), UUID(int=3)),
        stage_version="1.0.0",
        prompt_hash="a" * 64,
        prompt_git_sha="deadbeef",
        model="qwen/qwen3-32b",
        retrieval_version="fts-v1",
        confidence_rule="weighted-delta-v1",
    )
    assert payload["active_evidence_ids"] == [str(UUID(int=1)), str(UUID(int=2))]
```

- [ ] **Step 2: Run RED, implement, verify**

```bash
uv run pytest packages/investigation/tests/test_jobs.py -v
uv run ruff check packages/investigation
uv run mypy
```

- [ ] **Step 3: Commit**

```bash
git add packages/investigation
git commit -m "feat(investigation): add durable job and hash contracts"
```

---

### Task 4: Canonical State Database Schema and RLS

**Files:**
- Create: `supabase/migrations/0018_investigation_state.sql`
- Create: `supabase/tests/0006_investigation_state_rls.sql`
- Modify: `apps/api/src/casezero_api/hosted_verify.py`
- Modify: `scripts/verify_hosted_supabase.py`
- Modify: `tests/test_verify_hosted_supabase.py`

**Interfaces:**
- Creates `investigations`, `claims`, `claim_evidence_links`,
  `investigation_entities`, `entity_evidence_links`, `timeline_events`,
  `timeline_evidence_links`, `hypotheses`, `hypothesis_claim_links`,
  `unresolved_questions`, `hypothesis_critiques`, `hypothesis_tests`, and
  `confidence_revisions`.
- Confidence columns are `numeric(5,4)` with explicit bounds.
- Forced RLS allows blind writes only while parent case is `BLIND`, blind reads
  for `BLIND|LOCKED`, and evaluation reads only after an investigation lock.
- Extends Phase 2 post-lock triggers to every new table.

- [ ] **Step 1: Write failing hosted-state and pgTAP tests**

Require every table and forced RLS. Fixture attacks must reject cross-case and
post-lock canonical links and prove public roles have no privileges.

- [ ] **Step 2: Run RED synthetic verifier**

```bash
uv run pytest tests/test_verify_hosted_supabase.py -v
```

- [ ] **Step 3: Implement migration**

Use normalized link tables with composite primary keys. Add no generic metadata
JSON to canonical rows. Critique/test typed parameters may use strict JSONB with
a `schema_version` check because test-type-specific fields differ.

- [ ] **Step 4: Verify offline and dry-run**

```bash
uv run pytest tests/test_verify_hosted_supabase.py packages/investigation/tests -v
uv run ruff check .
uv run mypy
set -a; source .env; set +a; supabase db push --dry-run
```

Expected migration list contains only `0018_investigation_state.sql`.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/0018_investigation_state.sql supabase/tests/0006_investigation_state_rls.sql apps/api/src/casezero_api/hosted_verify.py scripts/verify_hosted_supabase.py tests/test_verify_hosted_supabase.py
git commit -m "feat(db): add canonical investigation state"
```

---

### Task 5: Event Contracts and Pure Replay

**Files:**
- Create: `packages/investigation/src/casezero_investigation/events.py`
- Create: `packages/investigation/src/casezero_investigation/replay.py`
- Create: `packages/investigation/tests/test_events.py`
- Create: `packages/investigation/tests/test_replay.py`
- Modify: `packages/investigation/src/casezero_investigation/__init__.py`

**Interfaces:**
- Closed event payload models for lifecycle/job, canonical creation, hypothesis,
  retrieval completion, critique/test creation, test result, confidence revision,
  status change, and investigation completion.
- `InvestigationEvent` carries sequence, previous/event hashes, algorithm, and
  typed payload.
- `replay(events: tuple[InvestigationEvent, ...]) -> InvestigationProjection`.

- [ ] **Step 1: Write failing event/replay tests**

Build a short complete event chain and assert reconstructed canonical/retrieval
state. Test sequence gap, previous-hash mismatch, unknown schema version,
duplicate create, update-before-create, set-array ordering, and ranked retrieval
ordering.

- [ ] **Step 2: Run RED**

```bash
uv run pytest packages/investigation/tests/test_events.py packages/investigation/tests/test_replay.py -v
```

- [ ] **Step 3: Implement typed discriminated payload union and reducer**

Reducer performs no SQL, model, network, or clock read. Set-like arrays normalize
by UUID/value; retrieval rank and per-test delta order remain significant.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest packages/investigation/tests/test_events.py packages/investigation/tests/test_replay.py -v
uv run ruff check packages/investigation
uv run mypy
git add packages/investigation
git commit -m "feat(investigation): add immutable events and replay fold"
```

---

### Task 6: Job and Event Database Functions

**Files:**
- Create: `supabase/migrations/0019_investigation_jobs_events.sql`
- Create: `supabase/tests/0007_investigation_jobs_events.sql`
- Create: `packages/investigation/src/casezero_investigation/repository.py`
- Create: `packages/investigation/tests/test_repository_db.py`

**Interfaces:**
- Tables: `investigation_jobs`, `investigation_job_attempts`,
  `model_request_attempts`, `investigation_events`.
- Functions: `create_investigation`, `claim_investigation_job`,
  `reserve_model_request`, `append_investigation_event`, `complete_stage_job`,
  `fail_stage_job`.
- `InvestigationRepository` wraps those functions and validates returned models.

- [ ] **Step 1: Write failing rollback DB tests**

Test two concurrent connections cannot claim the same job, expired lease reclaim,
three-attempt and three-request caps, changed-input new job, event sequence/hash,
and role/case-state denial.

- [ ] **Step 2: Run RED after schema migration only**

```bash
CASEZERO_DB_TEST=1 uv run pytest -m db packages/investigation/tests/test_repository_db.py -v
```

Expected: missing jobs/events functions.

- [ ] **Step 3: Implement migration/functions**

Claim function first expires stale jobs, closes open attempts, then uses
`FOR UPDATE SKIP LOCKED`. Event append hashes the exact envelope from spec §12.2
with `postgres-investigation-event-v1`. All security-definer functions verify
`current_setting('role')='casezero_blind'`, use fixed search paths, and revoke
public execution.

- [ ] **Step 4: Implement repository and run GREEN**

```bash
CASEZERO_DB_TEST=1 uv run pytest -m db packages/investigation/tests/test_repository_db.py -v
uv run pytest packages/investigation/tests -v
uv run ruff check packages/investigation
uv run mypy
```

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/0019_investigation_jobs_events.sql supabase/tests/0007_investigation_jobs_events.sql packages/investigation
git commit -m "feat(investigation): add resumable jobs and event chain"
```

---

### Task 7: Atomic Stage Persistence

**Files:**
- Create: `packages/investigation/src/casezero_investigation/persistence.py`
- Create: `packages/investigation/tests/test_persistence_db.py`
- Modify: `packages/investigation/src/casezero_investigation/repository.py`

**Interfaces:**
- `StageMutation` discriminated union contains complete canonical rows/links and
  event payloads for one stage.
- `persist_stage_result(connection, job, mutation, next_jobs) -> None` executes
  canonical writes, spans/events, job success, and next-job enqueue in one
  explicit transaction.

- [ ] **Step 1: Write failing atomicity tests**

Inject invalid evidence/reference in the final link insert and assert zero
canonical rows/events and unchanged job status. Then use a valid mutation and
assert all rows/event/job/next job commit together.

- [ ] **Step 2: Run RED**

```bash
CASEZERO_DB_TEST=1 uv run pytest -m db packages/investigation/tests/test_persistence_db.py -v
```

- [ ] **Step 3: Implement minimal stage-specific persistence dispatch**

Use explicit SQL per mutation type; do not generate table names from model data.
Verify worker lease owner and unexpired database time before commit.

- [ ] **Step 4: Verify and commit**

```bash
CASEZERO_DB_TEST=1 uv run pytest -m db packages/investigation/tests/test_persistence_db.py -v
uv run ruff check packages/investigation
uv run mypy
git add packages/investigation
git commit -m "feat(investigation): persist stage transitions atomically"
```

---

### Task 8: Candidate Promotion Stages

**Files:**
- Create: `packages/investigation/src/casezero_investigation/promotion.py`
- Create: `packages/investigation/tests/test_promotion.py`
- Create: `packages/investigation/tests/test_promotion_db.py`

**Interfaces:**
- Typed outputs `TimelinePromotion`, `EntityResolution`, `ClaimPromotion`.
- Deterministic validators allow only active EvidenceItem and immutable Phase 1
  candidate IDs for the same case.
- Converts float candidate confidence through the exact Decimal rule.

- [ ] **Step 1: Write failing promotion tests**

Test duplicate merge, preserved contradictions, alias normalization, deterministic
time conversion, ambiguous time remaining inferred, inactive/cross-case/invented
ID rejection, and canonical source-candidate/evidence links.

- [ ] **Step 2: Implement stage prompts and validators**

Use versioned prompt constants and strict results. No output writes directly;
return `StageMutation` values to Task 7 persistence.

- [ ] **Step 3: Run checks and commit**

```bash
uv run pytest packages/investigation/tests/test_promotion.py -v
CASEZERO_DB_TEST=1 uv run pytest -m db packages/investigation/tests/test_promotion_db.py -v
uv run ruff check packages/investigation
uv run mypy
git add packages/investigation
git commit -m "feat(investigation): promote canonical evidence state"
```

---

### Task 9: Retrieval Baseline Package

**Files:**
- Create: `packages/retrieval/pyproject.toml`
- Create: `packages/retrieval/src/casezero_retrieval/__init__.py`
- Create: `packages/retrieval/src/casezero_retrieval/models.py`
- Create: `packages/retrieval/src/casezero_retrieval/scoring.py`
- Create: `packages/retrieval/src/casezero_retrieval/repository.py`
- Create: `packages/retrieval/src/casezero_retrieval/py.typed`
- Create: `packages/retrieval/tests/test_scoring.py`
- Create: `packages/retrieval/tests/test_repository_db.py`
- Create: `supabase/migrations/0020_investigation_retrieval.sql`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- `EvidenceSearchQuery`, `EvidenceSearchResult`, `RetrievalIntent`.
- `score_fts_result(fts, entity, time, type) -> Decimal` uses weights
  `0.55/0.20/0.15/0.10` and four-place quantization.
- Postgres query joins active semantic completions and blind-eligible sources;
  stable tie-break is EvidenceItem UUID.
- Creates FTS index, `retrieval_queries`, `retrieval_results`, forced RLS, and
  post-lock guards.

- [ ] **Step 1: Write failing scoring/model tests**

Test exact weighted values, absent filters contributing zero, score bounds,
SUPPORT/CONTRADICT hash separation, limits `1–50`, and UUID tie ordering.

- [ ] **Step 2: Run RED, register package, implement baseline**

```bash
uv run pytest packages/retrieval/tests/test_scoring.py -v
uv sync
```

- [ ] **Step 3: Write/run rollback DB retrieval tests**

Prove official, post-cutoff, superseded, and cross-case EvidenceItems cannot enter
results; verify persisted rank/component scores and immutable query intent.

```bash
CASEZERO_DB_TEST=1 uv run pytest -m db packages/retrieval/tests/test_repository_db.py -v
```

- [ ] **Step 4: Verify and commit**

```bash
uv run ruff check packages/retrieval pyproject.toml
uv run mypy
uv run pytest packages/retrieval/tests -v
git add packages/retrieval supabase/migrations/0020_investigation_retrieval.sql pyproject.toml uv.lock
git commit -m "feat(retrieval): add blind metadata and FTS search"
```

---

### Task 10: Embedding Recall Probe and D2 Decision

**Files:**
- Create: `benchmark/retrieval/cen22fa375-v1.json`
- Create: `scripts/probe_retrieval_embeddings.py`
- Create: `tests/test_retrieval_probe.py`
- Create: `docs/decision-log/0011-embedding-retrieval-probe.md`
- Conditional create: `supabase/migrations/0021_investigation_vectors.sql`
- Conditional modify: `packages/retrieval/src/casezero_retrieval/repository.py`

**Interfaces:**
- `RetrievalProbeReport` records baseline/hybrid Recall@10 per query/category,
  mean delta, regressions, model/dimension, evidence-set hash, and decision.
- Hybrid weights `0.35/0.30/0.15/0.12/0.08`.
- D2 enables vectors only if all spec §8.4 thresholds pass.

- [ ] **Step 1: Write failing offline fixture/metric tests**

Test 12-query minimum, four required categories, no source payload, reviewer/time,
evidence hash, exact Recall@10, threshold boundary, and deterministic decision.

- [ ] **Step 2: Build blind human relevance fixture**

Use active CEN evidence only. Record reviewer, rationale, category, query/filter,
and expected EvidenceItem IDs. Do not inspect official findings.

- [ ] **Step 3: Run explicit live probe**

```bash
CASEZERO_LIVE=1 uv run python scripts/probe_retrieval_embeddings.py
```

Probe only an advertised Hyperfusion-compatible embedding model with dimension
`<=2000`. If no route is available, record `UNAVAILABLE` and retain FTS.

- [ ] **Step 4: Record D2**

Commit the measured report/decision. Create vector migration/repository path only
when every threshold passes; otherwise no pgvector production code is added.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/test_retrieval_probe.py packages/retrieval/tests -v
uv run ruff check .
uv run mypy
git add benchmark/retrieval/cen22fa375-v1.json scripts/probe_retrieval_embeddings.py tests/test_retrieval_probe.py docs/decision-log/0011-embedding-retrieval-probe.md packages/retrieval supabase/migrations uv.lock
git commit -m "chore(retrieval): resolve embedding decision from recall"
```

---

### Task 11: Tool and Investigation Span Layer

**Files:**
- Create: `packages/investigation/src/casezero_investigation/tools.py`
- Create: `packages/investigation/tests/test_tools.py`
- Create: `packages/observability/src/casezero_observability/investigation.py`
- Create: `packages/observability/tests/test_investigation.py`
- Create: `supabase/migrations/0022_investigation_spans.sql`
- Modify: `packages/evidence/src/casezero_evidence/blindness.py`
- Modify: `packages/evaluation/src/casezero_evaluation/leakage.py`
- Modify: `packages/observability/src/casezero_observability/reasoning.py`
- Modify: `packages/evidence/tests/test_blindness_models.py`
- Modify: `tests/blindness/test_capability_attacks.py`

**Interfaces:**
- Extends capabilities with `INVESTIGATION_TOOL` and `RETRIEVAL` and SQL check.
- Adds `investigation_id`, `job_id`, `parent_span_id`, `prompt_git_sha` to
  Phase 3 model-run context with all-or-none DB validation.
- `InvestigationSpanRecorder` writes AGENT/TOOL/RETRIEVAL spans.
- Tool registry exposes exactly the 11 Phase 3 tools in spec §7.

- [ ] **Step 1: Write failing capability/tool tests**

Assert exact registry names, injected context, strict inputs/outputs, tool-to-
BlindAccessService mapping, retrieval events per distinct source, mandatory
spans, and constructor absence of forbidden clients.

- [ ] **Step 2: Implement closed contracts and migration**

No arbitrary span attributes JSON. Phase 1 model rows remain nullable legacy;
Phase 3 rows require all linkage fields and prompt git SHA.

- [ ] **Step 3: Mutation-test capability absence**

Temporarily register a `web_search` tool, run the attack test to observe failure,
restore the registry, and rerun green.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest packages/investigation/tests/test_tools.py packages/observability/tests/test_investigation.py tests/blindness -v
uv run ruff check .
uv run mypy
git add packages/investigation packages/observability packages/evidence packages/evaluation tests/blindness supabase/migrations/0022_investigation_spans.sql
git commit -m "feat(investigation): add constrained tools and spans"
```

---

### Task 12: Competing Hypothesis Generation

**Files:**
- Create: `packages/investigation/src/casezero_investigation/generation.py`
- Create: `packages/investigation/tests/test_generation.py`

**Interfaces:**
- `HypothesisGenerationResult` requires at least three hypothesis drafts.
- `normalized_hypothesis_overlap(left, right) -> Decimal` uses normalized token
  Jaccard and rejects overlap `>0.7500`.
- Runner validates every claim/evidence ID and initial confidence before
  returning a stage mutation.

- [ ] **Step 1: Write failing diversity/reference tests**

Test three distinct drafts pass; two renamings over threshold fail; exactly `0.75`
passes; fewer than three and invented/cross-case claim IDs fail; three failed
structured attempts exhaust the job request budget without padding output.

- [ ] **Step 2: Implement prompt and generator stage**

Prompt requires distinguishing prediction and weakening evidence. Model receives
canonical blind claims/timeline/entities and constrained retrieval tools only.

- [ ] **Step 3: Verify and commit**

```bash
uv run pytest packages/investigation/tests/test_generation.py -v
uv run ruff check packages/investigation
uv run mypy
git add packages/investigation
git commit -m "feat(investigation): generate competing hypotheses"
```

---

### Task 13: Support and Contradiction Search Stages

**Files:**
- Create: `packages/investigation/src/casezero_investigation/search.py`
- Create: `packages/investigation/tests/test_search.py`

**Interfaces:**
- Creates separate SUPPORT and CONTRADICT `EvidenceSearchQuery` rows per
  hypothesis.
- `HypothesisSearchResult` stores ordered retrieval IDs and unresolved questions.
- Empty contradiction search creates an unresolved question and no confidence
  increase.

- [ ] **Step 1: Write failing intent-isolation tests**

Assert support and contradiction hashes differ for identical text/filters,
contradiction stage rejects SUPPORT intent/cache records, empty results create a
question, and result IDs are active/same-case.

- [ ] **Step 2: Implement stages using retrieval package**

Persist query intent, component scores, and access/retrieval spans. Never label an
empty contradiction result as support.

- [ ] **Step 3: Verify and commit**

```bash
uv run pytest packages/investigation/tests/test_search.py packages/retrieval/tests -v
uv run ruff check packages/investigation packages/retrieval
uv run mypy
git add packages/investigation packages/retrieval
git commit -m "feat(investigation): search support and contradictions separately"
```

---

### Task 14: Critic and Deterministic Falsification

**Files:**
- Create: `packages/investigation/src/casezero_investigation/critic.py`
- Create: `packages/investigation/src/casezero_investigation/falsification.py`
- Create: `packages/investigation/tests/test_critic.py`
- Create: `packages/investigation/tests/test_falsification.py`

**Interfaces:**
- `CriticStage.design_tests(hypothesis, contradiction_results) -> CritiqueBatch`.
- `execute_test(test, state) -> HypothesisTestResult` implements evidence
  presence, temporal consistency, claim contradiction, and separate inferred
  semantic comparison.

- [ ] **Step 1: Write critic RED tests**

Reject support field, invented IDs, tests without weakening predictions, and
model attempts over budget. Accept missing-evidence/alternative fields.

- [ ] **Step 2: Write deterministic executor RED tests**

Cover all four outcomes. Empty contradiction query maps through a proposed
presence test to `EXPECTED_EVIDENCE_MISSING` only when expected evidence is
specified; otherwise `INCONCLUSIVE`. Execution error yields FAILED and no delta.

- [ ] **Step 3: Implement critic and executors**

No causal-chain test exists until Phase 4. Semantic comparison is one separate
model job/result labeled inferred.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest packages/investigation/tests/test_critic.py packages/investigation/tests/test_falsification.py -v
uv run ruff check packages/investigation
uv run mypy
git add packages/investigation
git commit -m "feat(investigation): execute dedicated falsification"
```

---

### Task 15: Deterministic Confidence Revision

**Files:**
- Create: `packages/investigation/src/casezero_investigation/confidence.py`
- Create: `packages/investigation/tests/test_confidence.py`

**Interfaces:**
- `test_delta(outcome, strength) -> Decimal` implements the 12-cell table.
- `revise_confidence(hypothesis, tests) -> ConfidenceRevision` uses UUID order,
  four-place Decimal, clamp `[0.05,0.95]`, and rule `weighted-delta-v1`.
- `assign_hypothesis_statuses(...)` applies REJECTED before WEAKENED, then unique
  LEADING at `>=0.50`; ties remain ACTIVE.

- [ ] **Step 1: Write exhaustive RED tests**

Test all table cells, sum/quantization, lower/upper clamps, deterministic rationale
and test ordering, rejected/weak/active/leading thresholds, and tie behavior.

- [ ] **Step 2: Implement pure Decimal rule**

No model call or float arithmetic. INCONCLUSIVE always contributes exactly
`Decimal("0.0000")`.

- [ ] **Step 3: Verify and commit**

```bash
uv run pytest packages/investigation/tests/test_confidence.py -v
uv run ruff check packages/investigation
uv run mypy
git add packages/investigation
git commit -m "feat(investigation): revise confidence deterministically"
```

---

### Task 16: Stage Registry and Worker

**Files:**
- Create: `packages/investigation/src/casezero_investigation/runner.py`
- Create: `packages/investigation/src/casezero_investigation/worker.py`
- Create: `packages/investigation/tests/test_runner.py`
- Create: `packages/investigation/tests/test_worker.py`
- Modify: `apps/api/src/casezero_api/cli.py`
- Create: `apps/api/src/casezero_api/investigate.py`
- Create: `apps/api/tests/test_investigate_cli.py`

**Interfaces:**
- `StageRegistry` maps every Phase 3 stage to exactly one runner.
- `InvestigationWorker.run_once(worker_id: str) -> JobExecutionReport | None`.
- CLI `casezero investigate CEN22FA375` creates/reuses investigation and drains
  jobs; `casezero investigate-worker --once` claims one.

- [ ] **Step 1: Write failing stage/worker tests**

Test fixed order, no missing/extra stage, successful next enqueue, terminal
failure, cancellation rollback, lease loss before commit, expired reclaim,
completed reuse, and no in-memory conversation dependency.

- [ ] **Step 2: Write failing CLI tests**

Mock model/retrieval and assert payload-free reports, explicit fatal/nonfatal
failures, and no live call without `CASEZERO_LIVE=1` for real execution.

- [ ] **Step 3: Implement composition**

Use dedicated `RuntimeRole.BLIND` connection. Build only blind access,
investigation/retrieval repositories, constrained tools, spans, and Hyperfusion
model. Do not import NTSB, Context.dev, Supabase Storage, or evaluation official
access in `casezero_investigation`.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest packages/investigation/tests/test_runner.py packages/investigation/tests/test_worker.py apps/api/tests/test_investigate_cli.py -v
uv run ruff check .
uv run mypy
git add packages/investigation apps/api
git commit -m "feat(api): orchestrate resumable blind investigations"
```

---

### Task 17: Replay Verification and Crash-Resume Attacks

**Files:**
- Create: `tests/resume/__init__.py`
- Create: `tests/resume/test_investigation_resume.py`
- Create: `tests/blindness/test_investigation_attacks.py`
- Modify: `packages/investigation/src/casezero_investigation/runner.py`

**Interfaces:**
- `VERIFY_REPLAY` compares pure event projection with normalized canonical and
  retrieval relational projection.
- Attack suite covers invented IDs, tool injection, event tamper/gap, lease loss,
  confirmation-only contradiction, confidence tamper, and post-lock mutation.

- [ ] **Step 1: Write attack/resume tests**

Use offline fakes for worker interruption and event reducer. Use rollback DB for
lease/event-chain and post-lock RLS. Mutation-test one capability and one event
hash check, observe failures, restore, rerun.

- [ ] **Step 2: Implement replay comparison normalization**

Normalize set-like rows by primary UUID/value, retain retrieval rank/delta order,
exclude DB timestamps only, and compare full typed projections.

- [ ] **Step 3: Run Loop 2 and commit**

```bash
uv run pytest tests/resume tests/blindness -v
uv run ruff check .
uv run mypy
uv run pytest
git add tests/resume tests/blindness packages/investigation
git commit -m "test(investigation): prove resume replay and blind attacks"
```

---

### Task 18: Hosted Deployment and CEN22FA375 Exit

**Files:**
- Modify: `scripts/verify_hosted_supabase.py`
- Modify: `apps/api/src/casezero_api/hosted_verify.py`
- Modify: `tests/test_verify_hosted_supabase.py`
- Create: `docs/development/phase-3-grounding-review.md`
- Create: `docs/development/phase-3-validation.md`
- Modify: `docs/development/hosted-supabase-validation.md`

**Interfaces:**
- Hosted verifier checks Phase 3 tables/functions/forced RLS/public denial,
  persistent case `BLIND`, and zero lock.
- Grounding review records hashes, five claim IDs, every hypothesis, verdicts,
  rationale, reviewer, and date without source payload.

- [ ] **Step 1: Run migration dry-run**

```bash
set -a; source .env; set +a; supabase db push --dry-run
```

Expected: only Phase 3 migrations `0018` onward actually created by prior tasks;
no reset/drop/truncate/delete.

- [ ] **Step 2: Deploy and run hosted rollback gates**

```bash
set -a; source .env; set +a; supabase db push
set -a; source .env; set +a; CASEZERO_DB_TEST=1 uv run pytest -m db -v
```

Execute all `supabase/tests/*.sql` transactionally through the non-production
session pooler when Docker remains unavailable. Every pgTAP assertion must say
`ok`; `psql` exit zero alone is insufficient.

- [ ] **Step 3: Run D2 and one real investigation**

```bash
CASEZERO_LIVE=1 uv run python scripts/probe_retrieval_embeddings.py
CASEZERO_LIVE=1 uv run casezero investigate CEN22FA375
```

Stop and fix bounded failures; do not loop paid calls blindly. Record exact
job/stage/model/tool/retrieval/event counts and token usage.

- [ ] **Step 4: Perform grounding review**

Review at least five claims and every hypothesis against cited active evidence.
Any unsupported claim, ungrounded/duplicate hypothesis, or fabricated
contradiction fails the phase. Correction requires a version/config change and a
new investigation; never mutate the completed run.

- [ ] **Step 5: Run no-change, replay, leakage, and public denial gates**

```bash
CASEZERO_LIVE=1 uv run casezero investigate CEN22FA375
uv run pytest tests/resume tests/blindness -v
uv run python scripts/verify_hosted_supabase.py
```

Require zero new model/tool/retrieval work, replay equality, zero leakage
incidents, case `BLIND`, lock count zero, and blocked publishable REST/RPC/Storage.

- [ ] **Step 6: Run final full verification**

```bash
uv run ruff check .
uv run mypy
uv run pytest
```

Record exact counts and all unexecuted limitations in
`docs/development/phase-3-validation.md`.

- [ ] **Step 7: Commit validation**

```bash
git add scripts/verify_hosted_supabase.py apps/api/src/casezero_api/hosted_verify.py tests/test_verify_hosted_supabase.py docs/development/phase-3-grounding-review.md docs/development/phase-3-validation.md docs/development/hosted-supabase-validation.md
git commit -m "docs: record Phase 3 investigation validation"
```

- [ ] **Step 8: Generate required explanation and clean up**

Invoke `explain-diff-html` from Phase 2 endpoint commit `839aac9` through the
Phase 3 validation commit. Generate
`/tmp/2026-09-05-explanation-phase-3-investigation-engine.html`, validate all
links/section IDs/inline CSS/inline JavaScript, ensure every `<pre>` uses literal
`white-space: pre` or `white-space: pre-wrap`, and keep it outside the repo.

Terminate every worker/test/server, close previews, verify no hosted idle
transaction, confirm clean git status, update local-only `task.md`, and only then
mark Phase 3 complete.
