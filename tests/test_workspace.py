def test_workspace_packages_are_importable() -> None:
    import casezero_api
    import casezero_evidence
    import casezero_ntsb

    assert casezero_api.__name__ == "casezero_api"
    assert casezero_evidence.__name__ == "casezero_evidence"
    assert casezero_ntsb.__name__ == "casezero_ntsb"
