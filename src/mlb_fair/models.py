"""Core domain models.

These are the shared types every module speaks. Mock and live sources both
parse their raw payloads into these, so swapping a live source for a mock is a
one-line change and never ripples into the engine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Spine (MLB StatsAPI schedule) — the canonical fixture identity layer.
# --------------------------------------------------------------------------- #

DoubleHeaderType = Literal["N", "S", "Y"]  # None / Split-admission / Traditional


class Team(BaseModel):
    id: int
    name: str


class TeamRecord(BaseModel):
    wins: int = 0
    losses: int = 0
    pct: float = 0.0


# Pre-game lifecycle states during which we still quote. Once a game leaves
# this set (In Progress / Final / etc.) we stop emitting for it.
PREGAME_STATES = {
    "Scheduled",
    "Pre-Game",
    "Warmup",
    "Delayed Start",
    "Delayed",
}


class SpineGame(BaseModel):
    """One authoritative game from the schedule. `game_pk` is the immutable join key."""

    game_pk: int
    game_number: int = 1
    double_header: DoubleHeaderType = "N"
    game_date: datetime  # UTC start (scheduled)
    official_date: str  # local calendar date (YYYY-MM-DD) — authoritative for DH "same day"
    home: Team
    away: Team
    home_record: TeamRecord = Field(default_factory=TeamRecord)
    away_record: TeamRecord = Field(default_factory=TeamRecord)
    status: str = "Scheduled"  # status.detailedState
    scheduled_innings: int = 9
    venue: Optional[str] = None
    reverse_home_away: bool = False

    @property
    def is_doubleheader(self) -> bool:
        return self.double_header in ("S", "Y")

    @property
    def is_pregame(self) -> bool:
        return self.status in PREGAME_STATES

    @property
    def team_pair(self) -> frozenset[int]:
        """Unordered team pair — used for blocking, since home/away can flip on makeups."""
        return frozenset((self.home.id, self.away.id))

    @property
    def block_key(self) -> tuple[str, frozenset[int]]:
        return (self.official_date, self.team_pair)


# --------------------------------------------------------------------------- #
# Kalshi market identity + binding to a gamePk.
# --------------------------------------------------------------------------- #


class KalshiMarketStatus(str, Enum):
    UNOPENED = "unopened"
    INITIALIZED = "initialized"
    OPEN = "open"
    ACTIVE = "active"
    CLOSED = "closed"
    SETTLED = "settled"


class KalshiMarket(BaseModel):
    ticker: str
    event_ticker: str
    status: str = "initialized"
    open_time: Optional[datetime] = None
    close_time: Optional[datetime] = None
    created_time: Optional[datetime] = None
    updated_time: Optional[datetime] = None
    # NOTE (verified against the live KXMLBGAME series, 2026): each game is ONE event
    # with TWO markets, one per team. Within a market `yes_sub_title` and `no_sub_title`
    # are the SAME team — the contract is binary "does THIS team win?". So yes_sub_title
    # is the YES-side team for this contract; it is NOT the opposing pair.
    yes_sub_title: Optional[str] = None
    no_sub_title: Optional[str] = None
    rules_primary: Optional[str] = None
    # `occurrence_datetime` is the real UTC game start (the spine's join anchor on the
    # Kalshi side). Live MLB markets have NO `strike_date`; the *_expiration_time /
    # close_time fields are settlement deadlines days later, never the start time.
    occurrence_datetime: Optional[datetime] = None
    custom_strike: dict = Field(default_factory=dict)  # e.g. {"baseball_team": "<kalshi-uuid>"}
    product_metadata: dict = Field(default_factory=dict)
    # YES-side market prices (probabilities in [0,1]) — Kalshi quotes these in dollars.
    # Used to compare our sportsbook fair against the live contract price (the edge).
    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    last_price: Optional[float] = None


class KalshiEvent(BaseModel):
    event_ticker: str
    series_ticker: str
    title: Optional[str] = None  # "<away> vs <home>" — the authoritative team pair
    sub_title: Optional[str] = None  # "<AWY> vs <HOM> (Mon DD)"
    strike_date: Optional[datetime] = None  # absent on live MLB; kept for schema parity
    product_metadata: dict = Field(default_factory=dict)  # {"competition","competition_scope"}
    markets: list[KalshiMarket] = Field(default_factory=list)


class FixtureBinding(BaseModel):
    """Resolved link between a Kalshi event and a spine game.

    One event -> one gamePk, but the event carries two per-team contracts, so
    `market_yes` records every market_ticker -> yes_team_id. The singular
    `market_ticker`/`yes_team_id` are the representative (home-team) contract.
    """

    event_ticker: str
    market_ticker: str  # representative (home-team) market
    game_pk: int
    game_number: int
    yes_team_id: int  # which team the YES contract pays on -> picks fair_home vs fair_away
    market_yes: dict[str, int] = Field(default_factory=dict)  # market_ticker -> yes_team_id (both)
    confidence: str = "exact"  # exact | ordinal | text
    bound_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --------------------------------------------------------------------------- #
# Odds + fair value.
# --------------------------------------------------------------------------- #


class BookQuote(BaseModel):
    """A single book's two-way moneyline for one game, as fetched."""

    book: str
    game_pk: int
    home_price: float  # American odds
    away_price: float
    last_update: datetime
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DevigFair(BaseModel):
    home: float
    away: float
    overround: float
    method: str


class EmitRecord(BaseModel):
    """What the service publishes once per minute per fixture."""

    game_pk: int
    kalshi_market_ticker: Optional[str] = None
    home_team: str
    away_team: str
    game_number: int = 1
    emit_ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Populated when we have a fair:
    fair_home: Optional[float] = None
    fair_away: Optional[float] = None
    fair_yes: Optional[float] = None  # fair on the YES contract's team
    source_book: Optional[str] = None
    live_book_count: Optional[int] = None
    consensus_logodds: Optional[float] = None
    band_logodds: Optional[tuple[float, float]] = None  # (trailing edge, leading edge)

    # Populated when we don't:
    no_fair: bool = False
    reason: Optional[str] = None

    def to_line(self) -> dict:
        d = self.model_dump(mode="json", exclude_none=True)
        if self.no_fair:
            d["fair"] = "no sportsbook fair"
        return d
