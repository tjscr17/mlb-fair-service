"""Mock schedule source.

Reads the bundled StatsAPI-shaped fixture and filters by officialDate, so it is
behaviourally identical to the live client from the engine's point of view.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import SpineGame
from .base import parse_schedule

DEFAULT_FIXTURE = Path(__file__).resolve().parents[3] / "data" / "mock_schedule.json"


class MockSchedule:
    def __init__(self, fixture_path: Path | str = DEFAULT_FIXTURE):
        self._path = Path(fixture_path)
        self._payload = json.loads(self._path.read_text())

    async def fetch(self, start_date: str, end_date: str) -> list[SpineGame]:
        games = parse_schedule(self._payload)
        return [g for g in games if start_date <= g.official_date <= end_date]

    async def aclose(self) -> None:  # parity with the live client
        return None
