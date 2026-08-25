# Phase 1 Evidence Extraction Design

**Date:** 2026-08-25  
**Status:** Approved in chat; written spec pending owner file review  
**Source of product truth:** `product-spec.md` (local-only, never committed)  
**Depends on:** Phase 0 validation; ADRs 0003a, 0006, 0007, 0008, and 0009

## Goal and scope

Phase 1 converts approved public NTSB source artifacts into typed,
provenance-resolvable investigation inputs. It implements a two-pass pipeline:
local structural processing followed by bounded semantic interpretation. It
also creates provisional claim, entity, and timeline candidates for Phase 3 to
resolve.

The Phase 1 reference docket is `CEN22FA375`. Its full public docket is
inventoried and rights-reviewed. Processing is limited by explicit rights and
processing dispositions; completeness never justifies sending unclear-rights
material to a model.

Phase 1 includes:

- complete docket inventory and rights review for the reference case;
- PDF, scanned-page OCR, CSV/XLSX, TXT/HTML, and image processing;
- immutable versioned derived artifacts and ordered structural units;
- typed EvidenceItems with resolvable SourceLocators;
- provisional claim/entity/timeline candidates;
- processing checkpoints, idempotent reruns, model-run observability, and a
  case processing report;
- a Hyperfusion vision probe with Ollama as backup.

Phase 1 does not include:

- KMZ, audio, or video parsing;
- canonical entity resolution or cross-document claim adjudication;
- hypothesis generation, falsification, causal graphs, conclusions, locking,
  reveal, or benchmark comparison;
- semantic retrieval or embeddings;
- public redistribution of source or derived artifacts.

## Design principles

1. Deterministic structure precedes AI interpretation.
2. Original source bytes are immutable and remain authoritative.
3. Every structural unit and EvidenceItem resolves to an original source
   location.
4. Rights policy is executable routing, not documentation only.
5. AI outputs are provisional, labeled, schema-validated, and model-run
   traceable.
6. One failed unit does not erase successful sibling units.
7. Rerunning unchanged inputs does not duplicate state.
8. Official findings and post-cutoff material are unavailable to processors.
9. Default tests use no network or model provider.

## Architecture

### Package ownership

- `packages/evidence` owns rights enums, processing contracts, structural units,
  EvidenceItems, candidate types, locators, and locator resolution.
- `packages/ingestion` owns processor selection, modality processors, scan
  detection, semantic interpretation, candidate proposal, orchestration, and
  processing reports.
- `packages/observability` owns instrumented Pydantic AI model construction,
  prompt hashes, model runs, and stage/tool spans.
- `packages/ntsb` continues to own docket inventory acquisition and original
  source download only.
- `apps/api` composes repositories, stores, models, and the `casezero process`
  CLI. It contains no extraction logic.

### Two-pass flow

```text
curated docket inventory
    → rights and processing disposition
    → authoritative source bytes (eligible items only)
    → deterministic processor selection
    → processing run + derived artifact
    → ordered structural units with exact locators
    → rights-aware semantic routing
    → validated EvidenceItems
    → case-level claim/entity/timeline candidates
    → processing report
```

Processors never emit canonical investigation state. They emit structural
units. The semantic interpreter produces EvidenceItems. A separate case-level
candidate proposer uses persisted EvidenceItems and creates provisional
candidates. Phase 3 resolves, merges, disputes, or rejects those candidates.

## Docket inventory and rights

The current `source_documents` table represents retrieved bytes and requires a
checksum/storage path. It must not be weakened to represent link-only items.
Phase 1 adds a distinct `docket_items` inventory table.

### RightsStatus

- `NTSB_AUTHORED`
- `THIRD_PARTY_PERMISSION_CONFIRMED`
- `THIRD_PARTY_UNCLEAR`
- `UNKNOWN`

### ProcessingDisposition

- `AI_ALLOWED`: source may be sent to Hyperfusion; Ollama may be used as
  fallback.
- `LOCAL_ONLY`: source may be downloaded and processed locally; only Ollama may
  receive model input.
- `LINK_ONLY`: retain metadata and official link only; do not download,
  persist content, run local parsing, or send to a model.
- `EXCLUDED`: retain metadata plus an explicit exclusion reason; no processing.

