from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_UP, Decimal, localcontext
from uuid import UUID, uuid4

import pytest
from casezero_investigation.generation import (
    HYPOTHESES_PROMPT,
    HypothesisBundle,
    HypothesisClaimLink,
    HypothesisDraft,
    HypothesisGenerationResult,
    materialize_hypotheses,
    normalized_hypothesis_overlap,
)
from casezero_investigation.models import Claim, ClaimStatus, HypothesisStatus, LinkPolarity
from pydantic import ValidationError

CASE_ID = UUID(int=1)
INVESTIGATION_ID = UUID(int=2)
MODEL_RUN_ID = UUID(int=3)
CLAIM_ID = UUID(int=201)
OTHER_CLAIM_ID = UUID(int=202)
NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


def claim(identifier: UUID = CLAIM_ID, **changes: object) -> Claim:
    return Claim.model_validate(
        {
            "id": identifier,
            "investigation_id": INVESTIGATION_ID,
            "case_id": CASE_ID,
            "text": "Synthetic unit-test observation may indicate interrupted flow.",
            "status": ClaimStatus.INFERRED,
            "confidence": Decimal("0.6000"),
            "source_candidate_ids": (UUID(int=301),),
            "supporting_evidence_ids": (UUID(int=101),),
            "contradicting_evidence_ids": (),
            "model_run_id": UUID(int=4),
            "created_at": NOW - timedelta(minutes=1),
        }
        | changes
    )


def draft(**changes: object) -> HypothesisDraft:
    return HypothesisDraft.model_validate(
        {
            "title": "Interrupted fuel flow",
            "description": "A restricted supply path could explain reduced output.",
            "confidence": 0.38005,
            "distinguishing_prediction": "A flow restriction should be identifiable.",
            "weakening_evidence": "Continuous normal fuel flow would weaken this explanation.",
            "supporting_claim_ids": (CLAIM_ID,),
            "contradicting_claim_ids": (OTHER_CLAIM_ID,),
            "unresolved_questions": ("Was the flow measurement independently verified?",),
        }
        | changes
    )


def distinct_drafts() -> tuple[HypothesisDraft, ...]:
    return (
        draft(),
        draft(
            title="Recorder artifact",
            description="An instrument error may have distorted the measured values.",
            distinguishing_prediction="Independent sensor channels would disagree.",
            weakening_evidence="Agreement between redundant channels would weaken this explanation.",
            supporting_claim_ids=(),
            contradicting_claim_ids=(CLAIM_ID,),
            unresolved_questions=(),
            confidence=0.5,
        ),
        draft(
            title="Transient atmospheric disturbance",
            description="A localized gust might account for temporary motion changes.",
            distinguishing_prediction="Nearby weather samples would indicate a disturbance.",
            weakening_evidence="Stable local wind measurements would weaken this explanation.",
            supporting_claim_ids=(OTHER_CLAIM_ID,),
            contradicting_claim_ids=(),
            unresolved_questions=("Were nearby wind measurements available?", "How localized was the gust?"),
            confidence=0.85,
        ),
    )


def test_generation_materializes_typed_hypotheses_links_and_questions_without_mutation() -> None:
    output = HypothesisGenerationResult(hypotheses=distinct_drafts())
    claims = (claim(OTHER_CLAIM_ID), claim())
    before = (output.model_dump(), tuple(item.model_dump() for item in claims))
    with localcontext() as context:
        context.rounding = ROUND_UP
        bundle = materialize_hypotheses(
            output, claims, INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )
    assert isinstance(bundle, HypothesisBundle)
    assert isinstance(bundle.hypotheses, tuple)
    assert isinstance(bundle.claim_links, tuple)
    assert isinstance(bundle.questions, tuple)
    assert len(bundle.hypotheses) == 3 and len(bundle.claim_links) == 4
    assert len(bundle.questions) == 3
    assert len({item.id for item in (*bundle.hypotheses, *bundle.questions)}) == 6
    by_title = {item.title: item for item in bundle.hypotheses}
    for source in output.hypotheses:
        hypothesis = by_title[source.title]
        assert hypothesis.id.version == 4
        assert hypothesis.investigation_id == INVESTIGATION_ID and hypothesis.case_id == CASE_ID
        assert hypothesis.model_run_id == MODEL_RUN_ID and hypothesis.created_at == NOW
        assert hypothesis.status is HypothesisStatus.ACTIVE
        assert hypothesis.current_confidence == hypothesis.initial_confidence
        assert hypothesis.current_confidence.as_tuple().exponent == -4
        assert hypothesis.description == source.description
        assert hypothesis.distinguishing_prediction == source.distinguishing_prediction
        assert hypothesis.weakening_evidence == source.weakening_evidence
        links = tuple(link for link in bundle.claim_links if link.hypothesis_id == hypothesis.id)
        assert all(isinstance(link, HypothesisClaimLink) for link in links)
        assert {(link.claim_id, link.polarity) for link in links} == {
            *((identifier, LinkPolarity.SUPPORTING) for identifier in source.supporting_claim_ids),
            *((identifier, LinkPolarity.CONTRADICTING) for identifier in source.contradicting_claim_ids),
        }
        questions = tuple(item for item in bundle.questions if item.hypothesis_id == hypothesis.id)
        assert {item.text for item in questions} == set(source.unresolved_questions)
        assert all(item.created_at == NOW and item.id.version == 4 for item in questions)
    assert by_title[output.hypotheses[0].title].initial_confidence == Decimal("0.3800")
    for scoped in (*bundle.claim_links, *bundle.questions):
        assert scoped.investigation_id == INVESTIGATION_ID and scoped.case_id == CASE_ID
    assert HypothesisBundle.model_validate_json(bundle.model_dump_json()) == bundle
    assert (output.model_dump(), tuple(item.model_dump() for item in claims)) == before


