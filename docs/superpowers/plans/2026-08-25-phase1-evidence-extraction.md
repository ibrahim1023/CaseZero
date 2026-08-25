# Phase 1 Evidence Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Repository override:** `AGENTS.md` prohibits those execution skills. Execute
> this plan directly in the current session, test-first, one verified commit per
> task.

**Goal:** Convert the rights-eligible public evidence in the curated
`CEN22FA375` docket into versioned structural artifacts, provenance-resolvable
EvidenceItems, and provisional claim/entity/timeline candidates.

**Architecture:** A deterministic processor pass writes immutable derived
artifacts and ordered structural units. A separate rights-aware Pydantic AI pass
creates EvidenceItems, followed by a case-level candidate pass. Postgres owns the
processing ledger and model spans; large artifacts remain content-addressed.

**Tech Stack:** Python 3.12, Pydantic 2, PostgreSQL/Supabase RLS,
Docling 2.120.2, Polars 1.43.2, fastexcel 0.20.2, Pillow 12.3.0,
Pydantic AI 2.30.0, Hyperfusion OpenAI-compatible API, Ollama
OpenAI-compatible fallback, pytest, pgTAP, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-25-phase1-evidence-extraction-design.md`

## Global Constraints

- Use only officially public NTSB material; unclear-rights items default to
  `LINK_ONLY` and their bytes never enter stores, model calls, logs, or fixtures.
- Original and derived bytes are immutable and SHA-256 addressed.
- All boundary models are strict Pydantic models; all datetimes are aware UTC.
- Every StructuralUnit and EvidenceItem has a locator resolvable to the original
  source.
- Deterministic processors never call models. Models never parse entire large
  tables or unrestricted dockets.
- `qwen/qwen3-32b` is the initial Hyperfusion text model. Vision eligibility is
  measured; Ollama is backup and the only model allowed for `LOCAL_ONLY`.
- No default test calls NTSB, Context.dev, Hyperfusion, Ollama, or another live
  service.
- Work test-first. One task, one green verification loop, one commit. Never carry
  uncommitted work into the next task.
- Phase completion requires the full verification gate and
  `explain-diff-html`.

---

### Task 1: Rights, ledger, locator, and candidate contracts

**Files:**
- Modify: `packages/evidence/src/casezero_evidence/models.py`
- Modify: `packages/evidence/src/casezero_evidence/__init__.py`
- Create: `packages/evidence/src/casezero_evidence/processing.py`
- Create: `packages/evidence/src/casezero_evidence/candidates.py`
- Test: `packages/evidence/tests/test_processing_models.py`
- Test: `packages/evidence/tests/test_candidate_models.py`

**Interfaces:**
- Produces: `RightsStatus`, `ProcessingDisposition`, `ProcessingStatus`,
  `ReviewStatus`, `DerivedArtifactKind`, `StructuralUnitKind`,
  `DocketItem`, `ProcessingSource`, `ProcessingRun`, `DerivedArtifact`,
  `StructuralUnit`, `ClaimCandidate`, `EntityCandidate`, `TimelineCandidate`,
  and validated `BoundingBox`.
- Modifies: `PdfLocator` adds `bounding_box` and `reading_order`; `ImageLocator`
  adds dimensions and typed region; `EvidenceItem` adds `structural_unit_id`,
  `model_run_id`, and `review_status` while leaving `entities` empty in Phase 1.

- [ ] **Step 1: Write failing strict-model tests**

```python
from casezero_evidence.processing import (
    DocketItem,
    ProcessingDisposition,
    RightsStatus,
)


def test_unclear_rights_defaults_to_link_only() -> None:
    item = DocketItem(
        case_id=CASE_ID,
        title="Historical photo",
        source_url=AnyHttpUrl("https://data.ntsb.gov/photo.pdf"),
        rights_status=RightsStatus.THIRD_PARTY_UNCLEAR,
        attribution="Source: National Transportation Safety Board",
        review_note="Original photographer rights not confirmed",
        reviewed_at=NOW,
    )
    assert item.processing_disposition is ProcessingDisposition.LINK_ONLY


