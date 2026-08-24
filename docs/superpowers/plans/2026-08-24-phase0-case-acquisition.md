# Phase 0 — Foundation & Case Acquisition Implementation Plan

> **For the engineer (human or agentic):** execute tasks in order, directly in
> your session, test-first. Do NOT use Superpowers execution machinery — per
> `AGENTS.md`, follow this plan, the tests, and the verification loops
> directly. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `casezero ingest <ntsb-case-id>` acquires real case metadata and
docket evidence into an immutable, checksummed, visibility-classified source
store — with zero AI investigation.

**Architecture:** Python uv workspace; `packages/evidence` owns typed models;
`packages/ntsb` owns acquisition behind narrow interfaces; Supabase local
stack provides Postgres + Storage; all downloads deterministic with SHA-256
and rate limiting. No model calls anywhere in this phase except the isolated
capability spike (Task 10).

**Tech Stack:** Python 3.12, uv, FastAPI (shell only), Pydantic v2,
httpx, Supabase CLI local stack, pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-24-casezero-foundation-design.md`
(read first; product constraints trace to `product-spec.md`, local-only).

## Global Constraints

- No live network or model calls in default tests; live tests use
  `@pytest.mark.live` and require `CASEZERO_LIVE=1`.
- All datetimes aware UTC (`datetime` with `tzinfo=timezone.utc`).
- All Pydantic models `model_config = ConfigDict(strict=True)`.
- Source artifacts immutable after write; SHA-256 recorded at download.
- ≥5 s delay between NTSB requests; exponential backoff on 429/5xx.
- Never commit source artifacts or `product-spec.md`.
- Visibility classification happens at ingestion; ambiguous → blocked.
- Lint: ruff; types: mypy strict on `packages/`.
- Every commit passes Loop 1 of `docs/development/verification-loops.md`.

---

### Task 1: Workspace scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `packages/evidence/pyproject.toml`
- Create: `packages/ntsb/pyproject.toml`
- Create: `apps/api/pyproject.toml`
- Create: `pytest.ini`
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: importable `casezero_evidence`, `casezero_ntsb` packages; `uv run pytest` runs green with zero tests.

- [ ] **Step 1: Write root `pyproject.toml`**

```toml
[project]
name = "casezero"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []

[tool.uv.workspace]
members = ["packages/*", "apps/api"]

[tool.uv.sources]
casezero-evidence = { workspace = true }
casezero-ntsb = { workspace = true }

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "ruff>=0.6", "mypy>=1.11"]

[tool.ruff]
target-version = "py312"

[tool.mypy]
strict = true
packages = ["packages"]
```

- [ ] **Step 2: Write `packages/evidence/pyproject.toml`**

```toml
[project]
name = "casezero-evidence"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["pydantic>=2.8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/casezero_evidence"]
```

- [ ] **Step 3: Write `packages/ntsb/pyproject.toml`**

```toml
[project]
name = "casezero-ntsb"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["casezero-evidence", "httpx>=0.27", "pydantic>=2.8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/casezero_ntsb"]
```

- [ ] **Step 4: Write `pytest.ini`**

```ini
[pytest]
markers =
    live: requires CASEZERO_LIVE=1 and network access
addopts = -m "not live"
```

- [ ] **Step 5: Create empty package trees**

```bash
mkdir -p packages/evidence/src/casezero_evidence packages/ntsb/src/casezero_ntsb
touch packages/evidence/src/casezero_evidence/__init__.py packages/ntsb/src/casezero_ntsb/__init__.py
mkdir -p packages/evidence/tests packages/ntsb/tests
```

- [ ] **Step 6: Verify**

Run: `uv sync && uv run pytest && uv run ruff check . && uv run mypy packages/`
Expected: all pass (no tests collected is fine).

- [ ] **Step 7: Write CI workflow** (`.github/workflows/ci.yml`): setup-python
  3.12, `uv sync`, ruff, mypy, `uv run pytest`. No live marker.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml pytest.ini packages apps .github
git commit -m "chore: uv workspace scaffolding with evidence and ntsb packages"
```

---

### Task 2: Supabase local stack + first migration

**Files:**
- Create: `supabase/config.toml` (via `supabase init`)
- Create: `supabase/migrations/0001_cases_and_sources.sql`
- Test: `supabase/tests/0001_visibility_rls.sql`

