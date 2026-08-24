import re
from datetime import datetime

from casezero_evidence import Visibility

_FINAL_PATTERN = re.compile(r"\b(final report|probable cause|adopted)\b", re.IGNORECASE)
_OFFICIAL_PATTERN = re.compile(r"\b(analysis|findings?|recommendations?)\b", re.IGNORECASE)


def classify_visibility(
    document_type: str | None,
    title: str,
    published_at: datetime | None,
    cutoff: datetime,
) -> Visibility:
    searchable = f"{document_type or ''} {title}".replace("_", " ")
    if _FINAL_PATTERN.search(searchable):
        return Visibility.FINAL_FINDING
    if _OFFICIAL_PATTERN.search(searchable):
        return Visibility.OFFICIAL_ANALYSIS
    if published_at is not None and published_at > cutoff:
        return Visibility.OFFICIAL_ANALYSIS
    if not document_type:
        return Visibility.OFFICIAL_ANALYSIS
    return Visibility.INVESTIGATION_EVIDENCE
