import re
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from itertools import combinations
from unicodedata import normalize
from uuid import UUID

from casezero_evidence.models import StrictModel
from pydantic import ConfigDict, Field, field_validator, model_validator, validate_call

from casezero_investigation.models import (
    _FOUR_PLACES,
    Claim,
    Hypothesis,
    HypothesisStatus,
    LinkPolarity,
    UnresolvedQuestion,
    _require_utc,
)

HYPOTHESES_PROMPT = (
    "casezero.hypotheses.v1: AI-Generated - Not an Official Finding. Generate 3 to 5 materially "
    "distinct, provisional competing explanations requiring expert review. Do not assign blame, "
    "negligence, unlawful conduct, or liability to any person or company. Cite only supplied "
    "canonical claim IDs, with explicit supporting and contradicting polarity. Keep unknowns "
    "unknown and record unresolved questions rather than inventing facts or timestamps. "
    "Each hypothesis requires a distinguishing prediction and weakening evidence description. "
    "Use model confidence from 0.05 to 0.85, not calibrated probability. Do not rename the same "
    "explanation to satisfy the count. Do not choose canonical IDs, status, or creation context. "
    "Do not use external knowledge or official findings."
)
_TOKENS = re.compile(r"[^\W_]+")


class HypothesisDraft(StrictModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    confidence: float = Field(ge=0.05, le=0.85, allow_inf_nan=False)
    distinguishing_prediction: str = Field(min_length=1)
    weakening_evidence: str = Field(min_length=1)
    supporting_claim_ids: tuple[UUID, ...]
    contradicting_claim_ids: tuple[UUID, ...]
    unresolved_questions: tuple[str, ...]

    @field_validator("title", "description", "distinguishing_prediction", "weakening_evidence")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("hypothesis text must not be blank")
        return value

    @field_validator("supporting_claim_ids", "contradicting_claim_ids")
    @classmethod
    def require_unique_claims(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(value) != len(set(value)):
            raise ValueError("hypothesis claim reference IDs must be unique")
        return value

    @field_validator("unresolved_questions")
    @classmethod
    def require_questions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(" ".join(text.split()).casefold() for text in value)
        if any(not text for text in normalized):
            raise ValueError("unresolved questions must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("unresolved questions must be unique")
        return value

    @model_validator(mode="after")
    def require_grounding(self) -> "HypothesisDraft":
        if not self.supporting_claim_ids and not self.contradicting_claim_ids:
            raise ValueError("hypothesis requires at least one claim reference")
        return self


def normalized_hypothesis_overlap(left: HypothesisDraft, right: HypothesisDraft) -> Decimal:
    left_tokens = set(_TOKENS.findall(normalize("NFKC", f"{left.title} {left.description}").casefold()))
    right_tokens = set(_TOKENS.findall(normalize("NFKC", f"{right.title} {right.description}").casefold()))
    union = left_tokens | right_tokens
    if not union:
        return Decimal(1)
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        return Decimal(len(left_tokens & right_tokens)) / Decimal(len(union))


class HypothesisGenerationResult(StrictModel):
    hypotheses: tuple[HypothesisDraft, ...] = Field(min_length=3, max_length=5)

    @model_validator(mode="after")
    def require_diversity(self) -> "HypothesisGenerationResult":
        if any(
            normalized_hypothesis_overlap(left, right) > Decimal("0.75")
            for left, right in combinations(self.hypotheses, 2)
        ):
            raise ValueError("hypothesis token overlap exceeds 0.75; explanations must be distinct")
        return self


class HypothesisClaimLink(StrictModel):
    investigation_id: UUID
    case_id: UUID
    hypothesis_id: UUID
    claim_id: UUID
    polarity: LinkPolarity


class HypothesisBundle(StrictModel):
    hypotheses: tuple[Hypothesis, ...]
    claim_links: tuple[HypothesisClaimLink, ...]
    questions: tuple[UnresolvedQuestion, ...]


@validate_call(config=ConfigDict(strict=True))
def materialize_hypotheses(
    output: HypothesisGenerationResult,
    claims: tuple[Claim, ...],
    investigation_id: UUID,
    case_id: UUID,
    model_run_id: UUID,
    created_at: datetime,
) -> HypothesisBundle:
    _require_utc(created_at)
    output = HypothesisGenerationResult.model_validate(output.model_dump())
    claims = tuple(Claim.model_validate(item.model_dump()) for item in claims)
    claim_ids = {item.id for item in claims}
    if len(claim_ids) != len(claims):
        raise ValueError("duplicate input claim IDs")
    if any(
        item.case_id != case_id or item.investigation_id != investigation_id for item in claims
    ):
        raise ValueError("claim inputs must match the supplied investigation and case context")
    if any(
        identifier not in claim_ids
        for draft in output.hypotheses
        for identifier in (*draft.supporting_claim_ids, *draft.contradicting_claim_ids)
    ):
        raise ValueError("hypothesis references unknown claim IDs")
    hypotheses: list[Hypothesis] = []
    links: list[HypothesisClaimLink] = []
    questions: list[UnresolvedQuestion] = []
    for draft in output.hypotheses:
        confidence = Decimal(str(draft.confidence)).quantize(_FOUR_PLACES, rounding=ROUND_HALF_EVEN)
        hypothesis = Hypothesis(
            investigation_id=investigation_id,
            case_id=case_id,
            title=draft.title,
            description=draft.description,
            initial_confidence=confidence,
            current_confidence=confidence,
            status=HypothesisStatus.ACTIVE,
            distinguishing_prediction=draft.distinguishing_prediction,
            weakening_evidence=draft.weakening_evidence,
            model_run_id=model_run_id,
            created_at=created_at,
        )
        hypotheses.append(hypothesis)
        for polarity, identifiers in (
            (LinkPolarity.SUPPORTING, draft.supporting_claim_ids),
            (LinkPolarity.CONTRADICTING, draft.contradicting_claim_ids),
        ):
            links.extend(
                HypothesisClaimLink(
                    investigation_id=investigation_id,
                    case_id=case_id,
                    hypothesis_id=hypothesis.id,
                    claim_id=identifier,
                    polarity=polarity,
                )
                for identifier in identifiers
            )
        questions.extend(
            UnresolvedQuestion(
                investigation_id=investigation_id,
                case_id=case_id,
                hypothesis_id=hypothesis.id,
                text=text,
                created_at=created_at,
            )
            for text in sorted(draft.unresolved_questions, key=lambda item: " ".join(item.split()).casefold())
        )
    return HypothesisBundle(
        hypotheses=tuple(hypotheses),
        claim_links=tuple(sorted(
            links, key=lambda link: (str(link.hypothesis_id), str(link.claim_id), link.polarity.value),
        )),
        questions=tuple(questions),
    )
