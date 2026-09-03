from uuid import UUID

import pytest
from casezero_api.backfill import backfill_registered_cutoffs
from casezero_api.process import load_reference_manifest

CASE_ID = UUID("90000000-0000-0000-0000-000000000001")


class Repository:
    def __init__(self, *, known: bool = True, locked: bool = False) -> None:
        self.known = known
        self.locked = locked
        self.entered = []

    async def get_case_id(self, ntsb_number: str) -> UUID | None:
        return CASE_ID if self.known and ntsb_number == "CEN22FA375" else None

    async def has_lock(self, case_id: UUID) -> bool:
        assert case_id == CASE_ID
        return self.locked

    async def enter_blind(self, case_id, evidence_cutoff) -> None:
        self.entered.append((case_id, evidence_cutoff))


@pytest.mark.asyncio
async def test_backfill_uses_registered_manifest_cutoff_without_locking() -> None:
    repository = Repository()
    manifest = load_reference_manifest("CEN22FA375")

    count = await backfill_registered_cutoffs(repository, (manifest,))

    assert count == 1
    assert repository.entered == [(CASE_ID, manifest.blind_cutoff)]
    assert repository.locked is False


@pytest.mark.asyncio
async def test_backfill_rejects_missing_or_locked_case() -> None:
    manifest = load_reference_manifest("CEN22FA375")

    with pytest.raises(RuntimeError, match="not found"):
        await backfill_registered_cutoffs(Repository(known=False), (manifest,))
    with pytest.raises(RuntimeError, match="already locked"):
        await backfill_registered_cutoffs(Repository(locked=True), (manifest,))
