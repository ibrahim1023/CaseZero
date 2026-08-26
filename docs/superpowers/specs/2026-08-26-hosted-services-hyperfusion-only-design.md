# Hosted Services and Hyperfusion-Only Runtime Design

**Date:** 2026-08-26  
**Status:** Approved in chat; written spec pending owner file review  
**Scope:** Replace local durable services and Ollama routing without changing
CaseZero evidence, provenance, blindness, or rights contracts.

## Motivation

CaseZero currently uses hosted external acquisition/model APIs but persists its
database, source artifacts, and derived artifacts locally. The runtime also
uses Ollama as a fallback and as the `LOCAL_ONLY` semantic route. The owner has
chosen a free hosted-data/services setup and Hyperfusion for all LLM-related
testing and execution.

This design moves durable data to Supabase Free Postgres and private Supabase
Storage while keeping development-time Python/Docling/Polars execution on the
local machine. It removes Ollama from runtime and tests. Sources marked
`LOCAL_ONLY` are skipped for now; they are not downloaded, uploaded, parsed, or
sent to Hyperfusion.

Context.dev and Hyperfusion credits are available. Context.dev remains an
acquisition/discovery integration and is never exposed to blind investigation
workers.

## Current-state map

### Hosted/external

- Hyperfusion OpenAI-compatible text and vision inference;
- NTSB CAROL/developer APIs and docket artifact servers;
- Context.dev client (contract-tested, live comparison still pending);
- GitHub Actions for offline checks.

### Local

- Supabase CLI Docker stack for Postgres, RLS, migrations, and pgTAP;
- `LocalSourceStore` under `data/sources`;
- `LocalArtifactStore` under `data/artifacts`;
- curated downloads under `data/real-cases`;
- CLI/API/Docling/Polars/Pillow execution;
- Ollama fallback and `LOCAL_ONLY` semantic route.

## Target architecture

### Hosted Supabase

One Supabase Free development project becomes the only runtime persistence
platform for the current project stage:

- hosted Postgres for cases, inventory, provenance, processing ledger,
  EvidenceItems, candidates, and model runs;
- private `casezero-sources` bucket for authoritative source bytes;
- private `casezero-derived` bucket for parser/OCR/table/image artifacts.

Before public deployment, a separate production project is created. Tests and
development tooling must fail closed when configured with a
production-designated project.

### Local compute, hosted state

The CLI and document-processing worker may run on the developer machine during
Phases 1–7. Docling, Polars, fastexcel, and Pillow remain local compute
libraries, but all durable source/derived bytes and database state are hosted.
No runtime behavior depends on one machine's filesystem after an operation
commits.

Local filesystem stores remain available only as injected test fixtures. The
runtime composition layer cannot select them when hosted configuration is
required.

### Hyperfusion-only model boundary

All LLM/VLM execution uses Hyperfusion:

- text: `qwen/qwen3-32b` initially;
- vision: `google/gemma-4-31b-it` initially;
- future fallback may use another measured Hyperfusion model, never an
  unapproved provider.

Ollama dependencies, configuration, runtime construction, prompted-output
special cases, tests, and documentation are removed. A Hyperfusion failure is
recorded as retryable/terminal according to the existing bounded retry policy;
it is never silently routed to another vendor.

### Rights routing

- `AI_ALLOWED`: retrieve to private hosted Storage, process, and send bounded
  units to Hyperfusion.
- `LOCAL_ONLY`: audited skip for now; no download, upload, parsing, or model
  call.
- `LINK_ONLY`: audited skip; inventory and official link only.
- `EXCLUDED`: audited skip with reason.

`LOCAL_ONLY` is not automatically reclassified to `AI_ALLOWED`. Enabling it
later requires an explicit rights/privacy decision and a new approved route.

## Storage design

### Buckets and object keys

Both buckets are private.

```text
casezero-sources/sources/<sha256[0:2]>/<sha256>
casezero-derived/derived/<artifact-kind>/<sha256[0:2]>/<sha256>
```

Object keys contain no case title, person name, URL, or secret. Postgres stores
case/document relationships, checksums, byte size, attribution, rights,
visibility, processor version, and model lineage.

### Store interfaces

Existing store protocols remain consumers' boundary:

```python
class SourceStore(Protocol):
    def put(self, case_id: UUID, filename: str, data: bytes) -> StoredSource: ...
    def get(self, storage_path: Path) -> bytes: ...

class ArtifactStore(Protocol):
    def put(self, kind: DerivedArtifactKind, data: bytes) -> StoredArtifact: ...
    def get(self, storage_path: Path) -> bytes: ...
```

Hosted implementations use Supabase Storage and preserve the same
content-addressed return contracts. Interface paths are logical POSIX object
keys, not local filesystem paths.

### Upload consistency

For each object:

1. compute SHA-256 and byte size locally;
2. derive the immutable object key;
3. upload with no overwrite;
4. verify remote object metadata/content length and checksum metadata;
5. insert/update the Postgres blob/artifact row in the caller transaction.

If the object already exists, checksum/size metadata must match. Any mismatch
is an integrity conflict. A successful object upload followed by DB failure is
safe orphan data and may be reconciled; a DB row is never committed before
object verification.

