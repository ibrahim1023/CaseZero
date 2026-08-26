from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from casezero_evidence import (
    DerivedArtifactKind,
    DocketItem,
    DocumentType,
    ProcessingSource,
    RightsStatus,
    SourceDocument,
    StructuralUnitKind,
    TextLocator,
    Visibility,
)
from casezero_evidence.artifact_store import LocalArtifactStore
from casezero_ingestion.orchestrator import ProcessingOrchestrator
from casezero_ingestion.processors import StructuralOutput, StructuralUnitDraft
from casezero_ingestion.registry import ProcessorRegistry
from pydantic import AnyHttpUrl

CASE_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
NOW = datetime(2026, 8, 25, tzinfo=UTC)


class Repository:
    def __init__(self) -> None:
        self.runs: dict[tuple[str, str, str, str], dict[str, object]] = {}
        self.run_keys: dict[UUID, tuple[str, str, str, str]] = {}
        self.units: dict[UUID, tuple[object, ...]] = {}
        self.artifacts: list[object] = []

    async def find_successful_run(self, *key: str) -> UUID | None:
        run = self.runs.get(key)
        return run["id"] if run is not None and run["status"] == "SUCCEEDED" else None  # type: ignore[return-value]

    async def start_processing_run(self, **values: object) -> UUID:
        key = (
            str(values["source_checksum"]),
            str(values["processor_name"]),
            str(values["processor_version"]),
            str(values["configuration_hash"]),
        )
        run = self.runs.get(key)
        run_id = run["id"] if run is not None else uuid4()
        attempts = int(run["attempts"]) + 1 if run is not None else 1
        self.runs[key] = {"id": run_id, "status": "RUNNING", "attempts": attempts}
        self.run_keys[run_id] = key  # type: ignore[index]
        return run_id  # type: ignore[return-value]

    async def complete_processing_run(self, run_id: UUID, completed_at: datetime) -> None:
        self.runs[self.run_keys[run_id]]["status"] = "SUCCEEDED"

    async def fail_processing_run(self, run_id: UUID, *args: object) -> None:
        self.runs[self.run_keys[run_id]]["status"] = "FAILED"

    async def mark_processing_run_unsupported(
        self, run_id: UUID, completed_at: datetime, error_type: str, error_message: str
    ) -> None:
        self.runs[self.run_keys[run_id]]["status"] = "UNSUPPORTED"

    async def add_artifact_with_units(
        self, artifact: object, units: tuple[object, ...]
    ) -> None:
        self.artifacts.append(artifact)
        self.units[artifact.processing_run_id] = units  # type: ignore[attr-defined]

    async def get_structural_units_for_run(self, run_id: UUID):
        return self.units[run_id]


class TextProcessor:
    name = "fixture-text"

    def __init__(
        self,
        version: str = "1.0.0",
        fail_once: bool = False,
        configuration_tag: str = "default",
    ) -> None:
        self.version = version
        self.fail_once = fail_once
        self.configuration_tag = configuration_tag
        self.calls = 0

    def supports(self, media_type, document_type) -> bool:
        return media_type.value == "TEXT"

    def configuration(self) -> dict[str, object]:
        return {"configuration_tag": self.configuration_tag}

    def process(self, source: ProcessingSource) -> StructuralOutput:
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise ValueError("temporary parser failure")
        draft = StructuralUnitDraft(
            kind=StructuralUnitKind.TEXT_BLOCK,
            ordinal=0,
            content_checksum=source.document.checksum,
            locator=TextLocator(start=0, end=len(source.data)),
            payload={"text": source.data.decode()},
        )
        return StructuralOutput(
            DerivedArtifactKind.DOCUMENT_STRUCTURE,
            source.data,
            "application/json",
            (draft,),
        )


