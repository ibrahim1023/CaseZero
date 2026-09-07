from datetime import UTC, datetime
from decimal import Decimal
from typing import Self, cast
from uuid import UUID

import pytest
from casezero_evidence.models import DocumentType, EvidenceType
from casezero_retrieval import (
    EvidenceSearchQuery,
    EvidenceSearchResult,
    RetrievalIntent,
    RetrievalRepository,
)
from psycopg import AsyncConnection
from psycopg.errors import InsufficientPrivilege
from pydantic import ValidationError


class Cursor:
    def __init__(
        self,
        rows: list[tuple[object, ...]],
        role: str = "casezero_blind",
        failure: Exception | None = None,
    ) -> None:
        self.rows = rows
        self.role = role
        self.failure = failure
        self.calls: list[tuple[str, dict[str, object] | None]] = []
        self.closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.closed = True

    async def execute(self, sql: str, params: dict[str, object] | None = None) -> Self:
        self.calls.append((sql, params))
        if params is not None and self.failure is not None:
            raise self.failure
        return self

    async def fetchone(self) -> tuple[object, ...]:
        return (self.role,)

    async def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class Connection:
    def __init__(self, cursor: Cursor) -> None:
        self.selected_cursor = cursor

    def cursor(self) -> Cursor:
        return self.selected_cursor


def repository(cursor: Cursor) -> RetrievalRepository:
    return RetrievalRepository(cast(AsyncConnection[tuple[object, ...]], Connection(cursor)))


def query(**changes: object) -> EvidenceSearchQuery:
    return EvidenceSearchQuery.model_validate(
        {
            "investigation_id": UUID(int=1),
            "case_id": UUID(int=2),
            "intent": RetrievalIntent.SUPPORT,
            "query_text": "pressure indication",
        }
        | changes
    )


def result_row(evidence_id: int, fts: str = "0.4") -> tuple[object, ...]:
    return (
        UUID(int=evidence_id),
        Decimal(fts),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        (Decimal(fts) * Decimal("0.55")).quantize(Decimal("0.0001")),
        [],
    )


def search_sql(cursor: Cursor) -> str:
    assert len(cursor.calls) == 2
    assert cursor.calls[0] == ("select current_user", None)
    return " ".join(cursor.calls[1][0].lower().split())


async def test_retrieval_repository_binds_every_input_without_building_sql_from_text() -> None:
    cursor = Cursor([])
    text = "pressure'); drop table public.evidence_items; --"
    start = datetime(2026, 9, 1, tzinfo=UTC)
    end = datetime(2026, 9, 2, tzinfo=UTC)
    requested = query(
        query_text=text,
        entity_ids=(UUID(int=9), UUID(int=3), UUID(int=9)),
        evidence_types=(EvidenceType.TEXT,),
        document_types=(DocumentType.FACTUAL_REPORT,),
        start_at=start,
        end_at=end,
        limit=50,
    )
    retriever = repository(cursor)
    assert await retriever.search(requested) == ()
    sql = search_sql(cursor)
    assert text.lower() not in sql
    assert "plainto_tsquery('english'::regconfig, %(query_text)s::text)" in sql
    assert cursor.calls[1][1] == {
        "investigation_id": UUID(int=1),
        "case_id": UUID(int=2),
        "query_text": text,
        "entity_ids": [UUID(int=3), UUID(int=9)],
        "evidence_types": ["TEXT"],
        "document_types": ["FACTUAL_REPORT"],
        "start_at": start,
        "end_at": end,
        "limit": 50,
    }
    assert set(vars(retriever)) == {"_connection"}
    assert cursor.closed


async def test_retrieval_repository_searches_pinned_current_reviewed_evidence_only() -> None:
    cursor = Cursor([])
    await repository(cursor).search(query())
    sql = search_sql(cursor)
    for required in (
        "from public.investigation_evidence membership",
        "join public.investigations investigation",
        "investigation.id = membership.investigation_id",
        "investigation.case_id = membership.case_id",
        "membership.investigation_id = input.investigation_id",
        "membership.case_id = input.case_id",
        "join public.evidence_items evidence",
        "evidence.id = membership.evidence_id",
        "evidence.case_id = membership.case_id",
        "evidence.model_run_id is not distinct from membership.model_run_id",
        "join public.source_documents source",
        "source.id = evidence.source_document_id",
        "source.case_id = evidence.case_id",
        "source.visibility = 'investigation_evidence'",
        "evidence.review_status in ('not_required', 'accepted')",
        "left join public.semantic_unit_completions completion",
        "completion.structural_unit_id = evidence.structural_unit_id",
        "completion.model_run_id = evidence.model_run_id",
        "completion.model_run_id is not null",
        "evidence.model_run_id is null",
        "evidence.item->>'extraction_method' in ('deterministic', 'human')",
    ):
        assert required in sql
    for forbidden in (
        "claim_candidates",
        "entity_candidates",
        "timeline_candidates",
        "source_blobs",
        "storage_path",
        "set role",
        "reset role",
        "set row_security",
        "security definer",
        "source.title",
        "source.source_url",
    ):
        assert forbidden not in sql


