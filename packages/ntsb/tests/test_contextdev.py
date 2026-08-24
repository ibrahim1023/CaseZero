import json

import httpx
import pytest
import respx
from casezero_ntsb.contextdev import (
    CONTEXT_EXTRACT_URL,
    ContextDevClient,
    ContextDevConfigurationError,
    ContextDevPayloadError,
)


@pytest.mark.asyncio
async def test_context_client_requires_api_key() -> None:
    async with httpx.AsyncClient() as http_client:
        with pytest.raises(ContextDevConfigurationError, match="API key"):
            ContextDevClient(api_key="", http_client=http_client)


@respx.mock
@pytest.mark.asyncio
async def test_extract_manifest_sends_schema_and_validates_response() -> None:
    route = respx.post(CONTEXT_EXTRACT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "completed",
                "url": "https://data.ntsb.gov/Docket/?NTSBNumber=CEN25LA167",
                "urls_analyzed": ["https://data.ntsb.gov/Docket/?NTSBNumber=CEN25LA167"],
                "data": {
                    "caseId": "CEN25LA167",
                    "documents": [
                        {
                            "title": "Aircraft Accident Final Report",
                            "documentType": "FINAL_REPORT",
                            "sourceUrl": "https://data.ntsb.gov/final.pdf",
                            "fileType": "pdf",
                            "publishedAt": "2025-06-13T04:00:00Z",
                        }
                    ],
                },
                "metadata": {},
            },
        )
    )
    async with httpx.AsyncClient() as http_client:
        manifest = await ContextDevClient(
            api_key="ctxt_secret_test",
            http_client=http_client,
        ).extract_manifest("https://data.ntsb.gov/Docket/?NTSBNumber=CEN25LA167")

    assert manifest.case_id == "CEN25LA167"
    assert manifest.documents[0].title == "Aircraft Accident Final Report"
    assert route.calls[0].request.headers["Authorization"] == "Bearer ctxt_secret_test"

    payload = json.loads(route.calls[0].request.content)
    assert payload["factCheck"] is True
    assert payload["schema"]["additionalProperties"] is False
    assert set(payload["schema"]["required"]) == {"caseId", "documents"}
    document_schema = payload["schema"]["$defs"]["DocketDocument"]
    assert "sourceUrl" in document_schema["required"]


@respx.mock
@pytest.mark.asyncio
async def test_extract_manifest_rejects_invalid_data() -> None:
    respx.post(CONTEXT_EXTRACT_URL).mock(
        return_value=httpx.Response(200, json={"data": {"caseId": "CEN25LA167", "documents": []}})
    )
    async with httpx.AsyncClient() as http_client:
        client = ContextDevClient(api_key="ctxt_secret_test", http_client=http_client)
        with pytest.raises(ContextDevPayloadError, match="invalid docket manifest"):
            await client.extract_manifest("https://data.ntsb.gov/Docket/?NTSBNumber=CEN25LA167")
