from datetime import UTC, datetime, timedelta
from uuid import UUID

from casezero_evaluation import (
    LeakageContext,
    LeakageIncidentCode,
    SourceAuditMetadata,
    audit_temporal_leakage,
)
from casezero_evidence import (
    AccessAuditEvent,
    AccessCapability,
    AccessOperation,
    AuditReasonCode,
    RuntimeActor,
    Visibility,
    WorkflowStage,
)

CASE_ID = UUID("70000000-0000-0000-0000-000000000001")
EVIDENCE_ID = UUID("71000000-0000-0000-0000-000000000001")
FINAL_ID = UUID("71000000-0000-0000-0000-000000000002")
MISMATCH_ID = UUID("71000000-0000-0000-0000-000000000003")
UNKNOWN_ID = UUID("71000000-0000-0000-0000-000000000004")
CUTOFF = datetime(2025, 5, 1, tzinfo=UTC)


def event(
    identifier: int,
    *,
    stage: WorkflowStage = WorkflowStage.BLIND,
    actor: RuntimeActor = RuntimeActor.BLIND,
    capability: AccessCapability = AccessCapability.EVIDENCE_READ,
    operation: AccessOperation = AccessOperation.READ,
    document_id: UUID | None = EVIDENCE_ID,
    host: str | None = None,
    allowed: bool = True,
    occurred_at: datetime = CUTOFF,
) -> AccessAuditEvent:
    reason = (
        AuditReasonCode.BLOCKED_CAPABILITY
        if operation is AccessOperation.DENY
        else AuditReasonCode.ALLOWED_MODEL_HOST
        if operation is AccessOperation.NETWORK
        else AuditReasonCode.ALLOWED_EVIDENCE_READ
    )
    return AccessAuditEvent(
        id=UUID(f"72000000-0000-0000-0000-{identifier:012d}"),
        case_id=CASE_ID,
        stage=stage,
        actor_role=actor,
        capability=capability,
        operation=operation,
        target_document_id=document_id,
        network_host=host,
        allowed=allowed,
        reason_code=reason,
        occurred_at=occurred_at,
    )


def context(lock_time: datetime | None = None) -> LeakageContext:
    return LeakageContext(
        case_id=CASE_ID,
        evidence_cutoff=CUTOFF,
        locked_at=lock_time,
        sources={
            EVIDENCE_ID: SourceAuditMetadata(
                document_id=EVIDENCE_ID,
                visibility=Visibility.INVESTIGATION_EVIDENCE,
                published_at=CUTOFF,
                metadata_eligible=True,
            ),
            FINAL_ID: SourceAuditMetadata(
                document_id=FINAL_ID,
                visibility=Visibility.FINAL_FINDING,
                published_at=CUTOFF,
                metadata_eligible=False,
            ),
            MISMATCH_ID: SourceAuditMetadata(
                document_id=MISMATCH_ID,
                visibility=Visibility.INVESTIGATION_EVIDENCE,
                published_at=CUTOFF,
                metadata_eligible=False,
            ),
        },
    )


def test_allowed_blind_database_and_model_events_pass() -> None:
    events = (
        event(1),
        event(
            2,
            capability=AccessCapability.MODEL_INFERENCE,
            operation=AccessOperation.NETWORK,
            document_id=None,
            host="api.hyperfusion.io",
        ),
        event(
            3,
            capability=AccessCapability.SUPABASE_DATABASE,
            document_id=None,
        ),
    )

    report = audit_temporal_leakage(
        events, context(), allowed_hosts={"api.hyperfusion.io"}
    )

    assert report.passed is True
    assert report.incidents == ()


def test_attack_events_produce_all_incident_codes_in_stable_order() -> None:
    lock_time = CUTOFF + timedelta(hours=1)
    events = (
        event(8, document_id=UNKNOWN_ID),
        event(
            7,
            stage=WorkflowStage.PROCESSING,
            actor=RuntimeActor.PROCESSOR,
            capability=AccessCapability.SUPABASE_DATABASE,
            operation=AccessOperation.WRITE,
            document_id=None,
            occurred_at=lock_time + timedelta(seconds=1),
        ),
        event(
            6,
            capability=AccessCapability.MODEL_INFERENCE,
            operation=AccessOperation.NETWORK,
            document_id=None,
            host="unapproved.example",
        ),
        event(
            5,
            capability=AccessCapability.WEB_SEARCH,
            operation=AccessOperation.DENY,
            document_id=None,
            allowed=False,
        ),
        event(
            4,
            stage=WorkflowStage.EVALUATION,
            actor=RuntimeActor.EVALUATION,
            document_id=FINAL_ID,
            occurred_at=CUTOFF,
        ),
        event(3, document_id=MISMATCH_ID),
        event(2, document_id=FINAL_ID),
        event(1, document_id=EVIDENCE_ID, occurred_at=CUTOFF + timedelta(seconds=1)),
    )
    late_context = context(lock_time)
    late_context.sources[EVIDENCE_ID] = SourceAuditMetadata(
        document_id=EVIDENCE_ID,
        visibility=Visibility.INVESTIGATION_EVIDENCE,
        published_at=CUTOFF + timedelta(seconds=1),
        metadata_eligible=True,
    )

    report = audit_temporal_leakage(
        events, late_context, allowed_hosts={"api.hyperfusion.io"}
    )

    assert {incident.code for incident in report.incidents} == set(LeakageIncidentCode)
    assert report.incidents == tuple(
        sorted(
            report.incidents,
            key=lambda incident: (
                incident.occurred_at,
                str(incident.event_id),
                incident.code.value,
            ),
        )
    )
    assert report.passed is False