No automatic retry may overwrite an existing object or alter rights.

## Database and migration deployment

The current migrations remain the schema source of truth. Deployment uses a
linked Supabase project and checked-in migrations; the local validation rows are
not migrated.

The hosted setup sequence is:

1. owner creates/selects a Supabase Free project;
2. configure project reference/access token outside git;
3. link CLI to the project;
4. run migration lint/diff review;
5. push migrations;
6. create private buckets and policies;
7. run hosted schema/RLS verification;
8. re-ingest approved NTSB sources and process the reference case.

Custom `casezero_blind`, `casezero_eval`, and `casezero_processor` roles/policies
remain required. Hosted verification checks every expected table, forced RLS,
role visibility, and grants.

## Configuration and secrets

Runtime requires:

- `DATABASE_URL`: hosted direct/pooler connection suitable for psycopg;
- `SUPABASE_URL`;
- `SUPABASE_SERVICE_ROLE_KEY` (backend only);
- `SUPABASE_SOURCE_BUCKET=casezero-sources`;
- `SUPABASE_DERIVED_BUCKET=casezero-derived`;
- `HYPERFUSION_API_KEY` and `HYPERFUSION_BASE_URL`;
- `CASEZERO_TEXT_MODEL=qwen/qwen3-32b`;
- `CASEZERO_VISION_MODEL=google/gemma-4-31b-it`.

Migration/deployment additionally requires a Supabase project reference and
access token. Secret values never enter chat, git, logs, reports, frontend code,
or model prompts. The service-role key is not exposed to Next.js.

All `OLLAMA_*` settings are removed.

## Context.dev

Available credits allow a live integration check after the curated reference
manifest:

- run Context.dev against at least one curated docket;
- validate its schema output;
- compare omissions/extras/URLs against the curated reference;
- have CaseZero independently download approved originals to Supabase Storage;
- preserve hashes/provenance;
- verify blind tools have no Context.dev/web capability.

Context.dev never becomes an evidence store or reasoning provider. Its live
acceptance remains required before the Phase 7 go/no-go, not before hosted
Supabase migration.

## Testing strategy

### Default offline gate

Remains fully network-free:

- in-memory/fake SourceStore and ArtifactStore;
- mocked Supabase Storage HTTP responses;
- fake Pydantic AI/Hyperfusion responses;
- no Docker, hosted Supabase, NTSB, Context.dev, or paid calls.

### Hosted integration gate

Hosted integration tests require `CASEZERO_HOSTED_TEST=1` and a dedicated
non-production project/connection. They use unique case IDs and rollback DB
transactions. Storage tests use unique content-addressed fixtures and delete
only objects created by that test after DB rollback.

A project marker (environment plus database setting) must identify the target as
non-production before any hosted integration test runs.

Hosted checks replace local Supabase as the required environment gate:

- migrations applied at expected versions;
- schema/RLS/grants match;
- private buckets and policies exist;
- service-role upload/read works;
- anonymous/public bucket access fails;
- content-address idempotency and conflicts work;
- processor role cannot read official/final sources;
- Hyperfusion-only routing creates model-run records;
- restricted dispositions create skips and no objects/model calls.

The local Supabase Docker stack and `supabase start/stop/test db` are removed
from the normal verification workflow. SQL tests are executed against the
explicit hosted test target in rollback-safe form.

## Failure handling

- Hosted DB unavailable: no processing starts; report infrastructure failure.
- Storage upload unavailable: source/artifact remains uncommitted and retryable.
- Object exists with mismatched metadata: integrity conflict, no overwrite.
- Hyperfusion unavailable/schema-exhausted: record failed model run; no provider
  fallback.
- `LOCAL_ONLY`/`LINK_ONLY`/`EXCLUDED`: deterministic audited skip, not an error.
- Context.dev unavailable: curated manifests remain valid acquisition input.
- Free-tier limit approached: report stored bytes/counts; do not silently evict.

## Data migration and Phase 1 acceptance

Temporary local database rows are discarded. Approved source bytes are
re-retrieved or verified from the curated checksums and uploaded to hosted
Storage. Derived artifacts are regenerated under versioned processors.

The interrupted local Phase 1 run is evidence for debugging only and is not the
final acceptance run. Phase 1 exits after a fresh hosted run demonstrates:

1. all migrations and private buckets verified;
2. three `AI_ALLOWED` reference sources stored/processed;
3. the `LOCAL_ONLY` medical report and eleven other restricted items audited as
   skipped with no objects/model calls;
4. every eligible structural unit reaches a terminal semantic result or an
   explicitly reported bounded Hyperfusion failure;
5. EvidenceItems/candidates/model runs preserve provenance;
6. no-change hosted rerun is idempotent;
7. offline and hosted integration gates pass;
8. validation evidence and required `explain-diff-html` artifact are complete.

## Explicit non-goals

- deploying API/worker compute to a cloud runtime in this migration;
- exposing Storage objects publicly;
- copying temporary local DB rows;
- retaining Ollama as dormant fallback code;
- processing `LOCAL_ONLY` through Hyperfusion;
- using Context.dev in blind reasoning;
- introducing a second database or object-storage provider now.
