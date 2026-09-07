import json
import os
from asyncio import CancelledError
from decimal import Decimal
from uuid import uuid4

import pytest
from casezero_investigation.models import InvestigationStatus
from casezero_investigation.replay import replay
from casezero_investigation.repository import InvestigationRepository
from casezero_investigation.worker import Worker, runtime_configuration
from casezero_observability.reasoning import ModelFailure, StructuredGeneration
from psycopg import AsyncConnection

from .db_support import seed_case

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(os.getenv("CASEZERO_DB_TEST") != "1", reason="requires CASEZERO_DB_TEST=1"),
]


class StrictProvider:
    name = "qwen/qwen3-32b"

    def __init__(self, failures=(), *, semantic_failure=False, connection=None):
        self.failures = list(failures)
        self.requests = []
        self.semantic_failure = semantic_failure
        self.connection = connection

    async def generate(self, request):
        self.requests.append(request)
        if self.connection is not None:
            assert self.connection.autocommit
            assert await (await self.connection.execute(
                "select count(*) from model_request_attempts r join investigation_jobs j on j.id=r.job_id "
                "where r.status='RESERVED' and j.stage=%s and j.case_id=%s", (request.stage, request.case_id),
            )).fetchone() == (1,)
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, BaseException):
                raise failure
            if failure == "reference":
                return StructuredGeneration(request.output_type.model_construct(timeline=()))
            if failure == "schema":
                request.output_type.model_validate_json('{"private-key":"private-value"}')
        state = json.loads(request.prompt.split("\nINPUT\n", 1)[1].split("\nFEEDBACK\n", 1)[0])
        stage = request.stage
        if stage == "PROMOTE_TIMELINE":
            output = {"timeline": [{
                "source_candidate_ids": [c["id"]], "evidence_ids": c["evidence_ids"],
                "occurred_at": c["occurred_at"], "time_precision": c["time_precision"],
                "description": c["description"], "confidence": c["confidence"],
            } for c in state["candidates"]["timeline"]]}
        elif stage == "RESOLVE_ENTITIES":
            output = {"entities": [{
                "source_candidate_ids": [c["id"]], "evidence_ids": c["evidence_ids"],
                "type": c["type"], "canonical_name": c["proposed_canonical_name"], "aliases": c["aliases"],
            } for c in state["candidates"]["entities"]]}
        elif stage == "PROMOTE_CLAIMS":
            output = {"claims": [{
                "source_candidate_ids": [c["id"]], "text": c["text"], "status": c["status"],
                "confidence": c["confidence"], "supporting_evidence_ids": c["supporting_evidence_ids"],
                "contradicting_evidence_ids": c["contradicting_evidence_ids"],
            } for c in state["candidates"]["claims"]]}
        elif stage == "GENERATE_HYPOTHESES":
            assert set(state["state"]) == {"claims", "timeline", "entities", "questions"}
            assert not any(
                key in json.dumps(state["state"])
                for key in ("model_run_id", "created_at", "source_candidate_ids", "investigation_id")
            )
            claim_id = next(iter(state["state"]["claims"]))
            output = {"hypotheses": [{
                "title": title, "description": description, "confidence": confidence,
                "distinguishing_prediction": prediction, "weakening_evidence": weakening,
                "supporting_claim_ids": [claim_id], "contradicting_claim_ids": [],
                "unresolved_questions": [question],
            } for title, description, confidence, prediction, weakening, question in (
                ("Fuel interruption", "Restricted fuel flow reduced engine output", 0.7,
                 "Pressure should fall", "Normal fuel delivery", "Was fuel pressure measured?"),
                ("Ignition fault", "Electrical spark interruption degraded combustion", 0.6,
                 "Spark should disappear", "Continuous ignition", "Was spark continuity tested?"),
                ("Instrument error", "An indication defect misrepresented available thrust", 0.5,
                 "Independent thrust remains stable", "Measured physical deceleration",
                 "Were independent measurements available?"),
            )]}
        elif stage in {"SEARCH_SUPPORT", "SEARCH_CONTRADICTIONS"}:
            output = {"query_text": "power" if stage == "SEARCH_SUPPORT" else "spark"}
        elif stage == "DESIGN_FALSIFICATION_TESTS":
            assert len(state["evidence"]) == 1
            hypothesis = state["hypothesis"]
            assert len(state["contradiction_retrievals"]) == 1
            assert not state["contradiction_retrievals"][0]["results"]
            output = {
                "hypothesis_id": hypothesis["id"], "missing_evidence": ["Independent component measurement"],
                "critique_confidence": 0.7,
                "proposed_tests": [{
                    "type": "EVIDENCE_PRESENCE", "expected_observation": "An independent measurement is expected",
                    "strength": "MEDIUM", "execution_kind": "DETERMINISTIC",
                    "parameters": {"schema_version": "evidence-presence-v1", "subtype": "component_measurement"},
                    "evidence_ids": [], "claim_ids": [],
                }],
            }
            if hypothesis["title"] == "Fuel interruption":
                output["proposed_tests"].append({
                    "type": "SEMANTIC_COMPARISON", "expected_observation": "Observed loss should match the predicted interruption",
                    "strength": "LOW", "execution_kind": "AI", "parameters": {"schema_version": "semantic-comparison-v1"},
                    "evidence_ids": [state["evidence"][0]["id"]], "claim_ids": list(state["state"]["claims"]),
                })
        elif stage == "EXECUTE_FALSIFICATION_TESTS":
            assert len(state["evidence"]) == 1
            assert state["test"]["evidence_ids"] == [state["evidence"][0]["id"]]
            output = {"classification": "INFERRED", "outcome": "INCONCLUSIVE", "result_evidence_ids": [state["evidence"][0]["id"]]}
            if self.semantic_failure:
                output["result_evidence_ids"] = [str(uuid4())]
        else:
            raise AssertionError(f"unexpected provider stage: {stage}")
        return StructuredGeneration(request.output_type.model_validate_json(json.dumps(output)), 100, 50)


