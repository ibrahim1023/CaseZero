import hashlib
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from casezero_evidence import DerivedArtifact, ProcessingSource, StructuralUnit
from casezero_evidence.artifact_store import ArtifactStore

from casezero_ingestion.media import detect_media_type
from casezero_ingestion.registry import ProcessorRegistry
from casezero_ingestion.report import ProcessingReport


class ProcessingRepository(Protocol):
    async def find_successful_run(self, source_checksum: str, processor_name: str, processor_version: str, configuration_hash: str) -> UUID | None: ...
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
    async def fail_processing_run(self, run_id: UUID, completed_at: datetime, error_type: str, error_message: str, retryable: bool) -> None: ...
    async def add_derived_artifact(self, artifact: DerivedArtifact) -> None: ...
    async def add_structural_units(self, units: tuple[StructuralUnit, ...]) -> None: ...


class ProcessingOrchestrator:
    def __init__(self, registry: ProcessorRegistry, repository: ProcessingRepository, artifact_store: ArtifactStore) -> None:
        self._registry=registry; self._repository=repository; self._artifact_store=artifact_store

    async def process(self, sources: tuple[ProcessingSource,...]) -> ProcessingReport:
        succeeded=failed=reused=artifacts=unit_count=0
        for source in sources:
            try:
                media = detect_media_type(source.data, source.docket_item.file_type or "source")
                processor = self._registry.select(media, source.document.document_type)
            except (ValueError, LookupError) as error:
                now = datetime.now(UTC)
                run_id = await self._repository.start_processing_run(
                    source_document_id=source.document.id,
                    source_checksum=source.document.checksum,
                    processor_name="media-detection",
                    processor_version="1.0.0",
                    configuration_hash=hashlib.sha256(b"media-detection:1.0.0").hexdigest(),
                    started_at=now,
                )
                await self._repository.fail_processing_run(
                    run_id, now, type(error).__name__, str(error), False
                )
                failed += 1
                continue
            config_hash=hashlib.sha256(f"{processor.name}:{processor.version}".encode()).hexdigest()
            existing=await self._repository.find_successful_run(source.document.checksum,processor.name,processor.version,config_hash)
            if existing is not None:
                reused+=1; succeeded+=1; continue
            now=datetime.now(UTC)
            run_id=await self._repository.start_processing_run(source_document_id=source.document.id,source_checksum=source.document.checksum,processor_name=processor.name,processor_version=processor.version,configuration_hash=config_hash,started_at=now)
            try:
                output=processor.process(source)
                stored=self._artifact_store.put(output.artifact_kind,output.artifact_bytes)
                artifact=DerivedArtifact(processing_run_id=run_id,source_document_id=source.document.id,kind=output.artifact_kind,checksum=stored.checksum,storage_path=stored.storage_path.as_posix(),media_type=output.media_type,byte_size=stored.byte_size,tool_metadata=output.tool_metadata,created_at=now)
                units=tuple(StructuralUnit(derived_artifact_id=artifact.id,source_document_id=source.document.id,kind=draft.kind,ordinal=draft.ordinal,content_checksum=draft.content_checksum,locator=draft.locator,payload=draft.payload) for draft in output.units)
                await self._repository.add_derived_artifact(artifact)
                await self._repository.add_structural_units(units)
                await self._repository.complete_processing_run(run_id,datetime.now(UTC))
                succeeded+=1; artifacts+=1; unit_count+=len(units)
            except (ValueError,OSError) as error:
                await self._repository.fail_processing_run(run_id,datetime.now(UTC),type(error).__name__,str(error),True)
                failed+=1
        return ProcessingReport(len(sources),succeeded,failed,reused,artifacts,unit_count,0,0)
