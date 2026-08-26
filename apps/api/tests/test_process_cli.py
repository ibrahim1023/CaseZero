import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from casezero_api.cli import app
from casezero_api.process import CaseProcessingService, ProcessCaseError, load_reference_manifest
from casezero_evidence import (
    ClaimCandidate,
    ClaimStatus,
    EvidenceItem,
    EvidenceType,
    ExtractionMethod,
    ProcessingDisposition,
    ReviewStatus,
    StructuralUnit,
    StructuralUnitKind,
    TextLocator,
)
from casezero_evidence.source_store import LocalSourceStore
from casezero_ingestion.orchestrator import StructuralProcessingResult
from casezero_ingestion.report import ProcessingFailure, ProcessingReport
from casezero_ntsb.curation import CuratedCaseManifest
from typer.testing import CliRunner

CASE_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
NOW = datetime(2026, 8, 25, tzinfo=UTC)


def curated_manifest() -> CuratedCaseManifest:
    items = []
    for index, disposition in enumerate(("AI_ALLOWED", "LOCAL_ONLY", "LINK_ONLY"), start=1):
        data = f"source {index}".encode()
        items.append(
            {
                "title": f"Source {index}",
                "sourceUrl": f"https://data.ntsb.gov/{index}.txt",
                "documentType": "TEXT",
                "fileType": "txt",
                "publishedAt": "2024-03-20T17:00:00Z",
                "rightsStatus": "UNKNOWN" if disposition == "LINK_ONLY" else "NTSB_AUTHORED",
                "processingDisposition": disposition,
                "attribution": "Source: National Transportation Safety Board",
                "reviewNote": "fixture rights review",
                "reviewedAt": "2026-08-25T00:00:00Z",
                "expectedChecksum": (
                    hashlib.sha256(data).hexdigest()
                    if disposition != "LINK_ONLY"
                    else None
                ),
            }
        )
    return CuratedCaseManifest.model_validate(
        {
            "caseId": "CEN22FA375",
            "docketUrl": "https://data.ntsb.gov/Docket/?NTSBNumber=CEN22FA375",
            "expectedItemCount": 3,
            "items": items,
        },
        strict=False,
    )


class Repository:
    def __init__(self) -> None:
        self.docket_items = []
        self.documents = []
        self.evidence: dict[UUID, tuple[EvidenceItem, ...]] = {}
        self.candidates = []
        self.successful_model_runs: set[tuple[str, tuple[UUID, ...]]] = set()
        self.skips: list[tuple[UUID, str]] = []
        self.existing_sources: dict[str, object] = {}

    async def get_case_id(self, ntsb_number: str) -> UUID | None:
        return CASE_ID if ntsb_number == "CEN22FA375" else None

    async def upsert_docket_item(self, item):
        self.docket_items.append(item)
        return item.id

    async def record_processing_skip(
        self, docket_item_id: UUID, reason: str, created_at: datetime
    ) -> None:
        self.skips.append((docket_item_id, reason))

    async def get_source_document(self, case_id: UUID, source_url: str):
        return self.existing_sources.get(source_url)

    async def link_source_document(
        self, document, docket_item_id: UUID, storage_path: str, byte_size: int
    ) -> None:
        self.documents.append((document, docket_item_id, storage_path, byte_size))

    async def has_successful_model_run(
        self, case_id: UUID, stage: str, structural_unit_ids: tuple[UUID, ...]
    ) -> bool:
        return (stage, structural_unit_ids) in self.successful_model_runs

    async def get_evidence_items_for_unit(self, unit_id: UUID) -> tuple[EvidenceItem, ...]:
        return self.evidence.get(unit_id, ())

    async def add_evidence_items(
        self, items: tuple[EvidenceItem, ...], created_at: datetime
    ) -> None:
        for item in items:
            assert item.structural_unit_id is not None
            self.evidence[item.structural_unit_id] = self.evidence.get(
                item.structural_unit_id, ()
            ) + (item,)

    async def add_candidates(self, candidates) -> None:
        self.candidates.extend(candidates)

    async def get_model_usage(self, run_ids: tuple[UUID, ...]) -> dict[str, int]:
        return {"fake-model": len(set(run_ids))}


