from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from casezero_evaluation import (
    LeakageContext,
    LeakageIncidentCode,
    audit_temporal_leakage,
)
from casezero_evidence import (
    AccessAuditEvent,
    AccessCapability,
    AccessOperation,
    AuditReasonCode,
    RuntimeActor,
    WorkflowStage,
)

CASE_ID = UUID("80000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    "capability",
    [
        AccessCapability.WEB_SEARCH,
        AccessCapability.CONTEXT_DEV,
        AccessCapability.NTSB_ACQUISITION,
        AccessCapability.RAW_STORAGE,
    ],
)
def test_every_forbidden_blind_capability_is_an_incident(
    capability: AccessCapability,
) -> None:
    event = AccessAuditEvent(
        case_id=CASE_ID,
        stage=WorkflowStage.BLIND,
        actor_role=RuntimeActor.BLIND,
        capability=capability,
        operation=AccessOperation.DENY,
        allowed=False,
        reason_code=AuditReasonCode.BLOCKED_CAPABILITY,
        occurred_at=NOW,
    )
    context = LeakageContext(
        case_id=CASE_ID,
        evidence_cutoff=NOW,
        sources={},
    )

    report = audit_temporal_leakage(
        (event,), context, allowed_hosts={"api.groq.com"}
    )

    assert report.passed is False
    assert [incident.code for incident in report.incidents] == [
        LeakageIncidentCode.FORBIDDEN_BLIND_CAPABILITY
    ]


def test_unapproved_host_and_wrong_case_cannot_hide_in_audit_output() -> None:
    event = AccessAuditEvent(
        id=uuid4(),
        case_id=uuid4(),
        stage=WorkflowStage.BLIND,
        actor_role=RuntimeActor.BLIND,
        capability=AccessCapability.MODEL_INFERENCE,
        operation=AccessOperation.NETWORK,
        network_host="unapproved.example",
        allowed=True,
        reason_code=AuditReasonCode.ALLOWED_MODEL_HOST,
        occurred_at=NOW,
    )
    context = LeakageContext(
        case_id=CASE_ID,
        evidence_cutoff=NOW,
        sources={},
    )

    report = audit_temporal_leakage(
        (event,), context, allowed_hosts={"api.groq.com"}
    )

    assert {incident.code for incident in report.incidents} == {
        LeakageIncidentCode.FORBIDDEN_BLIND_HOST,
        LeakageIncidentCode.MALFORMED_AUDIT_EVENT,
    }
    assert "unapproved.example" not in report.model_dump_json()
