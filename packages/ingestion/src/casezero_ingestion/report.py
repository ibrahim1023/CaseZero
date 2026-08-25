from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProcessingReport:
    sources_total: int
    succeeded: int
    failed: int
    reused: int
    artifacts: int
    structural_units: int
    evidence_items: int
    candidates: int
