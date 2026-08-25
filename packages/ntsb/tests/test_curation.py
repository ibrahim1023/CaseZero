from pathlib import Path

from casezero_evidence import ProcessingDisposition, RightsStatus
from casezero_ntsb.curation import (
    CuratedCaseManifest,
    load_curated_manifest,
    validate_curated_manifest,
)


def item(index: int, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "title": f"Item {index}",
        "sourceUrl": f"https://data.ntsb.gov/Docket/Document/docBLOB?ID={index}",
        "documentType": "FACTUAL_REPORT",
        "fileType": "pdf",
        "rightsStatus": RightsStatus.NTSB_AUTHORED.value,
        "processingDisposition": ProcessingDisposition.AI_ALLOWED.value,
        "attribution": "Source: National Transportation Safety Board",
        "reviewNote": "NTSB-authored fixture item",
        "reviewedAt": "2026-08-25T00:00:00Z",
        "expectedChecksum": f"{index:064x}",
    }
    values.update(overrides)
    return values


def manifest_payload() -> dict[str, object]:
    return {
        "caseId": "CEN22FA375",
        "docketUrl": "https://data.ntsb.gov/Docket/?NTSBNumber=CEN22FA375",
        "expectedItemCount": 15,
        "items": [item(index) for index in range(1, 16)],
    }


def test_reference_manifest_requires_all_fifteen_reviewed_items() -> None:
    payload = manifest_payload()
    payload["items"] = payload["items"][:-1]  # type: ignore[index]
    manifest = CuratedCaseManifest.model_validate(payload, strict=False)

    report = validate_curated_manifest(manifest)

    assert report.valid is False
    assert report.errors == ("expected 15 docket items, found 14",)


def test_unclear_rights_must_remain_link_only_without_checksum() -> None:
    payload = manifest_payload()
    payload["items"][0] = item(1, rightsStatus="THIRD_PARTY_UNCLEAR", processingDisposition="LINK_ONLY", expectedChecksum=None)  # type: ignore[index]
    manifest = CuratedCaseManifest.model_validate(payload, strict=False)

    report = validate_curated_manifest(manifest)

    assert report.valid is True


def test_processable_item_requires_expected_checksum() -> None:
    payload = manifest_payload()
    payload["items"][0] = item(1, expectedChecksum=None)  # type: ignore[index]
    manifest = CuratedCaseManifest.model_validate(payload, strict=False)

    assert validate_curated_manifest(manifest).errors == (
        "processable item 1 is missing expectedChecksum",
    )


def test_manifest_rejects_non_ntsb_source_host() -> None:
    payload = manifest_payload()
    payload["items"][0] = item(1, sourceUrl="https://example.com/report.pdf")  # type: ignore[index]
    manifest = CuratedCaseManifest.model_validate(payload, strict=False)

    assert validate_curated_manifest(manifest).errors == (
        "item 1 sourceUrl must use an ntsb.gov host",
    )


def test_committed_reference_manifest_is_complete_and_valid() -> None:
    path = Path("fixtures/real-cases/cen22fa375/manifest.json")

    report = validate_curated_manifest(load_curated_manifest(path))

    assert report.valid, report.errors


def test_manifest_rejects_duplicate_urls() -> None:
    payload = manifest_payload()
    payload["items"][1] = item(2, sourceUrl="https://data.ntsb.gov/Docket/Document/docBLOB?ID=1")  # type: ignore[index]
    manifest = CuratedCaseManifest.model_validate(payload, strict=False)

    assert validate_curated_manifest(manifest).errors == (
        "duplicate sourceUrl at item 2",
    )
