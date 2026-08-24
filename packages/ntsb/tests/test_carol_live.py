import os
from datetime import date

import httpx
import pytest
from casezero_ntsb.carol import CarolClient


@pytest.mark.live
@pytest.mark.skipif(os.getenv("CASEZERO_LIVE") != "1", reason="requires CASEZERO_LIVE=1")
@pytest.mark.asyncio
async def test_carol_returns_real_aviation_case() -> None:
    async with httpx.AsyncClient() as http_client:
        cases = await CarolClient(http_client=http_client).search_cases(
            date_from=date(2025, 4, 30),
            date_to=date(2025, 4, 30),
        )

    assert cases
    assert all(case.ntsb_number for case in cases)
