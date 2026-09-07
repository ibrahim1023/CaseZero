import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from casezero_evidence import EvidenceItem
from casezero_evidence.access import PostgresAccessAuditRecorder
from casezero_evidence.blindness import (
    AccessAuditEvent,
    AccessCapability,
    AccessOperation,
    AuditReasonCode,
    RuntimeActor,
    WorkflowStage,
)
from casezero_evidence.models import StrictModel
from casezero_observability.reasoning import ModelFailure, ReasoningRequest, StructuredModel
from psycopg import Error
from pydantic import BaseModel, ValidationError

from casezero_investigation.canonical import canonical_digest
from casezero_investigation.confidence import assign_hypothesis_statuses, revise_confidence
from casezero_investigation.critic import CRITIC_PROMPT, CritiqueDraft, materialize_critique
from casezero_investigation.events import (
    ClaimCreatedPayload,
    ConfidenceRevisedPayload,
    CritiqueCreatedPayload,
    EntityCreatedPayload,
    HypothesisCreatedPayload,
    HypothesisStatusChangedPayload,
    InvestigationCompletedPayload,
    RetrievalCompletedPayload,
    TestCreatedPayload,
    TestResolvedPayload,
    TimelineCreatedPayload,
)
from casezero_investigation.falsification import execute_test
from casezero_investigation.generation import (
    HYPOTHESES_PROMPT,
    HypothesisGenerationResult,
    materialize_hypotheses,
)
from casezero_investigation.hypotheses import (
    ExecutionKind,
    HypothesisTest,
    HypothesisTestStatus,
    TestOutcome,
)
from casezero_investigation.jobs import (
    FailureCode,
    InvestigationConfig,
    InvestigationJob,
    ValidationIssue,
)
from casezero_investigation.models import (
    Investigation,
    InvestigationStatus,
    LinkPolarity,
    UnresolvedQuestion,
)
from casezero_investigation.models import InvestigationStage as Stage
from casezero_investigation.persistence import StageMutation, persist_stage_result
from casezero_investigation.promotion import (
    PROMOTION_CLAIMS_PROMPT,
    PROMOTION_ENTITIES_PROMPT,
    PROMOTION_TIMELINE_PROMPT,
    ClaimPromotion,
    EntityResolution,
    TimelinePromotion,
    materialize_claims,
    materialize_entities,
    materialize_timeline,
)
from casezero_investigation.replay import InvestigationProjection, ReplayError
from casezero_investigation.repository import CandidateBatch, InvestigationRepository
from casezero_investigation.tools import InvestigationTools, SearchDraft

SEARCH_PROMPT = (
    "casezero.search.v1: Draft a short metadata/FTS search for the supplied hypothesis and fixed "
    "retrieval intent. Use a few discriminating terms, not a sentence: all terms must match. "
    "SUPPORT seeks the distinguishing prediction; CONTRADICT seeks the weakening evidence. "
    "Use only supplied canonical state; no external knowledge or official findings. "
    "Return only search text, never IDs, scores, SQL, or execution context."
)
SEMANTIC_PROMPT = (
    "casezero.semantic-comparison.v1: AI-Generated - Not an Official Finding. Compare only the "
    "supplied evidence and claims against the immutable falsification test and hypothesis. "
    "The result is INFERRED, provisional and requires expert review. Cite only supplied evidence "
    "IDs in the fixed scope. Do not choose context, change test inputs, or assert blame or liability. "
    "Report CONTRADICTED for a grounded contradiction, SURVIVED only for positive observed matching "
    "evidence, EXPECTED_EVIDENCE_MISSING only when explicitly expected evidence is absent, otherwise "
    "INCONCLUSIVE. Empty contradiction retrieval is not support. No external knowledge or official findings."
)
PROMPTS = {
    Stage.PROMOTE_TIMELINE: PROMOTION_TIMELINE_PROMPT,
    Stage.RESOLVE_ENTITIES: PROMOTION_ENTITIES_PROMPT,
    Stage.PROMOTE_CLAIMS: PROMOTION_CLAIMS_PROMPT,
    Stage.GENERATE_HYPOTHESES: HYPOTHESES_PROMPT,
    Stage.SEARCH_SUPPORT: SEARCH_PROMPT,
    Stage.SEARCH_CONTRADICTIONS: SEARCH_PROMPT,
    Stage.DESIGN_FALSIFICATION_TESTS: CRITIC_PROMPT,
    Stage.EXECUTE_FALSIFICATION_TESTS: SEMANTIC_PROMPT,
}
MAX_INPUT_BYTES = 180_000


