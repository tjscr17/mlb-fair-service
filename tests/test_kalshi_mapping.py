"""P2 — Kalshi -> gamePk field-level mapping.

Covers: parser parity with the live shape, single-game bind, alias resolution
(incl. Athletics mess + same-city disambiguation), YES-side resolution, split &
traditional doubleheader assignment (explicit text + ordinal + drift), the
bijection guard refusing an ambiguous DH, loud unmatched/pending, suspended-game
mapping, and local-date normalization.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from mlb_fair.engine.registry import FixtureRegistry
from mlb_fair.kalshi.mapping import (
    apply,
    event_local_date,
    event_team_pair,
    explicit_game_number,
    load_aliases,
    map_events,
    resolve_team,
)
from mlb_fair.kalshi.mock import MockKalshiEvents
from mlb_fair.models import KalshiEvent, KalshiMarket
from mlb_fair.spine.mock import MockSchedule

UTC = timezone.utc
ALIASES = load_aliases()

# Event tickers in data/mock_kalshi_events.json
NYY_BOS = "KXMLBGAME-26JUN251910NYYBOS"
CHC_MIL_G1 = "KXMLBGAME-26JUN251310CHCMIL"
CHC_MIL_G2 = "KXMLBGAME-26JUN251910CHCMIL"
HOU_ATH_G1 = "KXMLBGAME-26JUN251607HOUATH-G1"
HOU_ATH_G2 = "KXMLBGAME-26JUN252015HOUATH-G2"
LAD_SF = "KXMLBGAME-26JUN251845LADSF"
PORTLAND = "KXMLBGAME-26JUN251730PORBOS"
TOR_TB = "KXMLBGAME-26JUN251700TORTB"


def _registry() -> FixtureRegistry:
    reg = FixtureRegistry()
    games = asyncio.run(MockSchedule().fetch("2026-06-25", "2026-06-25"))
    reg.upsert_spine(games)
    return reg


def _events() -> list[KalshiEvent]:
    return asyncio.run(MockKalshiEvents().fetch())


def _by_ticker(results) -> dict:
    return {r.event_ticker: r for r in results}


def _mk_event(ticker, occ, *, game_text=None, home="Athletics", away="Houston"):
    """Build a Houston@Athletics-shaped event inline (for DH edge cases)."""
    rules = f"If the team wins the game{f' ({game_text})' if game_text else ''}."
    return KalshiEvent(
        event_ticker=ticker,
        series_ticker="KXMLBGAME",
        title=f"{away} vs {home}",
        sub_title="HOU vs ATH (Jun 25)",
        markets=[
            KalshiMarket(ticker=f"{ticker}-{away[:3].upper()}", event_ticker=ticker,
                         yes_sub_title=away, no_sub_title=away,
                         occurrence_datetime=occ, rules_primary=rules),
            KalshiMarket(ticker=f"{ticker}-{home[:3].upper()}", event_ticker=ticker,
                         yes_sub_title=home, no_sub_title=home,
                         occurrence_datetime=occ, rules_primary=rules),
        ],
    )


# --------------------------------------------------------------------------- #
# Parser parity with the live schema
# --------------------------------------------------------------------------- #


def test_parser_matches_live_shape():
    events = _events()
    assert len(events) == 8
    ev = _by_ticker_events(events)[NYY_BOS]
    assert len(ev.markets) == 2
    assert ev.strike_date is None  # live MLB has no strike_date
    assert ev.product_metadata["competition"] == "Pro Baseball"
    m = ev.markets[0]
    assert m.occurrence_datetime is not None
    assert m.occurrence_datetime.tzinfo is not None
    # yes/no subtitles are the SAME team within a market (binary "does TEAM win?")
    assert m.yes_sub_title == m.no_sub_title
    # YES-side market prices parse from *_dollars
    assert m.yes_bid is not None and m.yes_ask is not None


def _by_ticker_events(events) -> dict:
    return {e.event_ticker: e for e in events}


# --------------------------------------------------------------------------- #
# Alias resolution
# --------------------------------------------------------------------------- #


def test_alias_resolution_and_disambiguation():
    assert resolve_team("New York Y", ALIASES) == 147  # Yankees
    assert resolve_team("New York M", ALIASES) == 121  # Mets — same-city disambiguation
    assert resolve_team("NYY", ALIASES) == 147
    assert resolve_team("Boston", ALIASES) == 111
    # Athletics naming mess
    for s in ("Athletics", "Oakland Athletics", "Oakland", "A's", "ATH", "OAK"):
        assert resolve_team(s, ALIASES) == 133, s
    assert resolve_team("Springfield Isotopes", ALIASES) is None


def test_event_team_pair_from_title_not_subtitles():
    ev = _by_ticker_events(_events())[NYY_BOS]
    assert event_team_pair(ev, ALIASES) == frozenset({147, 111})


# --------------------------------------------------------------------------- #
# Single game
# --------------------------------------------------------------------------- #


def test_single_game_binds_with_yes_sides():
    results = _by_ticker(map_events(_events(), _registry(), ALIASES))
    r = results[NYY_BOS]
    assert r.status == "bound"
    assert r.game_pk == 778001
    assert r.binding.confidence == "exact"
    # home (Boston, 111) is the representative side
    assert r.binding.yes_team_id == 111
    assert r.binding.market_ticker.endswith("-BOS")
    # both per-team contracts recorded
    assert set(r.binding.market_yes.values()) == {147, 111}
    assert len(r.binding.market_yes) == 2


def test_suspended_game_still_maps():
    # gamePk is immutable across lifecycle; mapping is independent of status.
    r = _by_ticker(map_events(_events(), _registry(), ALIASES))[LAD_SF]
    assert r.status == "bound"
    assert r.game_pk == 778030
    assert r.binding.yes_team_id == 137  # Giants (home)


# --------------------------------------------------------------------------- #
# Doubleheaders
# --------------------------------------------------------------------------- #


def test_split_dh_maps_by_ordinal():
    results = _by_ticker(map_events(_events(), _registry(), ALIASES))
    g1, g2 = results[CHC_MIL_G1], results[CHC_MIL_G2]
    assert (g1.game_pk, g1.game_number) == (778010, 1)
    assert (g2.game_pk, g2.game_number) == (778011, 2)
    assert g1.binding.confidence == "ordinal" and g2.binding.confidence == "ordinal"


def test_traditional_dh_maps_by_explicit_text():
    results = _by_ticker(map_events(_events(), _registry(), ALIASES))
    g1, g2 = results[HOU_ATH_G1], results[HOU_ATH_G2]
    assert (g1.game_pk, g1.game_number) == (778020, 1)
    assert (g2.game_pk, g2.game_number) == (778021, 2)
    assert g1.binding.confidence == "text" and g2.binding.confidence == "text"


def test_traditional_dh_ordinal_survives_g2_time_drift():
    # No explicit text; G2 absolute start drifts late, but ordinal order still pairs.
    reg = _registry()
    g1 = _mk_event("DRIFT-G1", datetime(2026, 6, 25, 20, 7, tzinfo=UTC))
    g2 = _mk_event("DRIFT-G2", datetime(2026, 6, 26, 1, 30, tzinfo=UTC))  # ~hours later
    res = _by_ticker(map_events([g2, g1], reg, ALIASES))  # deliberately out of order
    assert (res["DRIFT-G1"].game_pk, res["DRIFT-G1"].game_number) == (778020, 1)
    assert (res["DRIFT-G2"].game_pk, res["DRIFT-G2"].game_number) == (778021, 2)
    assert res["DRIFT-G1"].binding.confidence == "ordinal"


def test_bijection_guard_refuses_ambiguous_dh():
    # Same occurrence_datetime, no explicit text -> cannot order -> refuse, bind nothing.
    reg = _registry()
    same = datetime(2026, 6, 25, 20, 7, tzinfo=UTC)
    a = _mk_event("AMBIG-A", same)
    b = _mk_event("AMBIG-B", same)
    results = map_events([a, b], reg, ALIASES)
    assert all(r.status == "unmatched" and r.reason == "ambiguous_dh" for r in results)
    assert apply(results, reg) == 0
    assert reg.get(778020).binding is None and reg.get(778021).binding is None


# --------------------------------------------------------------------------- #
# Fail-safe paths
# --------------------------------------------------------------------------- #


def test_unknown_alias_is_loud_unmatched():
    r = _by_ticker(map_events(_events(), _registry(), ALIASES))[PORTLAND]
    assert r.status == "unmatched"
    assert r.reason == "unresolved_team"
    assert r.binding is None


def test_no_spine_game_is_pending():
    r = _by_ticker(map_events(_events(), _registry(), ALIASES))[TOR_TB]
    assert r.status == "pending"
    assert r.reason == "no_spine_game"


# --------------------------------------------------------------------------- #
# Local-date normalization & end-to-end apply
# --------------------------------------------------------------------------- #


def test_local_date_normalization_handles_utc_rollover():
    # G2's occurrence_datetime is 2026-06-26T00:15Z but the local (ET) date is 06-25.
    ev = _by_ticker_events(_events())[HOU_ATH_G2]
    assert ev.markets[0].occurrence_datetime.date().isoformat() == "2026-06-26"  # UTC
    assert event_local_date(ev) == "2026-06-25"  # league-local


def test_explicit_game_number_extraction():
    ev_g1 = _by_ticker_events(_events())[HOU_ATH_G1]
    ev_g2 = _by_ticker_events(_events())[HOU_ATH_G2]
    ev_single = _by_ticker_events(_events())[NYY_BOS]
    assert explicit_game_number(ev_g1) == 1
    assert explicit_game_number(ev_g2) == 2
    assert explicit_game_number(ev_single) is None


def test_apply_binds_exactly_the_resolved_fixtures():
    reg = _registry()
    results = map_events(_events(), reg, ALIASES)
    bound = [r for r in results if r.status == "bound"]
    assert len(bound) == 6  # all 6 spine games map; Portland unmatched, Toronto pending
    assert apply(results, reg) == 6
    # spot-check the binding landed on the right fixture
    assert reg.get(778001).is_bound
    assert reg.get(778021).binding.game_number == 2
