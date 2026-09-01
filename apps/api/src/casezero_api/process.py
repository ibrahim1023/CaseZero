import asyncio
import hashlib
import sys
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

import httpx
from casezero_evidence import (
    ClaimCandidate,
    DocketItem,
    DocumentType,
    EntityCandidate,
    EvidenceItem,
    ProcessingDisposition,
    ProcessingSource,
    ReviewStatus,
    SourceDocument,
    StructuralUnit,
    TimelineCandidate,
    Visibility,
)
from casezero_evidence.repository import EvidenceRepository, StoredSourceRecord
from casezero_evidence.source_store import SourceStore, StoreIntegrityError
from casezero_evidence.supabase_store import SupabaseArtifactStore, SupabaseSourceStore
from casezero_ingestion.candidates import CANDIDATE_PROMPT_TEMPLATE, CandidateProposer
from casezero_ingestion.images import ImageProcessor
from casezero_ingestion.orchestrator import (
    ProcessingOrchestrator,
    StructuralProcessingResult,
)
from casezero_ingestion.pdf import DoclingPdfAdapter, PdfProcessor
from casezero_ingestion.registry import ProcessorRegistry
from casezero_ingestion.report import ProcessingFailure, ProcessingReport
from casezero_ingestion.semantic import EVIDENCE_PROMPT_TEMPLATE, SemanticInterpreter
from casezero_ingestion.tables import TableProcessor
from casezero_ingestion.text import TextProcessor
from casezero_ntsb.curation import (
    CuratedCaseManifest,
    CuratedDocketItem,
    load_curated_manifest,
    validate_curated_manifest,
)
from casezero_ntsb.visibility import classify_visibility
from casezero_observability import (
    ModelFailure,
    ModelRouter,
    ModelRoutingDenied,
    PydanticReasoningModel,
)

from casezero_api.session import RuntimeRole, role_scoped_connection
from casezero_api.settings import HostedSettings

EVIDENCE_PROMPT_HASH = hashlib.sha256(EVIDENCE_PROMPT_TEMPLATE.encode()).hexdigest()
CANDIDATE_PROMPT_HASH = hashlib.sha256(CANDIDATE_PROMPT_TEMPLATE.encode()).hexdigest()


class ProcessCaseError(RuntimeError):
    pass


class CaseProcessingRepository(Protocol):
    async def get_case_id(self, ntsb_number: str) -> UUID | None: ...

    async def upsert_docket_item(self, item: DocketItem) -> UUID: ...

    async def record_processing_skip(
        self, docket_item_id: UUID, status: str, reason: str, created_at: datetime
    ) -> None: ...

    async def get_source_document(
        self, case_id: UUID, source_url: str
    ) -> StoredSourceRecord | None: ...

    async def link_source_document(
        self,
        document: SourceDocument,
        docket_item_id: UUID,
        storage_path: str,
        byte_size: int,
    ) -> None: ...

    async def has_successful_model_run(
        self,
        case_id: UUID,
        stage: str,
        structural_unit_ids: tuple[UUID, ...],
    ) -> bool: ...

    async def has_completed_semantic_unit(
        self, structural_unit_id: UUID, prompt_hash: str
    ) -> bool: ...

    async def complete_semantic_unit(
        self,
        structural_unit_id: UUID,
        evidence_count: int,
        prompt_hash: str,
        completed_at: datetime,
    ) -> None: ...

    async def persist_semantic_result(
        self,
        structural_unit_id: UUID,
        items: tuple[EvidenceItem, ...],
        prompt_hash: str,
        completed_at: datetime,
    ) -> None: ...

    async def has_completed_candidate_batch(
        self, case_id: UUID, batch_hash: str, prompt_hash: str
    ) -> bool: ...

    async def get_evidence_items_for_unit(
        self, structural_unit_id: UUID
    ) -> tuple[EvidenceItem, ...]: ...

    async def get_completed_evidence_items(
        self, structural_unit_id: UUID, prompt_hash: str
    ) -> tuple[EvidenceItem, ...]: ...

    async def add_evidence_items(
        self, items: tuple[EvidenceItem, ...], created_at: datetime
    ) -> None: ...

    async def add_candidates(
        self,
        candidates: tuple[ClaimCandidate | EntityCandidate | TimelineCandidate, ...],
    ) -> None: ...

    async def persist_candidate_batch(
        self,
        case_id: UUID,
        structural_unit_ids: tuple[UUID, ...],
        batch_hash: str,
        prompt_hash: str,
        candidates: tuple[ClaimCandidate | EntityCandidate | TimelineCandidate, ...],
        completed_at: datetime,
    ) -> None: ...

    async def get_model_usage(self, run_ids: tuple[UUID, ...]) -> dict[str, int]: ...