def test_bounding_box_rejects_reversed_coordinates() -> None:
    with pytest.raises(ValidationError):
        BoundingBox(x1=20, y1=10, x2=5, y2=30)
```

Candidate tests must assert non-empty evidence ids, UTC timeline values, strict
extra-field rejection, and that `EvidenceItem.entities` defaults to empty.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest packages/evidence/tests/test_processing_models.py packages/evidence/tests/test_candidate_models.py -v`  
Expected: collection fails because the models do not exist.

- [ ] **Step 3: Implement the exact enums and strict models**

```python
class RightsStatus(StrEnum):
    NTSB_AUTHORED = "NTSB_AUTHORED"
    THIRD_PARTY_PERMISSION_CONFIRMED = "THIRD_PARTY_PERMISSION_CONFIRMED"
    THIRD_PARTY_UNCLEAR = "THIRD_PARTY_UNCLEAR"
    UNKNOWN = "UNKNOWN"


class ProcessingDisposition(StrEnum):
    AI_ALLOWED = "AI_ALLOWED"
    LOCAL_ONLY = "LOCAL_ONLY"
    LINK_ONLY = "LINK_ONLY"
    EXCLUDED = "EXCLUDED"


class ProcessingStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"
    SKIPPED_RIGHTS = "SKIPPED_RIGHTS"


class BoundingBox(StrictModel):
    x1: float = Field(ge=0)
    y1: float = Field(ge=0)
    x2: float = Field(gt=0)
    y2: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> "BoundingBox":
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("bounding box coordinates must be ordered")
        return self
```

Implement every field named in the approved spec. `DocketItem` derives the
default disposition from rights status in an `after` validator; an explicit
less-restrictive disposition for unclear/unknown rights is rejected.
`ProcessingSource` contains exactly `docket_item: DocketItem`,
`document: SourceDocument`, and `data: bytes`; orchestration constructs it only
after rights and source-visibility checks.

- [ ] **Step 4: Verify GREEN and package exports**

Run: `uv run pytest packages/evidence/tests/test_processing_models.py packages/evidence/tests/test_candidate_models.py packages/evidence/tests/test_models.py -v`  
Expected: PASS.

- [ ] **Step 5: Run Loop 1 and commit**

```bash
uv run ruff check packages/evidence
uv run mypy packages/evidence
uv run pytest packages/evidence/tests -v
git add packages/evidence
git commit -m "feat(evidence): add Phase 1 processing and candidate contracts"
```

---

### Task 2: Processing ledger schema, processor role, and repository

**Files:**
- Create: `supabase/migrations/0002_evidence_processing.sql`
- Create: `supabase/tests/0002_processor_rls.sql`
- Create: `packages/evidence/src/casezero_evidence/repository.py`
- Test: `packages/evidence/tests/test_repository_db.py`

**Interfaces:**
- Consumes: Task 1 models.
- Produces: `EvidenceRepository` methods:
  `upsert_docket_item`, `get_processable_sources`, `start_processing_run`,
  `find_successful_run`, `complete_processing_run`, `fail_processing_run`,
  `add_derived_artifact`, `add_structural_units`, `add_evidence_items`, and
  `add_candidates`.

- [ ] **Step 1: Write pgTAP RED tests**

Create one BLIND case with investigation, official, and final source rows. Set
role `casezero_processor`; assert it sees only the investigation source, cannot
read final/official rows, can insert processing/evidence rows for the visible
source, and cannot mutate source bytes. Also assert a `LINK_ONLY` docket item
has no source-document row.

Run: `supabase start && supabase test db`  
Expected: FAIL because migration 0002 and processor role do not exist.

- [ ] **Step 2: Write repository integration RED tests**

```python
run_id = await repository.start_processing_run(
    source_document_id=source_id,
    source_checksum="a" * 64,
    processor_name="fixture",
    processor_version="1.0.0",
    configuration_hash="b" * 64,
)
await repository.complete_processing_run(run_id)
assert await repository.find_successful_run(
    "a" * 64, "fixture", "1.0.0", "b" * 64
) == run_id
```

Test shared source blobs: two source documents may reference one blob checksum.
Test changed checksum for the same docket item raises a persistence conflict.

