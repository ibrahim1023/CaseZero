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
    "semantic_unit_completions",
    "candidate_batch_completions",
    "investigation_locks",
    "access_audit_events",
    "investigations",
    "claims",
    "claim_evidence_links",
    "claim_candidate_links",
    "investigation_entities",
    "entity_evidence_links",
    "entity_candidate_links",
    "timeline_events",
    "timeline_evidence_links",
    "timeline_candidate_links",
    "hypotheses",
    "hypothesis_claim_links",
    "unresolved_questions",
    "hypothesis_critiques",
    "hypothesis_tests",
    "confidence_revisions",
    "confidence_revision_test_deltas",
    "investigation_evidence",
    "investigation_candidates",
    "investigation_jobs",
    "investigation_job_attempts",
    "model_request_attempts",
    "investigation_events",
    "investigation_spans",
    "retrieval_queries",
    "retrieval_results",
}
EXPECTED_BUCKETS = {"casezero-sources", "casezero-derived"}


@dataclass(frozen=True, slots=True)
class HostedState:
    environment: str
    tables: set[str]
    rls_tables: set[str]
    buckets: dict[str, bool]
    processor_visible_final: bool
    public_role_grants: int
    cutoff_column: bool
    lock_table: bool
    audit_table: bool
    lock_rls: bool
    audit_rls: bool
    reference_case_state: str
    reference_cutoff_matches: bool
    reference_locked: bool


def processor_policy_exposes_final(
    policy_qualifier: str, eligibility_definition: str
) -> bool:
    policy = policy_qualifier.casefold()
    helper = eligibility_definition.casefold()
    return "is_blind_metadata_eligible" not in policy or not all(
        value in helper
        for value in ("investigation_evidence", "ai_allowed", "final_report")
    )


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
    if state.public_role_grants:
        errors.append(f"public schema grants remain: {state.public_role_grants}")
    if not state.cutoff_column:
        errors.append("cases.evidence_cutoff is missing")
    if not state.lock_table or not state.lock_rls:
        errors.append("investigation_locks is missing forced RLS")
    if not state.audit_table or not state.audit_rls:
        errors.append("access_audit_events is missing forced RLS")
    if state.reference_case_state != "BLIND":
        errors.append("reference case state must remain BLIND")
    if not state.reference_cutoff_matches:
        errors.append("reference case cutoff does not match its manifest")
    if state.reference_locked:
        errors.append("reference case must remain unlocked")
    return tuple(errors)
