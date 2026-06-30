"""Failover waterfall: Pinnacle -> consensus -> back to Pinnacle."""

from __future__ import annotations

from datetime import datetime, timezone

from mlb_fair.config import Config
from mlb_fair.models import BookQuote
from mlb_fair.odds.selection import BandSelector

UTC = timezone.utc


def _q(book, h, a, now):
    return BookQuote(book=book, game_pk=1, home_price=h, away_price=a, last_update=now)


def _full(now):
    # 4 live books clustered around a modest home favorite
    return [
        _q("pinnacle", -150, 130, now),
        _q("draftkings", -148, 128, now),
        _q("fanduel", -152, 132, now),
        _q("betmgm", -149, 129, now),
    ]


def test_pinnacle_is_reference_when_live():
    sel = BandSelector(Config())
    now = datetime.now(UTC)
    r = sel.select(1, _full(now), now=now)
    assert r.source_book == "pinnacle"
    assert r.fair_home is not None and 0 < r.fair_home < 1


def test_failover_to_consensus_then_back():
    sel = BandSelector(Config())
    now = datetime.now(UTC)
    sel.select(1, _full(now), now=now)  # pinnacle live

    # Pinnacle drops -> 3 live soft books -> consensus tier
    soft = [q for q in _full(now) if q.book != "pinnacle"]
    r2 = sel.select(1, soft, now=now)
    assert r2.source_book == "__consensus__"
    assert r2.fair_home is not None

    # Pinnacle returns -> immediately switch back
    r3 = sel.select(1, _full(now), now=now)
    assert r3.source_book == "pinnacle"
