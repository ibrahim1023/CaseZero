from datetime import datetime
from typing import Protocol
from uuid import UUID

from casezero_ntsb.curation import CuratedCaseManifest


class CutoffRepository(Protocol):
    async def get_case_id(self, ntsb_number: str) -> UUID | None: ...

    async def has_lock(self, case_id: UUID) -> bool: ...

    async def enter_blind(self, case_id: UUID, evidence_cutoff: datetime) -> None: ...


async def backfill_registered_cutoffs(
    repository: CutoffRepository,
    manifests: tuple[CuratedCaseManifest, ...],
) -> int:
    count = 0
    for manifest in manifests:
        case_id = await repository.get_case_id(manifest.case_id)
        if case_id is None:
            raise RuntimeError(f"registered case not found: {manifest.case_id}")
        if await repository.has_lock(case_id):
            raise RuntimeError(f"registered case already locked: {manifest.case_id}")
        await repository.enter_blind(case_id, manifest.blind_cutoff)
        count += 1
    return count
