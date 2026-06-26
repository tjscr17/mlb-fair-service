"""Schedule spine: the canonical fixture identity layer.

Both the live MLB StatsAPI client and the mock parse the *same* raw schedule
JSON shape into `SpineGame`, so swapping live<->mock never touches the engine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from ..models import SpineGame, Team, TeamRecord


def _parse_dt(value: str) -> datetime:
    # StatsAPI uses trailing 'Z'; normalize for fromisoformat on all 3.11+.
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _parse_record(node: dict) -> TeamRecord:
    rec = node.get("leagueRecord", {}) or {}
    try:
        pct = float(rec.get("pct", 0.0))
    except (TypeError, ValueError):
        pct = 0.0
    return TeamRecord(wins=rec.get("wins", 0), losses=rec.get("losses", 0), pct=pct)


def parse_schedule(payload: dict) -> list[SpineGame]:
    """Parse a StatsAPI /schedule response into SpineGame objects."""
    games: list[SpineGame] = []
    for date_block in payload.get("dates", []):
        for g in date_block.get("games", []):
            away = g["teams"]["away"]
            home = g["teams"]["home"]
            games.append(
                SpineGame(
                    game_pk=g["gamePk"],
                    game_number=g.get("gameNumber", 1),
                    double_header=g.get("doubleHeader", "N"),
                    game_date=_parse_dt(g["gameDate"]),
                    official_date=g.get("officialDate", date_block.get("date")),
                    away=Team(id=away["team"]["id"], name=away["team"]["name"]),
                    home=Team(id=home["team"]["id"], name=home["team"]["name"]),
                    away_record=_parse_record(away),
                    home_record=_parse_record(home),
                    status=g.get("status", {}).get("detailedState", "Scheduled"),
                    scheduled_innings=g.get("scheduledInnings", 9),
                    venue=(g.get("venue") or {}).get("name"),
                    reverse_home_away=g.get("reverseHomeAwayStatus", False),
                )
            )
    return games


class ScheduleSource(Protocol):
    async def fetch(self, start_date: str, end_date: str) -> list[SpineGame]:
        """Return all games with officialDate in [start_date, end_date] (YYYY-MM-DD)."""
        ...