@pytest.mark.parametrize("semantic_failure", [False, True])
async def test_real_pipeline_materializers_retrieval_audits_replay_and_reuse(semantic_failure):
    async with (
        await AsyncConnection.connect(os.environ["DATABASE_URL"], autocommit=True) as connection,
        connection.transaction(force_rollback=True),
    ):
        case_id, _, _ = await seed_case(connection, complete=True)
        await connection.execute("set local role casezero_blind")
        repository = InvestigationRepository(connection)
        config = runtime_configuration(StrictProvider.name, "a" * 40)
        investigation = await repository.create(case_id, config.canonical_payload())
        provider = StrictProvider(semantic_failure=semantic_failure, connection=connection)
        worker = Worker(repository, investigation, provider, worker_id="pipeline")
        count = 0
        while await worker.run_once():
            count += 1
            assert count < 40
        result = await repository.get(investigation.id)
        assert result.status is InvestigationStatus.SUCCEEDED
        state = await repository.projection(investigation.id)
        assert state.canonical_state() == replay(await repository.events(investigation.id)).canonical_state()
        assert len(state.timeline) == 2
        assert len(state.hypotheses) == 3
        assert len(state.retrievals) == 6
        assert len(state.questions) == 6
        assert all(r.model_run_id for r in state.retrievals.values())
        assert all(h.current_confidence == h.initial_confidence - Decimal("0.08") for h in state.hypotheses.values())
        semantic = next(t for t in state.tests.values() if t.execution_kind.value == "AI")
        assert semantic.status.value == ("FAILED" if semantic_failure else "SUCCEEDED")
        assert (semantic.outcome is None) == semantic_failure
        assert len(semantic.evidence_ids) == 1
        expected_requests = 16 if semantic_failure else 14
        assert len(provider.requests) == expected_requests
        assert count == 20
        await connection.execute("reset role")
        rows = await (await connection.execute(
            "select capability,count(*) from access_audit_events where case_id=%s group by capability", (case_id,),
        )).fetchall()
        capabilities = dict(rows)
        assert capabilities["MODEL_INFERENCE"] == len(provider.requests)
        assert capabilities["RETRIEVAL"] == 3
        assert set(capabilities) <= {"MODEL_INFERENCE", "RETRIEVAL", "INVESTIGATION_TOOL", "EVIDENCE_READ"}
        await connection.execute("set local role casezero_blind")
        reused = await repository.create(case_id, config.canonical_payload())
        assert reused.id == investigation.id
        assert not await Worker(repository, reused, provider, worker_id="reuse").run_once()
        assert len(provider.requests) == expected_requests


