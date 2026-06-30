"""Emit scheduler — the wall-clock-aligned 60s tick.

Decoupled from odds polling: the odds cache refreshes faster (~20s) into memory; the
scheduler just reads the latest cached quotes each tick, runs the fair engine, and
emits. Stops quoting a fixture once it leaves the pre-game set (first pitch).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional, Protocol

from ..models import BookQuote, EmitRecord
from ..odds.selection import BandSelector
from .fair_engine import compute_fair
from .registry import FixtureRegistry


class OddsCache(Protocol):
    def get(self, game_pk: int) -> list[BookQuote]:
        ...


class EmitScheduler:
    def __init__(self, registry: FixtureRegistry, odds_cache, sink, selector: BandSelector, config):
        self.registry = registry
        self.odds = odds_cache
        self.sink = sink
        self.selector = selector
        self.cfg = config

    def emit_once(self, now: Optional[datetime] = None) -> list[EmitRecord]:
        now = now or datetime.now(timezone.utc)
        out: list[EmitRecord] = []
        for fx in self.registry.quotable():
            quotes = self.odds.get(fx.game_pk)
            rec = compute_fair(fx, quotes, self.selector, self.cfg.devig_method, now=now)
            self.sink.emit(rec)
            out.append(rec)
        return out

    async def run(self, ticks: Optional[int] = None, interval_s: Optional[float] = None) -> None:
        interval = interval_s if interval_s is not None else self.cfg.emit_interval_s
        tick = 0
        while ticks is None or tick < ticks:
            now = datetime.now(timezone.utc)
            advance = getattr(self.odds, "advance", None)
            if advance is not None:
                advance(tick, now)  # let a scripted/poller cache refresh first
            self.emit_once(now)
            tick += 1
            if ticks is not None and tick >= ticks:
                break
            await asyncio.sleep(interval)
