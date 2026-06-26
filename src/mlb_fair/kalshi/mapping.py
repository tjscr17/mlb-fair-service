"""Field-level join: Kalshi event -> spine `gamePk`.

The highest-risk join in the service. Identity comes from **fields, never
tickers** (Kalshi's docs warn ticker structure isn't a contract), and **every
uncertain path fails safe to "no binding, emit nothing"** rather than a guess.

Reality verified against the live `KXMLBGAME` series (2026), which differs from
the original spec wording:
  * No `strike_date` — the game-start anchor is `occurrence_datetime` (UTC).
  * `product_metadata` is just {"competition","competition_scope"} — no shortcut.
  * Each game = one event with TWO per-team markets; within a market yes/no
    subtitles are the SAME team. So the **team pair comes from the event
    title/sub_title**, and each market's `yes_sub_title` resolves one YES side.

Both Kalshi and the sportsbook map onto the spine independently; we NEVER match
Kalshi to the book directly. Home/away is read from the spine; the YES side is
resolved separately so the fair posts on the correct contract.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from ..engine.registry import Fixture, FixtureRegistry
from ..models import FixtureBinding, KalshiEvent

ALIASES_PATH = Path(__file__).resolve().parent / "team_aliases.json"

# MLB's "official date" (which defines "same calendar day" for doubleheaders) is
# the game's local date. We use US/Eastern as the league day-boundary: it's exact
# for the day-rollover cases that matter (a 7pm ET game is the next day in UTC),
# and the residual west-coast late-night ambiguity never crosses a date here. A
# per-venue tz table is the precise fix; noted for DESIGN.md.
_LEAGUE_TZ = ZoneInfo("America/New_York")

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")
_VS = re.compile(r"\s+(?:vs\.?|@)\s+", re.IGNORECASE)
_PAREN = re.compile(r"\(.*?\)")
_GAME_NUM = re.compile(r"\bgame\s*#?\s*0*([12])\b|\bg([12])\b", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Alias resolution
# --------------------------------------------------------------------------- #


def _norm(s: str) -> str:
    """Lowercase, drop apostrophes (so "A's" -> "as"), other punctuation -> space, collapse ws."""
    s = s.lower().replace("'", "").replace("’", "")
    return _WS.sub(" ", _PUNCT.sub(" ", s)).strip()


def load_aliases(path: Path | str = ALIASES_PATH) -> dict[str, int]:
    """Load the alias table, normalizing keys and skipping `_`-prefixed comments."""
    raw = json.loads(Path(path).read_text())
    return {_norm(k): int(v) for k, v in raw.items() if not k.startswith("_")}


def resolve_team(name: Optional[str], aliases: dict[str, int]) -> Optional[int]:
    """Resolve a Kalshi team string to an MLB team_id, or None on a miss."""
    if not name:
        return None
    return aliases.get(_norm(name))


# --------------------------------------------------------------------------- #
# Field extraction (all return None -> fail safe to unmatched)
# --------------------------------------------------------------------------- #


def _split_pair_text(text: str) -> list[str]:
    return [p.strip() for p in _VS.split(_PAREN.sub("", text)) if p.strip()]


def event_team_pair(event: KalshiEvent, aliases: dict[str, int]) -> Optional[frozenset[int]]:
    """Unordered team pair from the event title/sub_title (NOT yes/no subtitles)."""
    for text in (event.title, event.sub_title):
        if not text:
            continue
        parts = _split_pair_text(text)
        if len(parts) != 2:
            continue
        a, b = resolve_team(parts[0], aliases), resolve_team(parts[1], aliases)
        if a is not None and b is not None and a != b:
            return frozenset((a, b))
    return None


def event_occurrence(event: KalshiEvent) -> Optional[datetime]:
    """The game-start anchor (UTC); both markets of an event share it."""
    for m in event.markets:
        if m.occurrence_datetime is not None:
            return m.occurrence_datetime
    return None


def event_local_date(event: KalshiEvent) -> Optional[str]:
    """`occurrence_datetime` normalized to the league-local calendar date (YYYY-MM-DD)."""
    occ = event_occurrence(event)
    if occ is None:
        return None
    return occ.astimezone(_LEAGUE_TZ).date().isoformat()


def explicit_game_number(event: KalshiEvent) -> Optional[int]:
    """An explicit 'Game 2' / 'G2' signal from title/sub_title/rules, else None."""
    blobs = [event.title, event.sub_title, *(m.rules_primary for m in event.markets)]
    for b in blobs:
        if not b:
            continue
        m = _GAME_NUM.search(b)
        if m:
            return int(m.group(1) or m.group(2))
    return None


def resolve_yes_sides(
    event: KalshiEvent, home_id: int, away_id: int, aliases: dict[str, int]
) -> Optional[dict[str, int]]:
    """Map each market_ticker -> its YES team_id.

    Fails safe (None) unless every market's YES team resolves into the spine pair
    AND the markets together cover *both* teams exactly — i.e. the canonical
    two-per-team contract shape.
    """
    pair = {home_id, away_id}
    out: dict[str, int] = {}
    for m in event.markets:
        tid = resolve_team(m.yes_sub_title, aliases)
        if tid is None or tid not in pair:
            return None
        out[m.ticker] = tid
    if set(out.values()) != pair:
        return None
    return out


# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #


@dataclass
class MapResult:
    """Outcome of mapping one Kalshi event. Only `status == 'bound'` is applied."""

    event_ticker: str
    status: str  # "bound" | "pending" | "unmatched"
    binding: Optional[FixtureBinding] = None
    game_pk: Optional[int] = None
    game_number: Optional[int] = None
    reason: Optional[str] = None

    @classmethod
    def bound(cls, binding: FixtureBinding) -> "MapResult":
        return cls(
            binding.event_ticker, "bound", binding=binding,
            game_pk=binding.game_pk, game_number=binding.game_number,
        )

    @classmethod
    def pending(cls, event_ticker: str, reason: str) -> "MapResult":
        return cls(event_ticker, "pending", reason=reason)

    @classmethod
    def unmatched(cls, event_ticker: str, reason: str) -> "MapResult":
        return cls(event_ticker, "unmatched", reason=reason)


# --------------------------------------------------------------------------- #
# Binding construction
# --------------------------------------------------------------------------- #


def _build_binding(
    event: KalshiEvent, spine, game_number: int, confidence: str, aliases: dict[str, int]
) -> Optional[FixtureBinding]:
    yes = resolve_yes_sides(event, spine.home.id, spine.away.id, aliases)
    if yes is None:
        return None
    home_ticker = next((t for t, tid in yes.items() if tid == spine.home.id), None)
    if home_ticker is None:
        return None
    return FixtureBinding(
        event_ticker=event.event_ticker,
        market_ticker=home_ticker,  # representative = home-team contract
        game_pk=spine.game_pk,
        game_number=game_number,
        yes_team_id=spine.home.id,
        market_yes=yes,
        confidence=confidence,
    )


def _assign_dh(
    evs: list[KalshiEvent], block: list[Fixture]
) -> Optional[tuple[dict[int, int], str]]:
    """Bijection guard: map the events one-to-one onto the block's gameNumbers.

    Strongest signal first: explicit 'Game N' text; then ordinal `occurrence_datetime`
    order for whatever's left. Returns ({event_index: game_number}, confidence) or
    None when the ordering is ambiguous / not a clean bijection -> refuse, emit nothing.
    """
    target = [b.spine.game_number for b in block]
    assigned: dict[int, int] = {}
    for i, ev in enumerate(evs):
        gn = explicit_game_number(ev)
        if gn is not None:
            assigned[i] = gn

    used_ordinal = False
    if len(assigned) < len(evs):
        occ = [event_occurrence(ev) for ev in evs]
        if any(o is None for o in occ):
            return None
        remaining = [i for i in range(len(evs)) if i not in assigned]
        remaining_nums = sorted(set(target) - set(assigned.values()))
        if len(remaining) != len(remaining_nums):
            return None
        ro = sorted(remaining, key=lambda i: occ[i])
        for a, b in zip(ro, ro[1:]):
            if occ[a] == occ[b]:  # tie -> can't order -> ambiguous
                return None
        for slot, i in enumerate(ro):
            assigned[i] = remaining_nums[slot]
        used_ordinal = True

    if sorted(assigned.values()) != sorted(target) or len(set(assigned.values())) != len(evs):
        return None  # not a one-to-one bijection
    return assigned, ("ordinal" if used_ordinal else "text")


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def map_events(
    events: list[KalshiEvent],
    registry: FixtureRegistry,
    aliases: Optional[dict[str, int]] = None,
) -> list[MapResult]:
    """Map a batch of Kalshi events onto spine gamePks.

    Batched (not per-event) because a doubleheader's bijection guard needs both
    events of a (date, team-pair) block at once.
    """
    if aliases is None:
        aliases = load_aliases()

    results: list[MapResult] = []
    groups: dict[tuple[str, frozenset[int]], list[KalshiEvent]] = defaultdict(list)

    for ev in events:
        pair = event_team_pair(ev, aliases)
        if pair is None:
            results.append(MapResult.unmatched(ev.event_ticker, "unresolved_team"))
            continue
        local_date = event_local_date(ev)
        if local_date is None:
            results.append(MapResult.unmatched(ev.event_ticker, "no_occurrence_time"))
            continue
        groups[(local_date, pair)].append(ev)

    for (local_date, pair), evs in groups.items():
        block = registry.block(local_date, pair)

        if not block:
            # Spine is pulled 6 days out, so this is almost always an alias miss or
            # refresh lag, not a real new game — keep it loud (pending), don't guess.
            for ev in evs:
                results.append(MapResult.pending(ev.event_ticker, "no_spine_game"))
            continue

        if len(block) == 1:
            if len(evs) != 1:
                for ev in evs:
                    results.append(MapResult.unmatched(ev.event_ticker, "excess_events"))
                continue
            spine = block[0].spine
            binding = _build_binding(evs[0], spine, spine.game_number, "exact", aliases)
            results.append(
                MapResult.bound(binding) if binding is not None
                else MapResult.unmatched(evs[0].event_ticker, "yes_side_unresolved")
            )
            continue

        # Doubleheader (block of 2). Refuse unless the events map one-to-one.
        if len(block) != len(evs):
            for ev in evs:
                results.append(MapResult.unmatched(ev.event_ticker, "incomplete_dh"))
            continue

        assignment = _assign_dh(evs, block)
        if assignment is None:
            for ev in evs:
                results.append(MapResult.unmatched(ev.event_ticker, "ambiguous_dh"))
            continue

        assigned, confidence = assignment
        by_number = {b.spine.game_number: b.spine for b in block}
        bindings = [
            _build_binding(ev, by_number[assigned[i]], assigned[i], confidence, aliases)
            for i, ev in enumerate(evs)
        ]
        if any(b is None for b in bindings):
            for ev in evs:
                results.append(MapResult.unmatched(ev.event_ticker, "yes_side_unresolved"))
        else:
            results.extend(MapResult.bound(b) for b in bindings)

    return results


def apply(results: list[MapResult], registry: FixtureRegistry) -> int:
    """Bind every confirmed result into the registry. Returns the count bound."""
    n = 0
    for r in results:
        if r.status == "bound" and r.binding is not None:
            registry.bind(r.binding)
            n += 1
    return n
