"""Fixture registry — the gamePk-keyed source of truth for what we track.

Holds spine identity + lifecycle, plus (later) the Kalshi binding. Keying on the
immutable gamePk is what makes doubleheaders and suspend/resume safe: a resumed
game keeps its gamePk and is never duplicated, and home/away always comes from
the spine here, never inferred downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

from ..models import FixtureBinding, SpineGame


@dataclass
class Fixture:
    spine: SpineGame
    binding: Optional[FixtureBinding] = None
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_emit: datetime | None = None

    @property
    def game_pk(self) -> int:
        return self.spine.game_pk

    @property
    def is_bound(self) -> bool:
        return self.binding is not None

    @property
    def should_quote(self) -> bool:
        """We quote a fixture that is bound to a Kalshi market and still pre-game."""
        return self.is_bound and self.spine.is_pregame


class FixtureRegistry:
    def __init__(self) -> None:
        self._fixtures: dict[int, Fixture] = {}

    # ---- spine ingestion -------------------------------------------------- #

    def upsert_spine(self, games: Iterable[SpineGame]) -> list[int]:
        """Add new games, update existing in place (status / start-time drift).

        Returns the list of newly-seen gamePks. Never deletes on absence — a game
        briefly missing from a response shouldn't drop a tracked fixture.
        """
        now = datetime.now(timezone.utc)
        new_pks: list[int] = []
        for g in games:
            existing = self._fixtures.get(g.game_pk)
            if existing is None:
                self._fixtures[g.game_pk] = Fixture(spine=g, last_seen=now)
                new_pks.append(g.game_pk)
            else:
                existing.spine = g  # absorb status + start-time drift
                existing.last_seen = now
        return new_pks

    # ---- access ----------------------------------------------------------- #

    def get(self, game_pk: int) -> Optional[Fixture]:
        return self._fixtures.get(game_pk)

    def all(self) -> list[Fixture]:
        return list(self._fixtures.values())

    def quotable(self) -> list[Fixture]:
        return [f for f in self._fixtures.values() if f.should_quote]

    # ---- block index (for doubleheader mapping) --------------------------- #

    def block(self, official_date: str, team_pair: frozenset[int]) -> list[Fixture]:
        """All fixtures sharing a (local date, unordered team pair) block, ordered by start."""
        key = (official_date, team_pair)
        out = [f for f in self._fixtures.values() if f.spine.block_key == key]
        return sorted(out, key=lambda f: (f.spine.game_number, f.spine.game_date))

    def bind(self, binding: FixtureBinding) -> None:
        fx = self._fixtures.get(binding.game_pk)
        if fx is None:
            raise KeyError(f"cannot bind unknown gamePk {binding.game_pk}")
        fx.binding = binding

    def __len__(self) -> int:
        return len(self._fixtures)