def _prompt_state(state: InvestigationProjection) -> dict[str, object]:
    return {
        "claims": {
            str(identifier): claim.model_dump(mode="json", include={
                "text", "status", "confidence", "supporting_evidence_ids",
                "contradicting_evidence_ids",
            }) for identifier, claim in state.claims.items()
        },
        "timeline": {
            str(identifier): event.model_dump(mode="json", include={
                "occurred_at", "time_precision", "description", "confidence", "evidence_ids",
            }) for identifier, event in state.timeline.items()
        },
        "entities": {
            str(identifier): entity.model_dump(mode="json", include={
                "type", "canonical_name", "aliases", "evidence_ids",
            }) for identifier, entity in state.entities.items()
        },
        "questions": {
            str(identifier): question.model_dump(mode="json", include={"hypothesis_id", "text"})
            for identifier, question in state.questions.items()
        },
    }


class SemanticResult(StrictModel):
    classification: Literal["INFERRED"]
    outcome: TestOutcome
    result_evidence_ids: tuple[UUID, ...]


class StageExhausted(RuntimeError):
    def __init__(self, code: FailureCode, request_id: UUID | None = None) -> None:
        super().__init__(code.value)
        self.code = code
        self.request_id = request_id


def runtime_configuration(model: str, git_sha: str) -> InvestigationConfig:
    return InvestigationConfig(
        stage_versions={stage.value: "phase3-worker-v1" for stage in Stage},
        model_versions={stage.value: model if stage in PROMPTS else "deterministic" for stage in Stage},
        prompt_versions={stage.value: hashlib.sha256(PROMPTS.get(stage, "deterministic-v1").encode()).hexdigest() for stage in Stage},
        prompt_git_shas={stage.value: git_sha for stage in Stage},
        retrieval_version="fts-v1", confidence_rule="weighted-delta-v1",
    )


