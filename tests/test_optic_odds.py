"""OpticOdds adapter — selection grouping (no network).

Exercises the moneyline row -> per-book home/away collapse, which is the part most
likely to drift if OpticOdds field names change. The live fetch() path is not
network-tested here (it needs the shared key).
"""

from __future__ import annotations

from mlb_fair.odds.optic_odds import _group_books, _team_name


def test_team_name_uses_display_then_competitors():
    # OpticOdds uses *_team_display (NOT *_team); fall back to competitors[].name.
    assert _team_name({"home_team_display": "Toronto Blue Jays"}, "home") == "Toronto Blue Jays"
    assert _team_name({"away_competitors": [{"name": "Texas Rangers"}]}, "away") == "Texas Rangers"
    assert _team_name({}, "home") is None


def _rows():
    return [
        {"sportsbook": "Pinnacle", "selection": "Boston Red Sox", "price": -148, "timestamp": 1750000000},
        {"sportsbook": "Pinnacle", "selection": "New York Yankees", "price": 134, "timestamp": 1750000000},
        {"sportsbook": "DraftKings", "selection": "Boston Red Sox", "price": -150},
        {"sportsbook": "DraftKings", "selection": "New York Yankees", "price": 130},
    ]


def test_groups_selections_into_home_away():
    books = {b.book: b for b in _group_books(_rows(), "Boston Red Sox", "New York Yankees")}
    assert set(books) == {"pinnacle", "draftkings"}
    assert books["pinnacle"].home_price == -148 and books["pinnacle"].away_price == 134
    assert books["pinnacle"].title == "Pinnacle"
    assert books["pinnacle"].last_update is not None  # epoch timestamp parsed


def test_book_dropped_if_missing_a_side():
    rows = [{"sportsbook": "FanDuel", "selection": "Boston Red Sox", "price": -145}]
    assert _group_books(rows, "Boston Red Sox", "New York Yankees") == []


def test_matches_partial_selection_names():
    rows = [
        {"sportsbook": "BetMGM", "name": "Yankees", "price": 120},
        {"sportsbook": "BetMGM", "name": "Red Sox", "price": -140},
    ]
    books = _group_books(rows, "Boston Red Sox", "New York Yankees")
    assert len(books) == 1 and books[0].away_price == 120 and books[0].home_price == -140
