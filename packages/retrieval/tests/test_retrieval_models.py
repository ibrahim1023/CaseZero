import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

import pytest
from casezero_evidence.models import DocumentType, EvidenceType
from casezero_retrieval import EvidenceSearchQuery, EvidenceSearchResult, RetrievalIntent
from pydantic import ValidationError

START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(hours=1)


def query_values() -> dict[str, object]:
    return {
        "investigation_id": UUID(int=1),
        "case_id": UUID(int=2),
        "intent": RetrievalIntent.SUPPORT,
        "query_text": "pressure indication",
    }


def result_values() -> dict[str, object]:
    return {
        "evidence_id": UUID(int=3),
        "rank": 1,
        "fts_score": Decimal("0.4"),
        "entity_score": Decimal(0),
        "time_score": Decimal(0),
        "type_score": Decimal(0),
        "total_score": Decimal("0.2200"),
        "matched_filters": (),
    }


def test_retrieval_query_defaults_and_json_round_trip() -> None:
    query = EvidenceSearchQuery.model_validate(query_values())
    assert query.limit == 10
    assert query.config_version == "fts-v1"
    assert query.evidence_types == ()
    assert query.document_types == ()
    assert query.entity_ids == ()
    assert query.start_at is query.end_at is None
    assert EvidenceSearchQuery.model_validate_json(query.model_dump_json()) == query
    assert set(RetrievalIntent) == {
        RetrievalIntent.SUPPORT,
        RetrievalIntent.CONTRADICT,
        RetrievalIntent.NEUTRAL,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("investigation_id", str(UUID(int=1))),
        ("case_id", str(UUID(int=2))),
        ("intent", "SUPPORT"),
        ("intent", "SUPPORTING"),
        ("limit", "10"),
        ("limit", True),
        ("limit", 0),
        ("limit", 51),
        ("limit", 1.0),
        ("query_text", b"pressure"),
        ("query_text", ""),
        ("query_text", " \t\n"),
        ("query_text", "a" * 4097),
        ("query_text", "pressure\x00indication"),
        ("evidence_types", [EvidenceType.TEXT]),
        ("evidence_types", ("TEXT",)),
        ("document_types", ("FACTUAL_REPORT",)),
        ("entity_ids", (str(UUID(int=4)),)),
        ("config_version", "hybrid-v1"),
        ("visibility", "FINAL_FINDING"),
    ],
)
def test_retrieval_query_rejects_untyped_or_out_of_contract_input(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        EvidenceSearchQuery.model_validate(query_values() | {field: value})


@pytest.mark.parametrize("limit", [1, 50])
def test_retrieval_query_inclusive_text_and_limit_bounds(limit: int) -> None:
    query = EvidenceSearchQuery.model_validate(
        query_values() | {"limit": limit, "query_text": "a" * 4096}
    )
    assert query.limit == limit
    assert len(query.query_text) == 4096


@pytest.mark.parametrize("field", ["start_at", "end_at"])
@pytest.mark.parametrize(
    "value",
    [START.replace(tzinfo=None), START.astimezone(timezone(timedelta(hours=1)))],
)
def test_retrieval_query_requires_aware_utc_interval(field: str, value: datetime) -> None:
    with pytest.raises(ValidationError, match="UTC-aware"):
        EvidenceSearchQuery.model_validate(query_values() | {field: value})


def test_retrieval_query_interval_is_inclusive_and_may_be_open_ended() -> None:
    for values in (
        {"start_at": START},
        {"end_at": END},
        {"start_at": START, "end_at": END},
        {"start_at": START, "end_at": START},
    ):
        EvidenceSearchQuery.model_validate(query_values() | values)
    with pytest.raises(ValidationError, match="end_at"):
        EvidenceSearchQuery.model_validate(query_values() | {"start_at": END, "end_at": START})


def test_retrieval_canonical_payload_orders_set_filters_and_retains_intent() -> None:
    values = query_values() | {
        "entity_ids": (UUID(int=9), UUID(int=4), UUID(int=9)),
        "evidence_types": (EvidenceType.TEXT, EvidenceType.IMAGE, EvidenceType.TEXT),
        "document_types": (DocumentType.WEATHER_RECORD, DocumentType.FACTUAL_REPORT),
        "start_at": START,
        "end_at": END,
    }
    query = EvidenceSearchQuery.model_validate(values)
    payload = query.canonical_payload()
    assert payload == {
        "investigation_id": str(UUID(int=1)),
        "case_id": str(UUID(int=2)),
        "intent": "SUPPORT",
        "query_text": "pressure indication",
        "entity_ids": [str(UUID(int=4)), str(UUID(int=9))],
        "evidence_types": ["IMAGE", "TEXT"],
        "document_types": ["FACTUAL_REPORT", "WEATHER_RECORD"],
        "start_at": "2026-09-01T00:00:00Z",
        "end_at": "2026-09-01T01:00:00Z",
        "limit": 10,
        "config_version": "fts-v1",
    }
    assert query.entity_ids == (UUID(int=4), UUID(int=9))
    reordered = EvidenceSearchQuery.model_validate(
        values
        | {
            "entity_ids": (UUID(int=4), UUID(int=9)),
            "evidence_types": (EvidenceType.IMAGE, EvidenceType.TEXT),
            "document_types": (DocumentType.FACTUAL_REPORT, DocumentType.WEATHER_RECORD),
        }
    )
    assert reordered.canonical_payload() == payload
    digests = {
        sha256(
            json.dumps(
                EvidenceSearchQuery.model_validate(values | {"intent": intent}).canonical_payload(),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        for intent in RetrievalIntent
    }
    assert len(digests) == 3
    assert EvidenceSearchQuery.model_validate_json(query.model_dump_json()) == query


def test_retrieval_query_hash_input_includes_all_context_and_filters() -> None:
    original = EvidenceSearchQuery.model_validate(query_values()).canonical_payload()
    for change in (
        {"investigation_id": UUID(int=11)},
        {"case_id": UUID(int=12)},
        {"query_text": "normal pressure"},
        {"limit": 11},
        {"entity_ids": (UUID(int=4),)},
        {"evidence_types": (EvidenceType.TEXT,)},
        {"document_types": (DocumentType.FACTUAL_REPORT,)},
        {"start_at": START},
        {"end_at": END},
    ):
        assert EvidenceSearchQuery.model_validate(query_values() | change).canonical_payload() != (
            original
        )


def test_retrieval_contracts_are_frozen_and_payload_free() -> None:
    query = EvidenceSearchQuery.model_validate(query_values())
    result = EvidenceSearchResult.model_validate(result_values())
    with pytest.raises(ValidationError, match="frozen"):
        query.intent = RetrievalIntent.CONTRADICT
    with pytest.raises(ValidationError, match="frozen"):
        result.rank = 2
    assert set(type(result).model_fields) == {
        "evidence_id",
        "rank",
        "fts_score",
        "entity_score",
        "time_score",
        "type_score",
        "total_score",
        "matched_filters",
    }
    assert EvidenceSearchResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "field", ["fts_score", "entity_score", "time_score", "type_score", "total_score"]
)
@pytest.mark.parametrize(
    "value",
    [Decimal("-0.0001"), Decimal("1.0001"), Decimal("NaN"), Decimal("Infinity"), 0.5, "0.5", True],
)
def test_retrieval_result_scores_are_strict_finite_normalized_decimals(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        EvidenceSearchResult.model_validate(result_values() | {field: value})


@pytest.mark.parametrize(
    "change",
    [
        {"rank": 0},
        {"rank": 51},
        {"rank": "1"},
        {"rank": True},
        {"evidence_id": str(UUID(int=3))},
        {"matched_filters": ["entity"]},
        {"matched_filters": ("invented reason",)},
        {"source_bytes": b"not a retrieval field"},
    ],
)
def test_retrieval_result_rejects_invalid_rank_or_payload(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EvidenceSearchResult.model_validate(result_values() | change)
