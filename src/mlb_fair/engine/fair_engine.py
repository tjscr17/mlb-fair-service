"""Fair engine: turn a fixture's book quotes into an EmitRecord.

Runs the band selector, then resolves the fair onto the correct contract side using
the spine (home/away) and the binding (which team the YES contract pays on). When no
book is live it emits the binary `"no sportsbook fair"` record — there is no
"degraded but emit" tier.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..models import BookQuote, EmitRecord
from ..odds.selection import BandSelector
from .registry import Fixture


def compute_fair(
    fixture: Fixture,
    quotes: list[BookQuote],
    selector: BandSelector,
    method: Optional[str] = None,
    now: Optional[datetime] = None,
) -> EmitRecord:
    now = now or datetime.now(timezone.utc)
    s = fixture.spine
    b = fixture.binding
    sel = selector.select(s.game_pk, quotes, now=now, method=method)

    common = dict(
        game_pk=s.game_pk,
        kalshi_market_ticker=(b.market_ticker if b else None),
        home_team=s.home.name,
        away_team=s.away.name,
        game_number=s.game_number,
        emit_ts=now,
    )

    if sel.fair_home is None:
        return EmitRecord(**common, no_fair=True, reason=sel.reason)

    # YES side resolves to a specific team (binding); fall back to home if unbound.
    yes_is_home = (b is None) or (b.yes_team_id == s.home.id)
    fair_yes = sel.fair_home if yes_is_home else sel.fair_away

    return EmitRecord(
        **common,
        fair_home=round(sel.fair_home, 4),
        fair_away=round(sel.fair_away, 4),
        fair_yes=round(fair_yes, 4),
        source_book=sel.source_book,
        live_book_count=sel.live_book_count,
        consensus_logodds=round(sel.consensus_logodds, 4) if sel.consensus_logodds is not None else None,
        band_logodds=(
            (round(sel.band_logodds[0], 4), round(sel.band_logodds[1], 4))
            if sel.band_logodds is not None
            else None
        ),
    )