class StructuralOrchestrator(Protocol):
    async def process_with_units(
        self, sources: tuple[ProcessingSource, ...]
    ) -> StructuralProcessingResult: ...


class EvidenceInterpreter(Protocol):
    async def interpret(
        self,
        case_id: UUID,
        unit: StructuralUnit,
        disposition: ProcessingDisposition,
    ) -> tuple[EvidenceItem, ...]: ...


class EvidenceCandidateProposer(Protocol):
    async def propose(
        self,
        case_id: UUID,
        evidence: tuple[EvidenceItem, ...],
        disposition: ProcessingDisposition,
        created_at: datetime,
    ) -> tuple[ClaimCandidate | EntityCandidate | TimelineCandidate, ...]: ...


def _candidate_batches(
    evidence: tuple[EvidenceItem, ...], max_items: int
) -> tuple[tuple[EvidenceItem, ...], ...]:
    groups: list[list[EvidenceItem]] = []
    group_keys: dict[UUID, int] = {}
    for item in evidence:
        key = item.structural_unit_id or item.id
        if key not in group_keys:
            group_keys[key] = len(groups)
            groups.append([])
        groups[group_keys[key]].append(item)

    batches: list[tuple[EvidenceItem, ...]] = []
    current: list[EvidenceItem] = []
    for group in groups:
        if current and len(current) + len(group) > max_items:
            batches.append(tuple(current))
            current = []
        current.extend(group)
    if current:
        batches.append(tuple(current))
    return tuple(batches)


def _candidate_batch_hash(evidence: tuple[EvidenceItem, ...]) -> str:
    identifiers = "\n".join(sorted(str(item.id) for item in evidence))
    return hashlib.sha256(identifiers.encode()).hexdigest()