**Interfaces:**
- Produces: tables `cases(id uuid pk, ntsb_number text unique, title text, event_date timestamptz, metadata jsonb, state text check (state in ('ACQUIRING','BLIND','LOCKED','REVEALED')), created_at timestamptz)` and `source_documents(id uuid pk, case_id uuid references cases, title text, source_url text, published_at timestamptz, evidence_date timestamptz, retrieved_at timestamptz, document_type text, visibility text check (visibility in ('INVESTIGATION_EVIDENCE','OFFICIAL_ANALYSIS','FINAL_FINDING')), checksum text, storage_path text)`.
- Produces: DB roles `casezero_blind` (sees only INVESTIGATION_EVIDENCE) and `casezero_eval` (sees all).

- [ ] **Step 1: Write the failing RLS test** (`supabase/tests/0001_visibility_rls.sql`, pgTAP):

```sql
begin;
select plan(2);
insert into cases (ntsb_number, title, state) values ('TEST00AA000', 'fixture', 'BLIND');
insert into source_documents (case_id, title, source_url, retrieved_at, document_type, visibility, checksum, storage_path)
  select id, 'Factual', 'https://example.test/a.pdf', now(), 'FACTUAL_REPORT', 'INVESTIGATION_EVIDENCE', 'a', 'x' from cases;
insert into source_documents (case_id, title, source_url, retrieved_at, document_type, visibility, checksum, storage_path)
  select id, 'Final', 'https://example.test/b.pdf', now(), 'FINAL_REPORT', 'FINAL_FINDING', 'b', 'y' from cases;
set role casezero_blind;
select is((select count(*)::int from source_documents), 1, 'blind role sees only investigation evidence');
select is((select visibility from source_documents limit 1), 'INVESTIGATION_EVIDENCE', 'blocked rows invisible, not filtered client-side');
select * from finish();
rollback;
```

- [ ] **Step 2: Run to verify failure**

Run: `supabase start && supabase test db`
Expected: FAIL — relation/roles do not exist.

- [ ] **Step 3: Write migration 0001** — create the two tables, the two roles,
  `alter table source_documents enable row level security;` and policy:

```sql
create policy blind_evidence_only on source_documents
  to casezero_blind using (visibility = 'INVESTIGATION_EVIDENCE');
grant select on source_documents to casezero_blind;
grant select on all tables in schema public to casezero_eval;
```

- [ ] **Step 4: Run to verify pass** — `supabase test db` green.

- [ ] **Step 5: Commit**

```bash
git add supabase/
git commit -m "feat(db): cases and source_documents with blind-role RLS"
```

---

### Task 3: Core evidence models

**Files:**
- Create: `packages/evidence/src/casezero_evidence/models.py`
- Test: `packages/evidence/tests/test_models.py`

**Interfaces:**
- Produces: `SourceDocument`, `SourceLocator` (discriminated union on `kind`),
  `EvidenceItem`, `Visibility`, `DocumentType`, `ExtractionMethod`. Later tasks
  import these exact names.

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from casezero_evidence.models import EvidenceItem, PdfLocator, SourceDocument


def test_evidence_item_roundtrip_with_pdf_locator():
    item = EvidenceItem(
        case_id="018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f",
        source_document_id="018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e70",
        type="TEXT",
        observation="Oil pressure fluctuated during climb.",
        source_locator=PdfLocator(page=17, paragraph=4),
        extraction_method="DETERMINISTIC",
    )
    assert item.source_locator.page == 17
    assert EvidenceItem.model_validate(item.model_dump()) == item


def test_source_document_rejects_naive_datetime():
    with pytest.raises(ValidationError):
        SourceDocument(
            case_id="018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f",
            title="ATC factual report",
            source_url="https://data.ntsb.gov/example.pdf",
            retrieved_at=datetime(2026, 8, 24),  # naive — must be rejected
            document_type="FACTUAL_REPORT",
            visibility="INVESTIGATION_EVIDENCE",
            checksum="0" * 64,
        )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest packages/evidence/tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `models.py`** — strict Pydantic models per the design
  spec's contract section: `Visibility`/`DocumentType`/`ExtractionMethod` as
  `StrEnum`; `SourceLocator` as `Annotated[Union[PdfLocator, TableLocator,
  ImageLocator, AudioLocator, TextLocator], Field(discriminator="kind")]`;
  `EvidenceItem` and `SourceDocument` exactly as specified, ids defaulting to
  `uuid4`, datetimes validated aware-UTC via field validator.