class Worker:
    def __init__(
        self, repository: InvestigationRepository, investigation: Investigation, model: StructuredModel,
        *, worker_id: str, network_host: str = "api.hyperfusion.io",
    ) -> None:
        if network_host != "api.hyperfusion.io":
            raise ValueError("INVALID_MODEL_HOST")
        if not repository.connection.autocommit:
            raise ValueError("AUTOCOMMIT_REQUIRED")
        self.repository = repository
        self.investigation = investigation
        self.model = model
        self.worker_id = worker_id
        self.network_host = network_host

    async def run_once(self) -> bool:
        current = await self.repository.get(self.investigation.id)
        if current.status in {InvestigationStatus.SUCCEEDED, InvestigationStatus.FAILED}:
            return False
        config = await self.repository.configuration(current.id)
        expected = runtime_configuration(self.model.name, config.prompt_git_shas[Stage.PROMOTE_TIMELINE])
        if config != expected:
            raise ValueError("RUNTIME_CONFIGURATION_MISMATCH")
        job = await self.repository.claim(current.id, self.worker_id)
        if job is None:
            return False
        span = await self.repository.start_span(job, self.worker_id, "AGENT", job.stage.value, config.stage_versions[job.stage])
        tools = InvestigationTools(self.repository, current, job, self.worker_id, span)
        failure = FailureCode.PERSISTENCE_FAILED
        try:
            mutations = await self._stage(job, span, config, tools)
            await persist_stage_result(self.repository.connection, job, self.worker_id, span, mutations)
            return True
        except StageExhausted as error:
            failure = error.code
        except ReplayError:
            failure = FailureCode.REPLAY_MISMATCH
        except (Error, ValueError, TypeError, LookupError, PermissionError, RuntimeError):
            pass
        await self.repository.finish_span(span, job, self.worker_id, "FAILED")
        await self.repository.fail(job, self.worker_id, failure)
        return True

    async def _stage(
        self, job: InvestigationJob, span: UUID, config: InvestigationConfig, tools: InvestigationTools,
    ) -> tuple[StageMutation, ...]:
        state = await tools.get_state()
        if job.stage is Stage.VERIFY_REPLAY:
            return ()
        if job.stage is Stage.COMPLETE:
            return (InvestigationCompletedPayload(projection_hash=canonical_digest(state.canonical_state())),)
        if job.stage is Stage.REVISE_CONFIDENCE:
            revisions = tuple(revise_confidence(
                h, tuple(t for t in state.tests.values() if t.hypothesis_id == h.id), datetime.now(UTC),
            ) for h in state.hypotheses.values())
            revised = tuple(state.hypotheses[r.hypothesis_id].model_copy(update={"current_confidence": r.after}) for r in revisions)
            return (
                *(ConfidenceRevisedPayload(record=r) for r in revisions),
                *(HypothesisStatusChangedPayload(hypothesis_id=h.id, before=state.hypotheses[h.id].status, after=h.status)
                  for h in assign_hypothesis_statuses(revised) if h.status != state.hypotheses[h.id].status),
            )
        batch = CandidateBatch()
        if job.stage in {Stage.PROMOTE_TIMELINE, Stage.RESOLVE_ENTITIES, Stage.PROMOTE_CLAIMS}:
            batch = await tools.candidate_batch()
            if batch.empty:
                return ()
        evidence = await tools.get_evidence()
        if not evidence:
            raise StageExhausted(FailureCode.REFERENCE_INVALID)
        claims = tuple(state.claims.values())
        timeline = tuple(state.timeline.values())
        if job.stage is Stage.EXECUTE_FALSIFICATION_TESTS:
            test = state.tests[UUID(job.work_key)]
            if test.execution_kind is ExecutionKind.DETERMINISTIC:
                return (TestResolvedPayload(record=execute_test(test, evidence, claims, timeline, datetime.now(UTC))),)
            evidence = tuple(e for e in evidence if not test.evidence_ids or e.id in test.evidence_ids)
        data: dict[str, object]
        if not batch.empty:
            candidate_ids = {eid for entity in batch.entities for eid in entity.evidence_ids}
            candidate_ids.update(eid for event in batch.timeline for eid in event.evidence_ids)
            candidate_ids.update(eid for claim in batch.claims for eid in (*claim.supporting_evidence_ids, *claim.contradicting_evidence_ids))
            evidence = tuple(e for e in evidence if e.id in candidate_ids)
            data = {"candidates": batch.model_dump(mode="json"), "evidence": [e.model_dump(mode="json") for e in evidence]}
            data["sources"] = [(await tools.get_source_document(identifier)).model_dump(mode="json") for identifier in sorted({e.source_document_id for e in evidence})]
        elif job.stage is Stage.GENERATE_HYPOTHESES:
            if not claims:
                raise StageExhausted(FailureCode.REFERENCE_INVALID)
            data = {"state": _prompt_state(state)}
        elif job.stage in {Stage.SEARCH_SUPPORT, Stage.SEARCH_CONTRADICTIONS, Stage.DESIGN_FALSIFICATION_TESTS}:
            hypothesis = state.hypotheses[UUID(job.work_key)]
            data = {"hypothesis": hypothesis.model_dump(mode="json"), "state": _prompt_state(state)}
            if job.stage is Stage.DESIGN_FALSIFICATION_TESTS:
                contradictions = tuple(
                    retrieval for retrieval in state.retrievals.values()
                    if retrieval.hypothesis_id == hypothesis.id
                    and retrieval.query.intent.value == "CONTRADICT"
                )
                if not contradictions:
                    raise StageExhausted(FailureCode.REFERENCE_INVALID)
                links = state.hypothesis_claim_links[hypothesis.id]
                linked_claims = tuple(
                    state.claims[identifier]
                    for identifier in {*links.supporting, *links.contradicting}
                )
                relevant_evidence_ids = {
                    result.evidence_id for retrieval in contradictions for result in retrieval.results
                }
                relevant_evidence_ids.update(
                    identifier for claim in linked_claims
                    for identifier in (*claim.supporting_evidence_ids, *claim.contradicting_evidence_ids)
                )
                relevant_evidence_ids.update(
                    identifier for event in state.timeline.values() for identifier in event.evidence_ids
                )
                evidence = tuple(item for item in evidence if item.id in relevant_evidence_ids)
                data["contradiction_retrievals"] = [
                    retrieval.model_dump(mode="json") for retrieval in contradictions
                ]
                data["evidence"] = [item.model_dump(mode="json") for item in evidence]
            else:
                data["intent"] = "SUPPORT" if job.stage is Stage.SEARCH_SUPPORT else "CONTRADICT"
        elif job.stage is Stage.EXECUTE_FALSIFICATION_TESTS:
            test = state.tests[UUID(job.work_key)]
            data = {
                "test": test.model_dump(mode="json"), "hypothesis": state.hypotheses[test.hypothesis_id].model_dump(mode="json"),
                "evidence": [e.model_dump(mode="json") for e in evidence],
                "claims": [c.model_dump(mode="json") for c in claims if c.id in test.claim_ids],
            }
        else:
            raise ValueError("unsupported stage")
        prompt_input = json.dumps(data, sort_keys=True, ensure_ascii=False)
        if len(prompt_input.encode()) > MAX_INPUT_BYTES:
            raise StageExhausted(FailureCode.SCHEMA_INVALID)
        try:
            result, request_id = await self._generate(job, span, config, prompt_input, state, batch, evidence, tools)
        except StageExhausted as error:
            if job.stage is not Stage.EXECUTE_FALSIFICATION_TESTS or error.request_id is None:
                raise
            test = state.tests[UUID(job.work_key)]
            return (TestResolvedPayload(record=HypothesisTest.model_validate(test.model_dump() | {
                "status": HypothesisTestStatus.FAILED, "completed_at": datetime.now(UTC), "model_run_id": error.request_id,
            })),)
        if isinstance(result, SearchDraft):
            query, results = await tools.search(result)
            questions: tuple[UnresolvedQuestion, ...] = ()
            if query.intent.value == "CONTRADICT" and not results:
                questions = (UnresolvedQuestion(
                    investigation_id=state.investigation_id, case_id=state.case_id, hypothesis_id=UUID(job.work_key),
                    text="Contradiction retrieval returned no matches; expected weakening evidence remains unverified.", created_at=datetime.now(UTC),
                ),)
            return (RetrievalCompletedPayload(query_id=uuid4(), hypothesis_id=UUID(job.work_key), query=query, results=results, model_run_id=request_id, questions=questions),)
        return result

    async def _generate(
        self, job: InvestigationJob, span: UUID, config: InvestigationConfig, prompt_input: str,
        state: InvestigationProjection, batch: CandidateBatch, evidence: tuple[EvidenceItem, ...], tools: InvestigationTools,
    ) -> tuple[tuple[StageMutation, ...] | SearchDraft, UUID]:
        output_types: dict[Stage, type[BaseModel]] = {
            Stage.PROMOTE_TIMELINE: TimelinePromotion, Stage.RESOLVE_ENTITIES: EntityResolution,
            Stage.PROMOTE_CLAIMS: ClaimPromotion, Stage.GENERATE_HYPOTHESES: HypothesisGenerationResult,
            Stage.SEARCH_SUPPORT: SearchDraft, Stage.SEARCH_CONTRADICTIONS: SearchDraft,
            Stage.DESIGN_FALSIFICATION_TESTS: CritiqueDraft, Stage.EXECUTE_FALSIFICATION_TESTS: SemanticResult,
        }
        output_type = output_types[job.stage]
        previous = await self.repository.requests(job.id)
        feedback = tuple(issue for request in previous for issue in request.validation_issues)
        failure = FailureCode.PROVIDER_EXHAUSTED
        last_id = None
        for _ in range(3 - len(previous)):
            request = await self.repository.reserve_request(job.id, self.worker_id, job.attempt_count)
            await PostgresAccessAuditRecorder(self.repository.connection).record(AccessAuditEvent(
                case_id=state.case_id, stage=WorkflowStage.BLIND, actor_role=RuntimeActor.BLIND,
                capability=AccessCapability.MODEL_INFERENCE, operation=AccessOperation.NETWORK,
                network_host=self.network_host, allowed=True, reason_code=AuditReasonCode.ALLOWED_MODEL_HOST,
                occurred_at=datetime.now(UTC),
            ))
            last_id = request.id
            started = time.monotonic()
            generation = None
            issues: tuple[ValidationIssue, ...] = ()
            result: tuple[StageMutation, ...] | SearchDraft = ()
            status = "SUCCEEDED"
            try:
                generation = await self.model.generate(ReasoningRequest(
                    stage=job.stage.value, prompt_template=PROMPTS[job.stage], output_type=output_type, case_id=state.case_id,
                    prompt=PROMPTS[job.stage] + "\nINPUT\n" + prompt_input + "\nFEEDBACK\n" + json.dumps([i.model_dump(mode="json") for i in feedback]),
                ))
                output = output_type.model_validate(generation.output.model_dump())
                if generation.retry_count or generation.schema_failure_count:
                    raise ValueError("multiple provider requests forbidden")
                result = self._materialize(job, output, request.id, state, batch, evidence, tools)
            except ModelFailure as error:
                if type(error.__cause__).__name__ == "UnexpectedModelBehavior":
                    status = "SCHEMA_FAILED"
                    failure = FailureCode.SCHEMA_INVALID
                    issues = (ValidationIssue(path=("output",), code="schema_invalid"),)
                    feedback = issues
                else:
                    status = "PROVIDER_FAILED"
                    failure = FailureCode.PROVIDER_EXHAUSTED
            except ValidationError:
                status = "SCHEMA_FAILED"
                failure = FailureCode.SCHEMA_INVALID
                issues = (ValidationIssue(path=("output",), code="schema_invalid"),)
                feedback = issues
            except (ValueError, TypeError, KeyError):
                status = "SCHEMA_FAILED"
                failure = FailureCode.REFERENCE_INVALID
                issues = (ValidationIssue(path=("output",), code="structured_or_reference_invalid"),)
                feedback = issues
            await self.repository.finish_request(
                request.id, job, self.worker_id, status, issues, config.model_versions[job.stage],
                config.prompt_versions[job.stage], config.prompt_git_shas[job.stage], span,
                generation.input_tokens if generation else None, generation.output_tokens if generation else None,
                max(0, int((time.monotonic() - started) * 1000)),
            )
            if status == "SUCCEEDED":
                return result, request.id
        raise StageExhausted(failure, last_id)

    def _materialize(
        self, job: InvestigationJob, output: BaseModel, run_id: UUID, state: InvestigationProjection,
        batch: CandidateBatch, evidence: tuple[EvidenceItem, ...], tools: InvestigationTools,
    ) -> tuple[StageMutation, ...] | SearchDraft:
        now = datetime.now(UTC)
        investigation_id, case_id = state.investigation_id, state.case_id
        if isinstance(output, TimelinePromotion):
            return tuple(TimelineCreatedPayload(record=r) for r in materialize_timeline(output, batch.timeline, evidence, investigation_id, case_id, run_id, now))
        if isinstance(output, EntityResolution):
            return tuple(EntityCreatedPayload(record=r) for r in materialize_entities(output, batch.entities, evidence, investigation_id, case_id, run_id, now))
        if isinstance(output, ClaimPromotion):
            return tuple(ClaimCreatedPayload(record=r) for r in materialize_claims(output, batch.claims, evidence, investigation_id, case_id, run_id, now))
        if isinstance(output, HypothesisGenerationResult):
            bundle = materialize_hypotheses(output, tuple(state.claims.values()), investigation_id, case_id, run_id, now)
            return tuple(HypothesisCreatedPayload(
                record=h,
                supporting_claim_ids=tuple(link.claim_id for link in bundle.claim_links if link.hypothesis_id == h.id and link.polarity is LinkPolarity.SUPPORTING),
                contradicting_claim_ids=tuple(link.claim_id for link in bundle.claim_links if link.hypothesis_id == h.id and link.polarity is LinkPolarity.CONTRADICTING),
                questions=tuple(q for q in bundle.questions if q.hypothesis_id == h.id),
            ) for h in bundle.hypotheses)
        if isinstance(output, SearchDraft):
            tools.search_query(output)
            return output
        if isinstance(output, CritiqueDraft):
            critique = materialize_critique(output, state.hypotheses[UUID(job.work_key)], evidence, tuple(state.claims.values()), tuple(state.timeline.values()), job.id, run_id, now)
            return (CritiqueCreatedPayload(record=critique.critique), *(TestCreatedPayload(record=t) for t in critique.tests))
        if isinstance(output, SemanticResult):
            test = state.tests[UUID(job.work_key)]
            if not set(output.result_evidence_ids) <= {e.id for e in evidence}:
                raise ValueError("semantic result outside fixed evidence scope")
            if output.outcome in {TestOutcome.SURVIVED, TestOutcome.CONTRADICTED} and not output.result_evidence_ids:
                raise ValueError("semantic conclusion requires evidence")
            return (TestResolvedPayload(record=HypothesisTest.model_validate(test.model_dump() | {
                "status": HypothesisTestStatus.SUCCEEDED, "outcome": output.outcome,
                "result_evidence_ids": output.result_evidence_ids, "model_run_id": run_id, "completed_at": now,
            })),)
        raise TypeError("unsupported stage output")
