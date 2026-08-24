import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from casezero_ntsb.manifest import DocketManifest, load_manifest
from pydantic import ValidationError


@pytest.mark.parametrize("suffix", [".json", ".yaml"])
def test_load_curated_manifest_validates_json_and_yaml(tmp_path: Path, suffix: str) -> None:
    payload = {
        "caseId": "CEN25LA167",
        "documents": [
            {
                "title": "Aircraft Accident Final Report",
                "documentType": "FINAL_REPORT",
                "sourceUrl": "https://data.ntsb.gov/example.pdf",
                "fileType": "pdf",
                "pageCount": 4,
                "publishedAt": "2025-06-13T04:00:00Z",
            }
        ],
    }
    path = tmp_path / f"manifest{suffix}"
    if suffix == ".json":
        path.write_text(json.dumps(payload))
    else:
        path.write_text(
            "caseId: CEN25LA167\n"
            "documents:\n"
            "  - title: Aircraft Accident Final Report\n"
            "    documentType: FINAL_REPORT\n"
            "    sourceUrl: https://data.ntsb.gov/example.pdf\n"
            "    fileType: pdf\n"
            "    pageCount: 4\n"
            "    publishedAt: '2025-06-13T04:00:00Z'\n"
        )

    manifest = load_manifest(path)

    assert manifest.case_id == "CEN25LA167"
    assert str(manifest.documents[0].source_url) == "https://data.ntsb.gov/example.pdf"
    assert manifest.documents[0].published_at == datetime(2025, 6, 13, 4, tzinfo=UTC)


def test_manifest_rejects_document_without_source_url() -> None:
    with pytest.raises(ValidationError):
        DocketManifest.model_validate(
            {
                "caseId": "CEN25LA167",
                "documents": [{"title": "Untraceable document"}],
            }
        )


def test_load_manifest_rejects_unknown_format(tmp_path: Path) -> None:
    path = tmp_path / "manifest.toml"
    path.write_text("caseId = 'CEN25LA167'")

    with pytest.raises(ValueError, match="JSON or YAML"):
        load_manifest(path)
