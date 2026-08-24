from datetime import UTC, datetime

import httpx
import pytest
import respx
from casezero_ntsb.developer_api import NtsbApiClient, NtsbApiConfigurationError
from casezero_ntsb.models import CaseNotFound

ENDPOINT_TEMPLATE = "https://api.example.test/GetCase?identifier={identifier}"


def api_case() -> dict[str, object]:
    return {
        "cm_mkey": 200106,
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


def test_client_requires_subscription_key() -> None:
    with pytest.raises(NtsbApiConfigurationError, match="subscription key"):
        NtsbApiClient(
            subscription_key="",
            endpoint_template=ENDPOINT_TEMPLATE,
            http_client=httpx.AsyncClient(),
        )


def test_client_requires_portal_endpoint_template() -> None:
    with pytest.raises(NtsbApiConfigurationError, match="endpoint template"):
        NtsbApiClient(
            subscription_key="secret",
            endpoint_template="",
            http_client=httpx.AsyncClient(),
        )


@respx.mock
@pytest.mark.asyncio
async def test_get_case_sends_subscription_key_and_parses_case() -> None:
    route = respx.get("https://api.example.test/GetCase?identifier=CEN25LA167").mock(
        return_value=httpx.Response(200, json=api_case())
    )
    async with httpx.AsyncClient() as http_client:
        client = NtsbApiClient(
            subscription_key="secret",
            endpoint_template=ENDPOINT_TEMPLATE,
            http_client=http_client,
            minimum_interval=0,
        )
        case = await client.get_case("CEN25LA167")

    assert route.calls[0].request.headers["Ocp-Apim-Subscription-Key"] == "secret"
    assert case.ntsb_number == "CEN25LA167"
    assert case.event_date == datetime(2025, 4, 30, 17, 52, tzinfo=UTC)
    assert case.mkey == 200106


@respx.mock
@pytest.mark.asyncio
async def test_get_case_raises_typed_not_found() -> None:
    respx.get("https://api.example.test/GetCase?identifier=MISSING").mock(
        return_value=httpx.Response(404)
    )
    async with httpx.AsyncClient() as http_client:
        client = NtsbApiClient(
            subscription_key="secret",
            endpoint_template=ENDPOINT_TEMPLATE,
            http_client=http_client,
            minimum_interval=0,
        )
        with pytest.raises(CaseNotFound, match="MISSING"):
            await client.get_case("MISSING")
