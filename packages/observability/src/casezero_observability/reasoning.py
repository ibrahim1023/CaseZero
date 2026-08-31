import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from casezero_evidence import ProcessingDisposition
from pydantic import BaseModel
from pydantic_ai import Agent, ModelAPIError, UnexpectedModelBehavior
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider


class ModelFailure(RuntimeError):
    pass


class ModelRoutingDenied(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class ReasoningRequest[OutputT: BaseModel]:
    stage: str
    prompt: str
    prompt_template: str
    output_type: type[OutputT]
    case_id: UUID | None = None
    structural_unit_ids: tuple[UUID, ...] = ()

    @property
    def prompt_hash(self) -> str:
        return hashlib.sha256(self.prompt_template.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ReasoningResult[OutputT: BaseModel]:
    output: OutputT
    model_name: str
    fallback_used: bool
    run_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True, slots=True)
class StructuredGeneration[OutputT: BaseModel]:
    output: OutputT
    input_tokens: int | None = None
    output_tokens: int | None = None
    retry_count: int = 0
    schema_failure_count: int = 0


class StructuredModel(Protocol):
    name: str

    async def generate[OutputT: BaseModel](
        self, request: ReasoningRequest[OutputT]
    ) -> StructuredGeneration[OutputT]: ...


class ModelRunRecorder(Protocol):
    async def record_model_run(
        self,
        *,
        run_id: UUID,
        case_id: UUID,
        parent_run_id: UUID | None,
        stage: str,
        provider: str,
        model: str,
        prompt_hash: str,
        structural_unit_ids: tuple[UUID, ...],
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int,
        retry_count: int,
        schema_failure_count: int,
        status: str,
        created_at: datetime,
    ) -> None: ...


class ModelRouter:
    def __init__(
        self,
        primary: StructuredModel,
        *,
        recorder: ModelRunRecorder | None = None,
    ) -> None:
        self._primary = primary
        self._recorder = recorder

    async def generate[OutputT: BaseModel](
        self, request: ReasoningRequest[OutputT], disposition: ProcessingDisposition
    ) -> ReasoningResult[OutputT]:
        if disposition is not ProcessingDisposition.AI_ALLOWED:
            raise ModelRoutingDenied(f"model routing denied for {disposition.value}")
        run_id = uuid4()
        output = await self._run_model(self._primary, request, run_id, None)
        return ReasoningResult(output, self._primary.name, False, run_id)

    async def _run_model[OutputT: BaseModel](
        self,
        model: StructuredModel,
        request: ReasoningRequest[OutputT],
        run_id: UUID,
        parent_run_id: UUID | None,
    ) -> OutputT:
        started_at = datetime.now(UTC)
        started = time.monotonic()
        try:
            generation = await model.generate(request)
        except ModelFailure:
            await self._record(
                model,
                request,
                run_id,
                parent_run_id,
                started_at,
                started,
                "FAILED",
                None,
            )
            raise
        await self._record(
            model,
            request,
            run_id,
            parent_run_id,
            started_at,
            started,
            "SUCCEEDED",
            generation,
        )
        return generation.output

    async def _record(
        self,
        model: StructuredModel,
        request: ReasoningRequest[BaseModel],
        run_id: UUID,
        parent_run_id: UUID | None,
        started_at: datetime,
        started: float,
        status: str,
        generation: StructuredGeneration[BaseModel] | None,
    ) -> None:
        if self._recorder is None:
            return
        if request.case_id is None:
            raise ValueError("recorded model requests require case_id")
        await self._recorder.record_model_run(
            run_id=run_id,
            case_id=request.case_id,
            parent_run_id=parent_run_id,
            stage=request.stage,
            provider=str(getattr(model, "provider", "unclassified")),
            model=model.name,
            prompt_hash=request.prompt_hash,
            structural_unit_ids=request.structural_unit_ids,
            input_tokens=generation.input_tokens if generation is not None else None,
            output_tokens=generation.output_tokens if generation is not None else None,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            retry_count=generation.retry_count if generation is not None else 0,
            schema_failure_count=(
                generation.schema_failure_count if generation is not None else 0
            ),
            status=status,
            created_at=started_at,
        )


class PydanticReasoningModel:
    def __init__(self, name: str, model: OpenAIChatModel, provider: str = "openai-compatible") -> None:
        self.name = name
        self.provider = provider
        self._model = model

    @classmethod
    def openai_compatible(
        cls,
        name: str,
        base_url: str,
        api_key: str,
        *,
        provider: str = "openai-compatible",
    ) -> "PydanticReasoningModel":
        model = OpenAIChatModel(
            name,
            provider=OpenAIProvider(base_url=base_url, api_key=api_key),
        )
        return cls(name, model, provider)

    async def generate[OutputT: BaseModel](
        self, request: ReasoningRequest[OutputT]
    ) -> StructuredGeneration[OutputT]:
        agent = Agent(self._model, output_type=request.output_type, retries=2)
        try:
            result = await agent.run(request.prompt)
        except (UnexpectedModelBehavior, ModelAPIError) as error:
            raise ModelFailure(type(error).__name__) from error
        usage = result.usage
        return StructuredGeneration(
            output=result.output,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            retry_count=max(0, usage.requests - 1),
        )