async def test_empty_search_does_not_force_invented_missing_evidence_or_presence_tests():
    class ComparisonProvider(StrictProvider):
        async def generate(self, request):
            generated = await super().generate(request)
            if request.stage == "DESIGN_FALSIFICATION_TESTS":
                comparisons = tuple(t for t in generated.output.proposed_tests if t.type.value == "SEMANTIC_COMPARISON")
                if comparisons:
                    output = generated.output.model_copy(update={
                        "missing_evidence": (), "proposed_tests": comparisons,
                    })
                    return StructuredGeneration(output, generated.input_tokens, generated.output_tokens)
            return generated

    async with (
        await AsyncConnection.connect(os.environ["DATABASE_URL"], autocommit=True) as connection,
        connection.transaction(force_rollback=True),
    ):
        case_id, _, _ = await seed_case(connection, complete=True)
        await connection.execute("set local role casezero_blind")
        repository = InvestigationRepository(connection)
        investigation = await repository.create(case_id, runtime_configuration(StrictProvider.name, "a" * 40).canonical_payload())
        provider = ComparisonProvider(connection=connection)
        worker = Worker(repository, investigation, provider, worker_id="comparison")
        while await worker.run_once():
            assert len(provider.requests) <= 16
        assert (await repository.get(investigation.id)).status is InvestigationStatus.SUCCEEDED
        assert len([r for r in provider.requests if r.stage == "DESIGN_FALSIFICATION_TESTS"]) == 3
        state = await repository.projection(investigation.id)
        assert len(state.questions) == 6
        assert any(not critique.missing_evidence for critique in state.critiques.values())


async def test_reference_and_provider_exhaustion_leave_no_partial_canonical_mutations():
    async with (
        await AsyncConnection.connect(os.environ["DATABASE_URL"], autocommit=True) as connection,
        connection.transaction(force_rollback=True),
    ):
        case_id, _, _ = await seed_case(connection, complete=True)
        await connection.execute("set local role casezero_blind")
        repository = InvestigationRepository(connection)
        investigation = await repository.create(case_id, runtime_configuration(StrictProvider.name, "a" * 40).canonical_payload())
        provider = StrictProvider((ModelFailure("private provider payload"), "schema", "reference"), connection=connection)
        assert await Worker(repository, investigation, provider, worker_id="failure").run_once()
        assert (await repository.get(investigation.id)).status is InvestigationStatus.FAILED
        assert not (await repository.projection(investigation.id)).timeline
        job = await (await connection.execute("select id from investigation_jobs where investigation_id=%s", (investigation.id,))).fetchone()
        receipts = await repository.requests(job[0])
        assert [r.status.value for r in receipts] == ["PROVIDER_FAILED", "SCHEMA_FAILED", "SCHEMA_FAILED"]
        assert len(provider.requests) == 3
        assert receipts[-1].validation_issues[0].path == ("output",)
        assert "private" not in str(receipts)


