"""Odds source layer: parse The Odds API v4 shape into models + the source Protocol.

The live client and the mock parse the *same* raw shape, so swapping is one line.
The Odds API does not expose a game number, so doubleheader disambiguation rides
on the spine (ordinal commence_time), exactly like the Kalshi side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


@dataclass
class OddsBook:
    """One bookmaker's two-way h2h line for a game (American odds)."""

    book: str  # bookmaker key, e.g. "pinnacle"
    title: str
    home_price: float
    away_price: float
    last_update: Optional[datetime] = None  # per-book staleness clock (the band uses this in P3b)
    deep_link: Optional[str] = None  # direct link to the book's posting (OpticOdds provides this)


@dataclass
class OddsEvent:
    """One game's odds across books, pre-join (team names, no gamePk yet)."""

    id: str
    home_team: str
    away_team: str
    commence_time: Optional[datetime]
    books: list[OddsBook] = field(default_factory=list)


def parse_odds(payload) -> list[OddsEvent]:
    """Parse a The Odds API v4 /odds response (top-level array) into OddsEvent objects."""
    raw = payload if isinstance(payload, list) else payload.get("data") or payload.get("events") or []
    events: list[OddsEvent] = []
    for e in raw:
        home, away = e.get("home_team"), e.get("away_team")
        books: list[OddsBook] = []
        for bm in e.get("bookmakers", []):
            h2h = next((m for m in bm.get("markets", []) if m.get("key") == "h2h"), None)
            if not h2h:
                continue
            prices = {o.get("name"): o.get("price") for o in h2h.get("outcomes", [])}
            if home not in prices or away not in prices:
                continue
            books.append(
                OddsBook(
                    book=bm["key"],
                    title=bm.get("title", bm["key"]),
                    home_price=prices[home],
                    away_price=prices[away],
                    last_update=_parse_dt(bm.get("last_update") or h2h.get("last_update")),
                )
            )
        events.append(
            OddsEvent(
                id=e.get("id", ""),
                home_team=home,
                away_team=away,
                commence_time=_parse_dt(e.get("commence_time")),
                books=books,
            )
        )
    return events


class OddsSource(Protocol):
    async def fetch(self) -> list[OddsEvent]:
        ...

    async def aclose(self) -> None:
        ...
