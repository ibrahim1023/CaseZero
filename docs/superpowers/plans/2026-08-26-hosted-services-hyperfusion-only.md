# Hosted Services and Hyperfusion-Only Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Repository override:** `AGENTS.md` prohibits those execution skills. Execute
> directly, test-first, with one verified commit per task.

**Goal:** Make hosted Supabase Postgres/private Storage the only durable runtime
platform and Hyperfusion the only model provider, while audited-skipping all
non-AI-allowed material.

**Architecture:** Source and derived store protocols gain Supabase Storage
implementations using immutable checksum keys. Runtime composition requires
hosted Supabase configuration and a single Hyperfusion model route; local stores
remain injected test fixtures only. Hosted integration tests run only against an
explicit non-production project.

**Tech Stack:** Python 3.12, httpx, psycopg 3, Supabase Free Postgres/Storage,
Pydantic AI 2.30.0, Hyperfusion OpenAI-compatible API, pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-26-hosted-services-hyperfusion-only-design.md`

## Global Constraints

- Private buckets: `casezero-sources` and `casezero-derived`.
- Runtime durable state must not use local filesystem stores.
- Hyperfusion is the only model provider. Remove Ollama code/config/tests.
- `LOCAL_ONLY`, `LINK_ONLY`, and `EXCLUDED` never download, upload, parse, or
  call a model; each creates an audit skip.
- Service-role and database secrets remain backend-only and unlogged.
- Default tests are network-free. Hosted tests require
  `CASEZERO_HOSTED_TEST=1` and a non-production project marker.
- Do not copy local test rows to hosted Postgres; deploy migrations and re-ingest.
- One task, one verified commit.

---

### Task 1: Supabase private Storage adapters

**Files:**
- Create: `packages/evidence/src/casezero_evidence/supabase_store.py`
- Test: `packages/evidence/tests/test_supabase_store.py`
- Modify: `packages/evidence/src/casezero_evidence/__init__.py`

**Interfaces:**
- Produces `SupabaseSourceStore` implementing `SourceStore` and
  `SupabaseArtifactStore` implementing `ArtifactStore`.

- [ ] **Step 1: Write RED HTTP-contract tests**

```python
@respx.mock
def test_source_upload_uses_private_content_address_and_verifies_bytes():
    upload = respx.post(
        "https://project.supabase.co/storage/v1/object/casezero-sources/sources/aa/" + SHA
    ).mock(return_value=httpx.Response(200, json={"Key": "stored"}))
    respx.get(
        "https://project.supabase.co/storage/v1/object/casezero-sources/sources/aa/" + SHA
    ).mock(return_value=httpx.Response(200, content=DATA))

    stored = SupabaseSourceStore(URL, SERVICE_KEY, "casezero-sources").put(
        CASE_ID, "report.pdf", DATA
    )

    assert upload.calls[0].request.headers["authorization"] == f"Bearer {SERVICE_KEY}"
    assert stored.checksum == SHA
```

Also test existing-object 409 followed by matching GET is idempotent, mismatched
remote bytes raise `StoreIntegrityError`, artifact keys include artifact kind,
GET rejects malformed logical paths, and no request body/header leaks keys in
exceptions.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest packages/evidence/tests/test_supabase_store.py -v`  
Expected: module missing.

- [ ] **Step 3: Implement adapters**

Use one injected `httpx.Client`, headers `Authorization: Bearer` and `apikey`,
`x-upsert: false`, URL-quoted bucket/object segments, 120-second timeout, and
read-after-write byte verification. Never use public object URLs.

- [ ] **Step 4: Verify GREEN**

Run focused tests, all evidence tests, ruff, and mypy.

- [ ] **Step 5: Commit**

```bash
git add packages/evidence
git commit -m "feat(storage): add private Supabase source and artifact stores"
```

---

### Task 2: Hyperfusion-only routing and restricted-item skipping

