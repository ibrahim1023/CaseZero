from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from psycopg import AsyncConnection

from casezero_evidence.blindness import (
    AccessAuditEvent,
    AccessCapability,
    AccessOperation,
    AuditReasonCode,
    RuntimeActor,
    WorkflowStage,
)
from casezero_evidence.models import SourceDocument


class BlindAccessDenied(PermissionError):
    pass


class BlindSourceReader(Protocol):
    async def get_source_document_by_id(
        self, case_id: UUID, document_id: UUID
    ) -> SourceDocument | None: ...


class AccessAuditRecorder(Protocol):
    async def record(self, event: AccessAuditEvent) -> None: ...


class PostgresAccessAuditRecorder:
    def __init__(self, connection: AsyncConnection[tuple[object, ...]]) -> None:
        self._connection = connection

    async def record(self, event: AccessAuditEvent) -> None:
        cursor = await self._connection.execute(
            """
            select public.append_access_audit_event(
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                event.id,
                event.case_id,
                event.stage.value,
                event.actor_role.value,
                event.capability.value,
                event.operation.value,
                event.target_document_id,
                event.network_host,
                event.allowed,
                event.reason_code.value,
            ),
        )
        if await cursor.fetchone() != (event.id,):
            raise RuntimeError("access audit event was not persisted")


class BlindAccessService:
    def __init__(
        self,
        reader: BlindSourceReader,
        recorder: AccessAuditRecorder,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._reader = reader
        self._recorder = recorder
        self._now = now or (lambda: datetime.now(UTC))

    async def get_source_metadata(
        self, case_id: UUID, document_id: UUID
    ) -> SourceDocument:
        document = await self._reader.get_source_document_by_id(
            case_id, document_id
        )
        if document is None:
            await self._recorder.record(
                self._event(
                    case_id,
                    document_id,
                    AccessOperation.DENY,
                    False,
                    AuditReasonCode.BLOCKED_DOCUMENT,
                )
            )
            raise BlindAccessDenied("blind access denied")
        await self._recorder.record(
            self._event(
                case_id,
                document_id,
                AccessOperation.READ,
                True,
                AuditReasonCode.ALLOWED_EVIDENCE_READ,
            )
        )
        return document

    def _event(
        self,
        case_id: UUID,
        document_id: UUID,
        operation: AccessOperation,
        allowed: bool,
        reason_code: AuditReasonCode,
    ) -> AccessAuditEvent:
        return AccessAuditEvent(
            case_id=case_id,
            stage=WorkflowStage.BLIND,
            actor_role=RuntimeActor.BLIND,
            capability=AccessCapability.EVIDENCE_READ,
            operation=operation,
            target_document_id=document_id,
            allowed=allowed,
            reason_code=reason_code,
            occurred_at=self._now(),
        )
