"""All tunables in one place.

Polling cadences, the dispersion-band staleness parameters, and the failover
waterfall. Defaults are documented inline with the reasoning from DESIGN.md.
Override via environment variables (prefix MLB_) or by constructing Config directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_float(key: str, default: float) -> float:
    return float(os.environ.get(key, default))


def _env_int(key: str, default: int) -> int:
    return int(os.environ.get(key, default))


@dataclass
class Config:
    # ---- schedule window -------------------------------------------------- #
    window_days: int = 6  # today + 5; Kalshi lists <= ~5 days out
    spine_refresh_s: float = 240.0  # schedule is near-static; refresh slowly

    # ---- detection cadence ------------------------------------------------ #
    kalshi_poll_s: float = 2.0  # steady: ~1s expected detection latency
    kalshi_poll_batch_s: float = 1.0  # tighten during the daily listing window
    kalshi_poll_idle_s: float = 8.0  # relax overnight when nothing lists

    # ---- odds cadence ----------------------------------------------------- #
    # Strictly faster than emit so we never discover a dead book at quote time,
    # but matched to the aggregator's own ~30s pre-match refresh so we don't
    # re-fetch identical bytes.
    odds_poll_s: float = 20.0
    odds_poll_closing_s: float = 12.0  # final window, lines move faster

    # ---- emit cadence ----------------------------------------------------- #
    emit_interval_s: float = 60.0  # spec-mandated: once per minute until first pitch
    closing_window_s: float = 600.0  # last 10 min before first pitch = "closing"

    # ---- de-vig ----------------------------------------------------------- #
    devig_method: str = "multiplicative"  # multiplicative | additive | shin

    # ---- dispersion-band staleness --------------------------------------- #
    # All band math is in log-odds. A book is live while its de-vigged fair sits
    # inside an asymmetric band around the freshness/sharpness-weighted consensus.
    band_k: float = 2.5  # base half-width as a multiple of robust dispersion (s)
    band_asym_a: float = 1.0  # leading-side widen gain  (w_lead  = k*s*(1 + a*|d|))
    band_asym_b: float = 0.6  # trailing-side tighten gain (w_trail = k*s*max(1 - b*|d|, floor_frac))
    band_trail_floor_frac: float = 0.25  # trailing side can't tighten below this fraction of k*s
    band_floor_logodds: float = 0.05  # absolute floor on half-width (anti-flap on micro-noise)
    band_ceiling_logodds: float = 0.80  # absolute ceiling (guardrail on scattered/volatile games)
    drift_window_s: float = 75.0  # window for measuring consensus drift d
    min_books_for_band: int = 3  # below this the band isn't meaningful -> cold-start fallback
    hysteresis_frac: float = 0.15  # must re-enter by this fraction of half-width to rejoin

    # ---- cold-start backstop (the only place wall-clock age survives) ----- #
    cold_start_age_backstop_s: float = 180.0  # 1-2 book regime only

    # ---- failover waterfall (sharpness order) ----------------------------- #
    # First *live* book wins; switch back to Pinnacle the instant it returns live.
    book_priority: list[str] = field(
        default_factory=lambda: ["pinnacle", "betfair_ex", "circa", "__consensus__"]
    )
    # Books whose median forms the "__consensus__" fallback tier:
    consensus_books: list[str] = field(
        default_factory=lambda: ["draftkings", "fanduel", "betmgm", "caesars"]
    )

    # ---- sources ---------------------------------------------------------- #
    mode: str = "mock"  # mock | live
    odds_api_key: str = ""
    odds_api_regions: str = "us,eu"  # eu needed for Pinnacle (Business tier)

    @classmethod
    def from_env(cls) -> "Config":
        c = cls()
        c.window_days = _env_int("MLB_WINDOW_DAYS", c.window_days)
        c.kalshi_poll_s = _env_float("MLB_KALSHI_POLL_S", c.kalshi_poll_s)
        c.odds_poll_s = _env_float("MLB_ODDS_POLL_S", c.odds_poll_s)
        c.emit_interval_s = _env_float("MLB_EMIT_INTERVAL_S", c.emit_interval_s)
        c.devig_method = os.environ.get("MLB_DEVIG_METHOD", c.devig_method)
        c.mode = os.environ.get("MLB_MODE", c.mode)
        c.odds_api_key = os.environ.get("ODDS_API_KEY", c.odds_api_key)
        return c
