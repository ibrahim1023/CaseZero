import inspect
from datetime import UTC, datetime
from uuid import UUID

import pytest
from casezero_evidence import AccessOperation, BlindAccessDenied, BlindAccessService

CASE_ID = UUID("80000000-0000-0000-0000-000000000001")
DOCUMENT_ID = UUID("81000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 9, 1, tzinfo=UTC)


class DeniedReader:
    async def get_source_document_by_id(self, case_id, document_id):
        return None


class Recorder:
    def __init__(self) -> None:
        self.events = []

    async def record(self, event) -> None:
        self.events.append(event)


def test_blind_service_has_no_forbidden_capability_constructor() -> None:
    parameters = set(inspect.signature(BlindAccessService).parameters)
    assert parameters == {"reader", "recorder", "now"}
    assert parameters.isdisjoint(
        {"http_client", "ntsb", "context_dev", "web_search", "storage", "secret_key"}
    )


@pytest.mark.asyncio
async def test_blocked_document_probe_returns_no_metadata_and_is_audited() -> None:
    recorder = Recorder()
    service = BlindAccessService(DeniedReader(), recorder, now=lambda: NOW)

    with pytest.raises(BlindAccessDenied) as error:
        await service.get_source_metadata(CASE_ID, DOCUMENT_ID)

    assert str(error.value) == "blind access denied"
    assert recorder.events[0].operation is AccessOperation.DENY
    serialized = recorder.events[0].model_dump_json()
    assert "title" not in serialized
    assert "source_url" not in serialized
    assert "visibility" not in serialized