`THIRD_PARTY_UNCLEAR` and `UNKNOWN` default to `LINK_ONLY`. A human rights
review is required to select a less restrictive disposition. The manifest
records reviewer note and review timestamp. No Phase 1 artifact is used for
model training.

### DocketItem fields

- id, case id, title, official source URL;
- document/media type, file type, page count, published timestamp;
- rights status, processing disposition, attribution;
- review note and reviewed timestamp;
- optional expected SHA-256 for retrievable items.

Phase 1 normalizes content-addressed originals into
`source_blobs(checksum, storage_path, byte_size)`. `source_documents` gains a
required `docket_item_id` and `blob_checksum` reference. Multiple docket items
may legally reference identical bytes without violating a unique storage-path
constraint. Checksums and storage paths remain mandatory for every downloaded
source; link-only inventory has no `source_documents` row.

## Processing ledger

### ProcessingRun

A run records:

- source document and source checksum;
- processor name and semantic version;
- normalized configuration hash;
- status and typed error;
- start/completion timestamps;
- retryability and attempt count.

Statuses:

- `PENDING`
- `RUNNING`
- `SUCCEEDED`
- `FAILED`
- `UNSUPPORTED`
- `SKIPPED_RIGHTS`

The idempotency key is source checksum + processor name/version + configuration
hash. A prior successful match is reused. A changed processor or configuration
creates a new run and derived artifact without mutating history.

### DerivedArtifact

Immutable parser/OCR/table-profile/image-preparation output:

- processing run and source document ids;
- kind: `DOCUMENT_STRUCTURE`, `OCR_TRANSCRIPT`, `TABLE_PROFILE`,
  `TIME_SERIES_PROFILE`, or `IMAGE_PREPARATION`;
- checksum, storage path, media type, byte size, created timestamp;
- tool/model version metadata where applicable.

Large derived payloads live in the configured artifact store; Postgres holds
identity, hashes, locators, status, and queryable metadata.

### StructuralUnit

An ordered unit linked to a derived artifact and original source:

- kind: `TEXT_BLOCK`, `TABLE`, `TABLE_ROW_GROUP`, `TIME_SERIES_WINDOW`, or
  `IMAGE`;
- ordinal and content checksum;
- typed source locator;
- normalized text or compact JSON payload;
- deterministic metadata (columns, units, dimensions, row range, language);
- extraction method and confidence only when AI created the artifact (OCR).

## Locators and resolver contract

Existing locators remain discriminated Pydantic unions and gain the minimum
fields needed to resolve real parser output:

- PDF: page, optional section/paragraph, optional normalized bounding box,
  reading-order index;
- table: sheet, 1-based row range, exact columns;
- image: image id, pixel dimensions, optional validated pixel bounding box;
- text: original character start/end;
- audio remains defined but unused in Phase 1.

Bounding boxes require ordered coordinates within the known page/image bounds.
A full-image locator is valid; a model is never forced to invent a region.

`LocatorResolver.resolve(source, locator)` returns the exact referenced
text/rows/region or raises a typed resolution error. The provenance gate runs
this resolver for every fixture EvidenceItem and every persisted reference-case
EvidenceItem.

## Modality processors

All processors implement one deterministic contract:

```python
class Processor(Protocol):
    name: str
    version: str

    def supports(self, media_type: str, document_type: DocumentType) -> bool: ...
    def process(self, source: StoredSource) -> StructuralOutput: ...
```

`ProcessorRegistry` rejects ambiguous or unsupported selection. Media type is
validated from bytes; filename alone is not authoritative.

### Digital PDF

Docling (MIT) performs local layout, reading-order, section, text, and table
extraction. Page identity and geometry are preserved. Embedded images are
represented as image structural units linked to their PDF page.

### Scanned PDF and OCR

Scan detection is deterministic and page-specific. It uses extracted-character
count, text/font presence, and image coverage. Digital pages never route to
OCR. Scan-like pages are rendered locally, recorded as image-preparation
artifacts, and routed according to processing disposition.

OCR output is a derived transcript with page/bounding-box provenance and the
model/tool version. OCR text does not replace the original page. Low-confidence
transcription is flagged for review.

### CSV and XLSX

Polars performs local parsing and type/profile inference. The processor:

- preserves raw values, row numbers, sheet names, and original column names;
- records inferred type, null count, range, unit hints, and timestamp candidates;
- normalizes timestamps only when the conversion is mathematically explicit;
- keeps UTC, local, and recorder-relative semantics distinct;
- derives deterministic row groups, windows, and event candidates;
- sends only compact approved windows—not full large tables—to a model.

The `CEN22FA375` XLSX and CSV items exercise this path when rights review permits
processing. KMZ remains explicitly unsupported.

### TXT and HTML

Local parsing produces normalized text while preserving mapping to original
character offsets. Navigation/chrome removal may create a derived artifact but
must not destroy the original mapping.

### Images

Local processing records media type, dimensions, checksum, and orientation.
Approved images receive conservative structured observations. Observations are
`INFERRED`, carry confidence, cite the image/full-image or validated region
locator, and are persistently labeled AI-generated. They are not forensic
facts or official findings.

## Semantic interpretation and candidates

### Text semantic model

`qwen/qwen3-32b` is the initial Hyperfusion model for approved text units based
on ADR 0003a. Pydantic AI validates output schemas. Prompts receive bounded
structural units plus source ids/locators, never an entire docket dump.

### Vision model policy

Phase 1 probes account-available Hyperfusion VLMs and an Ollama vision candidate
using approved NTSB-authored images/scanned pages. A model is eligible when the
probe demonstrates:

- at least 95% final schema-valid completion;
- zero invented source ids;
- valid source locators;
- documented human review of evidence grounding.

If an eligible Hyperfusion model exists, it is primary. Ollama is fallback on
provider or final schema failure. `LOCAL_ONLY` routes only to Ollama. Each
attempt creates a separate model-run span. A valid low-confidence result is
flagged for review rather than retried until it appears confident.

Model output receives at most three validation-guided attempts, followed by one
permitted fallback attempt. Exhaustion creates a retryable or terminal unit
failure; it never fabricates an EvidenceItem.

### EvidenceItems

Persisted EvidenceItems add:

- structural unit id;
- optional model run id;
- review status: `NOT_REQUIRED`, `PENDING`, `ACCEPTED`, or `REJECTED`.

The existing case/source/type/observation/locator/extraction/confidence fields
remain. Phase 1 leaves `EvidenceItem.entities` empty: an unresolved mention must
not masquerade as a canonical `EntityReference`. The candidate proposer stores
entity mentions in `EntityCandidate` records linked to evidence. Phase 3 fills
canonical entity references after resolution.

### Candidate types

- `ClaimCandidate`: text, OBSERVED/INFERRED/DISPUTED/UNKNOWN status, supporting
  and contradicting evidence ids, confidence, model run.
- `EntityCandidate`: type, proposed canonical name, aliases, evidence ids,
  confidence, model run.
- `TimelineCandidate`: occurred time when known, precision
  EXACT/APPROXIMATE/RELATIVE/UNKNOWN, description, evidence ids, confidence,
  model run.

Candidates are append-only provisional records. Phase 3 is the only subsystem
that promotes them into canonical claims, entities, and timeline events.

## Orchestration and checkpointing

`casezero process <ntsb-number>` processes the current eligible evidence set.
The orchestrator:

1. persists or refreshes docket inventory;
2. applies rights dispositions;
3. creates `SKIPPED_RIGHTS`/`UNSUPPORTED` runs where applicable;
4. selects or reuses deterministic structural runs;
5. writes immutable derived artifacts and structural units;
6. semantically interprets eligible units;
7. creates candidates after all eligible documents reach terminal state;
8. writes a case processing report.

A failed unit does not roll back successful siblings. Resume dispatches only
pending/retryable failures. No-change reruns reuse successful idempotency keys.
Processor/config changes create new versions while preserving prior artifacts.

## Blindness, security, and observability

Phase 1 adds a `casezero_processor` database role. It may read only source rows
whose visibility is `INVESTIGATION_EVIDENCE`, read approved docket inventory,
and write processing/evidence/candidate/model-run tables. It cannot read
`OFFICIAL_ANALYSIS` or `FINAL_FINDING` and has no Context.dev or unrestricted
web-search capability.

Rights disposition is checked before source retrieval and again before each
model call. Hyperfusion receives only `AI_ALLOWED` payloads; Ollama receives
`AI_ALLOWED` or `LOCAL_ONLY`. `LINK_ONLY` content never enters stores, parser
payloads, prompts, logs, fixtures, or public artifacts.