- [ ] **Step 3: Implement migration 0002**

Create:

```sql
create table public.docket_items (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.cases(id),
  title text not null, source_url text not null,
  document_type text, file_type text, page_count integer,
  published_at timestamptz, rights_status text not null,
  processing_disposition text not null, attribution text not null,
  review_note text not null, reviewed_at timestamptz not null,
  expected_checksum text check (expected_checksum is null or expected_checksum ~ '^[0-9a-f]{64}$'),
  unique(case_id, source_url)
);
create table public.source_blobs (
  checksum text primary key check (checksum ~ '^[0-9a-f]{64}$'),
  storage_path text not null unique,
  byte_size bigint not null check (byte_size >= 0)
);
alter table public.source_documents
  add column docket_item_id uuid references public.docket_items(id),
  add column blob_checksum text references public.source_blobs(checksum);
alter table public.source_documents
  drop constraint if exists source_documents_storage_path_key;
create table public.processing_runs (
  id uuid primary key default gen_random_uuid(),
  source_document_id uuid not null references public.source_documents(id),
  source_checksum text not null, processor_name text not null,
  processor_version text not null, configuration_hash text not null,
  status text not null, error_type text, error_message text,
  retryable boolean not null default false, attempt_count integer not null default 1,
  started_at timestamptz not null, completed_at timestamptz,
  unique(source_checksum, processor_name, processor_version, configuration_hash)
);
create table public.derived_artifacts (
  id uuid primary key default gen_random_uuid(),
  processing_run_id uuid not null references public.processing_runs(id),
  source_document_id uuid not null references public.source_documents(id),
  kind text not null, checksum text not null, storage_path text not null,
  media_type text not null, byte_size bigint not null,
  tool_metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null
);
create table public.structural_units (
  id uuid primary key, derived_artifact_id uuid not null references public.derived_artifacts(id),
  source_document_id uuid not null references public.source_documents(id),
  kind text not null, ordinal integer not null, content_checksum text not null,
  locator jsonb not null, payload jsonb not null,
  unique(derived_artifact_id, ordinal)
);
create table public.model_runs (
  id uuid primary key, case_id uuid not null references public.cases(id),
  parent_run_id uuid references public.model_runs(id), stage text not null,
  provider text not null, model text not null, prompt_hash text not null,
  structural_unit_ids uuid[] not null default '{}', input_tokens integer,
  output_tokens integer, latency_ms integer not null, retry_count integer not null,
  schema_failure_count integer not null, status text not null,
  created_at timestamptz not null
);
create table public.evidence_items (
  id uuid primary key, case_id uuid not null references public.cases(id),
  source_document_id uuid not null references public.source_documents(id),
  structural_unit_id uuid not null references public.structural_units(id),
  model_run_id uuid references public.model_runs(id), item jsonb not null,
  review_status text not null, created_at timestamptz not null
);
create table public.claim_candidates (
  id uuid primary key, case_id uuid not null references public.cases(id),
  model_run_id uuid not null references public.model_runs(id),
  evidence_ids uuid[] not null, candidate jsonb not null, created_at timestamptz not null
);
create table public.entity_candidates (
  id uuid primary key, case_id uuid not null references public.cases(id),
  model_run_id uuid not null references public.model_runs(id),
  evidence_ids uuid[] not null, candidate jsonb not null, created_at timestamptz not null
);
create table public.timeline_candidates (
  id uuid primary key, case_id uuid not null references public.cases(id),
  model_run_id uuid not null references public.model_runs(id),
  evidence_ids uuid[] not null, candidate jsonb not null, created_at timestamptz not null
);
```

Create `casezero_processor nologin nobypassrls`, grants, RLS policies joining
source visibility, and write policies restricted to visible source ids. Preserve
existing Phase 0 rows during migration; new rows require docket/blob references
through a staged nullable-to-required transition documented in SQL.

- [ ] **Step 4: Implement repository and verify GREEN**

Use psycopg parameterized SQL and Pydantic JSON serialization. Repository
methods never commit; caller transaction owns atomicity.

Run:

