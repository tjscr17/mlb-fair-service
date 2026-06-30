"""Fair engine emits the binary 'no sportsbook fair' record when no book is live."""

from __future__ import annotations

from datetime import datetime, timezone

from mlb_fair.config import Config
from mlb_fair.engine.fair_engine import compute_fair
from mlb_fair.engine.registry import Fixture
from mlb_fair.models import BookQuote, FixtureBinding, SpineGame, Team
from mlb_fair.odds.selection import BandSelector

UTC = timezone.utc


def _fixture():
    spine = SpineGame(
        game_pk=778001,
        game_number=1,
        game_date=datetime(2026, 6, 25, 23, 10, tzinfo=UTC),
        official_date="2026-06-25",
        home=Team(id=111, name="Boston Red Sox"),
        away=Team(id=147, name="New York Yankees"),
        status="Scheduled",
    )
    binding = FixtureBinding(
        event_ticker="KX-EVT", market_ticker="KX-EVT-BOS", game_pk=778001,
        game_number=1, yes_team_id=111,
    )
    return Fixture(spine=spine, binding=binding)


def test_no_quotes_emits_no_fair():
    rec = compute_fair(_fixture(), [], BandSelector(Config()))
    assert rec.no_fair is True
    assert rec.fair_home is None
    line = rec.to_line()
    assert line["fair"] == "no sportsbook fair"
    assert line["game_pk"] == 778001


def test_fair_resolves_onto_yes_side():
    now = datetime.now(UTC)
    quotes = [
        BookQuote(book="pinnacle", game_pk=778001, home_price=-150, away_price=130, last_update=now),
        BookQuote(book="draftkings", game_pk=778001, home_price=-148, away_price=128, last_update=now),
        BookQuote(book="fanduel", game_pk=778001, home_price=-152, away_price=132, last_update=now),
    ]
    rec = compute_fair(_fixture(), quotes, BandSelector(Config()), now=now)
    assert rec.no_fair is False
    # YES team is home (Boston) -> fair_yes == fair_home, and home is the favorite
    assert rec.fair_yes == rec.fair_home
    assert rec.fair_home > 0.5
    assert rec.source_book == "pinnacle"
