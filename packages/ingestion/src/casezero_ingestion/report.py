import json
from dataclasses import asdict, dataclass, field
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ProcessingFailure:
    stage: str
    error_type: str
    message: str
    retryable: bool
    source_document_id: UUID | None = None
    structural_unit_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ProcessingReport:
    inventory_total: int = 0
    disposition_counts: dict[str, int] = field(default_factory=dict)
    status_counts: dict[str, int] = field(default_factory=dict)
    artifacts: int = 0
    structural_units: int = 0
    evidence_items: int = 0
    candidates: int = 0
    review_pending: int = 0
    model_usage: dict[str, int] = field(default_factory=dict)
    failures: tuple[ProcessingFailure, ...] = ()
    reused_structural_runs: int = 0
    reused_semantic_runs: int = 0
    reused_candidate_runs: int = 0

    @property
    def sources_total(self) -> int:
        return self.inventory_total

    @property
    def succeeded(self) -> int:
        return self.status_counts.get("SUCCEEDED", 0)

    @property
    def failed(self) -> int:
        return self.status_counts.get("FAILED", 0) + self.status_counts.get("UNSUPPORTED", 0)

    @property
    def reused(self) -> int:
        return (
            self.reused_structural_runs
            + self.reused_semantic_runs
            + self.reused_candidate_runs
        )

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["failures"] = [
            {
                **failure,
                **{
                    key: str(item)
                    for key, item in failure.items()
                    if isinstance(item, UUID)
                },
            }
            for failure in value["failures"]
        ]
        return value

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)