```bash
supabase db reset
supabase test db
CASEZERO_DB_TEST=1 uv run pytest packages/evidence/tests/test_repository_db.py -m db -v
```

Expected: pgTAP and Python integration tests pass.

- [ ] **Step 5: Run Loop 2 and commit**

```bash
git add supabase packages/evidence
git commit -m "feat(db): add rights-aware evidence processing ledger"
```

---

### Task 3: Derived artifact store and locator resolver

**Files:**
- Create: `packages/evidence/src/casezero_evidence/artifact_store.py`
- Create: `packages/evidence/src/casezero_evidence/locator.py`
- Test: `packages/evidence/tests/test_artifact_store.py`
- Test: `packages/evidence/tests/test_locator.py`
- Test fixtures: `packages/evidence/tests/fixtures/locator/`

**Interfaces:**
- Produces: `ArtifactStore.put(kind, data) -> StoredArtifact`,
  `ArtifactStore.get(path) -> bytes`, and
  `LocatorResolver.resolve(source_bytes, locator) -> ResolvedRegion`.

- [ ] **Step 1: Write RED tests**

Test artifact checksum/idempotency/tamper behavior. Build redistribution-safe
PDF-text, table, text, and image fixtures. Assert resolver returns exact text,
rows/columns, character spans, or cropped image dimensions. Invalid page/row/
region raises `LocatorResolutionError`.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest packages/evidence/tests/test_artifact_store.py packages/evidence/tests/test_locator.py -v`  
Expected: missing modules.

- [ ] **Step 3: Implement stores and resolver**

Reuse content-addressing behavior without coupling derived artifacts to
`SourceStore`. Resolver adapters accept parser metadata explicitly; they do not
reparse with an unrelated library. Image crops use Pillow and validate bounds.

- [ ] **Step 4: Verify GREEN**

Run the focused tests, then all evidence tests.

- [ ] **Step 5: Commit**

```bash
git add packages/evidence
git commit -m "feat(evidence): add derived artifact storage and locator resolution"
```

---

### Task 4: Curate the complete CEN22FA375 docket inventory

**Files:**
- Create: `fixtures/real-cases/cen22fa375/manifest.json`
- Create: `fixtures/real-cases/cen22fa375/retrieval.py`
- Create: `fixtures/real-cases/cen22fa375/README.md`
- Create: `packages/ntsb/src/casezero_ntsb/curation.py`
- Test: `packages/ntsb/tests/test_curation.py`

**Interfaces:**
- Produces: `validate_curated_manifest(manifest) -> CurationReport` proving all
  15 official docket entries are inventoried, every item is reviewed, eligible
  downloads have checksums, and unclear rights are `LINK_ONLY`.

- [ ] **Step 1: Write RED manifest-validation tests**

Tests reject missing review notes, unclear rights with AI/local disposition,
eligible sources without expected checksum, duplicate URLs, non-NTSB hosts,
and an inventory count other than the pinned 15 entries for this case.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest packages/ntsb/tests/test_curation.py -v`  
Expected: missing curation module/manifest.

- [ ] **Step 3: Curate and rights-review the docket**

Use the official docket only. Record all 15 item titles/URLs/types/page counts.
Classify NTSB-authored reports separately from pilot/operator/logbook/manual/
historical-photo items. Default unclear items to `LINK_ONLY`. The historical
Reddit photo must remain `THIRD_PARTY_UNCLEAR` + `LINK_ONLY`.

`retrieval.py` reads the manifest, downloads only `AI_ALLOWED`/`LOCAL_ONLY`
items with a five-second NTSB rate limit, verifies expected SHA-256, and writes
to gitignored `data/real-cases/cen22fa375/`. It prints names/status only.

- [ ] **Step 4: Execute live retrieval and verify GREEN**

Run:

```bash
CASEZERO_LIVE=1 uv run python fixtures/real-cases/cen22fa375/retrieval.py
uv run pytest packages/ntsb/tests/test_curation.py -v
```

Record retrieval date and rights limitations in the case README. Do not commit
artifacts.

- [ ] **Step 5: Commit**

```bash
git add fixtures/real-cases/cen22fa375 packages/ntsb
git commit -m "data: curate the CEN22FA375 reference docket"
```

