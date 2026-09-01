from datetime import UTC, datetime
from uuid import uuid4

import pytest
from casezero_evidence import (
    AccessAuditEvent,
    AccessCapability,
    AccessOperation,
    AssessmentSnapshot,
    AuditReasonCode,
    InvestigationLock,
    RuntimeActor,
    WorkflowStage,
)
from pydantic import ValidationError

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def test_lock_contract_requires_versioned_snapshot_and_hashes() -> None:
    lock = InvestigationLock(
        case_id=uuid4(),
        assessment_snapshot=AssessmentSnapshot(
            schema_version="phase2-lock-contract-v1",
            assessment_kind="fixture",
            payload={"result": "INSUFFICIENT_EVIDENCE"},
        ),
        assessment_hash="a" * 64,
        evidence_set_hash="b" * 64,
        hash_algorithm="postgres-jsonb-text-v1",
        model_versions={"evidence": "qwen/qwen3-32b"},
        prompt_versions={"evidence": "casezero.evidence.v1"},
        system_version="phase2-test",
        locked_at=NOW,
    )

    assert lock.hash_algorithm == "postgres-jsonb-text-v1"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"assessment_hash": "invalid"}, "assessment_hash"),
        ({"model_versions": {}}, "model_versions"),
        ({"prompt_versions": {}}, "prompt_versions"),
        ({"locked_at": NOW.replace(tzinfo=None)}, "UTC-aware"),
    ],
)
def test_lock_contract_fails_closed(changes: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "case_id": uuid4(),
        "assessment_snapshot": AssessmentSnapshot(
            schema_version="phase2-lock-contract-v1",
            assessment_kind="fixture",
            payload={},
        ),
        "assessment_hash": "a" * 64,
        "evidence_set_hash": "b" * 64,
        "hash_algorithm": "postgres-jsonb-text-v1",
        "model_versions": {"evidence": "model-v1"},
        "prompt_versions": {"evidence": "prompt-v1"},
        "system_version": "phase2-test",
        "locked_at": NOW,
    }
    values.update(changes)

    with pytest.raises(ValidationError, match=message):
        InvestigationLock.model_validate(values)


def test_network_event_requires_hostname_and_contains_no_payload_surface() -> None:
    with pytest.raises(ValidationError, match="network_host"):
        AccessAuditEvent(
            case_id=uuid4(),
            stage=WorkflowStage.BLIND,
            actor_role=RuntimeActor.BLIND,
            capability=AccessCapability.MODEL_INFERENCE,
            operation=AccessOperation.NETWORK,
            allowed=True,
            reason_code=AuditReasonCode.ALLOWED_MODEL_HOST,
            occurred_at=NOW,
        )

    with pytest.raises(ValidationError, match="network_host"):
        AccessAuditEvent(
            case_id=uuid4(),
            stage=WorkflowStage.BLIND,
            actor_role=RuntimeActor.BLIND,
            capability=AccessCapability.MODEL_INFERENCE,
            operation=AccessOperation.NETWORK,
            network_host="api.example.test/path?key=secret",
            allowed=True,
            reason_code=AuditReasonCode.ALLOWED_MODEL_HOST,
            occurred_at=NOW,
        )


def test_access_event_is_strict_and_utc_aware() -> None:
    event = AccessAuditEvent(
        case_id=uuid4(),
        stage=WorkflowStage.BLIND,
        actor_role=RuntimeActor.BLIND,
        capability=AccessCapability.EVIDENCE_READ,
        operation=AccessOperation.READ,
        target_document_id=uuid4(),
        allowed=True,
        reason_code=AuditReasonCode.ALLOWED_EVIDENCE_READ,
        occurred_at=NOW,
    )
    assert "payload" not in type(event).model_fields

    with pytest.raises(ValidationError, match="Extra inputs"):
        AccessAuditEvent.model_validate(
            {**event.model_dump(), "payload": "private source text"}
        )
