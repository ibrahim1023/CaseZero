import asyncio
import io
import json
import time
import zipfile
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx

from casezero_ntsb.models import AircraftMetadata, CaseMetadata

FILE_EXPORT_URL = "https://data.ntsb.gov/carol-main-public/api/Query/FileExport"


class CarolExportError(ValueError):
    pass


class CarolClient:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        minimum_interval: float = 5.0,
        max_attempts: int = 3,
    ) -> None:
        self._http_client = http_client
        self._clock = clock
        self._sleep = sleep
        self._minimum_interval = minimum_interval
        self._max_attempts = max_attempts
        self._last_request_at: float | None = None
        self._request_lock = asyncio.Lock()

    async def search_cases(
        self,
        *,
        date_from: date,
        date_to: date,
        mode: str = "Aviation",
    ) -> list[CaseMetadata]:
        if date_from > date_to:
            raise ValueError("date_from must not be after date_to")

        response = await self._post_with_retries(self._build_payload(date_from, date_to, mode))
        return self._parse_export(response.content)

    async def _post_with_retries(self, payload: dict[str, Any]) -> httpx.Response:
        for attempt in range(self._max_attempts):
            await self._wait_for_request_slot()
            response = await self._http_client.post(
                FILE_EXPORT_URL,
                headers={
                    "Accept": "*/*",
                    "Content-Type": "application/json",
                    "Origin": "https://data.ntsb.gov",
                    "User-Agent": "CaseZero/0.1",
                },
                json=payload,
                timeout=60,
            )
            if response.status_code != 429 and response.status_code < 500:
                response.raise_for_status()
                return response
            if attempt + 1 < self._max_attempts:
                await self._sleep(float(2**attempt))

        response.raise_for_status()
        raise RuntimeError("unreachable")

    async def _wait_for_request_slot(self) -> None:
        async with self._request_lock:
            if self._last_request_at is not None:
                elapsed = self._clock() - self._last_request_at
                if elapsed < self._minimum_interval:
                    await self._sleep(self._minimum_interval - elapsed)
            self._last_request_at = self._clock()

    @staticmethod
    def _build_payload(date_from: date, date_to: date, mode: str) -> dict[str, Any]:
        def rule(
            *, column: str, operator: str, value: str, field_name: str, input_type: str
        ) -> dict[str, Any]:
            return {
                "RuleType": "Simple",
                "Values": [value],
                "Columns": [column],
                "Operator": operator,
                "overrideColumn": "",
                "selectedOption": {
                    "FieldName": field_name,
                    "DisplayText": "",
                    "Columns": [column],
                    "Selectable": True,
                    "InputType": input_type,
                    "RuleType": 0,
                    "Options": None,
                    "TargetCollection": "cases",
                    "UnderDevelopment": True,
                },
            }

        rules = [
            rule(
                column="Event.EventDate",
                operator="is on or after",
                value=date_from.isoformat(),
                field_name="EventDate",
                input_type="Date",
            ),
            rule(
                column="Event.EventDate",
                operator="is on or before",
                value=date_to.isoformat(),
                field_name="EventDate",
                input_type="Date",
            ),
            rule(
                column="Event.Mode",
                operator="is",
                value=mode,
                field_name="Mode",
                input_type="Dropdown",
            ),
        ]
        return {
            "QueryGroups": [
                {
                    "QueryRules": rules,
                    "AndOr": "and",
                    "inLastSearch": False,
                    "editedSinceLastSearch": False,
                }
            ],
            "AndOr": "and",
            "TargetCollection": "cases",
            "ExportFormat": "data",
            "SessionId": 227230,
            "ResultSetSize": 500,
            "SortDescending": True,
        }

    @classmethod
    def _parse_export(cls, content: bytes) -> list[CaseMetadata]:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                json_names = [name for name in archive.namelist() if name.lower().endswith(".json")]
                if not json_names:
                    raise CarolExportError("CAROL export contains no JSON file")
                rows = json.loads(archive.read(json_names[0]))
        except (zipfile.BadZipFile, json.JSONDecodeError) as error:
            raise CarolExportError("CAROL returned an invalid export") from error

        if not isinstance(rows, list):
            raise CarolExportError("CAROL JSON export must contain a list")
        return [metadata for row in rows if (metadata := cls._parse_row(row)) is not None]

    @staticmethod
    def _parse_row(row: object) -> CaseMetadata | None:
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
            has_final_report=(
                isinstance(report_type, str) and report_type.casefold() == "final"
            ),
            mkey=mkey if isinstance(mkey, int) else None,
        )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
