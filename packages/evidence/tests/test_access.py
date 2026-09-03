import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from casezero_evidence import (
    AccessAuditEvent,
    AccessCapability,
    AccessOperation,
    AuditReasonCode,
    DocumentType,
    EvidenceItem,
    EvidenceType,
    ExtractionMethod,
    RuntimeActor,
    SourceDocument,
    TextLocator,
    Visibility,
    WorkflowStage,
)
from casezero_evidence.access import (
    BlindAccessDenied,
    BlindAccessService,
    PostgresAccessAuditRecorder,
)
from psycopg import AsyncConnection
from psycopg.errors import DatabaseError
from pydantic import AnyHttpUrl

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)
CASE_ID = UUID("60000000-0000-0000-0000-000000000001")
DOCUMENT_ID = UUID("61000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 9, 1, tzinfo=UTC)


class Reader:
    def __init__(
        self,
        document: SourceDocument | None,
        evidence: tuple[EvidenceItem, ...] = (),
    ) -> None:
        self.document = document
        self.evidence = evidence

    async def get_source_document_by_id(
        self, case_id: UUID, document_id: UUID
    ) -> SourceDocument | None:
        assert case_id == CASE_ID
        assert document_id == DOCUMENT_ID
        return self.document

    async def list_active_evidence(self, case_id: UUID) -> tuple[EvidenceItem, ...]:
        assert case_id == CASE_ID
        return self.evidence

    async def get_active_evidence(
        self, case_id: UUID, evidence_id: UUID
    ) -> EvidenceItem | None:
        assert case_id == CASE_ID
        return next((item for item in self.evidence if item.id == evidence_id), None)


class Recorder:
    def __init__(self) -> None:
        self.events = []

    async def record(self, event) -> None:
        self.events.append(event)


def document() -> SourceDocument:
    return SourceDocument(
        id=DOCUMENT_ID,
        case_id=CASE_ID,
        title="Factual report",
        source_url=AnyHttpUrl("https://data.ntsb.gov/factual.pdf"),
        published_at=NOW,
        retrieved_at=NOW,
        document_type=DocumentType.FACTUAL_REPORT,
        visibility=Visibility.INVESTIGATION_EVIDENCE,
        checksum="a" * 64,
    )


def evidence() -> EvidenceItem:
    return EvidenceItem(
        case_id=CASE_ID,
        source_document_id=DOCUMENT_ID,
        structural_unit_id=uuid4(),
        model_run_id=uuid4(),
        type=EvidenceType.TEXT,
        observation="Grounded observation",
        source_locator=TextLocator(start=0, end=8),
        extraction_method=ExtractionMethod.AI,
        confidence=0.9,
    )


@pytest.mark.asyncio
async def test_blind_source_read_records_payload_free_allowed_event() -> None:
    recorder = Recorder()
    service = BlindAccessService(Reader(document()), recorder, now=lambda: NOW)

    result = await service.get_source_metadata(CASE_ID, DOCUMENT_ID)

    assert result.id == DOCUMENT_ID
    assert set(vars(service)) == {"_reader", "_recorder", "_now"}
    assert recorder.events[0].operation is AccessOperation.READ
    assert recorder.events[0].reason_code is AuditReasonCode.ALLOWED_EVIDENCE_READ
    assert "payload" not in type(recorder.events[0]).model_fields


@pytest.mark.asyncio
async def test_blind_source_read_denies_without_revealing_metadata() -> None:
    recorder = Recorder()
    service = BlindAccessService(Reader(None), recorder, now=lambda: NOW)

    with pytest.raises(BlindAccessDenied, match="blind access denied") as error:
        await service.get_source_metadata(CASE_ID, DOCUMENT_ID)

    assert "document" not in str(error.value)
    assert recorder.events[0].operation is AccessOperation.DENY
    assert recorder.events[0].allowed is False
    assert recorder.events[0].reason_code is AuditReasonCode.BLOCKED_DOCUMENT
    assert recorder.events[0].target_document_id == DOCUMENT_ID


@pytest.mark.asyncio
async def test_blind_service_lists_and_gets_only_reader_active_evidence() -> None:
    item = evidence()
    recorder = Recorder()
    service = BlindAccessService(
        Reader(document(), (item,)), recorder, now=lambda: NOW
    )

    listed = await service.list_active_evidence(CASE_ID)
    selected = await service.get_evidence(CASE_ID, item.id)

    assert listed == (item,)
    assert selected == item
    assert recorder.events[0].capability is AccessCapability.SUPABASE_DATABASE
    assert recorder.events[0].target_document_id is None
    assert recorder.events[1].capability is AccessCapability.EVIDENCE_READ
    assert recorder.events[1].target_document_id == DOCUMENT_ID


@pytest.mark.asyncio
async def test_blind_service_denies_unknown_evidence_without_identifier_leak() -> None:
    recorder = Recorder()
    service = BlindAccessService(Reader(document()), recorder, now=lambda: NOW)

    with pytest.raises(BlindAccessDenied, match="blind access denied"):
        await service.get_evidence(CASE_ID, uuid4())

    assert recorder.events[0].operation is AccessOperation.DENY
    assert recorder.events[0].target_document_id is None


class Cursor:
    def __init__(self, event_id: UUID) -> None:
        self.event_id = event_id

    async def fetchone(self) -> tuple[UUID]:
        return (self.event_id,)


class Connection:
    def __init__(self) -> None:
        self.params: tuple[object, ...] | None = None

    async def execute(
        self, query: str, params: tuple[object, ...]
    ) -> Cursor:
        self.params = params
        return Cursor(params[0])


@pytest.mark.asyncio
async def test_postgres_recorder_persists_closed_event_fields_only() -> None:
    connection = Connection()
    event = AccessAuditEvent(
        case_id=CASE_ID,
        stage=WorkflowStage.BLIND,
        actor_role=RuntimeActor.BLIND,
        capability=AccessCapability.MODEL_INFERENCE,
        operation=AccessOperation.NETWORK,
        network_host="api.hyperfusion.io",
        allowed=True,
        reason_code=AuditReasonCode.ALLOWED_MODEL_HOST,
        occurred_at=NOW,
    )

    await PostgresAccessAuditRecorder(connection).record(event)

    assert connection.params == (
        event.id,
        CASE_ID,
        "BLIND",
        "BLIND",
        "MODEL_INFERENCE",
        "NETWORK",
        None,
        "api.hyperfusion.io",
        True,
        "ALLOWED_MODEL_HOST",
    )


@pytest.mark.db
@pytest.mark.skipif(os.getenv("CASEZERO_DB_TEST") != "1", reason="requires CASEZERO_DB_TEST=1")
@pytest.mark.asyncio
async def test_database_audit_function_is_role_bound_and_append_only() -> None:
    case_id = uuid4()
    event = AccessAuditEvent(
        case_id=case_id,
        stage=WorkflowStage.BLIND,
        actor_role=RuntimeActor.BLIND,
        capability=AccessCapability.EVIDENCE_READ,
        operation=AccessOperation.DENY,
        target_document_id=uuid4(),
        allowed=False,
        reason_code=AuditReasonCode.BLOCKED_DOCUMENT,
        occurred_at=NOW,
    )
    async with (
        await AsyncConnection.connect(DATABASE_URL) as connection,
        connection.transaction(force_rollback=True),
    ):
        await connection.execute(
            """
            insert into public.cases (
              id, ntsb_number, title, state, evidence_cutoff
            ) values (%s, %s, 'Audit fixture', 'BLIND', %s)
            """,
            (case_id, f"TEST-{case_id.hex[:12]}", NOW),
        )
        await connection.execute("set local role casezero_blind")
        await PostgresAccessAuditRecorder(connection).record(event)
        await connection.execute("reset role")

        stored = await (
            await connection.execute(
                """
                select stage, actor_role, capability, operation, allowed, reason_code
                from public.access_audit_events where id = %s
                """,
                (event.id,),
            )
        ).fetchone()
        assert stored == (
            "BLIND",
            "BLIND",
            "EVIDENCE_READ",
            "DENY",
            False,
            "BLOCKED_DOCUMENT",
        )

        with pytest.raises(DatabaseError):
            async with connection.transaction():
                await connection.execute(
                    "update public.access_audit_events set allowed = true where id = %s",
                    (event.id,),
                )