class StructuralOrchestrator:
    def __init__(self, events: list[str], terminal: bool = True) -> None:
        self.events = events
        self.terminal = terminal

    async def process_with_units(self, sources):
        self.events.append("structural")
        units = tuple(
            StructuralUnit(
                derived_artifact_id=uuid4(),
                source_document_id=source.document.id,
                kind=StructuralUnitKind.TEXT_BLOCK,
                ordinal=0,
                content_checksum=hashlib.sha256(source.data).hexdigest(),
                locator=TextLocator(start=0, end=len(source.data)),
                payload={"text": source.data.decode()},
            )
            for source in sources
        )
        status = "SUCCEEDED" if self.terminal else "RUNNING"
        report = ProcessingReport(
            status_counts={status: len(sources)},
            artifacts=len(sources) if self.terminal else 0,
            structural_units=len(units) if self.terminal else 0,
        )
        return StructuralProcessingResult(report=report, units=units if self.terminal else ())


class SemanticInterpreter:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.dispositions = []

    async def interpret(self, case_id: UUID, unit: StructuralUnit, disposition):
        self.events.append("semantic")
        self.dispositions.append(disposition)
        return (
            EvidenceItem(
                case_id=case_id,
                source_document_id=unit.source_document_id,
                structural_unit_id=unit.id,
                model_run_id=uuid4(),
                review_status=(
                    ReviewStatus.PENDING
                    if disposition is ProcessingDisposition.LOCAL_ONLY
                    else ReviewStatus.NOT_REQUIRED
                ),
                type=EvidenceType.TEXT,
                observation="Grounded fixture observation",
                source_locator=unit.locator,
                extraction_method=ExtractionMethod.AI,
                confidence=0.6 if disposition is ProcessingDisposition.LOCAL_ONLY else 0.9,
            ),
        )


class CandidateProposer:
    def __init__(self, events: list[str], repository: Repository) -> None:
        self.events = events
        self.repository = repository
        self.disposition = None

    async def propose(self, case_id, evidence, disposition, created_at):
        assert len(self.repository.evidence) == 2
        self.events.append("candidates")
        self.disposition = disposition
        return (
            ClaimCandidate(
                case_id=case_id,
                text="Provisional fixture claim",
                status=ClaimStatus.INFERRED,
                supporting_evidence_ids=(evidence[0].id,),
                confidence=0.5,
                model_run_id=uuid4(),
                created_at=created_at,
            ),
        )


@pytest.mark.asyncio
async def test_case_processing_composes_curated_inventory_sources_semantics_and_candidates(
    tmp_path: Path,
) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "01.txt").write_bytes(b"source 1")
    (downloads / "02.txt").write_bytes(b"source 2")
    repository = Repository()
    events: list[str] = []
    semantic = SemanticInterpreter(events)
    proposer = CandidateProposer(events, repository)
    service = CaseProcessingService(
        repository=repository,
        source_store=LocalSourceStore(tmp_path / "sources"),
        structural_orchestrator=StructuralOrchestrator(events),
        semantic_interpreter=semantic,
        candidate_proposer=proposer,
        now=lambda: NOW,
    )

    report = await service.process_case("CEN22FA375", curated_manifest(), downloads)

    assert len(repository.docket_items) == 3
    assert len(repository.documents) == 2
    assert len(repository.skips) == 1
    assert repository.skips[0][1].startswith("LINK_ONLY")
    assert semantic.dispositions == [
        ProcessingDisposition.AI_ALLOWED,
        ProcessingDisposition.LOCAL_ONLY,
    ]
    assert proposer.disposition is ProcessingDisposition.LOCAL_ONLY
    assert events == ["structural", "semantic", "semantic", "candidates"]
    assert report.inventory_total == 3
    assert report.disposition_counts == {
        "AI_ALLOWED": 1,
        "LINK_ONLY": 1,
        "LOCAL_ONLY": 1,
    }
    assert report.status_counts == {"SKIPPED_RIGHTS": 1, "SUCCEEDED": 2}
    assert (report.artifacts, report.structural_units, report.evidence_items) == (2, 2, 2)
    assert (report.candidates, report.review_pending) == (1, 1)
    assert report.model_usage == {"fake-model": 3}
    assert "payload" not in report.to_json()
    assert "source 1" not in report.to_json()