def source(
    document_id: UUID,
    text: str = "visible text",
    file_type: str = "txt",
    visibility: Visibility = Visibility.INVESTIGATION_EVIDENCE,
) -> ProcessingSource:
    data = text.encode()
    import hashlib

    checksum = hashlib.sha256(data).hexdigest()
    url = AnyHttpUrl(f"https://data.ntsb.gov/{document_id}.{file_type}")
    return ProcessingSource(
        docket_item=DocketItem(
            case_id=CASE_ID,
            title="Fixture source",
            source_url=url,
            document_type=DocumentType.TEXT,
            file_type=file_type,
            rights_status=RightsStatus.NTSB_AUTHORED,
            attribution="Source: National Transportation Safety Board",
            review_note="fixture",
            reviewed_at=NOW,
        ),
        document=SourceDocument(
            id=document_id,
            case_id=CASE_ID,
            title="Fixture source",
            source_url=url,
            retrieved_at=NOW,
            document_type=DocumentType.TEXT,
            visibility=visibility,
            checksum=checksum,
        ),
        data=data,
    )


@pytest.mark.asyncio
async def test_orchestrator_preserves_successful_sibling_and_resumes_retryable_failure(
    tmp_path: Path,
) -> None:
    repository = Repository()
    processor = TextProcessor(fail_once=True)
    orchestrator = ProcessingOrchestrator(
        ProcessorRegistry((processor,)), repository, LocalArtifactStore(tmp_path)
    )
    first_source = source(uuid4(), "first")
    second_source = source(uuid4(), "second")

    first = await orchestrator.process_with_units((first_source, second_source))
    second = await orchestrator.process_with_units((first_source, second_source))

    assert first.report.status_counts == {"FAILED": 1, "SUCCEEDED": 1}
    assert second.report.status_counts == {"SUCCEEDED": 2}
    assert second.report.reused_structural_runs == 1
    assert processor.calls == 3
    attempts = sorted(int(run["attempts"]) for run in repository.runs.values())
    assert attempts == [1, 2]


@pytest.mark.asyncio
async def test_orchestrator_reuses_unchanged_run_and_reprocesses_new_version(tmp_path: Path) -> None:
    repository = Repository()
    first_processor = TextProcessor("1.0.0")
    first = ProcessingOrchestrator(
        ProcessorRegistry((first_processor,)), repository, LocalArtifactStore(tmp_path)
    )
    document = source(uuid4())

    await first.process_with_units((document,))
    reused = await first.process_with_units((document,))
    changed_processor = TextProcessor("2.0.0")
    changed = await ProcessingOrchestrator(
        ProcessorRegistry((changed_processor,)), repository, LocalArtifactStore(tmp_path)
    ).process_with_units((document,))

    assert reused.report.reused_structural_runs == 1
    assert reused.report.artifacts == 0
    assert changed.report.reused_structural_runs == 0
    assert changed.report.artifacts == 1
    configured_processor = TextProcessor("2.0.0", configuration_tag="changed")
    configured = await ProcessingOrchestrator(
        ProcessorRegistry((configured_processor,)), repository, LocalArtifactStore(tmp_path)
    ).process_with_units((document,))
    assert configured.report.reused_structural_runs == 0
    assert configured.report.artifacts == 1
    assert len(repository.artifacts) == 3


@pytest.mark.asyncio
async def test_orchestrator_records_unsupported_kmz_without_aborting(tmp_path: Path) -> None:
    repository = Repository()
    document = source(uuid4(), "PK fixture", "kmz")

    result = await ProcessingOrchestrator(
        ProcessorRegistry(()), repository, LocalArtifactStore(tmp_path)
    ).process_with_units((document,))

    assert result.report.status_counts == {"UNSUPPORTED": 1}
    assert result.report.failures[0].stage == "structural"
    assert next(iter(repository.runs.values()))["status"] == "UNSUPPORTED"


@pytest.mark.asyncio
async def test_orchestrator_rejects_non_investigation_source_before_processor(
    tmp_path: Path,
) -> None:
    repository = Repository()
    processor = TextProcessor()
    document = source(uuid4(), visibility=Visibility.FINAL_FINDING)

    result = await ProcessingOrchestrator(
        ProcessorRegistry((processor,)), repository, LocalArtifactStore(tmp_path)
    ).process_with_units((document,))

    assert result.report.status_counts == {"FAILED": 1}
    assert processor.calls == 0
