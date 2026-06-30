"""Book selection + the dispersion-band staleness model (P3b).

Staleness is **the dispersion band, not a wall-clock age**. Each refresh we build a
freshness/sharpness-weighted robust consensus `c` of the live books (in log-odds)
and an **asymmetric** band whose width is a robust dispersion (MAD) of those same
books, keyed to the consensus drift: the trailing side tightens (laggards flagged
fast), the leading side loosens (a book out ahead is the freshest signal), and a
flat market is symmetric. Age only **weights** the consensus — it never gates.

A book outside its band is stale and drops out (with hysteresis on re-entry). Below
`min_books_for_band` the band isn't meaningful → best-available + a loose age
backstop. No live book → `"no sportsbook fair"`. The failover waterfall then picks
the first *live* book by sharpness, switching back to Pinnacle the moment it returns.

All math is in log-odds so the band doesn't collapse on heavy favorites; results are
mapped back to probability for emission.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..config import Config
from ..models import BookQuote
from .devig import devig

# Sharpness weights (limits taken, hold, line origination). Sharper books anchor the
# consensus; soft books only inform. Unknown books get a modest default.
SHARPNESS: dict[str, float] = {
    "pinnacle": 1.0,
    "betfair_ex": 0.95,
    "circa": 0.85,
    "draftkings": 0.6,
    "fanduel": 0.6,
    "betmgm": 0.55,
    "caesars": 0.55,
}
_DEFAULT_SHARPNESS = 0.5
_FRESHNESS_TAU_S = 120.0  # age-weighting half-life-ish; weights only, never gates


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _expit(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _weighted_median(pairs: list[tuple[float, float]]) -> float:
    """pairs = [(value, weight)] -> weighted median value."""
    pairs = sorted(pairs, key=lambda t: t[0])
    total = sum(w for _, w in pairs) or 1.0
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return pairs[-1][0]


@dataclass
class BookFair:
    book: str
    fair_home: float
    logit: float
    age_s: float
    weight: float


@dataclass
class Selection:
    game_pk: int
    source_book: Optional[str]
    fair_home: Optional[float]
    fair_away: Optional[float]
    live_book_count: int
    consensus_logodds: Optional[float]
    band_logodds: Optional[tuple[float, float]]  # (lower edge offset, upper edge offset)
    reason: Optional[str] = None  # set only when there is no fair
    live_books: list[str] = field(default_factory=list)


class BandSelector:
    """Stateful per-fixture selector: tracks consensus history (for drift) and the
    live set (for hysteresis) across refreshes."""

    def __init__(self, config: Optional[Config] = None):
        self.cfg = config or Config()
        self._consensus_hist: dict[int, list[tuple[float, float]]] = {}  # pk -> [(ts, c)]
        self._live: dict[int, set[str]] = {}  # pk -> live book keys last refresh

    # ---- public API ------------------------------------------------------- #

    def select(
        self,
        game_pk: int,
        quotes: list[BookQuote],
        now: Optional[datetime] = None,
        method: Optional[str] = None,
    ) -> Selection:
        now = now or datetime.now(timezone.utc)
        method = method or self.cfg.devig_method
        books = self._to_book_fairs(quotes, now, method)

        if not books:
            self._live[game_pk] = set()
            return Selection(game_pk, None, None, None, 0, None, None, reason="no_books")

        c = _weighted_median([(b.logit, b.weight) for b in books])
        s = self._scale(books, c)
        drift = self._drift(game_pk, c, s, now)
        lower_w, upper_w = self._band_widths(s, drift)

        live = self._live_set(game_pk, books, c, lower_w, upper_w)
        self._live[game_pk] = {b.book for b in live}

        source, fair_home = self._failover(live, books)
        if source is None or fair_home is None:
            reason = "all_books_stale" if books else "no_books"
            return Selection(game_pk, None, None, None, len(live), c, (-lower_w, upper_w), reason=reason)

        return Selection(
            game_pk=game_pk,
            source_book=source,
            fair_home=fair_home,
            fair_away=1.0 - fair_home,
            live_book_count=len(live),
            consensus_logodds=c,
            band_logodds=(-lower_w, upper_w),
            live_books=[b.book for b in live],
        )

    # ---- internals -------------------------------------------------------- #

    def _to_book_fairs(self, quotes, now, method) -> list[BookFair]:
        out: list[BookFair] = []
        for q in quotes:
            try:
                fr = devig(q.home_price, q.away_price, method)
            except Exception:
                continue
            age = max(0.0, (now - q.last_update).total_seconds())
            sharp = SHARPNESS.get(q.book, _DEFAULT_SHARPNESS)
            freshness = math.exp(-age / _FRESHNESS_TAU_S)  # weight only
            out.append(BookFair(q.book, fr.home, _logit(fr.home), age, sharp * freshness))
        return out

    def _scale(self, books: list[BookFair], c: float) -> float:
        if len(books) < 2:
            return 0.0
        devs = [(abs(b.logit - c), b.weight) for b in books]
        mad = _weighted_median(devs)
        return 1.4826 * mad

    def _drift(self, game_pk: int, c: float, s: float, now: datetime) -> float:
        ts = now.timestamp()
        hist = self._consensus_hist.setdefault(game_pk, [])
        hist.append((ts, c))
        window = self.cfg.drift_window_s
        # drop points older than the window (keep one just-outside as the baseline)
        while len(hist) > 2 and ts - hist[1][0] > window:
            hist.pop(0)
        if len(hist) < 2 or s <= 0:
            return 0.0
        c_past = hist[0][1]
        d_hat = (c - c_past) / s
        return max(-3.0, min(3.0, d_hat))  # clamp; extreme moves don't blow the band open

    def _band_widths(self, s: float, drift: float) -> tuple[float, float]:
        cfg = self.cfg
        base = max(cfg.band_floor_logodds, min(cfg.band_ceiling_logodds, cfg.band_k * s))
        ad = abs(drift)
        w_lead = base * (1.0 + cfg.band_asym_a * ad)
        w_trail = base * max(1.0 - cfg.band_asym_b * ad, cfg.band_trail_floor_frac)
        # Map lead/trail onto lower/upper edges by drift direction.
        if drift >= 0:  # consensus rising -> upper edge leads, lower edge trails
            return w_trail, w_lead  # (lower, upper)
        return w_lead, w_trail

    def _live_set(self, game_pk, books, c, lower_w, upper_w) -> list[BookFair]:
        # Cold start: band isn't meaningful below min_books_for_band.
        if len(books) < self.cfg.min_books_for_band:
            return self._cold_start(books)

        prev = self._live.get(game_pk, set())
        hyst = self.cfg.hysteresis_frac
        live: list[BookFair] = []
        for b in books:
            offset = b.logit - c
            width = upper_w if offset >= 0 else lower_w
            # hysteresis: a book already live stays while inside `width`; a book
            # re-entering must get inside by the hysteresis margin.
            thresh = width if b.book in prev else width * (1.0 - hyst)
            if abs(offset) <= thresh:
                live.append(b)
        return live

    def _cold_start(self, books: list[BookFair]) -> list[BookFair]:
        """1 book -> it is the fair; 2 books -> best-available under a loose age cap."""
        backstop = self.cfg.cold_start_age_backstop_s
        fresh = [b for b in books if b.age_s <= backstop]
        return fresh

    def _failover(self, live: list[BookFair], books: list[BookFair]):
        if not live:
            return None, None
        by_key = {b.book: b for b in live}
        for entry in self.cfg.book_priority:
            if entry == "__consensus__":
                soft = [by_key[k] for k in self.cfg.consensus_books if k in by_key]
                if soft:
                    med = _weighted_median([(b.logit, b.weight) for b in soft])
                    return "__consensus__", _expit(med)
            elif entry in by_key:
                return entry, by_key[entry].fair_home
        # A live book not named in the waterfall: fall back to the sharpest live one.
        best = max(live, key=lambda b: b.weight)
        return best.book, best.fair_home
