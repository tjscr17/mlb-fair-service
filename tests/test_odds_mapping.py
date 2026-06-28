"""Odds (The Odds API shape) -> spine gamePk join.

The sportsbook side of the spine: full team names resolve via the same alias
table, doubleheaders disambiguate by ordinal commence_time (no game number in the
feed), and unplaceable events are dropped rather than mis-mapped.
"""

from __future__ import annotations

import asyncio

from mlb_fair.engine.registry import FixtureRegistry
from mlb_fair.odds.base import parse_odds
from mlb_fair.odds.mapping import map_odds
from mlb_fair.odds.mock import MockOdds
from mlb_fair.spine.mock import MockSchedule


def _registry() -> FixtureRegistry:
    reg = FixtureRegistry()
    reg.upsert_spine(asyncio.run(MockSchedule().fetch("2026-06-25", "2026-06-25")))
    return reg


def _odds():
    return asyncio.run(MockOdds().fetch())


def test_parse_shape():
    events = _odds()
    assert len(events) == 6
    nyy = next(e for e in events if e.id == "odds-nyy-bos")
    assert nyy.home_team == "Boston Red Sox" and nyy.away_team == "New York Yankees"
    assert {b.book for b in nyy.books} == {"pinnacle", "draftkings", "fanduel", "betmgm"}
    pin = next(b for b in nyy.books if b.book == "pinnacle")
    assert pin.home_price == -148 and pin.away_price == 134
    assert pin.last_update is not None and pin.last_update.tzinfo is not None


def test_all_six_games_map():
    by_pk = map_odds(_odds(), _registry())
    assert set(by_pk) == {778001, 778010, 778011, 778020, 778021, 778030}


def test_split_dh_ordinal_join():
    by_pk = map_odds(_odds(), _registry())
    assert by_pk[778010].id == "odds-chc-mil-g1"  # earlier commence -> G1
    assert by_pk[778011].id == "odds-chc-mil-g2"


def test_traditional_dh_ordinal_join():
    by_pk = map_odds(_odds(), _registry())
    assert by_pk[778020].id == "odds-hou-ath-g1"
    assert by_pk[778021].id == "odds-hou-ath-g2"


def test_athletics_alias_and_suspended_game():
    by_pk = map_odds(_odds(), _registry())
    # "Athletics" resolves (alias mess) and the suspended game still gets odds.
    assert 778020 in by_pk  # Athletics home
    assert by_pk[778030].id == "odds-lad-sf"
    assert len(by_pk[778030].books) == 2


def test_unplaceable_event_dropped():
    # An odds event for teams not on the slate is simply not mapped (fail safe).
    payload = [
        {
            "id": "odds-phantom",
            "commence_time": "2026-06-25T20:00:00Z",
            "home_team": "Toronto Blue Jays",
            "away_team": "Tampa Bay Rays",
            "bookmakers": [
                {"key": "pinnacle", "title": "Pinnacle", "markets": [
                    {"key": "h2h", "outcomes": [
                        {"name": "Toronto Blue Jays", "price": -110},
                        {"name": "Tampa Bay Rays", "price": -110}]}]}
            ],
        }
    ]
    by_pk = map_odds(parse_odds(payload), _registry())
    assert by_pk == {}
