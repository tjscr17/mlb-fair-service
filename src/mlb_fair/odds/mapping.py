"""Odds event -> spine `gamePk` join.

The sportsbook side of the spine. Mirrors the Kalshi join: resolve team names via
the alias table, normalize the date to the league-local calendar day, block on
`(date, team-pair)`, and disambiguate doubleheaders by ordinal commence_time (The
Odds API exposes no game number). Fails safe — an event we can't place gets no
gamePk rather than a wrong one.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from ..engine.registry import FixtureRegistry
from ..kalshi.mapping import _LEAGUE_TZ, load_aliases, resolve_team
from .base import OddsEvent


def odds_local_date(event: OddsEvent) -> Optional[str]:
    if event.commence_time is None:
        return None
    return event.commence_time.astimezone(_LEAGUE_TZ).date().isoformat()


def map_odds(
    events: list[OddsEvent],
    registry: FixtureRegistry,
    aliases: Optional[dict[str, int]] = None,
) -> dict[int, OddsEvent]:
    """Return {game_pk: OddsEvent}. Unplaceable / ambiguous events are dropped."""
    if aliases is None:
        aliases = load_aliases()

    groups: dict[tuple[str, frozenset[int]], list[OddsEvent]] = defaultdict(list)
    for ev in events:
        home, away = resolve_team(ev.home_team, aliases), resolve_team(ev.away_team, aliases)
        local_date = odds_local_date(ev)
        if home is None or away is None or home == away or local_date is None:
            continue
        groups[(local_date, frozenset((home, away)))].append(ev)

    out: dict[int, OddsEvent] = {}
    for (local_date, pair), evs in groups.items():
        block = registry.block(local_date, pair)  # sorted by (game_number, game_date)
        if len(block) == 1 and len(evs) == 1:
            out[block[0].game_pk] = evs[0]
        elif len(block) == 2 and len(evs) == 2:
            ordered = sorted(evs, key=lambda e: e.commence_time or e.id)
            if ordered[0].commence_time == ordered[1].commence_time:
                continue  # tie -> can't order a DH -> bind neither
            for fx, ev in zip(block, ordered):
                out[fx.game_pk] = ev
        # else: incomplete/ambiguous -> no odds bound for this block
    return out
