"""Mock odds source — reads the bundled The Odds API-shaped fixture."""

from __future__ import annotations

import json
from pathlib import Path

from .base import OddsEvent, parse_odds

DEFAULT_FIXTURE = Path(__file__).resolve().parents[3] / "data" / "mock_odds.json"


class MockOdds:
    def __init__(self, fixture_path: Path | str = DEFAULT_FIXTURE):
        self._path = Path(fixture_path)
        self._payload = json.loads(self._path.read_text())

    async def fetch(self) -> list[OddsEvent]:
        return parse_odds(self._payload)

    async def aclose(self) -> None:  # parity with the live client
        return None