async def test_retrieval_filters_use_canonical_links_scoped_to_investigation_and_case() -> None:
    cursor = Cursor([])
    await repository(cursor).search(query())
    sql = search_sql(cursor)
    for required in (
        "from public.entity_evidence_links entity_link",
        "join public.investigation_entities entity",
        "entity.id = entity_link.entity_id",
        "entity.investigation_id = entity_link.investigation_id",
        "entity.case_id = entity_link.case_id",
        "entity_link.investigation_id = input.investigation_id",
        "entity_link.case_id = input.case_id",
        "entity_link.evidence_id = evidence.id",
        "entity_link.entity_id = any(input.entity_ids)",
        "count(distinct entity_link.entity_id)::numeric",
        "from public.timeline_evidence_links timeline_link",
        "join public.timeline_events event",
        "event.id = timeline_link.timeline_event_id",
        "event.investigation_id = timeline_link.investigation_id",
        "event.case_id = timeline_link.case_id",
        "timeline_link.investigation_id = input.investigation_id",
        "timeline_link.case_id = input.case_id",
        "timeline_link.evidence_id = evidence.id",
        "event.occurred_at is not null",
        "input.start_at is null or event.occurred_at >= input.start_at",
        "input.end_at is null or event.occurred_at <= input.end_at",
        "not has_entity_filter or entity_score > 0",
        "not has_time_filter or time_score > 0",
        "evidence.item->>'type' = any(input.evidence_types)",
        "source.document_type = any(input.document_types)",
    ):
        assert required in sql
    assert "evidence.item->>'occurred_at'" not in sql
    assert "evidence.item->'entities'" not in sql


async def test_retrieval_sql_normalizes_fts_and_orders_scores_then_uuid_before_limit() -> None:
    cursor = Cursor([])
    await repository(cursor).search(query())
    sql = search_sql(cursor)
    assert "to_tsvector('english'::regconfig, coalesce(evidence.item->>'observation', ''))" in sql
    assert "@@ input.tsquery" in sql
    assert "input.tsquery, 32)::numeric as fts_score" in sql
    assert (
        "round(0.55 * fts_score + 0.20 * entity_score + 0.15 * time_score + 0.10 * type_score, 4)"
        in sql
    )
    assert (
        "order by total_score desc, fts_score desc, entity_score desc, time_score desc, "
        "type_score desc, evidence_id asc limit %(limit)s"
    ) in sql
    assert cursor.calls[1][1] == {
        "investigation_id": UUID(int=1),
        "case_id": UUID(int=2),
        "query_text": "pressure indication",
        "entity_ids": [],
        "evidence_types": [],
        "document_types": [],
        "start_at": None,
        "end_at": None,
        "limit": 10,
    }


async def test_retrieval_repository_returns_typed_ranked_rows_without_source_payload() -> None:
    cursor = Cursor(
        [
            (
                UUID(int=9),
                Decimal("0.4"),
                Decimal("0.5"),
                Decimal(1),
                Decimal(1),
                Decimal("0.5700"),
                ["evidence_type", "document_type", "entity", "time"],
            ),
            result_row(3),
            result_row(8),
        ]
    )
    results = await repository(cursor).search(query())
    assert isinstance(results, tuple)
    assert all(isinstance(result, EvidenceSearchResult) for result in results)
    assert [result.rank for result in results] == [1, 2, 3]
    assert [result.evidence_id for result in results] == [UUID(int=9), UUID(int=3), UUID(int=8)]
    assert results[0].total_score == Decimal("0.5700")
    assert results[0].matched_filters == ("evidence_type", "document_type", "entity", "time")
    assert results[1].fts_score == Decimal("0.4")
    assert results[1].matched_filters == ()
    assert cursor.closed


@pytest.mark.parametrize("role", ["postgres", "casezero_eval", "casezero_processor", ""])
async def test_retrieval_repository_rejects_non_blind_connection_before_search(role: str) -> None:
    cursor = Cursor([result_row(3)], role=role)
    with pytest.raises(PermissionError, match="casezero_blind"):
        await repository(cursor).search(query())
    assert cursor.calls == [("select current_user", None)]
    assert cursor.closed


async def test_retrieval_repository_does_not_swallow_rls_or_database_failures() -> None:
    cursor = Cursor([], failure=InsufficientPrivilege("retrieval SELECT denied"))
    with pytest.raises(InsufficientPrivilege):
        await repository(cursor).search(query())
    assert cursor.closed


@pytest.mark.parametrize(
    ("column", "value"),
    [(0, "not-a-uuid"), (1, 0.5), (2, Decimal("1.1")), (6, ["invented reason"])],
)
async def test_retrieval_repository_rejects_malformed_database_rows(
    column: int, value: object
) -> None:
    row = list(result_row(3))
    row[column] = value
    cursor = Cursor([tuple(row)])
    with pytest.raises(ValidationError):
        await repository(cursor).search(query())
    assert cursor.closed