- [ ] **Step 4: Run to verify pass** — same command, green.

- [ ] **Step 5: Commit**

```bash
git add packages/evidence
git commit -m "feat(evidence): typed SourceDocument, SourceLocator, EvidenceItem models"
```

---

### Task 4: SourceStore with SHA-256 provenance

**Files:**
- Create: `packages/evidence/src/casezero_evidence/source_store.py`
- Test: `packages/evidence/tests/test_source_store.py`

**Interfaces:**
- Consumes: `SourceDocument` from Task 3.
- Produces: `class SourceStore(Protocol): put(case_id, filename, data: bytes) -> StoredSource; get(storage_path) -> bytes` and `LocalSourceStore(root: Path)` implementing it. `StoredSource { document: SourceDocument, path: Path, sha256: str }`. Investigation code never touches files except through this interface.

- [ ] **Step 1: Write the failing test** — write bytes, assert returned sha256
  matches `hashlib.sha256(data).hexdigest()`; assert re-`put` of identical
  bytes returns the same path (content-addressed); assert `get` round-trips.

- [ ] **Step 2: Run to verify failure** — module missing.

- [ ] **Step 3: Implement `LocalSourceStore`** — content-addressed layout
  `root/<sha256[:2]>/<sha256>`; write-once (refuse overwrite of differing
  bytes, no-op on identical); compute hash in a single streaming pass.

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Commit** — `feat(evidence): content-addressed local SourceStore`.

---

### Task 5: CAROL case catalog client

**Files:**
- Create: `packages/ntsb/src/casezero_ntsb/carol.py`
- Test: `packages/ntsb/tests/test_carol.py`
- Test: `packages/ntsb/tests/test_carol_live.py`

**Interfaces:**
- Produces: `class CarolClient: async def search_cases(self, *, mode: str = "Aviation", date_from: date, date_to: date) -> list[CaseMetadata]` where `CaseMetadata { ntsb_number, event_date, location, aircraft, status, has_final_report: bool }`. Produces `CaseCatalog` protocol it satisfies.

- [ ] **Step 1: Write failing offline test** — use `respx` to mock
  `https://data.ntsb.gov/carol-main-public/api/Query/FileExport` returning a
  ZIP containing a small JSON array; assert parsed `CaseMetadata` list and
  that the client waits ≥5 s between two calls (monkeypatch a clock).

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement** — httpx async client, POST FileExport, unzip
  in-memory, strict-parse rows, skip rows without an NTSB number, rate limiter
  (asyncio.Lock + last-call timestamp).

- [ ] **Step 4: Verify offline pass.**

- [ ] **Step 5: Write live smoke test** (`@pytest.mark.live`): one small date
  range, assert ≥1 case and valid `ntsb_number` shape.

- [ ] **Step 6: Commit** — `feat(ntsb): CAROL FileExport case catalog client`.

---

### Task 6: NTSB developer API client

**Files:**
- Create: `packages/ntsb/src/casezero_ntsb/developer_api.py`
- Test: `packages/ntsb/tests/test_developer_api.py`

**Interfaces:**
- Produces: `class NtsbApiClient: async def get_case(self, ntsb_number: str) -> CaseMetadata` (same `CaseMetadata` as Task 5 — catalog and point lookup agree on one type). Subscription key from `NTSB_API_SUBSCRIPTION_KEY`; missing key raises `NtsbApiKeyMissing` at construction.

- [ ] **Steps:** same TDD cycle — respx-mocked failure-first test (auth header
  sent, 404 → `CaseNotFound`), implementation, green, one `@pytest.mark.live`
  smoke, commit `feat(ntsb): developer API GetCase client`.

---

### Task 7: Docket manifests (curated loader + Context.dev client)

**Files:**
- Create: `packages/ntsb/src/casezero_ntsb/manifest.py`
- Create: `packages/ntsb/src/casezero_ntsb/contextdev.py`
- Create: `fixtures/real-cases/README.md` (manifest format documentation)
- Test: `packages/ntsb/tests/test_manifest.py`

