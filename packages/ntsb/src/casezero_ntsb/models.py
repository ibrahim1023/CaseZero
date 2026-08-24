from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class AircraftMetadata:
    make: str | None = None
    model: str | None = None
    registration_number: str | None = None
    category: str | None = None


@dataclass(frozen=True, slots=True)
class CaseMetadata:
    ntsb_number: str
    event_date: datetime
    location: str
    aircraft: AircraftMetadata
    status: str
    has_final_report: bool
    mkey: int | None = None


class CaseCatalog(Protocol):
    async def search_cases(
        self, *, date_from: date, date_to: date, mode: str = "Aviation"
    ) -> list[CaseMetadata]: ...
