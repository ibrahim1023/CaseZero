from datetime import datetime
from uuid import UUID

from casezero_evidence.models import EvidenceItem

from casezero_investigation.hypotheses import (
    ClaimContradictionParameters,
    EvidencePresenceParameters,
    ExecutionKind,
    HypothesisTest,
    HypothesisTestStatus,
    TemporalConsistencyParameters,
    TestOutcome,
)
from casezero_investigation.models import Claim, TimelineEvent, TimePrecision, _require_utc


def execute_test(
    test: HypothesisTest,
    evidence: tuple[EvidenceItem, ...],
    claims: tuple[Claim, ...],
    timeline: tuple[TimelineEvent, ...],
    completed_at: datetime,
) -> HypothesisTest:
    test = HypothesisTest.model_validate(test.model_dump())
    if test.status is not HypothesisTestStatus.PENDING:
        raise ValueError("only PENDING tests can be executed")
    if test.execution_kind is ExecutionKind.AI:
        raise ValueError("semantic comparison requires separate AI execution")
    _require_utc(completed_at)
    if completed_at < test.created_at:
        raise ValueError("test completion cannot precede creation")
    evidence_by_id = {item.id: item for item in evidence}
    claims_by_id = {item.id: item for item in claims}
    events_by_id = {item.id: item for item in timeline}
    if (
        len(evidence_by_id) != len(evidence)
        or len(claims_by_id) != len(claims)
        or len(events_by_id) != len(timeline)
    ):
        raise ValueError("duplicate state IDs in falsification inputs")
    canonical_state: tuple[Claim | TimelineEvent, ...] = (*claims, *timeline)
    if any(item.case_id != test.case_id for item in evidence) or any(
        item.case_id != test.case_id or item.investigation_id != test.investigation_id
        for item in canonical_state
    ):
        raise ValueError("falsification inputs do not match test context")
    if not set(test.evidence_ids) <= evidence_by_id.keys():
        raise ValueError("unknown evidence IDs in test")
    if not set(test.claim_ids) <= claims_by_id.keys():
        raise ValueError("unknown claim IDs in test")
    selected_evidence = (
        tuple(evidence_by_id[identifier] for identifier in test.evidence_ids)
        if test.evidence_ids
        else evidence
    )
    parameters = test.typed_parameters
    if isinstance(parameters, EvidencePresenceParameters):
        outcome, used_evidence_ids = _evidence_presence(
            parameters, selected_evidence, bool(test.evidence_ids)
        )
    elif isinstance(parameters, TemporalConsistencyParameters):
        outcome, used_evidence_ids = _temporal_consistency(
            parameters, events_by_id, {item.id for item in selected_evidence}
        )
    elif isinstance(parameters, ClaimContradictionParameters):
        outcome, used_evidence_ids = _claim_contradiction(
            tuple(claims_by_id[identifier] for identifier in test.claim_ids),
            {item.id for item in selected_evidence},
        )
    else:
        raise TypeError("unsupported deterministic test parameters")
    return HypothesisTest.model_validate(
        test.model_dump()
        | {
            "status": HypothesisTestStatus.SUCCEEDED,
            "outcome": outcome,
            "completed_at": completed_at,
            "result_evidence_ids": tuple(sorted(used_evidence_ids, key=str)),
            "claim_ids": tuple(sorted(test.claim_ids, key=str)),
        }
    )


def _evidence_presence(
    parameters: EvidencePresenceParameters,
    evidence: tuple[EvidenceItem, ...],
    named_evidence: bool,
) -> tuple[TestOutcome, set[UUID]]:
    if not named_evidence and parameters.evidence_type is None and parameters.subtype is None:
        return TestOutcome.INCONCLUSIVE, set()
    matches = {
        item.id
        for item in evidence
        if (parameters.evidence_type is None or item.type is parameters.evidence_type)
        and (parameters.subtype is None or item.subtype == parameters.subtype)
    }
    if matches:
        return TestOutcome.SURVIVED, matches
    return TestOutcome.EXPECTED_EVIDENCE_MISSING, set()


def _temporal_consistency(
    parameters: TemporalConsistencyParameters,
    events_by_id: dict[UUID, TimelineEvent],
    evidence_ids: set[UUID],
) -> tuple[TestOutcome, set[UUID]]:
    if not {parameters.before_event_id, parameters.after_event_id} <= events_by_id.keys():
        raise ValueError("unknown timeline event IDs in test parameters")
    before = events_by_id[parameters.before_event_id]
    after = events_by_id[parameters.after_event_id]
    before_evidence = set(before.evidence_ids) & evidence_ids
    after_evidence = set(after.evidence_ids) & evidence_ids
    used_evidence = before_evidence | after_evidence
    if (
        before.occurred_at is None
        or after.occurred_at is None
        or before.time_precision is not TimePrecision.EXACT
        or after.time_precision is not TimePrecision.EXACT
        or not before_evidence
        or not after_evidence
    ):
        return TestOutcome.INCONCLUSIVE, used_evidence
    if before.occurred_at < after.occurred_at:
        return TestOutcome.SURVIVED, used_evidence
    return TestOutcome.CONTRADICTED, used_evidence


def _claim_contradiction(
    claims: tuple[Claim, ...], evidence_ids: set[UUID]
) -> tuple[TestOutcome, set[UUID]]:
    used_evidence: set[UUID] = set()
    contradicted = False
    all_supported = bool(claims)
    for claim in claims:
        supporting = set(claim.supporting_evidence_ids) & evidence_ids
        contradicting = set(claim.contradicting_evidence_ids) & evidence_ids
        used_evidence |= supporting | contradicting
        contradicted = contradicted or bool(contradicting - supporting)
        all_supported = all_supported and bool(supporting) and not bool(supporting & contradicting)
    if contradicted:
        return TestOutcome.CONTRADICTED, used_evidence
    if all_supported:
        return TestOutcome.SURVIVED, used_evidence
    return TestOutcome.INCONCLUSIVE, used_evidence
