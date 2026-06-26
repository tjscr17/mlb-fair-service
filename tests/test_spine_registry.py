"""P1 tests: spine parsing + registry blocking, DH detection, lifecycle."""

import asyncio

from mlb_fair.engine.registry import FixtureRegistry
from mlb_fair.spine.mock import MockSchedule


def _load():
    src = MockSchedule()
    return asyncio.run(src.fetch("2026-06-25", "2026-06-25"))


def test_parses_all_games():
    games = _load()
    assert len(games) == 6
    pks = {g.game_pk for g in games}
    assert pks == {778001, 778010, 778011, 778020, 778021, 778030}


def test_records_and_utc():
    games = {g.game_pk: g for g in _load()}
    nyy_bos = games[778001]
    assert nyy_bos.away.name == "New York Yankees"
    assert nyy_bos.home_record.wins == 41
    assert nyy_bos.game_date.tzinfo is not None
    assert nyy_bos.game_date.hour == 23  # 7:10pm ET -> 23:10Z


def test_doubleheader_blocks_split_and_traditional():
    reg = FixtureRegistry()
    reg.upsert_spine(_load())

    # Split-admission DH: Cubs(112) @ Brewers(158)
    block = reg.block("2026-06-25", frozenset({112, 158}))
    assert [f.game_pk for f in block] == [778010, 778011]  # ordered G1, G2
    assert [f.spine.game_number for f in block] == [1, 2]
    assert all(f.spine.double_header == "S" for f in block)

    # Traditional DH: Astros(117) @ Athletics(133)
    trad = reg.block("2026-06-25", frozenset({117, 133}))
    assert [f.spine.game_number for f in trad] == [1, 2]
    assert all(f.spine.double_header == "Y" for f in trad)

    # Single game blocks to exactly one fixture
    single = reg.block("2026-06-25", frozenset({147, 111}))
    assert len(single) == 1


def test_lifecycle_pregame_flag():
    reg = FixtureRegistry()
    reg.upsert_spine(_load())
    assert reg.get(778001).spine.is_pregame is True
    assert reg.get(778030).spine.is_pregame is False  # Suspended -> not quotable


def test_upsert_idempotent_and_drift():
    reg = FixtureRegistry()
    games = _load()
    new = reg.upsert_spine(games)
    assert len(new) == 6
    again = reg.upsert_spine(games)  # same games -> no new pks
    assert again == []
    assert len(reg) == 6