---

### Task 5: Ingestion package and deterministic processor registry

**Files:**
- Create: `packages/ingestion/pyproject.toml`
- Create: `packages/ingestion/src/casezero_ingestion/__init__.py`
- Create: `packages/ingestion/src/casezero_ingestion/media.py`
- Create: `packages/ingestion/src/casezero_ingestion/processors.py`
- Create: `packages/ingestion/src/casezero_ingestion/registry.py`
- Modify: `pyproject.toml`
- Modify: `apps/api/pyproject.toml`
- Test: `packages/ingestion/tests/test_media.py`
- Test: `packages/ingestion/tests/test_registry.py`

**Interfaces:**
- Produces: `DetectedMediaType`, `detect_media_type(data, filename)`,
  `Processor` protocol, `StructuralOutput`, `ProcessorRegistry.select`.

- [ ] **Step 1: Write RED tests**

Use byte signatures for PDF, PNG/JPEG, CSV text, XLSX ZIP content types, HTML,
and plain text. Assert filename disagreement does not override bytes. Registry
returns one processor or raises `UnsupportedMediaError`/`AmbiguousProcessorError`.

- [ ] **Step 2: Verify RED**

Run focused tests; expect missing package.

- [ ] **Step 3: Add workspace package and implement**

```python
class Processor(Protocol):
    name: str
    version: str

    def supports(self, media_type: DetectedMediaType, document_type: DocumentType) -> bool:
        raise NotImplementedError

    def process(self, source: ProcessingSource) -> StructuralOutput:
        raise NotImplementedError
```

Detect XLSX by ZIP entries (`[Content_Types].xml`, `xl/workbook.xml`); detect
CSV only after UTF-8 text and consistent delimiter checks. No libmagic runtime
is required.

- [ ] **Step 4: Verify GREEN and workspace gate**

