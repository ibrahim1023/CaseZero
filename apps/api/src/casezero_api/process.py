import hashlib
import os
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

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
from casezero_evidence.artifact_store import LocalArtifactStore
from casezero_evidence.repository import EvidenceRepository, StoredSourceRecord
from casezero_evidence.source_store import LocalSourceStore, SourceStore, StoreIntegrityError
from casezero_ingestion.candidates import CandidateProposer
from casezero_ingestion.images import ImageProcessor
from casezero_ingestion.orchestrator import (
    ProcessingOrchestrator,
    StructuralProcessingResult,
)
from casezero_ingestion.pdf import DoclingPdfAdapter, PdfProcessor
from casezero_ingestion.registry import ProcessorRegistry
from casezero_ingestion.report import ProcessingFailure, ProcessingReport
from casezero_ingestion.semantic import SemanticInterpreter
from casezero_ingestion.tables import TableProcessor
from casezero_ingestion.text import TextProcessor
from casezero_ntsb.curation import (
    CuratedCaseManifest,
    CuratedDocketItem,
    load_curated_manifest,
    validate_curated_manifest,
)
from casezero_observability import (
    ModelFailure,
    ModelRouter,
    ModelRoutingDenied,
    PydanticReasoningModel,
)
from psycopg import AsyncConnection


class ProcessCaseError(RuntimeError):
    pass


