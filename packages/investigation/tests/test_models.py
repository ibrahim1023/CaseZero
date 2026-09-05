from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from casezero_investigation import (
    Claim,
    ClaimStatus,
    Hypothesis,
    HypothesisStatus,
    Investigation,
    InvestigationEntity,
    InvestigationStage,
    InvestigationStatus,
    TimelineEvent,
    TimePrecision,
)
from pydantic import ValidationError

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def valid_hypothesis() -> dict[str, object]:
    return {
        "investigation_id": uuid4(),
        "case_id": uuid4(),
        "title": "Fuel interruption",
        "description": "Fuel flow may have been interrupted.",
        "initial_confidence": Decimal("0.3800"),
        "current_confidence": Decimal("0.3800"),
        "status": HypothesisStatus.ACTIVE,
        "distinguishing_prediction": "Fuel-system abnormalities should be present.",
        "weakening_evidence": "Normal uninterrupted fuel flow would weaken it.",
        "model_run_id": uuid4(),
        "created_at": NOW,
    }


def test_hypothesis_uses_quantized_model_confidence() -> None:
    hypothesis = Hypothesis.model_validate(valid_hypothesis())
    assert hypothesis.current_confidence == Decimal("0.3800")


def test_hypothesis_rejects_unquantized_or_overconfident_initial_value() -> None:
    with pytest.raises(ValidationError, match="four decimal"):
        Hypothesis.model_validate(
            valid_hypothesis() | {"current_confidence": Decimal("0.33333")}
        )
    with pytest.raises(ValidationError, match="less than or equal to 0.85"):
        Hypothesis.model_validate(
            valid_hypothesis() | {"initial_confidence": Decimal("0.9000")}
        )


def test_claim_requires_candidate_and_evidence_lineage() -> None:
    values = {
        "investigation_id": uuid4(),
        "case_id": uuid4(),
        "text": "The engine lost power.",
        "status": ClaimStatus.OBSERVED,
        "confidence": Decimal("0.8000"),
        "source_candidate_ids": (uuid4(),),
        "supporting_evidence_ids": (uuid4(),),
        "contradicting_evidence_ids": (),
        "model_run_id": uuid4(),
        "created_at": NOW,
    }
    assert Claim.model_validate(values).text == "The engine lost power."
    with pytest.raises(ValidationError, match="source candidate"):
        Claim.model_validate(values | {"source_candidate_ids": ()})
    with pytest.raises(ValidationError, match="evidence"):
        Claim.model_validate(
            values
            | {
                "supporting_evidence_ids": (),
                "contradicting_evidence_ids": (),
            }
        )


def test_timeline_requires_utc_and_entity_aliases_normalize_for_identity() -> None:
    timeline = {
        "investigation_id": uuid4(),
        "case_id": uuid4(),
        "occurred_at": NOW,
        "time_precision": TimePrecision.EXACT,
        "description": "Recorded event",
        "confidence": Decimal("0.7000"),
        "source_candidate_ids": (uuid4(),),
        "evidence_ids": (uuid4(),),
        "model_run_id": uuid4(),
        "created_at": NOW,
    }
    assert TimelineEvent.model_validate(timeline).occurred_at == NOW
    with pytest.raises(ValidationError, match="UTC-aware"):
        TimelineEvent.model_validate(
            timeline | {"occurred_at": NOW.replace(tzinfo=None)}
        )

    entity = InvestigationEntity(
        investigation_id=uuid4(),
        case_id=uuid4(),
        type="component",
        canonical_name="Fuel Pump",
        aliases=(" fuel   pump ", "PUMP"),
        source_candidate_ids=(uuid4(),),
        evidence_ids=(uuid4(),),
        model_run_id=uuid4(),
        created_at=NOW,
    )
    assert entity.identity_aliases == ("fuel pump", "pump")


def test_investigation_starts_pending_at_timeline_stage() -> None:
    investigation = Investigation(
        case_id=uuid4(),
        status=InvestigationStatus.PENDING,
        current_stage=InvestigationStage.PROMOTE_TIMELINE,
        configuration_hash="a" * 64,
        model_versions={"timeline": "qwen/qwen3-32b"},
        prompt_versions={"timeline": "casezero.timeline.v1"},
        created_at=NOW,
    )
    assert investigation.current_stage is InvestigationStage.PROMOTE_TIMELINE
