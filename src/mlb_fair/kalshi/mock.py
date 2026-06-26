"""Mock Kalshi events source.

Reads the bundled Kalshi-shaped fixture and (optionally) filters by market
status, so it is behaviourally identical to the live poller from the mapper's
point of view.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..models import KalshiEvent
from .base import parse_events

DEFAULT_FIXTURE = Path(__file__).resolve().parents[3] / "data" / "mock_kalshi_events.json"


class MockKalshiEvents:
    def __init__(self, fixture_path: Path | str = DEFAULT_FIXTURE):
        self._path = Path(fixture_path)
        self._payload = json.loads(self._path.read_text())

    async def fetch(self, status: Optional[str] = None) -> list[KalshiEvent]:
        events = parse_events(self._payload)
        if status is None:
            return events
        # Keep events that have at least one market in the requested status.
        return [e for e in events if any(m.status == status for m in e.markets)]

    async def aclose(self) -> None:  # parity with the live client
        return None
