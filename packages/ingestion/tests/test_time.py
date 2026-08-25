from datetime import UTC

from casezero_ingestion.time import TimestampKind, classify_timestamp_column, normalize_explicit_utc


def test_explicit_utc_timestamp_normalizes() -> None:
    result = normalize_explicit_utc("2025-04-30T17:52:00Z")
    assert result.tzinfo is UTC


def test_relative_and_ambiguous_local_timestamps_stay_distinct() -> None:
    assert classify_timestamp_column("recorder_seconds", ["0", "1.5"]) is TimestampKind.RELATIVE
    assert classify_timestamp_column("local_time", ["12:01", "12:02"]) is TimestampKind.LOCAL
