from dataclasses import dataclass
from datetime import UTC, date, datetime
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


def parse_case_metadata(row: object) -> CaseMetadata | None:
    if not isinstance(row, dict):
        return None
    ntsb_number = row.get("cm_ntsbNum")
    event_date_raw = row.get("cm_eventDate")
    if not isinstance(ntsb_number, str) or not isinstance(event_date_raw, str):
        return None

    event_date = datetime.fromisoformat(event_date_raw).astimezone(UTC)
    vehicles = row.get("cm_vehicles")
    first_vehicle = vehicles[0] if isinstance(vehicles, list) and vehicles else {}
    if not isinstance(first_vehicle, dict):
        first_vehicle = {}

    location_parts = [row.get("cm_city"), row.get("cm_state") or row.get("cm_country")]
    location = ", ".join(part for part in location_parts if isinstance(part, str) and part)
    report_type = row.get("cm_mostRecentReportType")
    mkey = row.get("cm_mkey")
    return CaseMetadata(
        ntsb_number=ntsb_number,
        event_date=event_date,
        location=location,
        aircraft=AircraftMetadata(
            make=_optional_str(first_vehicle.get("make")),
            model=_optional_str(first_vehicle.get("model")),
            registration_number=_optional_str(first_vehicle.get("registrationNumber")),
            category=_optional_str(first_vehicle.get("aircraftCategory")),
        ),
        status=_optional_str(row.get("cm_completionStatus")) or "Unknown",
        has_final_report=isinstance(report_type, str) and report_type.casefold() == "final",
        mkey=mkey if isinstance(mkey, int) else None,
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