def test_generation_preserves_explicit_dual_polarity_claim_links() -> None:
    drafts = distinct_drafts()
    dual = draft(supporting_claim_ids=(CLAIM_ID,), contradicting_claim_ids=(CLAIM_ID,))
    bundle = materialize_hypotheses(
        HypothesisGenerationResult(hypotheses=(dual, *drafts[1:])),
        (claim(), claim(OTHER_CLAIM_ID)), INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
    )
    links = tuple(link for link in bundle.claim_links if link.hypothesis_id == bundle.hypotheses[0].id)
    assert {(link.claim_id, link.polarity) for link in links} == {
        (CLAIM_ID, LinkPolarity.SUPPORTING), (CLAIM_ID, LinkPolarity.CONTRADICTING),
    }


@pytest.mark.parametrize("count", [0, 1, 2, 6])
def test_generation_requires_between_three_and_five_drafts(count: int) -> None:
    with pytest.raises(ValidationError, match="at least 3|at most 5"):
        HypothesisGenerationResult(hypotheses=tuple(draft() for _ in range(count)))


def test_generation_accepts_five_distinct_hypotheses() -> None:
    drafts = (
        *distinct_drafts(),
        draft(title="Telemetry clock drift", description="Unsynchronized clocks may misalign channels."),
        draft(title="Electrical instability", description="Fluctuating bus voltage could interrupt power."),
    )
    output = HypothesisGenerationResult(hypotheses=drafts)
    assert len(materialize_hypotheses(
        output, (claim(), claim(OTHER_CLAIM_ID)), INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
    ).hypotheses) == 5


def test_overlap_normalizes_unicode_case_punctuation_and_repeated_tokens() -> None:
    left = draft(title="Fuel–pump pressure", description="ABNORMAL abnormal")
    right = draft(title="ＦＵＥＬ pump", description="Pressure abnormal!")
    assert normalized_hypothesis_overlap(left, right) == Decimal(1)


def test_exactly_three_quarters_overlap_is_permitted() -> None:
    left = draft(title="alpha beta", description="gamma")
    right = draft(title="alpha beta", description="gamma delta")
    third = draft(title="epsilon zeta", description="eta theta")
    assert normalized_hypothesis_overlap(left, right) == Decimal("0.75")
    assert len(HypothesisGenerationResult(hypotheses=(left, right, third)).hypotheses) == 3


def test_diversity_checks_all_pairs_and_does_not_count_new_predictions_as_distinct() -> None:
    first, second, _ = distinct_drafts()
    renamed = draft(
        title=second.title.upper(), description=second.description.upper(),
        distinguishing_prediction="A different prediction cannot rescue identical explanations.",
    )
    with pytest.raises(ValidationError, match="overlap|distinct|duplicate"):
        HypothesisGenerationResult(hypotheses=(first, second, renamed))


def test_overlap_above_threshold_is_not_rounded_down_to_three_quarters() -> None:
    common = tuple(f"token{index}" for index in range(15001))
    extra = tuple(f"other{index}" for index in range(5000))
    left = draft(title=common[0], description=" ".join(common[1:]))
    right = draft(title=common[0], description=" ".join((*common[1:], *extra)))
    overlap = normalized_hypothesis_overlap(left, right)
    assert Decimal("0.75") < overlap < Decimal("0.75005")
    with pytest.raises(ValidationError, match="overlap|distinct|duplicate"):
        HypothesisGenerationResult(hypotheses=(left, right, distinct_drafts()[0]))


