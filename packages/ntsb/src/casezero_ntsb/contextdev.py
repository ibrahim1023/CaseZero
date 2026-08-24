import json

import httpx
from pydantic import ValidationError

from casezero_ntsb.manifest import DocketManifest

CONTEXT_EXTRACT_URL = "https://api.context.dev/v1/web/extract"


class ContextDevConfigurationError(ValueError):
    pass


class ContextDevPayloadError(ValueError):
    pass


class ContextDevClient:
    def __init__(self, *, api_key: str, http_client: httpx.AsyncClient) -> None:
        if not api_key:
            raise ContextDevConfigurationError("Context.dev API key is required")
        self._api_key = api_key
        self._http_client = http_client

    async def extract_manifest(self, docket_url: str) -> DocketManifest:
        response = await self._http_client.post(
            CONTEXT_EXTRACT_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "url": docket_url,
                "schema": DocketManifest.model_json_schema(by_alias=True),
                "instructions": (
                    "Extract only documents explicitly listed in this NTSB docket. "
                    "Use direct public artifact URLs; do not infer missing documents or URLs."
                ),
                "factCheck": True,
                "maxPages": 10,
                "maxDepth": 1,
                "timeoutMS": 120000,
            },
            timeout=130,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        try:
            return DocketManifest.model_validate_json(json.dumps(data))
        except (TypeError, ValidationError) as error:
            raise ContextDevPayloadError("Context.dev returned an invalid docket manifest") from error