async def test_cancelled_request_reclaims_without_resetting_budget():
    async with (
        await AsyncConnection.connect(os.environ["DATABASE_URL"], autocommit=True) as connection,
        connection.transaction(force_rollback=True),
    ):
        case_id, _, _ = await seed_case(connection, complete=True)
        await connection.execute("set local role casezero_blind")
        repository = InvestigationRepository(connection)
        investigation = await repository.create(case_id, runtime_configuration(StrictProvider.name, "a" * 40).canonical_payload())
        provider = StrictProvider((CancelledError(), ModelFailure("private"), "reference"))
        with pytest.raises(CancelledError):
            await Worker(repository, investigation, provider, worker_id="cancelled").run_once()
        assert not (await repository.projection(investigation.id)).timeline
        await connection.execute("reset role")
        await connection.execute(
            "update investigation_jobs set claimed_at=clock_timestamp()-interval '31 minutes', "
            "lease_expires_at=clock_timestamp()-interval '1 minute' where investigation_id=%s",
            (investigation.id,),
        )
        await connection.execute("set local role casezero_blind")
        assert await Worker(repository, investigation, provider, worker_id=str(uuid4())).run_once()
        assert len(provider.requests) == 3
        assert (await repository.get(investigation.id)).status is InvestigationStatus.FAILED
        job = await (await connection.execute("select id from investigation_jobs where investigation_id=%s", (investigation.id,))).fetchone()
        receipts = await repository.requests(job[0])
        assert [r.request_ordinal for r in receipts] == [1, 2, 3]
        assert [r.status.value for r in receipts] == ["LEASE_EXPIRED", "PROVIDER_FAILED", "SCHEMA_FAILED"]
        assert receipts[-1].job_attempt_number == 2
        assert await (await connection.execute(
            "select count(*) from investigation_spans where investigation_id=%s and status='RUNNING'",
            (investigation.id,),
        )).fetchone() == (0,)


async def test_tools_are_bound_to_active_pinned_case_and_require_blind_role():
    from casezero_investigation.tools import InvestigationTools

    async with (
        await AsyncConnection.connect(os.environ["DATABASE_URL"], autocommit=True) as connection,
        connection.transaction(force_rollback=True),
    ):
        case_id, evidence, _ = await seed_case(connection, complete=True)
        other_case, other_evidence, _ = await seed_case(connection)
        await connection.execute("set local role casezero_blind")
        repository = InvestigationRepository(connection)
        config = runtime_configuration(StrictProvider.name, "a" * 40).canonical_payload()
        investigation = await repository.create(case_id, config)
        other = await repository.create(other_case, config)
        job = await repository.claim(investigation.id, "tools")
        span = await repository.start_span(job, "tools", "AGENT", job.stage.value, "phase3-worker-v1")
        tools = InvestigationTools(repository, investigation, job, "tools", span)
        assert len((await tools.candidate_batch()).timeline) == 2
        assert {e.case_id for e in await tools.get_evidence()} == {case_id}
        assert (await tools.get_source_document(evidence.source_document_id)).id == evidence.source_document_id
        with pytest.raises(PermissionError):
            await tools.get_source_document(other_evidence.source_document_id)
        with pytest.raises(ValueError, match="context"):
            InvestigationTools(repository, other, job, "tools", span)
        await connection.execute("reset role")
        with pytest.raises(PermissionError, match="casezero_blind"):
            await tools.get_state()
        await connection.execute("update evidence_items set review_status='REJECTED' where id=%s", (evidence.id,))
        await connection.execute("set local role casezero_blind")
        assert evidence.id not in {e.id for e in await tools.get_evidence()}
        with pytest.raises(PermissionError, match="candidate evidence unavailable"):
            await tools.candidate_batch()


async def test_model_audit_failure_prevents_provider_call(monkeypatch):
    from casezero_evidence.access import PostgresAccessAuditRecorder

    original = PostgresAccessAuditRecorder.record

    async def record(self, event):
        if event.capability.value == "MODEL_INFERENCE":
            raise RuntimeError("private audit failure")
        await original(self, event)

    monkeypatch.setattr(PostgresAccessAuditRecorder, "record", record)
    async with (
        await AsyncConnection.connect(os.environ["DATABASE_URL"], autocommit=True) as connection,
        connection.transaction(force_rollback=True),
    ):
        case_id, _, _ = await seed_case(connection, complete=True)
        await connection.execute("set local role casezero_blind")
        repository = InvestigationRepository(connection)
        investigation = await repository.create(case_id, runtime_configuration(StrictProvider.name, "a" * 40).canonical_payload())
        provider = StrictProvider()
        assert await Worker(repository, investigation, provider, worker_id="audit").run_once()
        assert not provider.requests
        assert (await repository.get(investigation.id)).status is InvestigationStatus.FAILED
        assert not (await repository.projection(investigation.id)).timeline


