from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from casezero_ntsb.downloader import DownloadResult
from casezero_ntsb.manifest import DocketManifest
from casezero_ntsb.models import CaseMetadata


class CaseLookup(Protocol):
    async def get_case(self, ntsb_number: str) -> CaseMetadata: ...


class CaseRepository(Protocol):
    async def upsert_case(self, metadata: CaseMetadata) -> UUID: ...

    async def mark_blind(self, case_id: UUID) -> None: ...


class ManifestDownloader(Protocol):
    async def download_manifest(
        self,
        case_id: UUID,
        manifest: DocketManifest,
        *,
        cutoff: datetime,
    ) -> DownloadResult: ...


@dataclass(frozen=True, slots=True)
class IngestSummary:
    ntsb_number: str
    documents_fetched: int
    bytes_stored: int
    visibility_counts: dict[str, int]
    retrieval_errors: int


class IngestService:
    def __init__(
        self,
        *,
        case_lookup: CaseLookup,
        downloader: ManifestDownloader,
        repository: CaseRepository,
    ) -> None:
        self._case_lookup = case_lookup
        self._downloader = downloader
        self._repository = repository

    async def ingest(
        self,
        ntsb_number: str,
        manifest: DocketManifest,
        *,
        cutoff: datetime,
    ) -> IngestSummary:
        if manifest.case_id != ntsb_number:
            raise ValueError("manifest caseId does not match the requested NTSB number")

        metadata = await self._case_lookup.get_case(ntsb_number)
        case_id = await self._repository.upsert_case(metadata)
        result = await self._downloader.download_manifest(case_id, manifest, cutoff=cutoff)
        await self._repository.mark_blind(case_id)
        counts = Counter(document.visibility.value for document in result.documents)
        return IngestSummary(
            ntsb_number=ntsb_number,
            documents_fetched=len(result.documents),
            bytes_stored=result.bytes_stored,
            visibility_counts=dict(sorted(counts.items())),
            retrieval_errors=len(result.errors),
        )
