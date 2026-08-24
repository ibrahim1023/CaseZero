from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import respx
from casezero_evidence import SourceDocument
from casezero_evidence.source_store import LocalSourceStore
from casezero_ntsb.downloader import NtsbSourceDownloader
from casezero_ntsb.manifest import DocketDocument, DocketManifest
from pydantic import AnyHttpUrl

CASE_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
CUTOFF = datetime(2025, 5, 1, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 8, 24, tzinfo=UTC)


class RecordingRepository:
    def __init__(self) -> None:
        self.saved: list[tuple[SourceDocument, str]] = []

    async def add(self, document: SourceDocument, storage_path: str) -> None:
        self.saved.append((document, storage_path))


def document(title: str, url: str, document_type: str = "FACTUAL_REPORT") -> DocketDocument:
    return DocketDocument(
        title=title,
        documentType=document_type,
        sourceUrl=AnyHttpUrl(url),
        fileType="pdf",
    )


@respx.mock
@pytest.mark.asyncio
async def test_download_manifest_stores_successes_and_records_failures(tmp_path: Path) -> None:
    factual_url = "https://data.ntsb.gov/factual.pdf"
    missing_url = "https://data.ntsb.gov/missing.pdf"
    respx.get(factual_url).mock(return_value=httpx.Response(200, content=b"factual bytes"))
    respx.get(missing_url).mock(return_value=httpx.Response(404))
    manifest = DocketManifest(
        caseId="CEN25LA167",
        documents=(
            document("Aircraft factual report", factual_url),
            document("Missing attachment", missing_url),
        ),
    )
    repository = RecordingRepository()

    async with httpx.AsyncClient() as http_client:
        result = await NtsbSourceDownloader(
            http_client=http_client,
            store=LocalSourceStore(tmp_path),
            repository=repository,
            minimum_interval=0,
            now=lambda: RETRIEVED_AT,
        ).download_manifest(CASE_ID, manifest, cutoff=CUTOFF)

    assert len(result.documents) == 1
    assert result.documents[0].checksum
    assert result.documents[0].retrieved_at == RETRIEVED_AT
    assert len(result.errors) == 1
    assert result.errors[0].source_url == missing_url
    assert len(repository.saved) == 1
    _, storage_path = repository.saved[0]
    assert (tmp_path / storage_path).read_bytes() == b"factual bytes"


@respx.mock
@pytest.mark.asyncio
async def test_download_manifest_rejects_redirect_outside_ntsb(tmp_path: Path) -> None:
    source_url = "https://data.ntsb.gov/redirect.pdf"
    external_url = "https://external.example.test/artifact.pdf"
    respx.get(source_url).mock(
        return_value=httpx.Response(302, headers={"Location": external_url})
    )
    respx.get(external_url).mock(return_value=httpx.Response(200, content=b"untrusted"))
    manifest = DocketManifest(
        caseId="CEN25LA167",
        documents=(document("Redirecting artifact", source_url),),
    )

    async with httpx.AsyncClient(follow_redirects=True) as http_client:
        result = await NtsbSourceDownloader(
            http_client=http_client,
            store=LocalSourceStore(tmp_path),
            repository=RecordingRepository(),
            minimum_interval=0,
            now=lambda: RETRIEVED_AT,
        ).download_manifest(CASE_ID, manifest, cutoff=CUTOFF)

    assert result.documents == ()
    assert result.errors[0].error == "source URL must use an ntsb.gov host"
    assert not tuple(path for path in tmp_path.rglob("*") if path.is_file())


@respx.mock
@pytest.mark.asyncio
async def test_download_manifest_rate_limits_and_is_content_idempotent(tmp_path: Path) -> None:
    first_url = "https://data.ntsb.gov/first.pdf"
    second_url = "https://data.ntsb.gov/second.pdf"
    respx.get(first_url).mock(return_value=httpx.Response(200, content=b"same bytes"))
    respx.get(second_url).mock(return_value=httpx.Response(200, content=b"same bytes"))
    manifest = DocketManifest(
        caseId="CEN25LA167",
        documents=(document("First", first_url), document("Second", second_url)),
    )
    now = 100.0
    sleeps: list[float] = []

    def clock() -> float:
        return now

    async def sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    async with httpx.AsyncClient() as http_client:
        result = await NtsbSourceDownloader(
            http_client=http_client,
            store=LocalSourceStore(tmp_path),
            repository=RecordingRepository(),
            clock=clock,
            sleep=sleep,
            now=lambda: RETRIEVED_AT,
        ).download_manifest(CASE_ID, manifest, cutoff=CUTOFF)

    assert sleeps == [5.0]
    assert result.storage_paths[0] == result.storage_paths[1]
    assert len(tuple(path for path in tmp_path.rglob("*") if path.is_file())) == 1
