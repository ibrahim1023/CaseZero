from decimal import ROUND_DOWN, Decimal, localcontext
from typing import cast

import pytest
from casezero_retrieval import score_fts_result, score_hybrid_result

ZERO = Decimal(0)
ONE = Decimal(1)


def test_retrieval_fts_baseline_uses_exact_fixed_weights() -> None:
    assert score_fts_result(ONE, ZERO, ZERO, ZERO) == Decimal("0.5500")
    assert score_fts_result(ZERO, ONE, ZERO, ZERO) == Decimal("0.2000")
    assert score_fts_result(ZERO, ZERO, ONE, ZERO) == Decimal("0.1500")
    assert score_fts_result(ZERO, ZERO, ZERO, ONE) == Decimal("0.1000")
    assert score_fts_result(ONE, ONE, ONE, ONE) == Decimal("1.0000")
    assert score_fts_result(
        fts=Decimal("0.4"), entity=Decimal("0.5"), time=ONE, type=ONE
    ) == Decimal("0.5700")


def test_retrieval_absent_filters_stay_zero_without_weight_redistribution() -> None:
    assert score_fts_result(Decimal("0.4")) == Decimal("0.2200")
    assert score_fts_result(ZERO) == Decimal("0.0000")
    assert score_hybrid_result(vector=ZERO, fts=ONE) == Decimal("0.3000")
    assert score_hybrid_result(vector=ONE, fts=ZERO) == Decimal("0.3500")


def test_retrieval_hybrid_probe_has_separate_fixed_score_not_baseline_plus_vector() -> None:
    assert score_hybrid_result(ONE, ZERO, ZERO, ZERO, ZERO) == Decimal("0.3500")
    assert score_hybrid_result(ZERO, ONE, ZERO, ZERO, ZERO) == Decimal("0.3000")
    assert score_hybrid_result(ZERO, ZERO, ONE, ZERO, ZERO) == Decimal("0.1500")
    assert score_hybrid_result(ZERO, ZERO, ZERO, ONE, ZERO) == Decimal("0.1200")
    assert score_hybrid_result(ZERO, ZERO, ZERO, ZERO, ONE) == Decimal("0.0800")
    assert score_hybrid_result(ONE, ONE, ONE, ONE, ONE) == Decimal("1.0000")
    assert score_hybrid_result(
        vector=Decimal("0.8"), fts=Decimal("0.4"), entity=Decimal("0.5"), time=ONE, type=ONE
    ) == Decimal("0.6750")


def test_retrieval_scores_quantize_once_with_postgres_numeric_rounding() -> None:
    assert score_fts_result(Decimal("0.003")) == Decimal("0.0017")
    assert str(score_fts_result(ONE)) == "0.5500"
    assert score_fts_result(Decimal("0.123456"), Decimal("0.222222")) == Decimal("0.1123")
    assert score_hybrid_result(Decimal("0.001"), ZERO) == Decimal("0.0004")


def test_retrieval_score_does_not_round_high_precision_components_before_the_total() -> None:
    assert score_fts_result(Decimal("0.000090909090909090909090909090909090909090909")) == Decimal(
        "0.0000"
    )
    assert score_hybrid_result(
        Decimal("0.000142857142857142857142857142857142857142857"), ZERO
    ) == Decimal("0.0000")


def test_retrieval_score_does_not_depend_on_callers_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 4
        context.rounding = ROUND_DOWN
        assert score_fts_result(Decimal("0.003")) == Decimal("0.0017")
        assert score_hybrid_result(ONE, ONE, ONE, ONE, ONE) == Decimal("1.0000")


@pytest.mark.parametrize("index", range(4))
@pytest.mark.parametrize(
    "value",
    [Decimal("-0.1"), Decimal("1.1"), Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_retrieval_fts_rejects_non_normalized_components(index: int, value: Decimal) -> None:
    values = [ZERO] * 4
    values[index] = value
    with pytest.raises(ValueError, match="finite.*0.*1"):
        score_fts_result(*values)


@pytest.mark.parametrize("index", range(5))
@pytest.mark.parametrize(
    "value", [Decimal("-0.1"), Decimal("1.1"), Decimal("NaN"), Decimal("Infinity")]
)
def test_retrieval_hybrid_rejects_non_normalized_components(index: int, value: Decimal) -> None:
    values = [ZERO] * 5
    values[index] = value
    with pytest.raises(ValueError, match="finite.*0.*1"):
        score_hybrid_result(*values)


@pytest.mark.parametrize("value", [0, 1, 0.5, "0.5", True, None])
def test_retrieval_scoring_does_not_coerce_non_decimal_values(value: object) -> None:
    invalid = cast(Decimal, value)
    with pytest.raises(TypeError, match="Decimal"):
        score_fts_result(invalid)
    with pytest.raises(TypeError, match="Decimal"):
        score_hybrid_result(invalid, ZERO)
