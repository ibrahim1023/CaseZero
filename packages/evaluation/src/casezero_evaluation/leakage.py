from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from casezero_evidence import (
    AccessAuditEvent,
    AccessCapability,
    AccessOperation,
    Visibility,
    WorkflowStage,
)
from casezero_evidence.models import StrictModel
from pydantic import field_validator


class LeakageIncidentCode(StrEnum):
    BLIND_BLOCKED_VISIBILITY_READ = "BLIND_BLOCKED_VISIBILITY_READ"
    BLIND_POST_CUTOFF_READ = "BLIND_POST_CUTOFF_READ"
    EVALUATION_BEFORE_LOCK = "EVALUATION_BEFORE_LOCK"
    FORBIDDEN_BLIND_CAPABILITY = "FORBIDDEN_BLIND_CAPABILITY"
    FORBIDDEN_BLIND_HOST = "FORBIDDEN_BLIND_HOST"
    CLASSIFICATION_MISMATCH = "CLASSIFICATION_MISMATCH"
    POST_LOCK_MUTATION = "POST_LOCK_MUTATION"
    MALFORMED_AUDIT_EVENT = "MALFORMED_AUDIT_EVENT"


class SourceAuditMetadata(StrictModel):
    document_id: UUID
    visibility: Visibility
    published_at: datetime | None
    metadata_eligible: bool

    @field_validator("published_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() != timedelta(0)
        ):
            raise ValueError("datetime must be UTC-aware")
        return value


class LeakageContext(StrictModel):
    case_id: UUID
    evidence_cutoff: datetime
    locked_at: datetime | None = None
    sources: dict[UUID, SourceAuditMetadata]

    @field_validator("evidence_cutoff", "locked_at")
    @classmethod
    def require_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() != timedelta(0)
        ):
            raise ValueError("datetime must be UTC-aware")
        return value


class LeakageIncident(StrictModel):
    code: LeakageIncidentCode
    event_id: UUID
    case_id: UUID
    target_document_id: UUID | None
    occurred_at: datetime


class LeakageReport(StrictModel):
    incidents: tuple[LeakageIncident, ...]

    @property
    def passed(self) -> bool:
        return not self.incidents


_FORBIDDEN_BLIND_CAPABILITIES = {
    AccessCapability.NTSB_ACQUISITION,
    AccessCapability.CONTEXT_DEV,
    AccessCapability.WEB_SEARCH,
    AccessCapability.RAW_STORAGE,
}


def audit_temporal_leakage(
    events: tuple[AccessAuditEvent, ...],
    context: LeakageContext,
    allowed_hosts: set[str],
) -> LeakageReport:
    incidents: list[LeakageIncident] = []
    for event in events:
        codes: set[LeakageIncidentCode] = set()
        source = (
            context.sources.get(event.target_document_id)
            if event.target_document_id is not None
            else None
        )
        if event.case_id != context.case_id:
            codes.add(LeakageIncidentCode.MALFORMED_AUDIT_EVENT)
        if event.stage is WorkflowStage.BLIND:
            if event.capability in _FORBIDDEN_BLIND_CAPABILITIES:
                codes.add(LeakageIncidentCode.FORBIDDEN_BLIND_CAPABILITY)
            if (
                event.operation is AccessOperation.NETWORK
                and event.network_host not in allowed_hosts
            ):
                codes.add(LeakageIncidentCode.FORBIDDEN_BLIND_HOST)
            if event.capability is AccessCapability.EVIDENCE_READ:
                if event.target_document_id is None or source is None:
                    codes.add(LeakageIncidentCode.MALFORMED_AUDIT_EVENT)
                else:
                    if source.visibility is not Visibility.INVESTIGATION_EVIDENCE:
                        codes.add(
                            LeakageIncidentCode.BLIND_BLOCKED_VISIBILITY_READ
                        )
                    if (
                        source.published_at is None
                        or source.published_at > context.evidence_cutoff
                    ):
                        codes.add(LeakageIncidentCode.BLIND_POST_CUTOFF_READ)
                    expected_eligibility = (
                        source.visibility is Visibility.INVESTIGATION_EVIDENCE
                        and source.published_at is not None
                        and source.published_at <= context.evidence_cutoff
                    )
                    if source.metadata_eligible is not expected_eligibility:
                        codes.add(LeakageIncidentCode.CLASSIFICATION_MISMATCH)
        if event.stage is WorkflowStage.EVALUATION and (
            context.locked_at is None or event.occurred_at < context.locked_at
        ):
            codes.add(LeakageIncidentCode.EVALUATION_BEFORE_LOCK)
        if (
            context.locked_at is not None
            and event.stage in {WorkflowStage.PROCESSING, WorkflowStage.BLIND}
            and event.operation is AccessOperation.WRITE
            and event.occurred_at > context.locked_at
        ):
            codes.add(LeakageIncidentCode.POST_LOCK_MUTATION)
        incidents.extend(_incidents(event, codes))
    return LeakageReport(
        incidents=tuple(
            sorted(
                incidents,
                key=lambda incident: (
                    incident.occurred_at,
                    str(incident.event_id),
                    incident.code.value,
                ),
            )
        )
    )


def _incidents(
    event: AccessAuditEvent, codes: set[LeakageIncidentCode]
) -> tuple[LeakageIncident, ...]:
    return tuple(
        LeakageIncident(
            code=code,
            event_id=event.id,
            case_id=event.case_id,
            target_document_id=event.target_document_id,
            occurred_at=event.occurred_at,
        )
        for code in sorted(codes, key=lambda value: value.value)
    )