Run focused tests, `uv sync`, ruff, and mypy including `packages/ingestion`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock packages/ingestion apps/api/pyproject.toml
git commit -m "feat(ingestion): add deterministic media and processor registry"
```

---

### Task 6: PDF structure processor and page scan detection

**Files:**
- Create: `packages/ingestion/src/casezero_ingestion/pdf.py`
- Create: `packages/ingestion/src/casezero_ingestion/scan_detection.py`
- Test: `packages/ingestion/tests/test_pdf.py`
- Test: `packages/ingestion/tests/test_scan_detection.py`
- Fixtures: `packages/ingestion/tests/fixtures/pdf/`

**Interfaces:**
- Produces: `PdfProcessor.process -> StructuralOutput` and
  `classify_pdf_page(PageSignals) -> DIGITAL | SCAN_LIKE`.

- [ ] **Step 1: Write RED fixture tests**

Fixtures: digital text, image-only scan, and mixed PDF. Assert page, reading
order, section/table metadata, bounding boxes, and that only scan pages create
`IMAGE_PREPARATION` units. Corrupt PDF raises `StructuralProcessingError`.

- [ ] **Step 2: Verify RED**

Run PDF tests; expect missing processor.

- [ ] **Step 3: Install Docling 2.120.2 and implement**

Run `uv add --package casezero-ingestion 'docling==2.120.2'`. Adapt Docling
output into CaseZero contracts; do not expose
Docling types outside `pdf.py`. Scan classification uses explicit thresholds in
a versioned `ScanDetectionConfig` included in configuration hashing.

- [ ] **Step 4: Verify GREEN and golden determinism**

Run focused tests twice and assert serialized structural output hashes match.

- [ ] **Step 5: Commit**

```bash
git add packages/ingestion uv.lock
git commit -m "feat(ingestion): extract PDF structure and detect scanned pages"
```

---

### Task 7: CSV/XLSX structural processor

**Files:**
- Create: `packages/ingestion/src/casezero_ingestion/tables.py`
- Create: `packages/ingestion/src/casezero_ingestion/time.py`
- Test: `packages/ingestion/tests/test_tables.py`
- Test: `packages/ingestion/tests/test_time.py`
- Fixtures: `packages/ingestion/tests/fixtures/tables/`

**Interfaces:**
- Produces: `TableProcessor`, `ColumnProfile`, `TableProfile`,
  `TimestampSemantics`, `normalize_explicit_utc`, and deterministic row-group /
  time-window StructuralUnits.

- [ ] **Step 1: Write RED tests**

Create synthetic CSV/XLSX fixtures with raw strings, nulls, units, UTC/local/
relative timestamps, irregular sampling, and multiple sheets. Assert original
values, row numbers, columns, and sheets round-trip. Ambiguous local time remains
unconverted. Large data is split into bounded row groups/windows.

- [ ] **Step 2: Verify RED**

Run table/time tests; expect missing modules.

- [ ] **Step 3: Install Polars 1.43.2 and fastexcel 0.20.2 and implement**

Run `uv add --package casezero-ingestion 'polars==1.43.2' 'fastexcel==0.20.2'`.
Use lazy reads where supported. Store compact profile/window JSON; never render
every row to prose. Time conversion requires explicit timezone/offset metadata.

- [ ] **Step 4: Verify GREEN**

Run focused tests and locator resolver integration for every generated table
unit.

- [ ] **Step 5: Commit**

```bash
git add packages/ingestion uv.lock
git commit -m "feat(ingestion): profile tables and derive deterministic time windows"
```

---

### Task 8: TXT/HTML and image preparation processors

**Files:**
- Create: `packages/ingestion/src/casezero_ingestion/text.py`
- Create: `packages/ingestion/src/casezero_ingestion/images.py`
- Test: `packages/ingestion/tests/test_text.py`
- Test: `packages/ingestion/tests/test_images.py`
- Fixtures: `packages/ingestion/tests/fixtures/text/`, `packages/ingestion/tests/fixtures/images/`

**Interfaces:**
- Produces: `TextProcessor`, `ImageProcessor`, offset maps, image metadata, and
  full-image StructuralUnits. No model calls.

- [ ] **Step 1: Write RED tests**

Assert HTML normalization maps each output span to original characters; scripts
and navigation are excluded with recorded mapping. Assert PNG/JPEG dimensions,
orientation, checksum, full-image locator, and region-bound validation. Corrupt
images fail safely.

- [ ] **Step 2: Verify RED**

Run focused tests; expect missing modules.

- [ ] **Step 3: Install Pillow 12.3.0 and implement**

Run `uv add --package casezero-ingestion 'pillow==12.3.0'`. Use stdlib HTML
parsing with explicit offset maps; Pillow verifies/decode images
and applies orientation only in a derived preparation artifact while retaining
original dimensions/transform metadata.

- [ ] **Step 4: Verify GREEN**

Run text/image and locator tests.

- [ ] **Step 5: Commit**

```bash
git add packages/ingestion uv.lock
git commit -m "feat(ingestion): preserve text offsets and prepare image evidence"
```

---

### Task 9: Model-run observability and Hyperfusion/Ollama routing

**Files:**
- Create: `packages/observability/pyproject.toml`
- Create: `packages/observability/src/casezero_observability/models.py`
- Create: `packages/observability/src/casezero_observability/repository.py`
- Create: `packages/observability/src/casezero_observability/reasoning.py`
- Modify: `pyproject.toml`
- Test: `packages/observability/tests/test_reasoning.py`
- Test: `packages/observability/tests/test_repository_db.py`

**Interfaces:**
- Produces: `ReasoningRequest[T]`, `ReasoningResult[T]`,
  `ReasoningModel.generate_structured`, `PydanticReasoningModel`, `ModelRouter`,
  and `ModelRunRepository`.

- [ ] **Step 1: Write RED routing/span tests**

Fake models assert:

```python
result = await router.generate_structured(request, disposition=AI_ALLOWED)
assert primary.calls == 1 and fallback.calls == 0

