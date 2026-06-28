"""Live Kalshi listing client.

Public read API (no auth). Base: https://api.elections.kalshi.com/trade-api/v2
Hierarchy is Series -> Event -> Market. We resolve the MLB game-winner series
(verified live: `KXMLBGAME`) and poll its events with nested markets, then hand
the raw JSON to the shared `parse_events` — identical schema to the mock.

Identity is derived from fields downstream (never the ticker); this client only
fetches. It returns the same `list[KalshiEvent]` as MockKalshiEvents, so the
mapper and webapp don't care which one they're given.
"""

from __future__ import annotations

from typing import Optional

import httpx

from ..models import KalshiEvent
from .base import parse_events

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
# The per-game moneyline series (verified live). Discovery augments this; we keep
# it as a safe default because category listings don't always surface it.
DEFAULT_SERIES = ["KXMLBGAME"]


class LiveKalshiEvents:
    def __init__(
        self,
        series_tickers: Optional[list[str]] = None,
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = 8.0,
    ):
        self._series = series_tickers or list(DEFAULT_SERIES)
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def discover_series(self) -> list[str]:
        """Best-effort: find MLB game series under Sports (don't parse tickers as a contract)."""
        client = await self._get_client()
        try:
            resp = await client.get(f"{KALSHI_BASE}/series", params={"category": "Sports"})
            resp.raise_for_status()
            series = resp.json().get("series", [])
            return [s["ticker"] for s in series if "MLBGAME" in (s.get("ticker", "").upper())]
        except Exception:
            return []

    async def fetch(self, status: Optional[str] = None) -> list[KalshiEvent]:
        """Return MLB events with nested markets. `status` filters server-side; default open+unopened."""
        client = await self._get_client()
        series = list(dict.fromkeys(self._series + await self.discover_series()))
        statuses = [status] if status else ["open", "unopened"]

        by_ticker: dict[str, KalshiEvent] = {}
        for series_ticker in series:
            for st in statuses:
                try:
                    resp = await client.get(
                        f"{KALSHI_BASE}/events",
                        params={
                            "series_ticker": series_ticker,
                            "with_nested_markets": "true",
                            "status": st,
                            "limit": 200,
                        },
                    )
                    resp.raise_for_status()
                    for ev in parse_events(resp.json()):
                        by_ticker.setdefault(ev.event_ticker, ev)  # dedupe across statuses
                except Exception:
                    continue  # a source dropping must never crash the service
        return list(by_ticker.values())

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