class CaseProcessingRepository(Protocol):
    async def get_case_id(self, ntsb_number: str) -> UUID | None: ...

    async def upsert_docket_item(self, item: DocketItem) -> UUID: ...

    async def record_processing_skip(
        self, docket_item_id: UUID, reason: str, created_at: datetime
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

    async def get_evidence_items_for_unit(
        self, structural_unit_id: UUID
    ) -> tuple[EvidenceItem, ...]: ...

    async def add_evidence_items(
        self, items: tuple[EvidenceItem, ...], created_at: datetime
    ) -> None: ...

    async def add_candidates(
        self,
        candidates: tuple[ClaimCandidate | EntityCandidate | TimelineCandidate, ...],
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
    ) -> None:
        self._repository = repository
        self._source_store = source_store
        self._structural_orchestrator = structural_orchestrator
        self._semantic_interpreter = semantic_interpreter
        self._candidate_proposer = candidate_proposer
        self._now = now or (lambda: datetime.now(UTC))

    async def process_case(
        self,
        ntsb_number: str,
        manifest: CuratedCaseManifest,
        downloads_root: Path,
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
                    f"{disposition.value}: {curated_item.review_note}",
                    self._now(),
                )
                statuses["SKIPPED_RIGHTS"] += 1
                continue
            try:
                source = await self._materialize_source(
                    case_id,
                    index,
                    curated_item,
                    docket_item,
                    docket_item_id,
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
        model_run_ids: list[UUID] = []
        reused_semantic = 0
        for unit in structural.units:
            disposition = source_dispositions[unit.source_document_id]
            unit_key = (unit.id,)
            if await self._repository.has_successful_model_run(case_id, "evidence", unit_key):
                reused_semantic += 1
                evidence.extend(await self._repository.get_evidence_items_for_unit(unit.id))
                continue
            try:
                interpreted = await self._semantic_interpreter.interpret(
                    case_id, unit, disposition
                )
            except (ModelFailure, ModelRoutingDenied, ValueError) as error:
                failures.append(
                    ProcessingFailure(
                        stage="semantic",
                        source_document_id=unit.source_document_id,
                        structural_unit_id=unit.id,
                        error_type=type(error).__name__,
                        message=str(error),
                        retryable=isinstance(error, ModelFailure),
                    )
                )
                continue
            await self._repository.add_evidence_items(interpreted, self._now())
            evidence.extend(interpreted)
            new_evidence.extend(interpreted)
            model_run_ids.extend(
                item.model_run_id for item in interpreted if item.model_run_id is not None
            )

        new_candidates: tuple[
            ClaimCandidate | EntityCandidate | TimelineCandidate, ...
        ] = ()
        reused_candidates = 0
        candidate_unit_ids = tuple(
            dict.fromkeys(
                item.structural_unit_id
                for item in evidence
                if item.structural_unit_id is not None
            )
        )
        if evidence:
            if await self._repository.has_successful_model_run(
                case_id, "candidates", candidate_unit_ids
            ):
                reused_candidates = 1
            else:
                candidate_disposition = (
                    ProcessingDisposition.LOCAL_ONLY
                    if any(
                        source_dispositions[item.source_document_id]
                        is ProcessingDisposition.LOCAL_ONLY
                        for item in evidence
                    )
                    else ProcessingDisposition.AI_ALLOWED
                )
                try:
                    new_candidates = await self._candidate_proposer.propose(
                        case_id,
                        tuple(evidence),
                        candidate_disposition,
                        self._now(),
                    )
                except (ModelFailure, ModelRoutingDenied, ValueError) as error:
                    failures.append(
                        ProcessingFailure(
                            stage="candidates",
                            error_type=type(error).__name__,
                            message=str(error),
                            retryable=isinstance(error, ModelFailure),
                        )
                    )
                else:
                    await self._repository.add_candidates(new_candidates)
                    model_run_ids.extend(candidate.model_run_id for candidate in new_candidates)

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
        downloads_root: Path,
    ) -> ProcessingSource:
        suffix = curated_item.file_type or "bin"
        downloaded_path = downloads_root / f"{index:02d}.{suffix}"
        existing = await self._repository.get_source_document(
            case_id, str(curated_item.source_url)
        )
        if (
            existing is not None
            and existing.document.visibility is not Visibility.INVESTIGATION_EVIDENCE
        ):
            raise ValueError(f"stored source is not investigation evidence for item {index:02d}")
        if downloaded_path.is_file():
            data = downloaded_path.read_bytes()
            retrieved_at = datetime.fromtimestamp(downloaded_path.stat().st_mtime, UTC)
        elif existing is not None:
            data = self._source_store.get(Path(existing.storage_path))
            retrieved_at = existing.document.retrieved_at
        else:
            raise FileNotFoundError(f"eligible source file is missing: {downloaded_path}")

        checksum = hashlib.sha256(data).hexdigest()
        if checksum != curated_item.expected_checksum:
            raise ValueError(f"eligible source checksum mismatch for item {index:02d}")
        stored = self._source_store.put(case_id, downloaded_path.name, data)
        if existing is not None:
            if existing.document.checksum != checksum:
                raise ValueError(f"stored source checksum mismatch for item {index:02d}")
            document = existing.document
        else:
            document = SourceDocument(
                case_id=case_id,
                title=curated_item.title,
                source_url=curated_item.source_url,
                published_at=curated_item.published_at,
                retrieved_at=retrieved_at,
                document_type=curated_item.document_type or DocumentType.OTHER,
                visibility=Visibility.INVESTIGATION_EVIDENCE,
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
    database_url = _required_environment("DATABASE_URL")
    manifest = load_reference_manifest(ntsb_number)
    repository_root = _repository_root()
    downloads_root = Path(
        os.getenv(
            "CASEZERO_CASE_DATA_ROOT",
            str(repository_root / "data" / "real-cases" / ntsb_number.casefold()),
        )
    )
    source_store = LocalSourceStore(Path(os.getenv("CASEZERO_SOURCE_ROOT", "data/sources")))
    artifact_store = LocalArtifactStore(
        Path(os.getenv("CASEZERO_ARTIFACT_ROOT", "data/artifacts"))
    )

    async with await AsyncConnection.connect(database_url) as connection:
        repository = EvidenceRepository(connection)
        router = _model_router(repository)
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
        ).process_case(ntsb_number, manifest, downloads_root)


def _model_router(repository: EvidenceRepository) -> ModelRouter:
    primary = PydanticReasoningModel.openai_compatible(
        os.getenv("CASEZERO_TEXT_MODEL", "qwen/qwen3-32b"),
        os.getenv("HYPERFUSION_BASE_URL", "https://api.hyperfusion.io/v1"),
        _required_environment("HYPERFUSION_API_KEY"),
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
    terminal = {"SUCCEEDED", "FAILED", "UNSUPPORTED", "SKIPPED_RIGHTS"}
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


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ProcessCaseError(f"{name} is required")
    return value


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]
