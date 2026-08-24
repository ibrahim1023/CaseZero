import asyncio
import time
from collections.abc import Awaitable, Callable
from urllib.parse import quote

import httpx

from casezero_ntsb.models import CaseMetadata, parse_case_metadata


class NtsbApiConfigurationError(ValueError):
    pass


class CaseNotFound(LookupError):
    pass


class NtsbApiPayloadError(ValueError):
    pass


class NtsbApiClient:
    def __init__(
        self,
        *,
        subscription_key: str,
        endpoint_template: str,
        http_client: httpx.AsyncClient,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        minimum_interval: float = 5.0,
        max_attempts: int = 3,
    ) -> None:
        if not subscription_key:
            raise NtsbApiConfigurationError("NTSB API subscription key is required")
        if not endpoint_template or "{identifier}" not in endpoint_template:
            raise NtsbApiConfigurationError(
                "NTSB GetCase endpoint template containing {identifier} is required"
            )

        self._subscription_key = subscription_key
        self._endpoint_template = endpoint_template
        self._http_client = http_client
        self._clock = clock
        self._sleep = sleep
        self._minimum_interval = minimum_interval
        self._max_attempts = max_attempts
        self._last_request_at: float | None = None
        self._request_lock = asyncio.Lock()

    async def get_case(self, identifier: str) -> CaseMetadata:
        url = self._endpoint_template.format(identifier=quote(identifier, safe=""))
        response = await self._get_with_retries(url)
        if response.status_code == 404:
            raise CaseNotFound(f"NTSB case {identifier} was not found")
        response.raise_for_status()

        payload = response.json()
        if isinstance(payload, list):
            payload = payload[0] if payload else None
        case = parse_case_metadata(payload)
        if case is None:
            raise NtsbApiPayloadError("GetCase returned an invalid case payload")
        return case

    async def _get_with_retries(self, url: str) -> httpx.Response:
        for attempt in range(self._max_attempts):
            await self._wait_for_request_slot()
            response = await self._http_client.get(
                url,
                headers={"Ocp-Apim-Subscription-Key": self._subscription_key},
                timeout=60,
            )
            if response.status_code == 404:
                return response
            if response.status_code != 429 and response.status_code < 500:
                return response
            if attempt + 1 < self._max_attempts:
                await self._sleep(float(2**attempt))
        return response

    async def _wait_for_request_slot(self) -> None:
        async with self._request_lock:
            if self._last_request_at is not None:
                elapsed = self._clock() - self._last_request_at
                if elapsed < self._minimum_interval:
                    await self._sleep(self._minimum_interval - elapsed)
            self._last_request_at = self._clock()
