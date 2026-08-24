import io
import json
import zipfile
from datetime import UTC, date, datetime

import httpx
import pytest
import respx
from casezero_ntsb.carol import FILE_EXPORT_URL, CarolClient
from casezero_ntsb.models import CaseNotFound


def carol_export(rows: list[dict[str, object]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("results.json", json.dumps(rows))
        archive.writestr("README.txt", "CAROL export fixture")
    return buffer.getvalue()


@respx.mock
@pytest.mark.asyncio
async def test_search_cases_posts_real_carol_shape_and_parses_export() -> None:
    route = respx.post(FILE_EXPORT_URL).mock(
        return_value=httpx.Response(
            200,
            content=carol_export(
                [
                    {
                        "cm_ntsbNum": "CEN25LA167",
                        "cm_eventDate": "2025-04-30T17:52:00Z",
                        "cm_city": "Bethany",
                        "cm_state": "OK",
                        "cm_completionStatus": "Completed",
                        "cm_mostRecentReportType": "Final",
                        "cm_vehicles": [
                            {
                                "make": "BELL",
                                "model": "505",
                                "registrationNumber": "N9TV",
                                "aircraftCategory": "HELI",
                            }
                        ],
                    }
                ]
            ),
        )
    )
    async with httpx.AsyncClient() as http_client:
        client = CarolClient(http_client=http_client)
        cases = await client.search_cases(
            date_from=date(2025, 4, 1),
            date_to=date(2025, 4, 30),
        )

    assert cases[0].ntsb_number == "CEN25LA167"
    assert cases[0].event_date == datetime(2025, 4, 30, 17, 52, tzinfo=UTC)
    assert cases[0].location == "Bethany, OK"
    assert cases[0].aircraft.make == "BELL"
    assert cases[0].has_final_report is True

    payload = json.loads(route.calls[0].request.content)
    rules = payload["QueryGroups"][0]["QueryRules"]
    assert [(rule["Columns"][0], rule["Operator"], rule["Values"][0]) for rule in rules] == [
        ("Event.EventDate", "is on or after", "2025-04-01"),
        ("Event.EventDate", "is on or before", "2025-04-30"),
        ("Event.Mode", "is", "Aviation"),
    ]
    assert payload["ExportFormat"] == "data"


@respx.mock
@pytest.mark.asyncio
async def test_get_case_queries_by_exact_ntsb_number() -> None:
    route = respx.post(FILE_EXPORT_URL).mock(
        return_value=httpx.Response(
            200,
            content=carol_export(
                [
                    {
                        "cm_ntsbNum": "CEN25LA167",
                        "cm_eventDate": "2025-04-30T17:52:00Z",
                        "cm_city": "Bethany",
                        "cm_state": "OK",
                        "cm_completionStatus": "Completed",
                        "cm_vehicles": [],
                    }
                ]
            ),
        )
    )
    async with httpx.AsyncClient() as http_client:
        case = await CarolClient(http_client=http_client).get_case("CEN25LA167")

    payload = json.loads(route.calls[0].request.content)
    rule = payload["QueryGroups"][0]["QueryRules"][0]
    assert (rule["Columns"], rule["Operator"], rule["Values"]) == (
        ["Event.NTSBNumber"],
        "is",
        ["CEN25LA167"],
    )
    assert case.ntsb_number == "CEN25LA167"


@respx.mock
@pytest.mark.asyncio
async def test_get_case_raises_not_found_for_empty_export() -> None:
    respx.post(FILE_EXPORT_URL).mock(
        return_value=httpx.Response(200, content=carol_export([]))
    )
    async with httpx.AsyncClient() as http_client:
        with pytest.raises(CaseNotFound, match="MISSING"):
            await CarolClient(http_client=http_client).get_case("MISSING")


@respx.mock
@pytest.mark.asyncio
async def test_search_cases_waits_five_seconds_between_requests() -> None:
    respx.post(FILE_EXPORT_URL).mock(
        return_value=httpx.Response(200, content=carol_export([]))
    )
    now = 100.0
    sleeps: list[float] = []

    def clock() -> float:
        return now

    async def sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    async with httpx.AsyncClient() as http_client:
        client = CarolClient(http_client=http_client, clock=clock, sleep=sleep)
        await client.search_cases(date_from=date(2025, 4, 1), date_to=date(2025, 4, 1))
        await client.search_cases(date_from=date(2025, 4, 2), date_to=date(2025, 4, 2))

    assert sleeps == [5.0]


@respx.mock
@pytest.mark.asyncio
async def test_search_cases_retries_server_errors_with_backoff() -> None:
    route = respx.post(FILE_EXPORT_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, content=carol_export([])),
        ]
    )
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async with httpx.AsyncClient() as http_client:
        client = CarolClient(http_client=http_client, sleep=sleep, minimum_interval=0)
        assert await client.search_cases(
            date_from=date(2025, 4, 1), date_to=date(2025, 4, 1)
        ) == []

    assert route.call_count == 2
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_search_cases_rejects_reversed_date_range() -> None:
    async with httpx.AsyncClient() as http_client:
        client = CarolClient(http_client=http_client)
        with pytest.raises(ValueError, match="date_from must not be after date_to"):
            await client.search_cases(date_from=date(2025, 5, 1), date_to=date(2025, 4, 1))
