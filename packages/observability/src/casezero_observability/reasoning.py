import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from casezero_evidence import ProcessingDisposition
from pydantic import BaseModel
from pydantic_ai import Agent, ModelAPIError, PromptedOutput, UnexpectedModelBehavior
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
    output_type: type[OutputT]
    case_id: UUID | None = None
    structural_unit_ids: tuple[UUID, ...] = ()

    @property
    def prompt_hash(self) -> str:
        return hashlib.sha256(self.prompt.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ReasoningResult[OutputT: BaseModel]:
    output: OutputT
    model_name: str
    fallback_used: bool
    run_id: UUID = field(default_factory=uuid4)


class StructuredModel(Protocol):
    name: str

    async def generate[OutputT: BaseModel](
        self, request: ReasoningRequest[OutputT]
    ) -> OutputT: ...


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
        local: StructuredModel,
        *,
        recorder: ModelRunRecorder | None = None,
    ) -> None:
        self._primary = primary
        self._local = local
        self._recorder = recorder

    async def generate[OutputT: BaseModel](
        self, request: ReasoningRequest[OutputT], disposition: ProcessingDisposition
    ) -> ReasoningResult[OutputT]:
        if disposition in {ProcessingDisposition.LINK_ONLY, ProcessingDisposition.EXCLUDED}:
            raise ModelRoutingDenied(f"model routing denied for {disposition.value}")
        if disposition is ProcessingDisposition.LOCAL_ONLY:
            run_id = uuid4()
            output = await self._run_model(self._local, request, run_id, None)
            return ReasoningResult(output, self._local.name, False, run_id)

        primary_run_id = uuid4()
        try:
            output = await self._run_model(self._primary, request, primary_run_id, None)
            return ReasoningResult(output, self._primary.name, False, primary_run_id)
        except ModelFailure:
            fallback_run_id = uuid4()
            output = await self._run_model(
                self._local, request, fallback_run_id, primary_run_id
            )
            return ReasoningResult(output, self._local.name, True, fallback_run_id)

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
            output = await model.generate(request)
        except ModelFailure:
            await self._record(
                model,
                request,
                run_id,
                parent_run_id,
                started_at,
                started,
                "FAILED",
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
        )
        return output

    async def _record(
        self,
        model: StructuredModel,
        request: ReasoningRequest[BaseModel],
        run_id: UUID,
        parent_run_id: UUID | None,
        started_at: datetime,
        started: float,
        status: str,
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
            input_tokens=None,
            output_tokens=None,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            retry_count=0,
            schema_failure_count=0,
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
    ) -> OutputT:
        output_type = (
            PromptedOutput(request.output_type)
            if self.provider == "ollama"
            else request.output_type
        )
        agent = Agent(self._model, output_type=output_type, retries=2)
        try:
            result = await agent.run(request.prompt)
        except (UnexpectedModelBehavior, ModelAPIError) as error:
            raise ModelFailure(type(error).__name__) from error
        return result.output
