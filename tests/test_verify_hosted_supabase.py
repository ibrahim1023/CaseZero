from casezero_api.hosted_verify import (
    HostedState,
    evaluate_hosted_state,
    processor_policy_exposes_final,
)

PHASE2_TABLES = {
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
}
PHASE3_TABLES = {
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
}
EXPECTED_TABLES = PHASE2_TABLES | PHASE3_TABLES


def state(**changes) -> HostedState:
    values = {
        "environment": "development",
        "tables": EXPECTED_TABLES,
        "rls_tables": EXPECTED_TABLES,
        "buckets": {"casezero-sources": False, "casezero-derived": False},
        "processor_visible_final": False,
        "public_role_grants": 0,
        "cutoff_column": True,
        "lock_table": True,
        "audit_table": True,
        "lock_rls": True,
        "audit_rls": True,
        "reference_case_state": "BLIND",
        "reference_cutoff_matches": True,
        "reference_locked": False,
    }
    values.update(changes)
    return HostedState(**values)


def test_processor_policy_verifier_follows_eligibility_helper() -> None:
    policy = "is_blind_metadata_eligible(source.title, source.visibility)"
    helper = "INVESTIGATION_EVIDENCE AI_ALLOWED FINAL_REPORT"
    assert processor_policy_exposes_final(policy, helper) is False
    assert processor_policy_exposes_final(policy, "AI_ALLOWED") is True
    assert processor_policy_exposes_final("visibility only", helper) is True


def test_development_private_hosted_state_passes() -> None:
    assert evaluate_hosted_state(state()) == ()


def test_phase3_canonical_tables_and_forced_rls_are_required() -> None:
    errors = evaluate_hosted_state(
        state(tables=PHASE2_TABLES, rls_tables=PHASE2_TABLES)
    )
    assert any("claims" in error for error in errors)
    assert any("hypotheses" in error for error in errors)
    assert any("RLS" in error for error in errors)


def test_phase2_schema_is_required() -> None:
    errors = evaluate_hosted_state(
        state(
            cutoff_column=False,
            lock_table=False,
            audit_table=False,
            lock_rls=False,
            audit_rls=False,
        )
    )
    assert any("evidence_cutoff" in error for error in errors)
    assert any("investigation_locks" in error for error in errors)
    assert any("access_audit_events" in error for error in errors)


def test_production_target_fails_closed() -> None:
    assert "production" in evaluate_hosted_state(state(environment="production"))[0]


def test_public_or_missing_bucket_fails() -> None:
    errors = evaluate_hosted_state(state(buckets={"casezero-sources": True}))
    assert any("private" in error for error in errors)
    assert any("casezero-derived" in error for error in errors)


def test_reference_case_must_remain_blind_cutoff_bound_and_unlocked() -> None:
    errors = evaluate_hosted_state(
        state(
            reference_case_state="LOCKED",
            reference_cutoff_matches=False,
            reference_locked=True,
        )
    )
    assert any("reference case state" in error for error in errors)
    assert any("reference case cutoff" in error for error in errors)
    assert any("reference case must remain unlocked" in error for error in errors)


def test_public_role_grants_fail_closed() -> None:
    errors = evaluate_hosted_state(state(public_role_grants=1))
    assert any("grants" in error for error in errors)


def test_missing_rls_or_visibility_leak_fails() -> None:
    errors = evaluate_hosted_state(
        state(rls_tables=EXPECTED_TABLES - {"evidence_items"}, processor_visible_final=True)
    )
    assert any("RLS" in error for error in errors)
    assert any("FINAL_FINDING" in error for error in errors)
