from casezero_api.hosted_verify import HostedState, evaluate_hosted_state

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
}


def state(**changes) -> HostedState:
    values = {
        "environment": "development",
        "tables": EXPECTED_TABLES,
        "rls_tables": EXPECTED_TABLES,
        "buckets": {"casezero-sources": False, "casezero-derived": False},
        "processor_visible_final": False,
        "public_role_grants": 0,
    }
    values.update(changes)
    return HostedState(**values)


def test_development_private_hosted_state_passes() -> None:
    assert evaluate_hosted_state(state()) == ()


def test_production_target_fails_closed() -> None:
    assert "production" in evaluate_hosted_state(state(environment="production"))[0]


def test_public_or_missing_bucket_fails() -> None:
    errors = evaluate_hosted_state(state(buckets={"casezero-sources": True}))
    assert any("private" in error for error in errors)
    assert any("casezero-derived" in error for error in errors)


def test_public_role_grants_fail_closed() -> None:
    errors = evaluate_hosted_state(state(public_role_grants=1))
    assert any("grants" in error for error in errors)


def test_missing_rls_or_visibility_leak_fails() -> None:
    errors = evaluate_hosted_state(
        state(rls_tables=EXPECTED_TABLES - {"evidence_items"}, processor_visible_final=True)
    )
    assert any("RLS" in error for error in errors)
    assert any("FINAL_FINDING" in error for error in errors)
