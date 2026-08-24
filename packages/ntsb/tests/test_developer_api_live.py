import os

import httpx
import pytest
from casezero_ntsb.developer_api import NtsbApiClient


@pytest.mark.live
@pytest.mark.skipif(
    not os.getenv("NTSB_API_SUBSCRIPTION_KEY") or not os.getenv("NTSB_GET_CASE_URL_TEMPLATE"),
    reason="requires NTSB developer API configuration",
)
@pytest.mark.asyncio
async def test_get_case_returns_real_case() -> None:
    async with httpx.AsyncClient() as http_client:
        case = await NtsbApiClient(
            subscription_key=os.environ["NTSB_API_SUBSCRIPTION_KEY"],
            endpoint_template=os.environ["NTSB_GET_CASE_URL_TEMPLATE"],
            http_client=http_client,
        ).get_case("CEN25LA167")

    assert case.ntsb_number == "CEN25LA167"