async def test_search_retry_preserves_successful_request_provenance():
    async with (
        await AsyncConnection.connect(os.environ["DATABASE_URL"], autocommit=True) as connection,
        connection.transaction(force_rollback=True),
    ):
        case_id, _, _ = await seed_case(connection, complete=True)
        await connection.execute("set local role casezero_blind")
        repository = InvestigationRepository(connection)
        investigation = await repository.create(case_id, runtime_configuration(StrictProvider.name, "a" * 40).canonical_payload())
        provider = StrictProvider(connection=connection)
        worker = Worker(repository, investigation, provider, worker_id="search")
        for _ in range(4):
            assert await worker.run_once()
        provider.failures = ["schema"]
        assert await worker.run_once()
        payload = next(iter((await repository.projection(investigation.id)).retrievals.values()))
        event = next(e for e in await repository.events(investigation.id) if e.event_type == "RETRIEVAL_COMPLETED")
        assert payload.model_run_id == event.model_run_id
        row = await (await connection.execute("select job_id from model_request_attempts where id=%s", (payload.model_run_id,))).fetchone()
        attempts = await repository.requests(row[0])
        assert [r.status.value for r in attempts] == ["SCHEMA_FAILED", "SUCCEEDED"]
        assert payload.model_run_id == attempts[-1].id
        assert provider.requests[-1].prompt.split("\nFEEDBACK\n")[0] == provider.requests[-2].prompt.split("\nFEEDBACK\n")[0]
        assert "private" not in provider.requests[-1].prompt


async def test_stage_rollback_retains_successful_receipt_and_fails_terminally(monkeypatch):
    from casezero_investigation import persistence

    original = persistence._write

    async def write_then_fail(connection, job, mutation):
        await original(connection, job, mutation)
        raise ValueError("private mutation payload")

    monkeypatch.setattr(persistence, "_write", write_then_fail)
    async with (
        await AsyncConnection.connect(os.environ["DATABASE_URL"], autocommit=True) as connection,
        connection.transaction(force_rollback=True),
    ):
        case_id, _, _ = await seed_case(connection, complete=True)
        await connection.execute("set local role casezero_blind")
        repository = InvestigationRepository(connection)
        investigation = await repository.create(case_id, runtime_configuration(StrictProvider.name, "a" * 40).canonical_payload())
        provider = StrictProvider(connection=connection)
        assert await Worker(repository, investigation, provider, worker_id="rollback").run_once()
        assert (await repository.get(investigation.id)).status is InvestigationStatus.FAILED
        assert not (await repository.projection(investigation.id)).timeline
        assert await (await connection.execute(
            "select status from model_request_attempts where investigation_id=%s", (investigation.id,),
        )).fetchone() == ("SUCCEEDED",)
        assert {e.event_type for e in await repository.events(investigation.id)} == {"INVESTIGATION_STARTED", "STAGE_TRANSITION"}
        await connection.execute("reset role")
        assert await (await connection.execute(
            "select count(*) from access_audit_events where case_id=%s and capability='MODEL_INFERENCE'", (case_id,),
        )).fetchone() == (1,)


async def test_only_genuinely_empty_promotion_batches_skip_model():
    async with (
        await AsyncConnection.connect(os.environ["DATABASE_URL"], autocommit=True) as connection,
        connection.transaction(force_rollback=True),
    ):
        case_id, _, _ = await seed_case(connection)
        await connection.execute("set local role casezero_blind")
        repository = InvestigationRepository(connection)
        investigation = await repository.create(case_id, runtime_configuration(StrictProvider.name, "a" * 40).canonical_payload())
        provider = StrictProvider()
        worker = Worker(repository, investigation, provider, worker_id="empty")
        assert await worker.run_once()
        assert await worker.run_once()
        assert not provider.requests
        assert (await repository.get(investigation.id)).current_stage.value == "PROMOTE_CLAIMS"