class CaseProcessingService:
    def __init__(
        self,
        *,
        repository: CaseProcessingRepository,
        source_store: SourceStore,
        structural_orchestrator: StructuralOrchestrator,
        semantic_interpreter: EvidenceInterpreter,
        candidate_proposer: EvidenceCandidateProposer,
        now: Callable[[], datetime] | None = None,
        semantic_concurrency: int = 4,
        candidate_concurrency: int = 4,
        candidate_batch_size: int = 100,
        on_semantic_progress: Callable[[int, int], None] | None = None,
        on_candidate_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        self._repository = repository
        self._source_store = source_store
        self._structural_orchestrator = structural_orchestrator
        self._semantic_interpreter = semantic_interpreter
        if semantic_concurrency < 1 or candidate_concurrency < 1:
            raise ValueError("model concurrency must be at least 1")
        if candidate_batch_size < 1:
            raise ValueError("candidate_batch_size must be at least 1")
        self._candidate_proposer = candidate_proposer
        self._now = now or (lambda: datetime.now(UTC))
        self._semantic_concurrency = semantic_concurrency
        self._candidate_concurrency = candidate_concurrency
        self._candidate_batch_size = candidate_batch_size
        self._on_semantic_progress = on_semantic_progress or (lambda done, total: None)
        self._on_candidate_progress = on_candidate_progress or (lambda done, total: None)

    async def process_case(
        self,
        ntsb_number: str,
        manifest: CuratedCaseManifest,
        downloads_root: Path | None,
    ) -> ProcessingReport:
        if manifest.case_id != ntsb_number:
            raise ProcessCaseError("curated manifest caseId does not match requested case")
        curation = validate_curated_manifest(manifest)
        if not curation.valid:
            raise ProcessCaseError("; ".join(curation.errors))
        case_id = await self._repository.get_case_id(ntsb_number)
        if case_id is None:
            raise ProcessCaseError(
                f"case {ntsb_number} does not exist; ingest case metadata before processing"
            )

        dispositions: Counter[str] = Counter()
        statuses: Counter[str] = Counter()
        failures: list[ProcessingFailure] = []
        sources: list[ProcessingSource] = []
        source_dispositions: dict[UUID, ProcessingDisposition] = {}

        for index, curated_item in enumerate(manifest.items, start=1):
            docket_item = _docket_item(case_id, curated_item)
            docket_item_id = await self._repository.upsert_docket_item(docket_item)
            disposition = curated_item.processing_disposition
            dispositions[disposition.value] += 1
            if disposition in {
                ProcessingDisposition.LOCAL_ONLY,
                ProcessingDisposition.LINK_ONLY,
                ProcessingDisposition.EXCLUDED,
            }:
                existing = await self._repository.get_source_document(
                    case_id, str(curated_item.source_url)
                )
                if existing is not None:
                    raise ProcessCaseError(
                        f"rights-skipped item already has stored source: {curated_item.title}"
                    )
                await self._repository.record_processing_skip(
                    docket_item_id,
                    "SKIPPED_RIGHTS",
                    f"{disposition.value}: {curated_item.review_note}",
                    self._now(),
                )
                statuses["SKIPPED_RIGHTS"] += 1
                continue
            visibility = classify_visibility(
                curated_item.document_type.value if curated_item.document_type else None,
                curated_item.title,
                curated_item.published_at,
                manifest.blind_cutoff,
            )
            if visibility is not Visibility.INVESTIGATION_EVIDENCE:
                await self._repository.record_processing_skip(
                    docket_item_id,
                    "SKIPPED_VISIBILITY",
                    f"{visibility.value}: blocked by blind cutoff classifier",
                    self._now(),
                )
                statuses["SKIPPED_VISIBILITY"] += 1
                continue
            try:
                source = await self._materialize_source(
                    case_id,
                    index,
                    curated_item,
                    docket_item,
                    docket_item_id,
                    visibility,
                    downloads_root,
                )
            except (OSError, StoreIntegrityError, ValueError) as error:
                statuses["FAILED"] += 1
                failures.append(
                    ProcessingFailure(
                        stage="source",
                        error_type=type(error).__name__,
                        message=str(error),
                        retryable=isinstance(error, OSError),
                    )
                )
                continue
            sources.append(source)
            source_dispositions[source.document.id] = disposition

        structural = await self._structural_orchestrator.process_with_units(tuple(sources))
        statuses.update(structural.report.status_counts)
        failures.extend(structural.report.failures)
        if not _all_documents_terminal(statuses):
            failures.append(
                ProcessingFailure(
                    stage="candidates",
                    error_type="NonTerminalDocuments",
                    message="candidate stage deferred until all eligible documents are terminal",
                    retryable=True,
                )
            )
            return _compose_report(
                manifest,
                dispositions,
                statuses,
                structural.report,
                failures,
            )

        evidence: list[EvidenceItem] = []
        new_evidence: list[EvidenceItem] = []
        persistence_lock = asyncio.Lock()
        model_run_ids: list[UUID] = []
        reused_semantic = 0
        pending_units: list[StructuralUnit] = []
        for unit in structural.units:
            if await self._repository.has_completed_semantic_unit(
                unit.id, EVIDENCE_PROMPT_HASH
            ):
                reused_semantic += 1
                evidence.extend(
                    await self._repository.get_completed_evidence_items(
                        unit.id, EVIDENCE_PROMPT_HASH
                    )
                )
                continue
            pending_units.append(unit)

        semaphore = asyncio.Semaphore(self._semantic_concurrency)
        completed_units = 0

        async def interpret_one(
            unit: StructuralUnit,
        ) -> tuple[tuple[EvidenceItem, ...], ProcessingFailure | None]:
            nonlocal completed_units
            disposition = source_dispositions[unit.source_document_id]
            try:
                async with semaphore:
                    interpreted = await self._semantic_interpreter.interpret(
                        case_id, unit, disposition
                    )
                completed_at = self._now()
                async with persistence_lock:
                    await self._repository.persist_semantic_result(
                        unit.id, interpreted, EVIDENCE_PROMPT_HASH, completed_at
                    )
                return interpreted, None
            except (ModelFailure, ModelRoutingDenied, ValueError) as error:
                return (), ProcessingFailure(
                    stage="semantic",
                    source_document_id=unit.source_document_id,
                    structural_unit_id=unit.id,
                    error_type=type(error).__name__,
                    message=str(error),
                    retryable=isinstance(error, ModelFailure),
                )
            finally:
                completed_units += 1
                self._on_semantic_progress(completed_units, len(pending_units))

        semantic_results = await asyncio.gather(
            *(interpret_one(unit) for unit in pending_units)
        )
        for interpreted, failure in semantic_results:
            if failure is not None:
                failures.append(failure)
                continue
            evidence.extend(interpreted)
            new_evidence.extend(interpreted)
            model_run_ids.extend(
                item.model_run_id for item in interpreted if item.model_run_id is not None
            )

        if any(failure.stage == "semantic" for failure in failures):
            failures.append(
                ProcessingFailure(
                    stage="candidates",
                    error_type="Deferred",
                    message="candidate stage deferred until all semantic units succeed",
                    retryable=True,
                )
            )
            model_usage = await self._repository.get_model_usage(tuple(model_run_ids))
            return _compose_report(
                manifest,
                dispositions,
                statuses,
                structural.report,
                failures,
                evidence_items=len(new_evidence),
                review_pending=sum(
                    item.review_status is ReviewStatus.PENDING for item in new_evidence
                ),
                model_usage=model_usage,
                reused_semantic_runs=reused_semantic,
            )

        new_candidates: list[
            ClaimCandidate | EntityCandidate | TimelineCandidate
        ] = []
        reused_candidates = 0
        pending_candidate_batches: list[
            tuple[tuple[EvidenceItem, ...], tuple[UUID, ...], str]
        ] = []
        for batch in _candidate_batches(tuple(evidence), self._candidate_batch_size):
            candidate_unit_ids = tuple(
                dict.fromkeys(
                    item.structural_unit_id
                    for item in batch
                    if item.structural_unit_id is not None
                )
            )
            batch_hash = _candidate_batch_hash(batch)
            if await self._repository.has_completed_candidate_batch(
                case_id, batch_hash, CANDIDATE_PROMPT_HASH
            ):
                reused_candidates += 1
            else:
                pending_candidate_batches.append(
                    (batch, candidate_unit_ids, batch_hash)
                )

        candidate_semaphore = asyncio.Semaphore(self._candidate_concurrency)
        completed_candidate_batches = 0

        async def propose_batch(
            pending: tuple[tuple[EvidenceItem, ...], tuple[UUID, ...], str],
        ) -> tuple[
            tuple[ClaimCandidate | EntityCandidate | TimelineCandidate, ...],
            ProcessingFailure | None,
        ]:
            nonlocal completed_candidate_batches
            batch, structural_unit_ids, batch_hash = pending
            disposition = (
                ProcessingDisposition.LOCAL_ONLY
                if any(
                    source_dispositions[item.source_document_id]
                    is ProcessingDisposition.LOCAL_ONLY
                    for item in batch
                )
                else ProcessingDisposition.AI_ALLOWED
            )
            try:
                async with candidate_semaphore:
                    proposed = await self._candidate_proposer.propose(
                        case_id, batch, disposition, self._now()
                    )
                async with persistence_lock:
                    await self._repository.persist_candidate_batch(
                        case_id,
                        structural_unit_ids,
                        batch_hash,
                        CANDIDATE_PROMPT_HASH,
                        proposed,
                        self._now(),
                    )
                return proposed, None
            except (ModelFailure, ModelRoutingDenied, ValueError) as error:
                return (), ProcessingFailure(
                    stage="candidates",
                    error_type=type(error).__name__,
                    message=str(error),
                    retryable=isinstance(error, ModelFailure),
                )
            finally:
                completed_candidate_batches += 1
                self._on_candidate_progress(
                    completed_candidate_batches, len(pending_candidate_batches)
                )

        candidate_results = await asyncio.gather(
            *(propose_batch(batch) for batch in pending_candidate_batches)
        )
        for proposed, failure in candidate_results:
            if failure is not None:
                failures.append(failure)
                continue
            new_candidates.extend(proposed)
            model_run_ids.extend(candidate.model_run_id for candidate in proposed)

        model_usage = await self._repository.get_model_usage(tuple(model_run_ids))
        return _compose_report(
            manifest,
            dispositions,
            statuses,
            structural.report,
            failures,
            evidence_items=len(new_evidence),
            candidates=len(new_candidates),
            review_pending=sum(
                item.review_status is ReviewStatus.PENDING for item in new_evidence
            ),
            model_usage=model_usage,
            reused_semantic_runs=reused_semantic,
            reused_candidate_runs=reused_candidates,
        )

    async def _materialize_source(
        self,
        case_id: UUID,
        index: int,
        curated_item: CuratedDocketItem,
        docket_item: DocketItem,
        docket_item_id: UUID,
        visibility: Visibility,
        downloads_root: Path | None,
    ) -> ProcessingSource:
        suffix = curated_item.file_type or "bin"
        downloaded_path = (
            downloads_root / f"{index:02d}.{suffix}" if downloads_root is not None else None
        )
        existing = await self._repository.get_source_document(
            case_id, str(curated_item.source_url)
        )
        if (
            existing is not None
            and existing.document.visibility is not Visibility.INVESTIGATION_EVIDENCE
        ):
            raise ValueError(f"stored source is not investigation evidence for item {index:02d}")
        if existing is not None:
            data = self._source_store.get(Path(existing.storage_path))
            retrieved_at = existing.document.retrieved_at
        elif downloaded_path is not None and downloaded_path.is_file():
            data = downloaded_path.read_bytes()
            retrieved_at = datetime.fromtimestamp(downloaded_path.stat().st_mtime, UTC)
        else:
            raise FileNotFoundError(
                f"eligible hosted source is missing for item {index:02d}"
            )

        checksum = hashlib.sha256(data).hexdigest()
        if checksum != curated_item.expected_checksum:
            raise ValueError(f"eligible source checksum mismatch for item {index:02d}")
        if existing is not None:
            if existing.document.checksum != checksum:
                raise ValueError(f"stored source checksum mismatch for item {index:02d}")
            return ProcessingSource(
                docket_item=docket_item, document=existing.document, data=data
            )
        stored = self._source_store.put(case_id, f"{index:02d}.{suffix}", data)
        document = SourceDocument(
            case_id=case_id,
            title=curated_item.title,
            source_url=curated_item.source_url,
            published_at=curated_item.published_at,
            retrieved_at=retrieved_at,
            document_type=curated_item.document_type or DocumentType.OTHER,
            visibility=visibility,
            checksum=checksum,
        )
        await self._repository.link_source_document(
            document,
            docket_item_id,
            stored.storage_path.as_posix(),
            stored.size_bytes,
        )
        return ProcessingSource(docket_item=docket_item, document=document, data=data)


def load_reference_manifest(ntsb_number: str) -> CuratedCaseManifest:
    normalized = ntsb_number.casefold()
    manifest_path = _repository_root() / "fixtures" / "real-cases" / normalized / "manifest.json"
    if normalized != "cen22fa375" or not manifest_path.is_file():
        raise ProcessCaseError(f"no curated manifest is registered for {ntsb_number}")
    return load_curated_manifest(manifest_path)


async def process_from_environment(ntsb_number: str) -> ProcessingReport:
    settings = HostedSettings.from_environment()
    manifest = load_reference_manifest(ntsb_number)
    with httpx.Client() as storage_client:
        async with role_scoped_connection(
            settings.database_url.get_secret_value(), RuntimeRole.PROCESSOR
        ) as connection:
            source_store = SupabaseSourceStore(
                settings.supabase_url,
                settings.supabase_secret_key.get_secret_value(),
                settings.source_bucket,
                client=storage_client,
            )
            artifact_store = SupabaseArtifactStore(
                settings.supabase_url,
                settings.supabase_secret_key.get_secret_value(),
                settings.derived_bucket,
                client=storage_client,
            )
            repository = EvidenceRepository(connection)
            router = _model_router(repository, settings)
            orchestrator = ProcessingOrchestrator(
                ProcessorRegistry(
                    (
                        PdfProcessor(DoclingPdfAdapter()),
                        TableProcessor(),
                        TextProcessor(),
                        ImageProcessor(),
                    )
                ),
                repository,
                artifact_store,
            )
            return await CaseProcessingService(
                repository=repository,
                source_store=source_store,
                structural_orchestrator=orchestrator,
                semantic_interpreter=SemanticInterpreter(router),
                candidate_proposer=CandidateProposer(router),
                semantic_concurrency=4,
                on_semantic_progress=lambda done, total: print(
                    f"semantic {done}/{total}", file=sys.stderr, flush=True
                ),
                on_candidate_progress=lambda done, total: print(
                    f"candidates {done}/{total}", file=sys.stderr, flush=True
                ),
            ).process_case(ntsb_number, manifest, None)


def _model_router(repository: EvidenceRepository, settings: HostedSettings) -> ModelRouter:
    primary = PydanticReasoningModel.openai_compatible(
        settings.text_model,
        settings.hyperfusion_base_url,
        settings.hyperfusion_api_key.get_secret_value(),
        provider="hyperfusion",
    )
    return ModelRouter(primary, recorder=repository)


def _docket_item(case_id: UUID, item: CuratedDocketItem) -> DocketItem:
    return DocketItem(
        case_id=case_id,
        title=item.title,
        source_url=item.source_url,
        document_type=item.document_type,
        file_type=item.file_type,
        page_count=item.page_count,
        published_at=item.published_at,
        rights_status=item.rights_status,
        processing_disposition=item.processing_disposition,
        attribution=item.attribution,
        review_note=item.review_note,
        reviewed_at=item.reviewed_at,
        expected_checksum=item.expected_checksum,
    )


def _all_documents_terminal(statuses: Counter[str]) -> bool:
    terminal = {
        "SUCCEEDED",
        "FAILED",
        "UNSUPPORTED",
        "SKIPPED_RIGHTS",
        "SKIPPED_VISIBILITY",
    }
    return not (set(statuses) - terminal)


def _compose_report(
    manifest: CuratedCaseManifest,
    dispositions: Counter[str],
    statuses: Counter[str],
    structural: ProcessingReport,
    failures: list[ProcessingFailure],
    *,
    evidence_items: int = 0,
    candidates: int = 0,
    review_pending: int = 0,
    model_usage: dict[str, int] | None = None,
    reused_semantic_runs: int = 0,
    reused_candidate_runs: int = 0,
) -> ProcessingReport:
    return ProcessingReport(
        inventory_total=len(manifest.items),
        disposition_counts=dict(sorted(dispositions.items())),
        status_counts=dict(sorted(statuses.items())),
        artifacts=structural.artifacts,
        structural_units=structural.structural_units,
        evidence_items=evidence_items,
        candidates=candidates,
        review_pending=review_pending,
        model_usage=model_usage or {},
        failures=tuple(failures),
        reused_structural_runs=structural.reused_structural_runs,
        reused_semantic_runs=reused_semantic_runs,
        reused_candidate_runs=reused_candidate_runs,
    )


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]
