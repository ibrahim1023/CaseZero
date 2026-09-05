from datetime import UTC, datetime

import pytest
from casezero_investigation import (
    ClaimCreatedPayload,
    HypothesisCreatedPayload,
    InvestigationEvent,
    InvestigationStage,
    InvestigationStartedPayload,
    InvestigationStatus,
    ReplayError,
    replay,
)
from test_events import CASE_ID, INVESTIGATION_ID, claim, hypothesis

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def event(sequence, event_type, target_type, target_id, payload, previous, digest):
    return InvestigationEvent(
        investigation_id=INVESTIGATION_ID,
        case_id=CASE_ID,
        sequence=sequence,
        event_type=event_type,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
        previous_event_hash=previous,
        event_hash=digest,
        hash_algorithm="postgres-investigation-event-v1",
        created_at=NOW,
    )


def test_replay_reconstructs_created_claim_and_hypothesis() -> None:
    claim_record = claim()
    hypothesis_record = hypothesis()
    events = (
        event(
            1,
            "INVESTIGATION_STARTED",
            "investigation",
            INVESTIGATION_ID,
            InvestigationStartedPayload(
                configuration_hash="a" * 64,
                current_stage=InvestigationStage.PROMOTE_TIMELINE,
                status=InvestigationStatus.PENDING,
            ),
            None,
            "1" * 64,
        ),
        event(
            2,
            "CLAIM_CREATED",
            "claim",
            claim_record.id,
            ClaimCreatedPayload(record=claim_record),
            "1" * 64,
            "2" * 64,
        ),
        event(
            3,
            "HYPOTHESIS_CREATED",
            "hypothesis",
            hypothesis_record.id,
            HypothesisCreatedPayload(record=hypothesis_record),
            "2" * 64,
            "3" * 64,
        ),
    )

    projection = replay(events)

    assert projection.claims == {claim_record.id: claim_record}
    assert projection.hypotheses == {hypothesis_record.id: hypothesis_record}
    assert projection.event_head_hash == "3" * 64


def test_replay_rejects_sequence_gap_or_hash_break() -> None:
    started = event(
        1,
        "INVESTIGATION_STARTED",
        "investigation",
        INVESTIGATION_ID,
        InvestigationStartedPayload(
            configuration_hash="a" * 64,
            current_stage=InvestigationStage.PROMOTE_TIMELINE,
            status=InvestigationStatus.PENDING,
        ),
        None,
        "1" * 64,
    )
    claim_record = claim()

    with pytest.raises(ReplayError, match="sequence"):
        replay(
            (
                started,
                event(
                    3,
                    "CLAIM_CREATED",
                    "claim",
                    claim_record.id,
                    ClaimCreatedPayload(record=claim_record),
                    "1" * 64,
                    "2" * 64,
                ),
            )
        )
    with pytest.raises(ReplayError, match="previous hash"):
        replay(
            (
                started,
                event(
                    2,
                    "CLAIM_CREATED",
                    "claim",
                    claim_record.id,
                    ClaimCreatedPayload(record=claim_record),
                    "f" * 64,
                    "2" * 64,
                ),
            )
        )
