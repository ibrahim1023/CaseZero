from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from casezero_api.cli import app
from casezero_api.ingest import IngestService, IngestSummary
from casezero_evidence import DocumentType, SourceDocument, Visibility
from casezero_ntsb.downloader import DownloadResult, RetrievalError
from casezero_ntsb.manifest import DocketManifest
from casezero_ntsb.models import AircraftMetadata, CaseMetadata
from pydantic import AnyHttpUrl
from typer.testing import CliRunner

CASE_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
DOCUMENT_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e70")
CUTOFF = datetime(2025, 5, 1, tzinfo=UTC)


class CaseLookup:
    async def get_case(self, ntsb_number: str) -> CaseMetadata:
        assert ntsb_number == "CEN25LA167"
        return CaseMetadata(
            ntsb_number=ntsb_number,
            event_date=datetime(2025, 4, 30, 17, 52, tzinfo=UTC),
            location="Bethany, OK",
            aircraft=AircraftMetadata(make="BELL", model="505"),
            status="Completed",
            has_final_report=True,
        )


class CaseRepository:
    def __init__(self) -> None:
        self.entered_blind: list[tuple[UUID, datetime]] = []

    async def upsert_case(self, metadata: CaseMetadata) -> UUID:
        assert metadata.ntsb_number == "CEN25LA167"
        return CASE_ID

    async def enter_blind(self, case_id: UUID, evidence_cutoff: datetime) -> None:
        self.entered_blind.append((case_id, evidence_cutoff))


class Downloader:
    async def download_manifest(
        self, case_id: UUID, manifest: DocketManifest, *, cutoff: datetime
    ) -> DownloadResult:
        assert case_id == CASE_ID
        assert manifest.case_id == "CEN25LA167"
        assert cutoff == CUTOFF
        return DownloadResult(
            documents=(
                SourceDocument(
                    id=DOCUMENT_ID,
                    case_id=CASE_ID,
                    title="Aircraft factual report",
                    source_url=AnyHttpUrl("https://data.ntsb.gov/factual.pdf"),
                    retrieved_at=datetime(2026, 8, 24, tzinfo=UTC),
                    document_type=DocumentType.FACTUAL_REPORT,
                    visibility=Visibility.INVESTIGATION_EVIDENCE,
                    checksum="a" * 64,
                ),
            ),
            storage_paths=("aa/" + "a" * 64,),
            bytes_stored=1200,
            errors=(
                RetrievalError(
                    title="Missing attachment",
                    source_url="https://data.ntsb.gov/missing.pdf",
                    error="404 Not Found",
                ),
            ),
        )


async def test_ingest_service_completes_case_with_explicit_partial_failures() -> None:
    repository = CaseRepository()
    manifest = DocketManifest.model_validate(
        {
            "caseId": "CEN25LA167",
            "documents": [
                {
                    "title": "Aircraft factual report",
                    "documentType": "FACTUAL_REPORT",
                    "sourceUrl": "https://data.ntsb.gov/factual.pdf",
                }
            ],
        },
        strict=False,
    )

    summary = await IngestService(
        case_lookup=CaseLookup(),
        downloader=Downloader(),
        repository=repository,
    ).ingest("CEN25LA167", manifest, cutoff=CUTOFF)

    assert summary.documents_fetched == 1
    assert summary.visibility_counts == {"INVESTIGATION_EVIDENCE": 1}
    assert summary.retrieval_errors == 1
    assert repository.entered_blind == [(CASE_ID, CUTOFF)]


def test_ingest_cli_reports_partial_errors_without_failing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"caseId":"CEN25LA167","documents":[]}')

    async def fake_ingest_from_environment(
        ntsb_number: str, manifest_path: Path | None, cutoff: datetime
    ) -> IngestSummary:
        assert ntsb_number == "CEN25LA167"
        assert manifest_path is not None
        assert cutoff == CUTOFF
        return IngestSummary(
            ntsb_number=ntsb_number,
            documents_fetched=3,
            bytes_stored=1200,
            visibility_counts={"INVESTIGATION_EVIDENCE": 2, "FINAL_FINDING": 1},
            retrieval_errors=1,
        )

    monkeypatch.setattr("casezero_api.cli.ingest_from_environment", fake_ingest_from_environment)
    result = CliRunner().invoke(
        app,
        [
            "ingest",
            "CEN25LA167",
            "--manifest",
            str(manifest_path),
            "--cutoff",
            "2025-05-01T00:00:00Z",
        ],
    )

    assert result.exit_code == 0
    assert "Documents fetched: 3" in result.output
    assert "Retrieval errors: 1" in result.output
