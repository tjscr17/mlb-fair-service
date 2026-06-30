"""Service entrypoint — wires pollers + scheduler. `--mode {mock,live}`.

Mock runs a self-contained accelerated slate (the spec's acceptance scenario): a
split DH, a straight DH, a mid-session Pinnacle outage (failover + switch-back), and
an all-books-stale window that emits `"no sportsbook fair"`. Live builds the same
pipeline from StatsAPI + Kalshi + OpticOdds.

    python -m mlb_fair.main --mode mock
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from .config import Config
from .emit.sink import JsonlSink
from .engine.fair_engine import compute_fair  # noqa: F401 (re-export convenience)
from .engine.registry import FixtureRegistry
from .engine.scheduler import EmitScheduler
from .kalshi.mapping import apply, map_events
from .models import BookQuote
from .odds.mapping import map_odds
from .odds.selection import BandSelector

MOCK_DATE = "2026-06-25"
_FRESH_AGE_S = 5.0
_STALE_AGE_S = 9999.0


class ScriptedOddsCache:
    """Mock odds feed that mutates over ticks to exercise failover + no-fair.

    Holds base book lines per gamePk and re-stamps freshness each tick. Scenario:
      tick 3 -> Pinnacle drops on one fixture (outage)   -> failover to next book
      tick 4 -> Pinnacle returns                          -> switch back
      tick >=5 -> one fixture goes stale w/ <3 books      -> "no sportsbook fair"
    """

    def __init__(self, base: dict[int, list[tuple[str, float, float]]]):
        self.base = base
        self.current: dict[int, list[BookQuote]] = {}
        pks = sorted(base)
        self.outage_pk = pks[0] if pks else None
        self.nofair_pk = pks[-1] if pks else None

    def advance(self, tick: int, now: datetime) -> None:
        cur: dict[int, list[BookQuote]] = {}
        for pk, lines in self.base.items():
            books: list[tuple[str, float, float]] = list(lines)
            age = _FRESH_AGE_S
            if pk == self.outage_pk and tick == 3:
                books = [ln for ln in books if ln[0] != "pinnacle"]  # outage
            if pk == self.nofair_pk and tick >= 5:
                books = books[:2]      # drop below the band's 3-book minimum
                age = _STALE_AGE_S     # and blow past the cold-start age backstop
            ts = now - timedelta(seconds=age)
            cur[pk] = [
                BookQuote(book=b, game_pk=pk, home_price=h, away_price=a, last_update=ts)
                for (b, h, a) in books
            ]
        self.current = cur

    def get(self, game_pk: int) -> list[BookQuote]:
        return self.current.get(game_pk, [])


async def _build_mock(cfg: Config):
    from .kalshi.mock import MockKalshiEvents
    from .odds.mock import MockOdds
    from .spine.mock import MockSchedule

    reg = FixtureRegistry()
    reg.upsert_spine(await MockSchedule().fetch(MOCK_DATE, MOCK_DATE))
    apply(map_events(await MockKalshiEvents().fetch(), reg), reg)

    by_pk = map_odds(await MockOdds().fetch(), reg)
    quotable_pks = {f.game_pk for f in reg.quotable()}  # scenario must target emitted fixtures
    base = {
        pk: [(bk.book, bk.home_price, bk.away_price) for bk in oe.books]
        for pk, oe in by_pk.items()
        if pk in quotable_pks
    }
    return reg, ScriptedOddsCache(base)


async def _build_live(cfg: Config):
    from .kalshi.client import LiveKalshiEvents
    from .odds.mock import MockOdds
    from .odds.optic_odds import LiveOpticOdds
    from .spine.statsapi import StatsApiSchedule

    today = datetime.now(timezone.utc).date()
    start, end = today.isoformat(), (today + timedelta(days=cfg.window_days - 1)).isoformat()

    reg = FixtureRegistry()
    reg.upsert_spine(await StatsApiSchedule().fetch(start, end))
    apply(map_events(await LiveKalshiEvents().fetch(), reg), reg)

    import os

    odds_src = LiveOpticOdds() if os.environ.get("OPTIC_ODDS_API_KEY") else MockOdds()
    by_pk = map_odds(await odds_src.fetch(), reg)
    now = datetime.now(timezone.utc)
    cache_data = {
        pk: [
            BookQuote(book=bk.book, game_pk=pk, home_price=bk.home_price,
                      away_price=bk.away_price, last_update=bk.last_update or now)
            for bk in oe.books
        ]
        for pk, oe in by_pk.items()
    }

    class _StaticCache:
        def get(self, game_pk):
            return cache_data.get(game_pk, [])

    return reg, _StaticCache()


async def run(args) -> None:
    cfg = Config.from_env()
    cfg.devig_method = args.method or cfg.devig_method
    reg, cache = await (_build_live(cfg) if args.mode == "live" else _build_mock(cfg))

    print(f"# mode={args.mode}  tracked={len(reg)}  quotable={len(reg.quotable())}  "
          f"ticks={args.ticks}  interval={args.interval}s", flush=True)

    scheduler = EmitScheduler(reg, cache, JsonlSink(path=args.out), BandSelector(cfg), cfg)
    await scheduler.run(ticks=args.ticks, interval_s=args.interval)


def cli() -> None:
    ap = argparse.ArgumentParser(prog="mlb-fair", description="MLB fair-value emit service")
    ap.add_argument("--mode", choices=["mock", "live"], default="mock")
    ap.add_argument("--ticks", type=int, default=6, help="number of emit cycles (mock); 0 = run forever")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between emits (60 in prod)")
    ap.add_argument("--method", default=None, help="devig method override")
    ap.add_argument("--out", default="emits.jsonl", help="JSONL audit log path")
    args = ap.parse_args()
    if args.ticks == 0:
        args.ticks = None
    asyncio.run(run(args))


if __name__ == "__main__":
    cli()
