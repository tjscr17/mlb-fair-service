"""Dispersion-band staleness: trend-laggard ejection, cold-start, all-stale -> no fair."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mlb_fair.config import Config
from mlb_fair.models import BookQuote
from mlb_fair.odds.selection import BandSelector

UTC = timezone.utc


def _q(book, h, a, when, pk=1):
    return BookQuote(book=book, game_pk=pk, home_price=h, away_price=a, last_update=when)


def test_trend_laggard_ejected_on_trailing_side():
    """When the market moves but one book stays put, the laggard falls out the
    (tightened) trailing side of the band — the frozen-book case, for free."""
    sel = BandSelector(Config())
    t0 = datetime.now(UTC)

    # baseline: everyone near a pick'em
    base = [_q("pinnacle", -105, -105, t0), _q("draftkings", -106, -104, t0),
            _q("fanduel", -104, -106, t0), _q("betmgm", -105, -105, t0)]
    sel.select(1, base, now=t0)

    # 30s later the market moves hard to the home side — but betmgm is frozen
    t1 = t0 + timedelta(seconds=30)
    moved = [_q("pinnacle", -260, 220, t1), _q("draftkings", -260, 220, t1),
             _q("fanduel", -260, 220, t1), _q("betmgm", -105, -105, t1)]
    r = sel.select(1, moved, now=t1)

    assert "betmgm" not in r.live_books          # the laggard is flagged stale
    assert "pinnacle" in r.live_books            # the movers stay live
    assert r.live_book_count >= 3
    assert r.source_book == "pinnacle"


def test_cold_start_one_and_two_books():
    sel = BandSelector(Config())
    now = datetime.now(UTC)
    # 1 book -> it IS the fair
    r1 = sel.select(2, [_q("pinnacle", -150, 130, now, pk=2)], now=now)
    assert r1.source_book == "pinnacle" and r1.live_book_count == 1
    # 2 books -> best-available (Pinnacle priority), band not yet engaged
    r2 = sel.select(3, [_q("pinnacle", -150, 130, now, pk=3),
                        _q("draftkings", -148, 128, now, pk=3)], now=now)
    assert r2.source_book == "pinnacle" and r2.live_book_count == 2


def test_all_books_stale_emits_no_fair():
    sel = BandSelector(Config())
    now = datetime.now(UTC)
    old = now - timedelta(seconds=10_000)  # past the cold-start age backstop
    r = sel.select(4, [_q("pinnacle", -150, 130, old, pk=4),
                       _q("draftkings", -148, 128, old, pk=4)], now=now)
    assert r.fair_home is None
    assert r.reason == "all_books_stale"
