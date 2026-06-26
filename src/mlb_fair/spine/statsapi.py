"""Live MLB StatsAPI schedule client.

GET https://statsapi.mlb.com/api/v1/schedule
    ?sportId=1&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD
    &hydrate=team,linescore
"""

from __future__ import annotations

import httpx

from ..models import SpineGame
from .base import parse_schedule

STATSAPI_BASE = "https://statsapi.mlb.com/api/v1"


class StatsApiSchedule:
    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 8.0):
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def fetch(self, start_date: str, end_date: str) -> list[SpineGame]:
        client = await self._get_client()
        resp = await client.get(
            f"{STATSAPI_BASE}/schedule",
            params={
                "sportId": 1,
                "startDate": start_date,
                "endDate": end_date,
                "hydrate": "team,linescore",
            },
        )
        resp.raise_for_status()
        return parse_schedule(resp.json())

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
