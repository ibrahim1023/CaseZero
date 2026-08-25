from datetime import UTC, datetime
from enum import StrEnum


class TimestampKind(StrEnum):
    UTC = "UTC"
    LOCAL = "LOCAL"
    RELATIVE = "RELATIVE"
    UNKNOWN = "UNKNOWN"


def normalize_explicit_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("timestamp is not explicit UTC")
    return parsed.astimezone(UTC)


def classify_timestamp_column(name: str, values: list[str]) -> TimestampKind:
    normalized = name.casefold()
    if any(token in normalized for token in ("relative", "recorder", "elapsed", "seconds")):
        return TimestampKind.RELATIVE
    if "utc" in normalized or all(value.endswith(("Z", "+00:00")) for value in values):
        return TimestampKind.UTC
    if any(token in normalized for token in ("local", "clock")):
        return TimestampKind.LOCAL
    return TimestampKind.UNKNOWN