**Files:**
- Modify: `packages/observability/src/casezero_observability/reasoning.py`
- Modify: `packages/observability/tests/test_reasoning.py`
- Modify: `apps/api/src/casezero_api/process.py`
- Modify: `apps/api/tests/test_process_cli.py`
- Modify: `packages/ntsb/src/casezero_ntsb/curation.py`
- Modify: `packages/ntsb/tests/test_curation.py`
- Modify: `fixtures/real-cases/cen22fa375/manifest.json`
- Modify: `.env.example`

**Interfaces:**
- `ModelRouter(primary, recorder=None)` has no local model.
- `AI_ALLOWED` routes to Hyperfusion; all other dispositions raise
  `ModelRoutingDenied` before provider invocation.

- [ ] **Step 1: Write RED routing/rights tests**

```python
@pytest.mark.parametrize("disposition", [LOCAL_ONLY, LINK_ONLY, EXCLUDED])
async def test_non_ai_allowed_never_calls_model(disposition):
    model = FakeModel("hyperfusion")
    with pytest.raises(ModelRoutingDenied):
        await ModelRouter(model).generate(REQUEST, disposition)
    assert model.calls == 0
```

Update processing tests to assert `LOCAL_ONLY` creates `SKIPPED_RIGHTS`, no
source document/object, and no semantic call. Curation validation requires a
checksum only for `AI_ALLOWED`; remove the medical report checksum.

- [ ] **Step 2: Verify RED**

Run observability, curation, and process CLI tests; expected failures show
Ollama/local routing still active.

- [ ] **Step 3: Remove Ollama and implement single-provider behavior**

Delete local model constructor/configuration and `PromptedOutput` provider
special case. Keep bounded Hyperfusion retry and model-run recording. Add
`CASEZERO_TEXT_MODEL` and `CASEZERO_VISION_MODEL`; remove all `OLLAMA_*` envs.

- [ ] **Step 4: Verify GREEN and search for remnants**

```bash
uv run pytest packages/observability/tests packages/ntsb/tests/test_curation.py apps/api/tests/test_process_cli.py -v
```

Repository grep for `Ollama|OLLAMA|ollama` must return only historical ADRs /
validation reports explicitly labeled superseded, or be updated to current
policy.

- [ ] **Step 5: Commit**

```bash
git add packages/observability packages/ntsb apps/api fixtures/real-cases/cen22fa375 .env.example docs
git commit -m "refactor(models): use Hyperfusion-only hosted inference"
```

---

### Task 3: Hosted runtime composition

**Files:**
- Create: `apps/api/src/casezero_api/settings.py`
- Modify: `apps/api/src/casezero_api/cli.py`
- Modify: `apps/api/src/casezero_api/process.py`
- Test: `apps/api/tests/test_settings.py`
- Test: `apps/api/tests/test_process_cli.py`

**Interfaces:**
- Produces strict `HostedSettings.from_environment()` and hosted store/model
  factories. Runtime commands cannot default to localhost or filesystem paths.

- [ ] **Step 1: Write RED settings tests**

Test missing `DATABASE_URL`, `SUPABASE_URL`, service key, bucket names, or
Hyperfusion key fails at startup. Reject localhost/127.0.0.1 database and
Supabase URLs in hosted mode. Ensure `repr(settings)` redacts secrets.

- [ ] **Step 2: Verify RED**

Run settings tests; module missing.

- [ ] **Step 3: Implement hosted settings/composition**

Instantiate `SupabaseSourceStore`/`SupabaseArtifactStore` with one backend
httpx client. Build only a Hyperfusion `PydanticReasoningModel`. Keep factory
parameters injectable so tests use fakes.

- [ ] **Step 4: Verify GREEN**

Run API tests; assert no runtime import/construction of local stores or Ollama.

- [ ] **Step 5: Commit**

```bash
git add apps/api .env.example
git commit -m "feat(api): require hosted Supabase runtime configuration"
```

---

### Task 4: Hosted environment marker, buckets, and verification

**Files:**
- Create: `supabase/migrations/0004_hosted_environment_and_buckets.sql`
- Create: `scripts/verify_hosted_supabase.py`
- Create: `tests/test_verify_hosted_supabase.py`
- Modify: `pytest.ini`
- Modify: `docs/development/verification-loops.md`

