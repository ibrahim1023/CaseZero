from collections.abc import Collection, Sequence
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from fractions import Fraction
from uuid import UUID

from casezero_retrieval.models import RetrievalProbeCategory, RetrievalProbeSample

_ZERO = Decimal(0)
_FOUR_PLACES = Decimal("0.0001")
_FTS_WEIGHTS = (Decimal("0.55"), Decimal("0.20"), Decimal("0.15"), Decimal("0.10"))
_HYBRID_WEIGHTS = (
    Decimal("0.35"),
    Decimal("0.30"),
    Decimal("0.15"),
    Decimal("0.12"),
    Decimal("0.08"),
)


def score_fts_result(
    fts: Decimal,
    entity: Decimal = _ZERO,
    time: Decimal = _ZERO,
    type: Decimal = _ZERO,
) -> Decimal:
    return _weighted_score((fts, entity, time, type), _FTS_WEIGHTS)


def score_hybrid_result(
    vector: Decimal,
    fts: Decimal,
    entity: Decimal = _ZERO,
    time: Decimal = _ZERO,
    type: Decimal = _ZERO,
) -> Decimal:
    return _weighted_score((vector, fts, entity, time, type), _HYBRID_WEIGHTS)


def _weighted_score(components: tuple[Decimal, ...], weights: tuple[Decimal, ...]) -> Decimal:
    for component in components:
        if not isinstance(component, Decimal):
            raise TypeError("score components must be Decimal values")
        if not component.is_finite() or not _ZERO <= component <= Decimal(1):
            raise ValueError("score components must be finite and between 0 and 1")
    precision = max(28, max(len(component.as_tuple().digits) for component in components) + 4)
    with localcontext(Context(prec=precision, rounding=ROUND_HALF_UP)):
        total = sum(
            (component * weight for component, weight in zip(components, weights, strict=True)),
            _ZERO,
        )
        return total.quantize(_FOUR_PLACES)


def recall_at_10(
    ranked_evidence_ids: Sequence[UUID], expected_evidence_ids: Collection[UUID]
) -> Fraction:
    expected = set(expected_evidence_ids)
    if not expected:
        raise ValueError("Recall@10 requires expected evidence IDs")
    return Fraction(len(set(ranked_evidence_ids[:10]) & expected), len(expected))


def passes_d2_recall_gate(samples: Sequence[RetrievalProbeSample]) -> bool:
    if len(samples) < 12:
        raise ValueError("D2 requires at least 12 queries")
    if len({sample.query_id for sample in samples}) != len(samples):
        raise ValueError("D2 requires unique query identifiers")
    if {sample.category for sample in samples} != set(RetrievalProbeCategory):
        raise ValueError("D2 requires all four approved query categories")

    category_deltas: dict[RetrievalProbeCategory, list[Fraction]] = {
        category: [] for category in RetrievalProbeCategory
    }
    for sample in samples:
        delta = recall_at_10(
            sample.hybrid_evidence_ids, sample.expected_evidence_ids
        ) - recall_at_10(sample.baseline_evidence_ids, sample.expected_evidence_ids)
        category_deltas[sample.category].append(delta)

    total_delta = sum((sum(values, Fraction()) for values in category_deltas.values()), Fraction())
    category_means = tuple(
        sum(values, Fraction()) / len(values) for values in category_deltas.values()
    )
    return (
        total_delta / len(samples) >= Fraction(1, 10)
        and sum(delta > 0 for delta in category_means) >= 2
        and all(delta >= -Fraction(1, 20) for delta in category_means)
    )
