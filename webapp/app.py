"""Demo UI backend.

A thin FastAPI layer over the real service code (spine + Kalshi mapping + de-vig).
It runs the mock slate through `map_events`, then serves the result as JSON plus a
single static page that visualizes the gamePk join and recomputes fairs live.

Run locally:   python -m uvicorn webapp:app --reload   ->  http://localhost:8000
Deploy:        see api/index.py + vercel.json
"""

from __future__ import annotations

import asyncio
import statistics
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from mlb_fair.engine.registry import FixtureRegistry
from mlb_fair.kalshi.mapping import apply, map_events
from mlb_fair.kalshi.mock import MockKalshiEvents
from mlb_fair.odds.devig import devig
from mlb_fair.odds.mapping import map_odds
from mlb_fair.odds.mock import MockOdds
from mlb_fair.spine.mock import MockSchedule

STATIC = Path(__file__).resolve().parent / "static"
SLATE_DATE = "2026-06-25"

DH_LABEL = {"N": "single", "S": "split-DH", "Y": "straight-DH"}

# Display metadata + best-effort posting links for each bookmaker (The Odds API
# h2h doesn't ship deep links, so we link the book's site). region: us | eu.
BOOK_META: dict[str, dict] = {
    "pinnacle": {"title": "Pinnacle", "region": "eu", "url": "https://www.pinnacle.com"},
    "circa": {"title": "Circa", "region": "us", "url": "https://www.circasports.com"},
    "draftkings": {"title": "DraftKings", "region": "us", "url": "https://sportsbook.draftkings.com"},
    "fanduel": {"title": "FanDuel", "region": "us", "url": "https://sportsbook.fanduel.com"},
    "betmgm": {"title": "BetMGM", "region": "us", "url": "https://sports.betmgm.com"},
}
# Sharpness-ordered reference preference (full band-staleness selection is P3b).
FAILOVER_PRIORITY = ["pinnacle", "circa", "draftkings", "fanduel", "betmgm"]

# Plausible American moneylines per gamePk — just seed values; the UI lets you edit them.
DEFAULT_ODDS: dict[int, tuple[int, int]] = {
    778001: (-150, +130),  # NYY @ BOS
    778010: (-120, +100),  # Cubs @ Brewers G1
    778011: (-110, -110),  # Cubs @ Brewers G2 (pick'em)
    778020: (-200, +170),  # Astros @ Athletics G1
    778021: (-185, +160),  # Astros @ Athletics G2
    778030: (-135, +115),  # Dodgers @ Giants (suspended)
}

_STATUS_ORDER = {"bound": 0, "pending": 1, "unmatched": 2}


def _pipeline():
    """Run the whole mock pipeline once: spine -> Kalshi join -> odds join.

    Returns (registry, kalshi_results, events_by_ticker, odds_by_gamepk).
    """
    games = asyncio.run(MockSchedule().fetch(SLATE_DATE, SLATE_DATE))
    reg = FixtureRegistry()
    reg.upsert_spine(games)

    events = asyncio.run(MockKalshiEvents().fetch())
    results = map_events(events, reg)
    apply(results, reg)

    odds_by_pk = map_odds(asyncio.run(MockOdds().fetch()), reg)
    return reg, results, {e.event_ticker: e for e in events}, odds_by_pk


def build_slate() -> dict:
    """Run the full mock pipeline and shape it for the UI."""
    reg, results, ev_by_ticker, odds_by_pk = _pipeline()
    rows: list[dict] = []
    for r in results:
        ev = ev_by_ticker.get(r.event_ticker)
        row: dict = {
            "event_ticker": r.event_ticker,
            "status": r.status,
            "reason": r.reason,
            "title": ev.title if ev else None,
        }
        if r.status == "bound" and r.binding is not None:
            fx = reg.get(r.game_pk)
            s = fx.spine
            home_odds, away_odds = DEFAULT_ODDS.get(s.game_pk, (-110, -110))
            row.update(
                {
                    "game_pk": s.game_pk,
                    "game_number": s.game_number,
                    "home_team": s.home.name,
                    "home_id": s.home.id,
                    "away_team": s.away.name,
                    "away_id": s.away.id,
                    "double_header": DH_LABEL[s.double_header],
                    "spine_status": s.status,
                    "quotable": fx.should_quote,
                    "confidence": r.binding.confidence,
                    "yes_team_id": r.binding.yes_team_id,  # representative (home) YES side
                    "default_home_price": home_odds,
                    "default_away_price": away_odds,
                    "book_count": len(odds_by_pk[s.game_pk].books) if s.game_pk in odds_by_pk else 0,
                }
            )
        rows.append(row)

    rows.sort(key=lambda x: (_STATUS_ORDER.get(x["status"], 9), x.get("game_pk", 0), x["event_ticker"]))

    spine = [
        {
            "game_pk": f.spine.game_pk,
            "away": f.spine.away.name,
            "home": f.spine.home.name,
            "game_number": f.spine.game_number,
            "double_header": DH_LABEL[f.spine.double_header],
            "status": f.spine.status,
        }
        for f in sorted(reg.all(), key=lambda x: (x.spine.game_date, x.spine.game_number))
    ]

    return {
        "slate_date": SLATE_DATE,
        "spine_count": len(spine),
        "events_count": len(events),
        "bound_count": sum(1 for r in rows if r["status"] == "bound"),
        "quotable_count": len(reg.quotable()),
        "spine": spine,
        "results": rows,
    }