**Interfaces:**
- Migration creates one `casezero_environment` row and two private buckets.
- Verifier exits nonzero for production, public/missing buckets, missing schema,
  disabled RLS, or processor visibility leakage.

- [ ] **Step 1: Write RED verifier tests**

Use fake query results to test development passes; production marker, public
bucket, missing migration, and visible final source fail closed.

- [ ] **Step 2: Verify RED**

Run verifier tests; module missing.

- [ ] **Step 3: Implement migration/verifier**

```sql
create table public.casezero_environment (
  singleton boolean primary key default true check (singleton),
  name text not null check (name in ('development','staging','production'))
);
insert into public.casezero_environment (name) values ('development');
insert into storage.buckets (id, name, public)
values ('casezero-sources','casezero-sources',false),
       ('casezero-derived','casezero-derived',false)
on conflict (id) do update set public = false;
```

The verifier uses the hosted database connection plus Storage API and prints
statuses only, never credentials.

- [ ] **Step 4: Verify GREEN**

Run offline verifier tests. Add pytest marker `hosted`; default addopts exclude
it. Replace local Supabase commands in verification docs with explicit hosted
commands.

- [ ] **Step 5: Commit**

```bash
git add supabase scripts/verify_hosted_supabase.py tests/test_verify_hosted_supabase.py pytest.ini docs/development
git commit -m "feat(platform): add hosted Supabase environment gate"
```

---

### Task 5: Deploy fresh Supabase Free development project

**Files:**
- Modify: local `.env` only (gitignored)
- Create: `docs/development/hosted-supabase-validation.md`

**Prerequisite:** owner-provided Supabase Free project credentials configured
securely on this machine: project reference/access token, hosted DB URL,
Supabase URL, and service-role key.

- [ ] **Step 1: Confirm secret names only**

Use dry-run/name-only inspection. Never print values. Confirm target project is
not production.

- [ ] **Step 2: Link and inspect migration diff**

Run Supabase CLI link/migration commands against the development project. Read
the migration plan before applying; do not reset/drop the hosted database.

- [ ] **Step 3: Push migrations and verify**

Run linked migration push, then `scripts/verify_hosted_supabase.py` with
`CASEZERO_HOSTED_TEST=1`. Confirm private buckets and RLS.

- [ ] **Step 4: Record measured deployment evidence**

Document project environment (not identifiers/secrets), migration versions,
bucket privacy, RLS checks, and command outcomes.

- [ ] **Step 5: Commit documentation**

```bash
git add docs/development/hosted-supabase-validation.md
git commit -m "docs: record hosted Supabase development validation"
```

---

### Task 6: Fresh hosted Phase 1 acceptance

**Files:**
- Modify: `docs/development/phase-1-validation.md` or create if absent
- Update: local `task.md` only (gitignored)

- [ ] **Step 1: Re-ingest AI_ALLOWED sources to hosted Storage/DB**

Run curated retrieval/ingest with hosted settings. Assert three approved source
objects, no medical/other restricted objects, and 12 audited skips (one
LOCAL_ONLY plus eleven existing restricted entries).

- [ ] **Step 2: Process with Hyperfusion only**

Run `casezero process CEN22FA375`. Record structural/evidence/candidate/model
counts and bounded failures. Confirm every model run provider is Hyperfusion.

- [ ] **Step 3: Run no-change idempotency pass**

No new source/derived objects, structural artifacts, or successful model calls.
Any retry occurs only for previously failed retryable units and is reported.

- [ ] **Step 4: Run full gates**

```bash
uv run ruff check .
uv run mypy
uv run pytest
CASEZERO_HOSTED_TEST=1 uv run pytest -m hosted -v
uv run python scripts/verify_hosted_supabase.py
```

- [ ] **Step 5: Commit Phase 1 validation evidence**

Record exact results, skipped checks, limitations, free-tier usage, and all
unexecuted integrations. Commit separately.

- [ ] **Step 6: Generate phase explanation**

Invoke `explain-diff-html` for the complete Phase 1 range including hosted
migration, inspect the `/tmp` HTML, mark Phase 1 complete, and leave a clean
working tree. Do not push unless explicitly requested.
