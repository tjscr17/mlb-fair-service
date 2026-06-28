"""Live OpticOdds source adapter.

OpticOdds is a different API than The Odds API, so this adapter maps its responses
into the shared `OddsEvent`/`OddsBook` model — after which `map_odds` and everything
downstream is identical.

Flow (v3):
  GET /fixtures/active?sport=baseball&league=mlb   -> fixtures (id, home/away, start)
  GET /fixtures/odds?fixture_id=..&sportsbook=..&market=Moneyline&odds_format=AMERICAN

Auth is the `X-Api-Key` header. The key is read from the environment and is never
logged. Callers should cache results (the webapp does) — OpticOdds allows 2,500
req/15s but the key is shared, so we keep request volume minimal: batch fixtures,
cap at 5 sportsbooks/request, and only request odds for fixtures we actually have.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import httpx

from .base import OddsBook, OddsEvent, _parse_dt

OPTIC_BASE = "https://api.opticodds.com/api/v3"
DEFAULT_SPORTSBOOKS = ["Pinnacle", "DraftKings", "FanDuel", "BetMGM", "Caesars"]  # max 5/request


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _ts(value) -> Optional[datetime]:
    """OpticOdds timestamps may be ISO strings or unix epoch seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    return _parse_dt(str(value))


def _book_key(name: str) -> str:
    return name.strip().lower().replace(" ", "")


def _team_name(fx: dict, side: str) -> Optional[str]:
    """OpticOdds team name: `<side>_team_display`, falling back to the competitor name."""
    v = fx.get(f"{side}_team_display")
    if v:
        return v
    comp = fx.get(f"{side}_competitors")
    if isinstance(comp, list) and comp and isinstance(comp[0], dict):
        return comp[0].get("name") or comp[0].get("abbreviation")
    return None


class LiveOpticOdds:
    def __init__(
        self,
        api_key: Optional[str] = None,
        sportsbooks: Optional[list[str]] = None,
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = 10.0,
    ):
        self._key = api_key or os.environ.get("OPTIC_ODDS_API_KEY", "")
        env_books = os.environ.get("OPTIC_SPORTSBOOKS", "")
        self._books = sportsbooks or (
            [b.strip() for b in env_books.split(",") if b.strip()] or DEFAULT_SPORTSBOOKS
        )[:5]
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    @property
    def _headers(self) -> dict:
        return {"X-Api-Key": self._key}

    async def fetch(self) -> list[OddsEvent]:
        if not self._key:
            return []  # no key -> behave like "no odds" rather than erroring
        client = await self._get_client()

        # 1) Active MLB fixtures (one request).
        resp = await client.get(
            f"{OPTIC_BASE}/fixtures/active",
            params={"sport": "baseball", "league": "mlb"},
            headers=self._headers,
        )
        resp.raise_for_status()
        fixtures = resp.json().get("data", []) or []
        fx_by_id = {f["id"]: f for f in fixtures if f.get("id")}
        if not fx_by_id:
            return []

        # 2) Moneyline odds, batched (few requests; all 5 books in one shot).
        odds_by_fx: dict[str, list[dict]] = defaultdict(list)
        for chunk in _chunks(list(fx_by_id), 5):
            params: list[tuple[str, str]] = [
                ("sport", "baseball"),
                ("league", "mlb"),
                ("market", "Moneyline"),
                ("odds_format", "AMERICAN"),
            ]
            params += [("fixture_id", fid) for fid in chunk]
            params += [("sportsbook", b) for b in self._books]
            r = await client.get(f"{OPTIC_BASE}/fixtures/odds", params=params, headers=self._headers)
            r.raise_for_status()
            for item in r.json().get("data", []) or []:
                # Response may nest odds under a fixture, or be flat odds rows.
                if isinstance(item.get("odds"), list):
                    fid = item.get("id") or item.get("fixture_id")
                    for row in item["odds"]:
                        odds_by_fx[fid].append(row)
                else:
                    fid = item.get("fixture_id") or item.get("id")
                    odds_by_fx[fid].append(item)

        # 3) Shape into OddsEvent, matching each selection to the fixture's home/away.
        events: list[OddsEvent] = []
        for fid, fx in fx_by_id.items():
            home, away = _team_name(fx, "home"), _team_name(fx, "away")
            events.append(
                OddsEvent(
                    id=str(fid),
                    home_team=home,
                    away_team=away,
                    commence_time=_parse_dt(fx.get("start_date")),
                    books=_group_books(odds_by_fx.get(fid, []), home, away),
                )
            )
        return events

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()


def _group_books(rows: list[dict], home: Optional[str], away: Optional[str]) -> list[OddsBook]:
    """Collapse moneyline rows (one per selection) into per-book home/away prices."""
    h, a = _norm(home), _norm(away)
    agg: dict[str, dict] = defaultdict(dict)
    for o in rows:
        if o.get("is_main") is False:  # skip alternate lines; keep the main moneyline
            continue
        book = o.get("sportsbook")
        sel = o.get("selection") or o.get("name") or o.get("normalized_selection")
        price = o.get("price")
        if not book or sel is None or price is None:
            continue
        side = None
        n = _norm(sel)
        if n == h or (h and h in n) or (n and n in h):
            side = "home"
        elif n == a or (a and a in n) or (n and n in a):
            side = "away"
        if side is None:
            continue
        agg[book][side] = price
        agg[book].setdefault("last", o.get("timestamp") or o.get("last_update") or o.get("updated_at"))

    books: list[OddsBook] = []
    for book, d in agg.items():
        if "home" in d and "away" in d:
            books.append(
                OddsBook(
                    book=_book_key(book),
                    title=book,
                    home_price=float(d["home"]),
                    away_price=float(d["away"]),
                    last_update=_ts(d.get("last")),
                )
            )
    return books