primary.fail_with_schema_exhaustion()
result = await router.generate_structured(request, disposition=AI_ALLOWED)
assert fallback.calls == 1
assert result.fallback_from_run_id is not None
```

`LOCAL_ONLY` must call Ollama directly; `LINK_ONLY` raises
`ModelRoutingDenied`. Spans store hashes/ids/usage/status, never source text or
API keys.

- [ ] **Step 2: Verify RED**

Run observability tests; expect missing package.

- [ ] **Step 3: Implement package and Pydantic AI adapters**

Hyperfusion uses `OpenAIChatModel` + configured `OpenAIProvider`; Ollama uses its
OpenAI-compatible base URL and configured model id. Three validation-guided
attempts are the maximum, followed by one fallback. Prompt template hash is
SHA-256 of versioned template text; request payload is not logged.

- [ ] **Step 4: Verify GREEN**

Run fake-model tests and opt-in local Postgres repository test. No live calls.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock packages/observability
git commit -m "feat(observability): add traced Hyperfusion and Ollama model routing"
```

---

### Task 10: Semantic EvidenceItem interpreter

**Files:**
- Create: `packages/ingestion/src/casezero_ingestion/semantic.py`
- Create: `packages/ingestion/src/casezero_ingestion/prompts/evidence_v1.py`
- Test: `packages/ingestion/tests/test_semantic.py`

**Interfaces:**
- Consumes: StructuralUnits, ProcessingDisposition, ReasoningModel.
- Produces: `SemanticInterpreter.interpret(unit) -> Sequence[EvidenceItem]`.

- [ ] **Step 1: Write RED tests**

Fake structured responses test evidence/source/unit id allowlists, OBSERVED vs
INFERRED, confidence bounds, pending review for low-confidence/vision output,
empty entities, and refusal to process LINK_ONLY. Invented ids reject the whole
response and create no EvidenceItem.

- [ ] **Step 2: Verify RED**

Run semantic tests; expect missing module.

- [ ] **Step 3: Implement prompt and interpreter**

Text prompts include one bounded unit, locator, and allowed ids. Table prompts
receive profile/window only. Image schema requires conservative observation,
confidence, and optional valid region. Persist model-run id on every AI item.

- [ ] **Step 4: Verify GREEN**

Run semantic, model-router, and evidence-model tests.

- [ ] **Step 5: Commit**

```bash
git add packages/ingestion
git commit -m "feat(ingestion): interpret structural units into grounded EvidenceItems"
```

---

### Task 11: Provisional claim, entity, and timeline candidates

**Files:**
- Create: `packages/ingestion/src/casezero_ingestion/candidates.py`
- Create: `packages/ingestion/src/casezero_ingestion/prompts/candidates_v1.py`
- Test: `packages/ingestion/tests/test_candidates.py`

**Interfaces:**
- Produces: `CandidateProposer.propose(case_id, evidence_ids) -> CandidateSet`.

- [ ] **Step 1: Write RED tests**

Fake model output must use only allowed EvidenceItem ids; candidates with no
evidence reject. Timeline precision/time consistency is validated. Entity
mentions create `EntityCandidate`, not `EvidenceItem.entities`. Rerun appends a
new version tied to a new model run without mutating prior candidates.

- [ ] **Step 2: Verify RED**

Run candidate tests; expect missing module.

- [ ] **Step 3: Implement bounded case-level proposal**

Batch EvidenceItems by document/type/time to stay within context; persist a
candidate-set model run and immutable candidate rows. This stage proposes only;
it does not merge aliases, adjudicate disputes, or select causes.

- [ ] **Step 4: Verify GREEN**

Run candidate and repository tests.

- [ ] **Step 5: Commit**

```bash
git add packages/ingestion
git commit -m "feat(ingestion): propose evidence-bound investigation candidates"
```

---

### Task 12: Resumable processing orchestrator and CLI

**Files:**
- Create: `packages/ingestion/src/casezero_ingestion/orchestrator.py`
- Create: `packages/ingestion/src/casezero_ingestion/report.py`
- Create: `apps/api/src/casezero_api/process.py`
- Modify: `apps/api/src/casezero_api/cli.py`
- Create: `apps/api/tests/test_process_cli.py`

**Interfaces:**
- Produces: `ProcessingOrchestrator.process_case(case_id) -> ProcessingReport`
  and `casezero process <ntsb-number>`.

- [ ] **Step 1: Write RED orchestrator/CLI tests**

