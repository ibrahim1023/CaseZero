import hashlib
from dataclasses import dataclass, field
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
    output_type: type[OutputT]

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

    async def generate[OutputT: BaseModel](self, request: ReasoningRequest[OutputT]) -> OutputT: ...


class ModelRouter:
    def __init__(self, primary: StructuredModel, local: StructuredModel) -> None:
        self._primary = primary
        self._local = local

    async def generate[OutputT: BaseModel](
        self, request: ReasoningRequest[OutputT], disposition: ProcessingDisposition
    ) -> ReasoningResult[OutputT]:
        if disposition in {ProcessingDisposition.LINK_ONLY, ProcessingDisposition.EXCLUDED}:
            raise ModelRoutingDenied(f"model routing denied for {disposition.value}")
        if disposition is ProcessingDisposition.LOCAL_ONLY:
            output = await self._local.generate(request)
            return ReasoningResult(output, self._local.name, False)
        try:
            output = await self._primary.generate(request)
            return ReasoningResult(output, self._primary.name, False)
        except ModelFailure:
            output = await self._local.generate(request)
            return ReasoningResult(output, self._local.name, True)


class PydanticReasoningModel:
    def __init__(self, name: str, model: OpenAIChatModel) -> None:
        self.name = name
        self._model = model

    @classmethod
    def openai_compatible(cls, name: str, base_url: str, api_key: str) -> "PydanticReasoningModel":
        model = OpenAIChatModel(
            name,
            provider=OpenAIProvider(base_url=base_url, api_key=api_key),
        )
        return cls(name, model)

    async def generate[OutputT: BaseModel](self, request: ReasoningRequest[OutputT]) -> OutputT:
        agent = Agent(self._model, output_type=request.output_type, retries=2)
        try:
            result = await agent.run(request.prompt)
        except (UnexpectedModelBehavior, ModelAPIError) as error:
            raise ModelFailure(type(error).__name__) from error
        return result.output
