import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse
from uuid import UUID

import httpx
from casezero_evidence import DocumentType, SourceDocument
from casezero_evidence.source_store import SourceStore, StoreIntegrityError

from casezero_ntsb.manifest import DocketDocument, DocketManifest
from casezero_ntsb.visibility import classify_visibility


class SourceDocumentRepository(Protocol):
    async def add(self, document: SourceDocument, storage_path: str) -> None: ...


@dataclass(frozen=True, slots=True)
class RetrievalError:
    title: str
    source_url: str
    error: str


@dataclass(frozen=True, slots=True)
class DownloadResult:
    documents: tuple[SourceDocument, ...]
    storage_paths: tuple[str, ...]
    errors: tuple[RetrievalError, ...]


class NtsbSourceDownloader:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        store: SourceStore,
        repository: SourceDocumentRepository,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] | None = None,
        minimum_interval: float = 5.0,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._http_client = http_client
        self._store = store
        self._repository = repository
        self._clock = clock
        self._sleep = sleep
        self._now = now or _utc_now
        self._minimum_interval = minimum_interval
        self._max_attempts = max_attempts
        self._last_request_at: float | None = None
        self._request_lock = asyncio.Lock()

    async def download_manifest(
        self,
        case_id: UUID,
        manifest: DocketManifest,
        *,
        cutoff: datetime,
    ) -> DownloadResult:
        documents: list[SourceDocument] = []
        storage_paths: list[str] = []
        errors: list[RetrievalError] = []

        for docket_document in manifest.documents:
            try:
                document, storage_path = await self._download_document(
                    case_id, docket_document, cutoff
                )
            except (httpx.HTTPError, OSError, StoreIntegrityError, ValueError) as error:
                errors.append(
                    RetrievalError(
                        title=docket_document.title,
                        source_url=str(docket_document.source_url),
                        error=str(error),
                    )
                )
                continue

            await self._repository.add(document, storage_path)
            documents.append(document)
            storage_paths.append(storage_path)

        return DownloadResult(
            documents=tuple(documents),
            storage_paths=tuple(storage_paths),
            errors=tuple(errors),
        )

    async def _download_document(
        self,
        case_id: UUID,
        docket_document: DocketDocument,
        cutoff: datetime,
    ) -> tuple[SourceDocument, str]:
        source_url = str(docket_document.source_url)
        self._validate_ntsb_url(source_url)
        response = await self._get_with_retries(source_url)
        response.raise_for_status()
        self._validate_ntsb_url(str(response.url))

        filename = unquote(Path(urlparse(source_url).path).name) or "docket-artifact"
        stored = self._store.put(case_id, filename, response.content)
        document = SourceDocument(
            case_id=case_id,
            title=docket_document.title,
            source_url=docket_document.source_url,
            published_at=docket_document.published_at,
            retrieved_at=self._now(),
            document_type=_document_type(docket_document.document_type),
            visibility=classify_visibility(
                docket_document.document_type,
                docket_document.title,
                docket_document.published_at,
                cutoff,
            ),
            checksum=stored.checksum,
        )
        return document, stored.storage_path.as_posix()

    async def _get_with_retries(self, source_url: str) -> httpx.Response:
        for attempt in range(self._max_attempts):
            await self._wait_for_request_slot()
            response = await self._http_client.get(source_url, timeout=120)
            if response.status_code != 429 and response.status_code < 500:
                return response
            if attempt + 1 < self._max_attempts:
                await self._sleep(float(2**attempt))
        return response

    async def _wait_for_request_slot(self) -> None:
        async with self._request_lock:
            if self._last_request_at is not None:
                elapsed = self._clock() - self._last_request_at
                if elapsed < self._minimum_interval:
                    await self._sleep(self._minimum_interval - elapsed)
            self._last_request_at = self._clock()

    @staticmethod
    def _validate_ntsb_url(source_url: str) -> None:
        host = urlparse(source_url).hostname
        if host != "ntsb.gov" and (host is None or not host.endswith(".ntsb.gov")):
            raise ValueError("source URL must use an ntsb.gov host")


def _document_type(value: str | None) -> DocumentType:
    if not value:
        return DocumentType.OTHER
    normalized = value.strip().upper().replace(" ", "_").replace("-", "_")
    try:
        return DocumentType(normalized)
    except ValueError:
        return DocumentType.OTHER


def _utc_now() -> datetime:
    return datetime.now(UTC)
