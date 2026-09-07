from datetime import UTC, datetime

import pytest
from casezero_investigation.events import (
    ClaimCreatedPayload,
    HypothesisCreatedPayload,
    InvestigationEvent,
    InvestigationStartedPayload,
)
from casezero_investigation.models import InvestigationStage, InvestigationStatus
from casezero_investigation.replay import ReplayError, replay

from .test_events import CASE_ID, INVESTIGATION_ID, claim, hypothesis

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def event(sequence, event_type, target_type, target_id, payload, previous=None):
    value = InvestigationEvent(
        investigation_id=INVESTIGATION_ID,
        case_id=CASE_ID,
        sequence=sequence,
        event_type=event_type,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
        model_run_id=getattr(getattr(payload, "record", None), "model_run_id", None),
        previous_event_hash=previous,
        event_hash="0" * 64,
        hash_algorithm="postgres-investigation-event-v1",
        created_at=NOW,
    )
    return value.model_copy(update={"event_hash": value.computed_hash()})


def started_event():
    return event(
        1, "INVESTIGATION_STARTED", "investigation", INVESTIGATION_ID,
        InvestigationStartedPayload(
            configuration_hash="a" * 64,
            current_stage=InvestigationStage.PROMOTE_TIMELINE,
            status=InvestigationStatus.PENDING,
        ),
    )


def test_replay_reconstructs_created_claim_and_hypothesis() -> None:
    claim_record = claim()
    hypothesis_record = hypothesis()
    started = started_event()
    claimed = event(
        2, "CLAIM_CREATED", "claim", claim_record.id,
        ClaimCreatedPayload(record=claim_record), started.event_hash,
    )
    proposed = event(
        3, "HYPOTHESIS_CREATED", "hypothesis", hypothesis_record.id,
        HypothesisCreatedPayload(
            record=hypothesis_record, supporting_claim_ids=(claim_record.id,),
        ), claimed.event_hash,
    )
    projection = replay((started, claimed, proposed))
    assert projection.claims == {claim_record.id: claim_record}
    assert projection.hypotheses == {hypothesis_record.id: hypothesis_record}
    assert projection.event_head_hash == proposed.event_hash


def test_replay_rejects_a_forged_event_digest() -> None:
    with pytest.raises(ReplayError, match="digest"):
        replay((started_event().model_copy(update={"event_hash": "f" * 64}),))


def test_replay_rejects_tampered_payload_even_with_valid_hash_links() -> None:
    started = started_event()
    changed = started.model_copy(update={
        "payload": started.payload.model_copy(update={"configuration_hash": "b" * 64})
    })
    with pytest.raises(ReplayError, match="digest"):
        replay((changed,))


def test_replay_rejects_sequence_gap_or_hash_break() -> None:
    started = started_event()
    claim_record = claim()
    with pytest.raises(ReplayError, match="sequence"):
        replay((started, event(
            3, "CLAIM_CREATED", "claim", claim_record.id,
            ClaimCreatedPayload(record=claim_record), started.event_hash,
        )))
    with pytest.raises(ReplayError, match="previous hash"):
        replay((started, event(
            2, "CLAIM_CREATED", "claim", claim_record.id,
            ClaimCreatedPayload(record=claim_record), "f" * 64,
        )))
