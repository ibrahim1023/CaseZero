from datetime import UTC, datetime
from uuid import UUID

import pytest
from casezero_evidence.candidates import (
    ClaimCandidate,
    ClaimStatus,
    EntityCandidate,
    TimelineCandidate,
    TimePrecision,
)
from pydantic import ValidationError

CASE_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e6f")
EVIDENCE_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e73")
MODEL_RUN_ID = UUID("018f9c7e-3b2a-7c1d-9e4f-1a2b3c4d5e74")
NOW = datetime(2026, 8, 25, tzinfo=UTC)


def test_claim_candidate_requires_supporting_or_contradicting_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        ClaimCandidate(
            case_id=CASE_ID,
            text="The engine lost power.",
            status=ClaimStatus.INFERRED,
            supporting_evidence_ids=(),
            contradicting_evidence_ids=(),
            confidence=0.5,
            model_run_id=MODEL_RUN_ID,
            created_at=NOW,
        )


def test_entity_candidate_preserves_mentions_without_canonical_reference() -> None:
    candidate = EntityCandidate(
        case_id=CASE_ID,
        type="component",
        proposed_canonical_name="left rudder cable",
        aliases=("left cable",),
        evidence_ids=(EVIDENCE_ID,),
        confidence=0.8,
        model_run_id=MODEL_RUN_ID,
        created_at=NOW,
    )

    assert candidate.evidence_ids == (EVIDENCE_ID,)


def test_timeline_candidate_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError, match="UTC-aware"):
        TimelineCandidate(
            case_id=CASE_ID,
            occurred_at=NOW.replace(tzinfo=None),
            time_precision=TimePrecision.EXACT,
            description="Aircraft departed controlled flight.",
            evidence_ids=(EVIDENCE_ID,),
            confidence=0.9,
            model_run_id=MODEL_RUN_ID,
            created_at=NOW,
        )


def test_relative_timeline_candidate_may_omit_absolute_time() -> None:
    candidate = TimelineCandidate(
        case_id=CASE_ID,
        occurred_at=None,
        time_precision=TimePrecision.RELATIVE,
        description="Shortly after takeoff, smoke became visible.",
        evidence_ids=(EVIDENCE_ID,),
        confidence=0.7,
        model_run_id=MODEL_RUN_ID,
        created_at=NOW,
    )

    assert candidate.occurred_at is None