app = FastAPI(title="MLB Fair-Value Demo")


@app.get("/api/slate")
def slate() -> JSONResponse:
    return JSONResponse(build_slate())


@app.get("/api/devig")
def api_devig(home: float, away: float, method: str = "multiplicative") -> JSONResponse:
    """De-vig a two-way American moneyline into fair win probabilities."""
    try:
        fr = devig(home, away, method)
    except Exception as exc:  # invalid odds/method -> 400, surfaced in the UI
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(
        {"home": fr.home, "away": fr.away, "overround": fr.overround, "method": fr.method}
    )


@app.get("/api/game/{game_pk}")
def game_detail(game_pk: int, method: str = "multiplicative") -> JSONResponse:
    """Full detail for one mapped game: source links, Kalshi contracts, per-book odds + fairs."""
    reg, _results, _ev, odds_by_pk = _pipeline()
    fx = reg.get(game_pk)
    if fx is None or not fx.is_bound or fx.binding is None:
        return JSONResponse({"error": f"no bound fixture for gamePk {game_pk}"}, status_code=404)

    s = fx.spine
    b = fx.binding
    name_by_id = {s.home.id: s.home.name, s.away.id: s.away.name}

    kalshi_markets = [
        {
            "ticker": ticker,
            "team_id": tid,
            "team": name_by_id.get(tid, str(tid)),
            "side": "home" if tid == s.home.id else "away",
            "url": f"https://kalshi.com/markets/{ticker}",
        }
        for ticker, tid in b.market_yes.items()
    ]

    books: list[dict] = []
    oe = odds_by_pk.get(game_pk)
    if oe is not None:
        for bk in oe.books:
            try:
                fr = devig(bk.home_price, bk.away_price, method)
            except Exception:
                continue
            meta = BOOK_META.get(bk.book, {"title": bk.title or bk.book, "region": "us", "url": None})
            books.append(
                {
                    "book": bk.book,
                    "title": meta["title"],
                    "region": meta["region"],
                    "url": meta["url"],
                    "home_price": bk.home_price,
                    "away_price": bk.away_price,
                    "last_update": bk.last_update.isoformat() if bk.last_update else None,
                    "fair_home": fr.home,
                    "fair_away": fr.away,
                    "hold": fr.overround,
                }
            )

    consensus = None
    if books:
        consensus = {
            "fair_home": statistics.median([x["fair_home"] for x in books]),
            "fair_away": statistics.median([x["fair_away"] for x in books]),
        }
    present = {x["book"] for x in books}
    source_book = next((p for p in FAILOVER_PRIORITY if p in present), books[0]["book"] if books else None)

    return JSONResponse(
        {
            "game_pk": s.game_pk,
            "game_number": s.game_number,
            "home_team": s.home.name,
            "home_id": s.home.id,
            "away_team": s.away.name,
            "away_id": s.away.id,
            "double_header": DH_LABEL[s.double_header],
            "spine_status": s.status,
            "quotable": fx.should_quote,
            "confidence": b.confidence,
            "yes_team_id": b.yes_team_id,
            "method": method,
            "event_ticker": b.event_ticker,
            "links": {
                "kalshi": f"https://kalshi.com/markets/{b.event_ticker}",
                "mlb_gameday": f"https://www.mlb.com/gameday/{s.game_pk}",
                "statsapi": f"https://statsapi.mlb.com/api/v1.1/game/{s.game_pk}/feed/live",
            },
            "kalshi_markets": kalshi_markets,
            "books": books,
            "consensus": consensus,
            "source_book": source_book,
        }
    )


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))
