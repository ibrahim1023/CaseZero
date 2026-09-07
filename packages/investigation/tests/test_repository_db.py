import os

import pytest
from casezero_investigation.replay import replay
from casezero_investigation.repository import InvestigationRepository
from psycopg import AsyncConnection
from psycopg.errors import ObjectNotInPrerequisiteState, ProgramLimitExceeded
from psycopg.types.json import Jsonb

from .db_support import seed_case

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(os.getenv("CASEZERO_DB_TEST") != "1", reason="requires CASEZERO_DB_TEST=1"),
]
CONFIG = {
    "version": "test-v1",
    "model_versions": {"PROMOTE_TIMELINE": "fixture"},
    "prompt_versions": {"PROMOTE_TIMELINE": "test-v1"},
}


async def test_repository_replays_database_generated_event_hash() -> None:
    async with (
        await AsyncConnection.connect(os.environ["DATABASE_URL"]) as connection,
        connection.transaction(force_rollback=True),
    ):
        case_id, _, _ = await seed_case(connection)
        await connection.execute("set local role casezero_blind")
        repository = InvestigationRepository(connection)
        investigation = await repository.create(case_id, CONFIG)
        events = await repository.events(investigation.id)
        projected = replay(events)
        assert projected.investigation_id == investigation.id
        assert projected.configuration_hash == investigation.configuration_hash
        job = await repository.claim(investigation.id, "worker")
        assert job is not None
        assert job.attempt_count == 1
        request = await repository.reserve_request(job.id, "worker", 1)
        assert request.request_ordinal == 1


async def test_creation_pins_evidence_and_reuses_the_same_configuration() -> None:
    async with (
        await AsyncConnection.connect(os.environ["DATABASE_URL"]) as connection,
        connection.transaction(force_rollback=True),
    ):
        case_id, item, candidate = await seed_case(connection)
        await connection.execute("set local role casezero_blind")
        first = await (
            await connection.execute(
                "select id, status, current_stage from public.create_investigation(%s, %s)",
                (case_id, Jsonb(CONFIG)),
            )
        ).fetchone()
        second = await (
            await connection.execute(
                "select id, status, current_stage from public.create_investigation(%s, %s)",
                (case_id, Jsonb(CONFIG)),
            )
        ).fetchone()
        assert first == second
        assert first[1:] == ("PENDING", "PROMOTE_TIMELINE")
        assert await (
            await connection.execute(
                "select evidence_id, model_run_id from investigation_evidence where investigation_id=%s",
                (first[0],),
            )
        ).fetchall() == [(item.id, item.model_run_id)]
        assert await (
            await connection.execute(
                "select candidate_id from investigation_candidates where investigation_id=%s",
                (first[0],),
            )
        ).fetchall() == [(candidate.id,)]
        changed = await (
            await connection.execute(
                "select id from public.create_investigation(%s, %s)",
                (case_id, Jsonb({**CONFIG, "version": "test-v2"})),
            )
        ).fetchone()
        assert changed[0] != first[0]


async def test_claim_fencing_and_cumulative_request_budget() -> None:
    async with (
        await AsyncConnection.connect(os.environ["DATABASE_URL"]) as connection,
        connection.transaction(force_rollback=True),
    ):
        case_id, _, _ = await seed_case(connection)
        await connection.execute("set local role casezero_blind")
        investigation_id = (
            await (
                await connection.execute(
                    "select id from public.create_investigation(%s, %s)",
                    (case_id, Jsonb(CONFIG)),
                )
            ).fetchone()
        )[0]
        job = await (
            await connection.execute(
                "select id, attempt_count from public.claim_investigation_job(%s, %s)",
                (investigation_id, "worker-a"),
            )
        ).fetchone()
        assert job is not None and job[1] == 1
        assert (
            await (
                await connection.execute(
                    "select id from public.claim_investigation_job(%s, %s)",
                    (investigation_id, "worker-b"),
                )
            ).fetchone()
            is None
        )
        for ordinal in (1, 2, 3):
            request = await (
                await connection.execute(
                    "select request_ordinal from public.reserve_model_request(%s,%s,%s)",
                    (job[0], "worker-a", 1),
                )
            ).fetchone()
            assert request == (ordinal,)
        with pytest.raises(ProgramLimitExceeded):
            async with connection.transaction():
                await connection.execute(
                    "select * from public.reserve_model_request(%s,%s,%s)",
                    (job[0], "worker-a", 1),
                )
        with pytest.raises(ObjectNotInPrerequisiteState):
            async with connection.transaction():
                await connection.execute(
                    "select public.complete_stage_job(%s,%s,%s)",
                    (job[0], "worker-b", 1),
                )


async def test_expired_lease_is_reclaimed_without_resetting_request_budget() -> None:
    async with (
        await AsyncConnection.connect(os.environ["DATABASE_URL"]) as connection,
        connection.transaction(force_rollback=True),
    ):
        case_id, _, _ = await seed_case(connection)
        await connection.execute("set local role casezero_blind")
        investigation_id = (
            await (
                await connection.execute(
                    "select id from public.create_investigation(%s, %s)",
                    (case_id, Jsonb(CONFIG)),
                )
            ).fetchone()
        )[0]
        job_id = (
            await (
                await connection.execute(
                    "select id from public.claim_investigation_job(%s,%s)",
                    (investigation_id, "old"),
                )
            ).fetchone()
        )[0]
        await connection.execute(
            "select * from reserve_model_request(%s,%s,%s)", (job_id, "old", 1)
        )
        await connection.execute("reset role")
        await connection.execute(
            "update investigation_jobs set lease_expires_at=clock_timestamp()-interval '1 second', "
            "claimed_at=clock_timestamp()-interval '1 hour' where id=%s",
            (job_id,),
        )
        await connection.execute("set local role casezero_blind")
        reclaimed = await (
            await connection.execute(
                "select id, attempt_count, model_request_count from public.claim_investigation_job(%s,%s)",
                (investigation_id, "new"),
            )
        ).fetchone()
        assert reclaimed == (job_id, 2, 1)
        assert await (
            await connection.execute(
                "select outcome from investigation_job_attempts where job_id=%s and attempt_number=1",
                (job_id,),
            )
        ).fetchone() == ("LEASE_EXPIRED",)
        with pytest.raises(ObjectNotInPrerequisiteState):
            async with connection.transaction():
                await connection.execute(
                    "select public.complete_stage_job(%s,%s,%s)", (job_id, "old", 1)
                )
        assert await (
            await connection.execute(
                "select request_ordinal from public.reserve_model_request(%s,%s,%s)",
                (job_id, "new", 2),
            )
        ).fetchone() == (2,)