@pytest.mark.asyncio
async def test_link_only_item_with_existing_source_fails_closed(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "01.txt").write_bytes(b"source 1")
    (downloads / "02.txt").write_bytes(b"source 2")
    repository = Repository()
    repository.existing_sources["https://data.ntsb.gov/3.txt"] = object()
    service = CaseProcessingService(
        repository=repository,
        source_store=LocalSourceStore(tmp_path / "sources"),
        structural_orchestrator=StructuralOrchestrator([]),
        semantic_interpreter=SemanticInterpreter([]),
        candidate_proposer=CandidateProposer([], repository),
        now=lambda: NOW,
    )

    with pytest.raises(ProcessCaseError, match="rights-skipped item already has stored source"):
        await service.process_case("CEN22FA375", curated_manifest(), downloads)


@pytest.mark.asyncio
async def test_candidate_stage_waits_for_terminal_document_states(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "01.txt").write_bytes(b"source 1")
    (downloads / "02.txt").write_bytes(b"source 2")
    repository = Repository()
    events: list[str] = []
    service = CaseProcessingService(
        repository=repository,
        source_store=LocalSourceStore(tmp_path / "sources"),
        structural_orchestrator=StructuralOrchestrator(events, terminal=False),
        semantic_interpreter=SemanticInterpreter(events),
        candidate_proposer=CandidateProposer(events, repository),
        now=lambda: NOW,
    )

    report = await service.process_case("CEN22FA375", curated_manifest(), downloads)

    assert events == ["structural"]
    assert report.candidates == 0
    assert report.failures == (
        ProcessingFailure(
            stage="candidates",
            error_type="NonTerminalDocuments",
            message="candidate stage deferred until all eligible documents are terminal",
            retryable=True,
        ),
    )


def test_reference_manifest_loader_is_pinned_to_cen22fa375() -> None:
    manifest = load_reference_manifest("CEN22FA375")

    assert manifest.case_id == "CEN22FA375"
    assert manifest.expected_item_count == 15
    with pytest.raises(ProcessCaseError, match="no curated manifest"):
        load_reference_manifest("CEN25LA167")


def test_process_cli_prints_payload_free_report_and_partial_failures_exit_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = ProcessingReport(
        inventory_total=3,
        status_counts={"FAILED": 1, "SUCCEEDED": 2},
        failures=(
            ProcessingFailure(
                stage="structural",
                error_type="FixtureError",
                message="safe failure",
                retryable=True,
            ),
        ),
    )

    async def fake_process_from_environment(ntsb_number: str) -> ProcessingReport:
        assert ntsb_number == "CEN22FA375"
        return report

    monkeypatch.setattr("casezero_api.cli.process_from_environment", fake_process_from_environment)
    result = CliRunner().invoke(app, ["process", "CEN22FA375"])

    assert result.exit_code == 0
    assert '"inventory_total": 3' in result.output
    assert "safe failure" in result.output
    assert "payload" not in result.output


def test_process_cli_exits_nonzero_only_for_case_level_fatal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fatal(ntsb_number: str) -> ProcessingReport:
        raise ProcessCaseError("case does not exist")

    monkeypatch.setattr("casezero_api.cli.process_from_environment", fatal)
    result = CliRunner().invoke(app, ["process", "CEN22FA375"])

    assert result.exit_code == 1
    assert "case does not exist" in result.output