Test rights skips, unsupported KMZ, successful sibling preservation, retryable
resume, no-change idempotency, version-change reprocessing, candidate stage only
after terminal document states, report counts, and nonzero CLI exit only for
case-level fatal failure.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest apps/api/tests/test_process_cli.py -v`  
Expected: missing process command/orchestrator.

- [ ] **Step 3: Implement orchestrator and report**

Report contains inventory totals, disposition/status counts, artifact/unit/
evidence/candidate counts, review-pending count, model usage, failures, and
idempotency reuse counts. It contains no source payloads. The CLI assembles
repositories, processor registry, model router, and stores without domain logic.

- [ ] **Step 4: Verify GREEN**

Run process CLI tests plus ingestion, evidence, and observability test suites.

- [ ] **Step 5: Commit**

```bash
git add packages/ingestion apps/api
git commit -m "feat(ingestion): orchestrate resumable case processing"
```

---

### Task 13: Hyperfusion and Ollama vision capability decision

**Files:**
- Create: `scripts/probe_hyperfusion_ollama_vision.py`
- Create: `docs/decision-log/0010-vision-model-results.md`
- Test: `tests/test_vision_probe.py`

**Interfaces:**
- Produces a resumable JSON report in gitignored `benchmark/results/` and an
  evidence-backed primary/fallback vision model decision.

- [ ] **Step 1: Write RED harness tests**

Fake clients prove checkpoint resume, schema/provider-failure classification,
source-id allowlisting, locator validation, and aggregation. The harness must
never persist image bytes, prompts, or credentials in its report.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_vision_probe.py -v`  
Expected: missing probe module.

- [ ] **Step 3: Implement and execute the probe**

Use approved NTSB-authored reference images/scanned pages only. Probe available
Hyperfusion VLM ids and one configured Ollama candidate. Record schema success,
source-id integrity, locator validity, latency, provider failures, and human
grounding review. Select Hyperfusion primary only if it meets the approved gate;
otherwise record Ollama according to measured results. Never invent a pass.

- [ ] **Step 4: Verify measured report**

Run harness unit tests, lint, and a report validator that checks expected sample
count, model ids, no source payload fields, and complete human-review decisions.

- [ ] **Step 5: Commit**

```bash
git add scripts/probe_hyperfusion_ollama_vision.py tests/test_vision_probe.py docs/decision-log/0010-vision-model-results.md
git commit -m "chore(spike): select the Phase 1 vision model route"
```

---

### Task 14: Real-case Phase 1 exit and validation evidence

**Files:**
- Create: `docs/development/phase-1-validation.md`

**Interfaces:**
- Produces the measured Phase 1 acceptance record; no product behavior.

- [ ] **Step 1: Execute the full Phase 1 gate**

```bash
uv run ruff check .
uv run mypy packages/ apps/api/
uv run pytest
supabase start
supabase test db
CASEZERO_DB_TEST=1 uv run pytest -m db -v
CASEZERO_LIVE=1 uv run python fixtures/real-cases/cen22fa375/retrieval.py
CASEZERO_LIVE=1 uv run casezero process CEN22FA375
```

- [ ] **Step 2: Verify reference-case invariants**

Every eligible supported item must be successful; every link-only/excluded/
unsupported item must have a reviewed reason; every EvidenceItem locator must
resolve; the no-change rerun must reuse prior runs without duplicate state.

- [ ] **Step 3: Write measured validation evidence**

Record exact commands/results, inventory/disposition/status counts, artifact /
unit / evidence / candidate counts, model usage, review-pending items, skips,
failures, idempotency result, rights limitations, and unexecuted checks. Do not
claim extraction quality beyond measured human review.

- [ ] **Step 4: Commit**

```bash
git add docs/development/phase-1-validation.md
git commit -m "docs: record Phase 1 real-case validation evidence"
```

- [ ] **Step 5: Generate required phase explanation**

Invoke `explain-diff-html` for the commit before Task 1 through the validation
commit. Inspect the `/tmp` HTML, report its path/limitations, mark local
`task.md` Phase 1 complete, stop Supabase/background processes, and leave the
working tree clean.
