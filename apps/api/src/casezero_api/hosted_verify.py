from dataclasses import dataclass

EXPECTED_TABLES = {
    "cases",
    "docket_items",
    "source_documents",
    "source_blobs",
    "processing_runs",
    "structural_units",
    "evidence_items",
    "model_runs",
}
EXPECTED_BUCKETS = {"casezero-sources", "casezero-derived"}


@dataclass(frozen=True, slots=True)
class HostedState:
    environment: str
    tables: set[str]
    rls_tables: set[str]
    buckets: dict[str, bool]
    processor_visible_final: bool


def evaluate_hosted_state(state: HostedState) -> tuple[str, ...]:
    errors: list[str] = []
    if state.environment == "production":
        errors.append("hosted integration tests refuse production")
    elif state.environment not in {"development", "staging"}:
        errors.append("hosted environment marker is missing or invalid")
    missing_tables = EXPECTED_TABLES - state.tables
    if missing_tables:
        errors.append("missing tables: " + ", ".join(sorted(missing_tables)))
    missing_rls = EXPECTED_TABLES - state.rls_tables
    if missing_rls:
        errors.append("RLS missing: " + ", ".join(sorted(missing_rls)))
    for bucket in sorted(EXPECTED_BUCKETS):
        if bucket not in state.buckets:
            errors.append(f"missing private bucket: {bucket}")
        elif state.buckets[bucket]:
            errors.append(f"bucket must be private: {bucket}")
    if state.processor_visible_final:
        errors.append("processor policy may expose FINAL_FINDING")
    return tuple(errors)
