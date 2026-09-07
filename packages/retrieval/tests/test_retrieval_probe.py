from fractions import Fraction
from uuid import UUID

import pytest
from casezero_retrieval import (
    RetrievalProbeCategory,
    RetrievalProbeSample,
    passes_d2_recall_gate,
    recall_at_10,
)
from pydantic import ValidationError

EXPECTED = tuple(UUID(int=value) for value in range(1, 21))
CATEGORIES = tuple(RetrievalProbeCategory)


def samples(
    hybrid_hits: tuple[int, int, int, int] = (10, 9, 5, 4),
    *,
    baseline_hits: int = 5,
    expected: tuple[UUID, ...] = EXPECTED,
) -> tuple[RetrievalProbeSample, ...]:
    return tuple(
        RetrievalProbeSample(
            query_id=f"synthetic-metric-{category.value}-{index}",
            category=category,
            expected_evidence_ids=expected,
            baseline_evidence_ids=expected[:baseline_hits],
            hybrid_evidence_ids=expected[: hybrid_hits[category_index]],
        )
        for category_index, category in enumerate(CATEGORIES)
        for index in range(3)
    )


def test_retrieval_recall_at_10_is_an_exact_ratio_not_precision_at_10() -> None:
    assert recall_at_10(EXPECTED[:10], EXPECTED) == Fraction(1, 2)
    assert recall_at_10(EXPECTED[:2], EXPECTED[:3]) == Fraction(2, 3)
    assert recall_at_10((), EXPECTED) == Fraction(0)
    assert recall_at_10((UUID(int=999),), EXPECTED) == Fraction(0)
    assert recall_at_10(EXPECTED[:3], EXPECTED[:3]) == Fraction(1)


def test_retrieval_recall_uses_only_top_ten_and_never_counts_duplicate_hits_twice() -> None:
    assert recall_at_10(EXPECTED, EXPECTED) == Fraction(1, 2)
    ranking = (EXPECTED[0],) * 10 + EXPECTED[1:]
    assert recall_at_10(ranking, EXPECTED[:3]) == Fraction(1, 3)
    assert recall_at_10(EXPECTED[:2], EXPECTED[:3] + EXPECTED[:2]) == Fraction(2, 3)


def test_retrieval_recall_does_not_invent_a_score_for_an_unlabeled_query() -> None:
    with pytest.raises(ValueError, match="expected evidence"):
        recall_at_10(EXPECTED, ())
    with pytest.raises(ValidationError):
        RetrievalProbeSample(
            query_id="unlabeled-metric-fixture",
            category=CATEGORIES[0],
            expected_evidence_ids=(),
            baseline_evidence_ids=(),
            hybrid_evidence_ids=(),
        )


def test_retrieval_d2_accepts_exact_mean_and_regression_boundaries() -> None:
    assert passes_d2_recall_gate(samples()) is True
    assert passes_d2_recall_gate(tuple(reversed(samples()))) is True


def test_retrieval_d2_requires_at_least_ten_percentage_points_mean_improvement() -> None:
    assert passes_d2_recall_gate(samples((9, 9, 5, 4))) is False


def test_retrieval_d2_requires_improvement_in_two_categories_not_only_overall_mean() -> None:
    assert passes_d2_recall_gate(samples((10, 0, 0, 0), baseline_hits=0)) is False


def test_retrieval_d2_rejects_any_category_regression_above_five_percentage_points() -> None:
    assert passes_d2_recall_gate(samples((10, 10, 7, 3))) is False


def test_retrieval_d2_mean_is_query_weighted_not_an_unweighted_category_mean() -> None:
    initial = samples()
    extras = tuple(
        RetrievalProbeSample(
            query_id=f"synthetic-extra-{index}",
            category=CATEGORIES[2],
            expected_evidence_ids=EXPECTED,
            baseline_evidence_ids=EXPECTED[:5],
            hybrid_evidence_ids=EXPECTED[:5],
        )
        for index in range(9)
    )
    assert passes_d2_recall_gate(initial + extras) is False


def test_retrieval_d2_uses_category_means_not_each_individual_query_regression() -> None:
    initial = samples()
    category = CATEGORIES[3]
    replacements = tuple(
        RetrievalProbeSample(
            query_id=f"synthetic-mixed-{index}",
            category=category,
            expected_evidence_ids=EXPECTED,
            baseline_evidence_ids=EXPECTED[:5],
            hybrid_evidence_ids=EXPECTED[:hits],
        )
        for index, hits in enumerate((1, 5, 6))
    )
    assert passes_d2_recall_gate(
        tuple(row for row in initial if row.category != category) + replacements
    )


def test_retrieval_d2_validates_minimum_query_count_and_distinct_query_identifiers() -> None:
    with pytest.raises(ValueError, match="12"):
        passes_d2_recall_gate(samples()[:11])
    with pytest.raises(ValueError, match="unique query"):
        passes_d2_recall_gate(samples() + (samples()[0],))


def test_retrieval_d2_requires_all_four_approved_query_categories() -> None:
    incomplete = tuple(row for row in samples() if row.category != CATEGORIES[3])
    extras = tuple(
        RetrievalProbeSample(
            query_id=f"synthetic-replacement-{index}",
            category=CATEGORIES[0],
            expected_evidence_ids=EXPECTED,
            baseline_evidence_ids=EXPECTED[:5],
            hybrid_evidence_ids=EXPECTED[:10],
        )
        for index in range(3)
    )
    with pytest.raises(ValueError, match="four.*categories"):
        passes_d2_recall_gate(incomplete + extras)
    assert {category.value for category in CATEGORIES} == {
        "COMPONENT_ENTITY_ALIASES",
        "TEMPORAL_SEQUENCE",
        "TECHNICAL_SYMPTOM",
        "CONTRADICTION_ORIENTED",
    }


def test_retrieval_probe_sample_is_strict_and_contains_no_source_payload() -> None:
    sample = samples()[0]
    assert RetrievalProbeSample.model_validate_json(sample.model_dump_json()) == sample
    for change in (
        {"category": "OTHER"},
        {"query_id": ""},
        {"expected_evidence_ids": [EXPECTED[0]]},
        {"source_payload": "must not be in a metric input"},
    ):
        with pytest.raises(ValidationError):
            RetrievalProbeSample.model_validate(sample.model_dump() | change)
