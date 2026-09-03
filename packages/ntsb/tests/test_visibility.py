from datetime import UTC, datetime, timedelta

import pytest
from casezero_evidence import Visibility
from casezero_ntsb.visibility import classify_visibility

CUTOFF = datetime(2025, 5, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("document_type", "title", "published_at", "expected"),
    [
        ("FINAL_REPORT", "Aircraft Accident Final Report", None, Visibility.FINAL_FINDING),
        ("REPORT", "Probable Cause", None, Visibility.FINAL_FINDING),
        ("REPORT", "Adopted report", None, Visibility.FINAL_FINDING),
        ("ANALYSIS", "Human Performance Analysis", None, Visibility.OFFICIAL_ANALYSIS),
        ("FINDINGS", "Findings and recommendations", None, Visibility.OFFICIAL_ANALYSIS),
        (
            "FACTUAL_REPORT",
            "Aircraft factual report",
            CUTOFF + timedelta(microseconds=1),
            Visibility.OFFICIAL_ANALYSIS,
        ),
        (
            "FACTUAL_REPORT",
            "Aircraft factual report",
            CUTOFF,
            Visibility.INVESTIGATION_EVIDENCE,
        ),
        ("WITNESS_STATEMENT", "Witness statement", None, Visibility.OFFICIAL_ANALYSIS),
        (None, "Unclassified attachment", None, Visibility.OFFICIAL_ANALYSIS),
    ],
)
def test_classify_visibility_is_deterministic_and_conservative(
    document_type: str | None,
    title: str,
    published_at: datetime | None,
    expected: Visibility,
) -> None:
    assert classify_visibility(document_type, title, published_at, CUTOFF) is expected
