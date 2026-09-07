from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from casezero_investigation.canonical import canonical_digest
from casezero_investigation.events import (
    ClaimCreatedPayload,
    HypothesisCreatedPayload,
    InvestigationEvent,
    InvestigationStartedPayload,
)
from casezero_investigation.models import (
    Claim,
    ClaimStatus,
    Hypothesis,
    HypothesisStatus,
    InvestigationStage,
    InvestigationStatus,
)
from pydantic import ValidationError

CASE_ID = UUID("a0000000-0000-0000-0000-000000000001")
INVESTIGATION_ID = UUID("a1000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 9, 5, tzinfo=UTC)


def claim() -> Claim:
    return Claim(
        investigation_id=INVESTIGATION_ID,
        case_id=CASE_ID,
        text="The engine lost power.",
        status=ClaimStatus.OBSERVED,
        confidence=Decimal("0.8000"),
        source_candidate_ids=(uuid4(),),
        supporting_evidence_ids=(uuid4(),),
        contradicting_evidence_ids=(),
        model_run_id=uuid4(),
        created_at=NOW,
    )


def hypothesis() -> Hypothesis:
    return Hypothesis(
        investigation_id=INVESTIGATION_ID,
        case_id=CASE_ID,
        title="Fuel interruption",
        description="Fuel flow may have been interrupted.",
        initial_confidence=Decimal("0.3800"),
        current_confidence=Decimal("0.3800"),
        status=HypothesisStatus.ACTIVE,
        distinguishing_prediction="Fuel-system abnormalities should exist.",
        weakening_evidence="Normal uninterrupted fuel flow would weaken it.",
        model_run_id=uuid4(),
        created_at=NOW,
    )


def test_event_payload_is_typed_and_hash_link_is_strict() -> None:
    payload = InvestigationStartedPayload(
        configuration_hash="a" * 64,
        current_stage=InvestigationStage.PROMOTE_TIMELINE,
        status=InvestigationStatus.PENDING,
    )
    event = InvestigationEvent(
        investigation_id=INVESTIGATION_ID,
        case_id=CASE_ID,
        sequence=1,
        event_type="INVESTIGATION_STARTED",
        target_type="investigation",
        target_id=INVESTIGATION_ID,
        payload=payload,
        previous_event_hash=None,
        event_hash="b" * 64,
        hash_algorithm="postgres-investigation-event-v1",
        created_at=NOW,
    )
    assert event.payload == payload
    assert event.payload.schema_version == "1"
    assert event.hash_envelope() == {
        "previous_hash": None,
        "investigation_id": str(INVESTIGATION_ID),
        "case_id": str(CASE_ID),
        "sequence": 1,
        "event_type": "INVESTIGATION_STARTED",
        "target_type": "investigation",
        "target_id": str(INVESTIGATION_ID),
        "payload": payload.model_dump(mode="json"),
        "model_run_id": None,
    }
    assert event.computed_hash() == canonical_digest(event.hash_envelope())
    assert event.computed_hash() == event.model_copy(update={
        "id": uuid4(), "created_at": datetime(2026, 9, 6, tzinfo=UTC),
    }).computed_hash()
    with pytest.raises(ValidationError, match="previous_event_hash"):
        InvestigationEvent.model_validate(
            event.model_dump() | {"sequence": 2, "previous_event_hash": None}
        )


def test_created_payloads_retain_complete_canonical_records() -> None:
    claim_record = claim()
    hypothesis_record = hypothesis()
    assert ClaimCreatedPayload(record=claim_record).record == claim_record
    assert HypothesisCreatedPayload(
        record=hypothesis_record,
        supporting_claim_ids=(claim_record.id,),
        contradicting_claim_ids=(),
        questions=(),
    ).record == hypothesis_record


def test_hypothesis_creation_requires_claim_links() -> None:
    with pytest.raises(ValidationError, match="claim"):
        HypothesisCreatedPayload(record=hypothesis())


def test_payload_construction_rejects_model_copy_hidden_extra_fields() -> None:
    record = claim().model_copy(update={"unexpected": "field"})
    with pytest.raises(ValidationError, match="Extra inputs"):
        ClaimCreatedPayload(record=record)
