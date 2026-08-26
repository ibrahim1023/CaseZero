import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from casezero_evidence import DerivedArtifact, ProcessingSource, StructuralUnit
from casezero_evidence.artifact_store import ArtifactIntegrityError, ArtifactStore

from casezero_ingestion.media import MediaDetectionError, detect_media_type
from casezero_ingestion.processors import Processor
from casezero_ingestion.registry import ProcessorRegistry, UnsupportedMediaError
from casezero_ingestion.report import ProcessingFailure, ProcessingReport


class ProcessingRepository(Protocol):
    async def find_successful_run(
        self,
        source_checksum: str,
        processor_name: str,
        processor_version: str,
        configuration_hash: str,
    ) -> UUID | None: ...

    async def start_processing_run(
        self,
        *,
        source_document_id: UUID,
        source_checksum: str,
        processor_name: str,
        processor_version: str,
        configuration_hash: str,
        started_at: datetime,
    ) -> UUID: ...

    async def complete_processing_run(self, run_id: UUID, completed_at: datetime) -> None: ...

    async def fail_processing_run(
        self,
        run_id: UUID,
        completed_at: datetime,
        error_type: str,
        error_message: str,
        retryable: bool,
    ) -> None: ...

    async def mark_processing_run_unsupported(
        self,
        run_id: UUID,
        completed_at: datetime,
        error_type: str,
        error_message: str,
    ) -> None: ...

    async def add_artifact_with_units(
        self, artifact: DerivedArtifact, units: tuple[StructuralUnit, ...]
    ) -> None: ...

    async def get_structural_units_for_run(
        self, run_id: UUID
    ) -> tuple[StructuralUnit, ...]: ...


@dataclass(frozen=True, slots=True)
class StructuralProcessingResult:
    report: ProcessingReport
    units: tuple[StructuralUnit, ...]


class ProcessingOrchestrator:
    def __init__(
        self,
        registry: ProcessorRegistry,
        repository: ProcessingRepository,
        artifact_store: ArtifactStore,
    ) -> None:
        self._registry = registry
        self._repository = repository
        self._artifact_store = artifact_store

    async def process(self, sources: tuple[ProcessingSource, ...]) -> ProcessingReport:
        return (await self.process_with_units(sources)).report

    async def process_with_units(
        self, sources: tuple[ProcessingSource, ...]
    ) -> StructuralProcessingResult:
        statuses: Counter[str] = Counter()
        failures: list[ProcessingFailure] = []
        units: list[StructuralUnit] = []
        artifacts = 0
        unit_count = 0
        reused = 0

        for source in sources:
            if source.document.visibility.value != "INVESTIGATION_EVIDENCE":
                now = datetime.now(UTC)
                run_id = await self._repository.start_processing_run(
                    source_document_id=source.document.id,
                    source_checksum=source.document.checksum,
                    processor_name="visibility-gate",
                    processor_version="1.0.0",
                    configuration_hash=hashlib.sha256(b"visibility-gate:1.0.0").hexdigest(),
                    started_at=now,
                )
                await self._repository.fail_processing_run(
                    run_id, now, "VisibilityDenied", "source is not investigation evidence", False
                )
                statuses["FAILED"] += 1
                failures.append(
                    ProcessingFailure(
                        stage="structural",
                        source_document_id=source.document.id,
                        error_type="VisibilityDenied",
                        message="source is not investigation evidence",
                        retryable=False,
                    )
                )
                continue
            selection = await self._select_processor(source)
            if isinstance(selection, ProcessingFailure):
                statuses["UNSUPPORTED"] += 1
                failures.append(selection)
                continue
            processor = selection
            configuration_hash = hashlib.sha256(
                json.dumps(
                    {
                        "name": processor.name,
                        "version": processor.version,
                        "configuration": processor.configuration(),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            existing = await self._repository.find_successful_run(
                source.document.checksum,
                processor.name,
                processor.version,
                configuration_hash,
            )
            if existing is not None:
                reused += 1
                statuses["SUCCEEDED"] += 1
                units.extend(await self._repository.get_structural_units_for_run(existing))
                continue

            started_at = datetime.now(UTC)
            run_id = await self._repository.start_processing_run(
                source_document_id=source.document.id,
                source_checksum=source.document.checksum,
                processor_name=processor.name,
                processor_version=processor.version,
                configuration_hash=configuration_hash,
                started_at=started_at,
            )
            try:
                output = processor.process(source)
                stored = self._artifact_store.put(output.artifact_kind, output.artifact_bytes)
                artifact = DerivedArtifact(
                    processing_run_id=run_id,
                    source_document_id=source.document.id,
                    kind=output.artifact_kind,
                    checksum=stored.checksum,
                    storage_path=stored.storage_path.as_posix(),
                    media_type=output.media_type,
                    byte_size=stored.byte_size,
                    tool_metadata=output.tool_metadata,
                    created_at=started_at,
                )
                source_units = tuple(
                    StructuralUnit(
                        derived_artifact_id=artifact.id,
                        source_document_id=source.document.id,
                        kind=draft.kind,
                        ordinal=draft.ordinal,
                        content_checksum=draft.content_checksum,
                        locator=draft.locator,
                        payload=draft.payload,
                    )
                    for draft in output.units
                )
                await self._repository.add_artifact_with_units(artifact, source_units)
                await self._repository.complete_processing_run(run_id, datetime.now(UTC))
            except (ArtifactIntegrityError, OSError, ValueError) as error:
                await self._repository.fail_processing_run(
                    run_id,
                    datetime.now(UTC),
                    type(error).__name__,
                    str(error),
                    True,
                )
                statuses["FAILED"] += 1
                failures.append(
                    ProcessingFailure(
                        stage="structural",
                        source_document_id=source.document.id,
                        error_type=type(error).__name__,
                        message=str(error),
                        retryable=True,
                    )
                )
                continue

            statuses["SUCCEEDED"] += 1
            artifacts += 1
            unit_count += len(source_units)
            units.extend(source_units)

        report = ProcessingReport(
            inventory_total=len(sources),
            status_counts=dict(sorted(statuses.items())),
            artifacts=artifacts,
            structural_units=unit_count,
            failures=tuple(failures),
            reused_structural_runs=reused,
        )
        return StructuralProcessingResult(report=report, units=tuple(units))

    async def _select_processor(
        self, source: ProcessingSource
    ) -> Processor | ProcessingFailure:
        try:
            if (source.docket_item.file_type or "").casefold() == "kmz":
                raise UnsupportedMediaError("KMZ processing is not supported in Phase 1")
            media_type = detect_media_type(
                source.data, source.docket_item.file_type or "source"
            )
            return self._registry.select(media_type, source.document.document_type)
        except (MediaDetectionError, UnsupportedMediaError) as error:
            now = datetime.now(UTC)
            processor_name = "unsupported-media"
            processor_version = "1.0.0"
            configuration_hash = hashlib.sha256(
                f"{processor_name}:{processor_version}".encode()
            ).hexdigest()
            run_id = await self._repository.start_processing_run(
                source_document_id=source.document.id,
                source_checksum=source.document.checksum,
                processor_name=processor_name,
                processor_version=processor_version,
                configuration_hash=configuration_hash,
                started_at=now,
            )
            await self._repository.mark_processing_run_unsupported(
                run_id, now, type(error).__name__, str(error)
            )
            return ProcessingFailure(
                stage="structural",
                source_document_id=source.document.id,
                error_type=type(error).__name__,
                message=str(error),
                retryable=False,
            )
