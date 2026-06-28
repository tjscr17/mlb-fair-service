"""Kalshi listing layer: parse raw events into models + the source Protocol.

Both the live poller and the mock parse the *same* raw Kalshi JSON shape into
`KalshiEvent` / `KalshiMarket`, so swapping live<->mock never touches the mapper.

Schema verified against the live `KXMLBGAME` series (2026): one event per game,
two per-team markets, no `strike_date`; the game start is `occurrence_datetime`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Protocol

from ..models import KalshiEvent, KalshiMarket


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _money(value) -> Optional[float]:
    """Kalshi dollar price string ("0.4800") -> float prob; None/blank/0.0 -> None."""
    if value in (None, ""):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _parse_market(m: dict) -> KalshiMarket:
    return KalshiMarket(
        ticker=m["ticker"],
        event_ticker=m["event_ticker"],
        status=m.get("status", "initialized"),
        open_time=_parse_dt(m.get("open_time")),
        close_time=_parse_dt(m.get("close_time")),
        created_time=_parse_dt(m.get("created_time")),
        updated_time=_parse_dt(m.get("updated_time")),
        yes_sub_title=m.get("yes_sub_title"),
        no_sub_title=m.get("no_sub_title"),
        rules_primary=m.get("rules_primary"),
        occurrence_datetime=_parse_dt(m.get("occurrence_datetime")),
        custom_strike=m.get("custom_strike") or {},
        product_metadata=m.get("product_metadata") or {},
        yes_bid=_money(m.get("yes_bid_dollars")),
        yes_ask=_money(m.get("yes_ask_dollars")),
        last_price=_money(m.get("last_price_dollars")),
    )


def parse_events(payload: dict) -> list[KalshiEvent]:
    """Parse a Kalshi /events?with_nested_markets response into KalshiEvent objects."""
    events: list[KalshiEvent] = []
    for e in payload.get("events", []):
        events.append(
            KalshiEvent(
                event_ticker=e["event_ticker"],
                series_ticker=e.get("series_ticker", ""),
                title=e.get("title"),
                sub_title=e.get("sub_title"),
                strike_date=_parse_dt(e.get("strike_date")),
                product_metadata=e.get("product_metadata") or {},
                markets=[_parse_market(m) for m in e.get("markets", [])],
            )
        )
    return events


class KalshiSource(Protocol):
    async def fetch(self, status: Optional[str] = None) -> list[KalshiEvent]:
        """Return MLB events (optionally filtered to a single market `status`)."""
        ...

    async def aclose(self) -> None:
        ...