Model telemetry records provider/model, stage, prompt-template hash, structural
unit ids, tokens when reported, latency, retries, schema failures, fallback
relationship, and result status. It does not record source text, third-party
payloads, chain-of-thought, or secrets. Validated derived output is persisted as
product state; prompt templates remain versioned in git.

Official material and AI outputs remain separate. Public-facing outputs are
outside this phase, but all generated observations carry the labels and source
links required by README legal/public-communication guardrails.

## Error model

Typed failures include:

- unsupported media/processor;
- rights skip;
- source checksum or locator mismatch;
- corrupt parser input;
- structural extraction failure;
- OCR/semantic provider failure;
- schema validation exhaustion;
- artifact-store failure;
- persistence conflict.

Failures record stage, source/unit id, retryability, attempt count, and safe
message. Source contents and secrets are not included. Structural failure blocks
semantics only for the affected unit/document. Every skip and failure appears in
the processing report.

## Testing strategy

### Offline processor fixtures

Synthetic, redistribution-safe fixtures cover:

- digital, scanned, and mixed PDFs;
- PDF reading order, tables, embedded images, malformed pages;
- CSV/XLSX typed columns, nulls, irregular timestamps, relative time;
- HTML/TXT offset mapping;
- image dimensions and valid/invalid regions;
- corrupt and unsupported inputs.

Golden files contain deterministic structural units and locators, not generated
prose. Updating a golden requires an intentional processor-version change and
reviewed diff.

### Invariants

Tests prove:

- every locator resolves to original fixture/reference source;
- only scan-like pages route to OCR;
- raw table values and rows remain recoverable;
- `LINK_ONLY` is neither downloaded nor modeled;
- `LOCAL_ONLY` never reaches Hyperfusion;
- model outputs cannot invent source/evidence ids;
- repeated identical runs create no duplicates;
- changed processor/config creates a new immutable artifact version;
- unit failures preserve successful siblings and resume correctly;
- processor role cannot read official/final source rows.

### Model tests and live probes

Default tests use Pydantic AI test models or fake provider responses for schema
retry, fallback, low-confidence review, candidates, and spans. Live
Hyperfusion/Ollama probes are explicit opt-in tests and publish measured reports.
No ordinary CI test makes paid or network calls.

## Reference-case curation

The complete public `CEN22FA375` docket is inventoried before processor work.
Each item receives a rights status, disposition, attribution, and review note.
Eligible retrievable sources receive pinned checksums and a retrieval script.
Unclear-rights items remain official links only. NTSB-authored examination and
factual reports form the minimum real semantic set; approved table/image items
exercise the other processors. The historical Reddit image is not treated as
NTSB-authored merely because it appears in the docket.

## Phase 1 exit criteria

Phase 1 is complete only when:

1. all `CEN22FA375` docket items are inventoried and rights-reviewed;
2. every supported `AI_ALLOWED`/`LOCAL_ONLY` source reaches a successful
   terminal processing state;
3. every `LINK_ONLY`, `EXCLUDED`, and `UNSUPPORTED` item has a reviewed reason;
4. every persisted EvidenceItem resolves to its original source;
5. claim/entity/timeline candidates cite EvidenceItems and remain provisional;
6. digital PDF pages do not route through OCR;
7. table output preserves original values, rows, columns, sheets, and time
   semantics;
8. image observations are AI-labeled and pass sampled human grounding review;
9. every model operation has a model-run record;
10. no-change rerun is idempotent and version change preserves history;
11. all failures/skips appear in the case processing report;
12. offline lint/type/test gates, database/RLS tests, live vision probe, and the
    one-real-case processing report pass;
13. the Phase 1 `explain-diff-html` artifact is generated and inspected.

“Fully processed” means all supported and rights-eligible material, not every
publicly listed item regardless of rights or supported modality. Phase 1 proves
trustworthy extraction and provenance. It does not claim causal accuracy.

## Explicitly deferred

- live Context.dev comparison occurs after curated reference manifests and
  before the Phase 7 go/no-go (ADR 0009);
- semantic retrieval and embedding model decision D2 occur in Phase 3;
- KMZ and audio processors require separate accepted requirements;
- hosted Supabase and public correction/removal UI are deployment/UI work;
- model quality, citation faithfulness, contradiction discovery, and causal
  accuracy are evaluated in later phases.