**Interfaces:**
- Produces: `DocketManifest` (Pydantic, per design spec), `load_manifest(path: Path) -> DocketManifest` for curated YAML/JSON fixtures, and
  `class ContextDevClient: async def extract_manifest(self, docket_url: str) -> DocketManifest`.
  Curated path is the default until open decision D4 resolves.

- [ ] **Steps:** TDD cycle — fixture manifest (one synthetic case, two
  documents with fake URLs) loads and validates; `ContextDevClient` validates
  that every manifest document has a `source_url` (rejects entries without
  one); commit `feat(ntsb): docket manifest model, curated loader, Context.dev extraction client`.

---

### Task 8: Downloader + visibility classification

**Files:**
- Create: `packages/ntsb/src/casezero_ntsb/downloader.py`
- Create: `packages/ntsb/src/casezero_ntsb/visibility.py`
- Test: `packages/ntsb/tests/test_downloader.py`
- Test: `packages/ntsb/tests/test_visibility.py`

**Interfaces:**
- Consumes: `DocketManifest` (Task 7), `SourceStore` (Task 4), `SourceDocument` (Task 3).
- Produces: `classify_visibility(document_type: str | None, title: str, published_at: datetime | None, cutoff: datetime) -> Visibility` and
  `async def download_manifest(manifest, store, db) -> list[SourceDocument]` — downloads each original, checksums, stores, inserts row with classified visibility; failures appended to manifest `retrieval_errors[]`, never raised past the case boundary.

**Classification rules (deterministic, tested):** document_type or title
matching `final report|probable cause|adopted` → `FINAL_FINDING`; matching
`analysis|finding|recommendation` → `OFFICIAL_ANALYSIS`; published after the
case cutoff → `OFFICIAL_ANALYSIS`; otherwise `INVESTIGATION_EVIDENCE`;
unknown type → blocked (`OFFICIAL_ANALYSIS`) until curated.

- [ ] **Steps:** TDD cycle per rule (one test per rule + one boundary test at
  the exact cutoff instant); downloader test with respx + `LocalSourceStore`
  in tmp_path asserting checksum equality and idempotent re-download; commit
  `feat(ntsb): source downloader with deterministic visibility classification`.

---

### Task 9: `casezero ingest` CLI

**Files:**
- Create: `apps/api/src/casezero_api/cli.py`
- Test: `apps/api/tests/test_ingest_cli.py`

**Interfaces:**
- Consumes: everything above. Produces: `casezero ingest <ntsb-number>
  [--manifest PATH] [--cutoff ISO8601]` — resolves metadata (developer API,
  CAROL fallback), obtains manifest (curated fixture or Context.dev),
  downloads, prints summary: documents fetched, bytes, per-visibility counts,
  retrieval errors.

- [ ] **Steps:** TDD — CLI test drives the full path with mocked clients and
  asserts the summary fields and that a partially-failing manifest exits 0
  with errors reported (not a crash); commit `feat(api): casezero ingest CLI`.

---

### Task 10: Hyperfusion capability spike (throwaway measurement)

**Files:**
- Create: `scripts/spike_hyperfusion_structured_output.py`
- Create: `docs/decision-log/0003a-spike-results.md` (results recorded here)

**Interfaces:**
- Consumes: Hyperfusion API. Produces: measured schema-failure and tool-call
  failure rates for the exact prompt shapes Phases 1–3 will use (evidence
  extraction, claim extraction, hypothesis generation, critique). 30 calls per
  shape per candidate model.

- [ ] **Step 1: Write the spike script** — Pydantic AI agents with the real
  result models from Task 3 (plus stand-ins for Claim/Hypothesis per the
  design spec contracts), pointed at `HYPERFUSION_BASE_URL`, recording
  failures per (model, shape) to a JSON report.

- [ ] **Step 2: Run once with `CASEZERO_LIVE=1`**, record numbers in
  `0003a-spike-results.md`, including a per-stage model recommendation
  (resolves open decision D3) and a go/no-go note on the fallback from
  ADR 0003.

- [ ] **Step 3: Commit** — `chore(spike): hyperfusion structured-output capability results`.

---

## Follow-on plans (not this plan)

- Phase 1 plan: processors (pdf/table/image/text), claim/entity/timeline
  candidate extraction — starts after Task 10's model table is known.
- Phase 2 plan: locking + cutoff enforcement + leakage auditor — RLS
  foundation from Task 2 carries over.