@pytest.mark.parametrize("field", ["supporting_claim_ids", "contradicting_claim_ids"])
def test_generation_rejects_invented_claim_references(field: str) -> None:
    drafts = distinct_drafts()
    output = HypothesisGenerationResult(hypotheses=(draft(**{field: (uuid4(),)}), *drafts[1:]))
    with pytest.raises(ValueError, match="claim"):
        materialize_hypotheses(
            output, (claim(), claim(OTHER_CLAIM_ID)), INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


@pytest.mark.parametrize("field", ["case_id", "investigation_id"])
def test_generation_rejects_foreign_claim_context_even_when_unreferenced(field: str) -> None:
    with pytest.raises(ValueError, match="context|case|investigation"):
        materialize_hypotheses(
            HypothesisGenerationResult(hypotheses=distinct_drafts()),
            (claim(), claim(OTHER_CLAIM_ID), claim(UUID(int=203), **{field: uuid4()})),
            INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


def test_generation_rejects_duplicate_input_claim_ids_and_no_available_claims() -> None:
    output = HypothesisGenerationResult(hypotheses=distinct_drafts())
    for claims in ((), (claim(), claim(), claim(OTHER_CLAIM_ID))):
        with pytest.raises(ValueError, match="claim"):
            materialize_hypotheses(output, claims, INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW)


def test_hypothesis_drafts_require_grounding_and_unique_references() -> None:
    with pytest.raises(ValidationError, match="claim|reference"):
        draft(supporting_claim_ids=(), contradicting_claim_ids=())
    for field in ("supporting_claim_ids", "contradicting_claim_ids"):
        with pytest.raises(ValidationError, match="unique|duplicate"):
            draft(**{field: (CLAIM_ID, CLAIM_ID)})


@pytest.mark.parametrize("field", ["title", "description", "distinguishing_prediction", "weakening_evidence"])
def test_hypothesis_substantive_text_must_not_be_blank(field: str) -> None:
    with pytest.raises(ValidationError):
        draft(**{field: " \n\t "})


def test_unresolved_questions_require_nonblank_distinct_text() -> None:
    with pytest.raises(ValidationError):
        draft(unresolved_questions=(" \n",))
    with pytest.raises(ValidationError, match="unique|duplicate"):
        draft(unresolved_questions=("Unknown measurement?", " unknown   MEASUREMENT? "))


@pytest.mark.parametrize("confidence", [-0.1, 0.04999, 0.85001, 1.0, float("nan"), float("inf"), "0.7", True])
def test_initial_confidence_bounds_apply_before_quantization(confidence: object) -> None:
    with pytest.raises(ValidationError):
        draft(confidence=confidence)


@pytest.mark.parametrize("confidence", [0.05, 0.85])
def test_initial_confidence_includes_both_bounds(confidence: float) -> None:
    assert draft(confidence=confidence).confidence == confidence


@pytest.mark.parametrize("field", ["id", "status", "case_id", "investigation_id", "model_run_id", "created_at"])
def test_generation_drafts_cannot_choose_canonical_ids_or_context(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        draft(**{field: uuid4()})


def test_generation_contracts_are_strict_tuple_models() -> None:
    output = HypothesisGenerationResult(hypotheses=distinct_drafts())
    assert HypothesisGenerationResult.model_validate_json(output.model_dump_json()) == output
    with pytest.raises(ValidationError):
        HypothesisGenerationResult.model_validate({"hypotheses": list(distinct_drafts())})
    with pytest.raises(ValidationError):
        draft(supporting_claim_ids=[CLAIM_ID])
    with pytest.raises(ValidationError, match="Extra inputs"):
        HypothesisGenerationResult.model_validate(output.model_dump() | {"official_finding": "not allowed"})
    bundle = materialize_hypotheses(
        output, (claim(), claim(OTHER_CLAIM_ID)), INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
    )
    with pytest.raises(ValidationError):
        HypothesisBundle.model_validate(bundle.model_dump() | {"claim_links": list(bundle.claim_links)})
    with pytest.raises(ValidationError, match="Extra inputs"):
        HypothesisClaimLink.model_validate(bundle.claim_links[0].model_dump() | {"rationale": "not allowed"})


@pytest.mark.parametrize("created_at", [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=2)))])
def test_generation_requires_aware_utc_context(created_at: datetime) -> None:
    with pytest.raises(ValueError, match="UTC-aware"):
        materialize_hypotheses(
            HypothesisGenerationResult(hypotheses=distinct_drafts()),
            (claim(), claim(OTHER_CLAIM_ID)), INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, created_at,
        )


def test_generation_revalidates_mutated_output_and_claim_timestamps() -> None:
    output = HypothesisGenerationResult(hypotheses=distinct_drafts())
    output.hypotheses[0].confidence = 0.95
    with pytest.raises(ValidationError):
        materialize_hypotheses(
            output, (claim(), claim(OTHER_CLAIM_ID)), INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )
    invalid_claim = claim()
    invalid_claim.created_at = NOW.replace(tzinfo=None)
    with pytest.raises(ValidationError, match="UTC-aware"):
        materialize_hypotheses(
            HypothesisGenerationResult(hypotheses=distinct_drafts()),
            (invalid_claim, claim(OTHER_CLAIM_ID)), INVESTIGATION_ID, CASE_ID, MODEL_RUN_ID, NOW,
        )


def test_hypotheses_prompt_is_versioned_provisional_and_requires_competing_explanations() -> None:
    assert HYPOTHESES_PROMPT.startswith("casezero.hypotheses.v1:")
    for instruction in (
        "provisional", "not an official finding", "cite", "ids", "unknown", "blame",
        "distinguishing", "weakening", "model confidence", "3", "5",
    ):
        assert instruction in HYPOTHESES_PROMPT.casefold()
    assert "CEN22FA375" not in HYPOTHESES_PROMPT
