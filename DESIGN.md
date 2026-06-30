# DESIGN — MLB Fair-Value Service

A live fixture-detection + sportsbook fair-value service for Kalshi market making. It
detects new MLB fixtures as they list on Kalshi and emits a de-vigged sportsbook fair
per fixture, once per minute, until first pitch.

## The core decision: a canonical schedule "spine"

Neither Kalshi nor the odds feed exposes a stable game identifier that survives
doubleheaders, so we **never match Kalshi to the sportsbook directly**. We anchor both
to a third source with authoritative identity — **MLB StatsAPI's schedule** — and key
everything on its immutable `gamePk`. Kalshi markets map → `gamePk`; sportsbook events
map → `gamePk`; the engine joins on `gamePk`. Doubleheaders, suspensions, and start-time
drift become properties of a spine row rather than parsing problems (a suspended game
keeps its `gamePk`, so a resumption never duplicates). Every uncertain match **fails safe
to "unmatched, emit nothing,"** never to a guess.

## Detection cadence

Poll Kalshi's MLB markets list every ~2s (tighter during the daily listing window),
giving ~1s expected / 2s worst-case detection latency. Listings are bursty and request
cost is negligible against the queue-position edge; latency is measured by logging
detection time vs the market's `created_time`/`open_time`. We poll for *discovery*
(listing push isn't reliably exposed) and decouple it from emission.

## Kalshi → gamePk mapping (highest-risk join)

Identity comes from **fields, never tickers** (Kalshi's docs warn ticker structure isn't
a contract). Verified against the live `KXMLBGAME` series: each game is **one event with
two per-team markets**, there is **no `strike_date`** (the start anchor is
`occurrence_datetime`), and `product_metadata` carries no structured identity. So: team
pair from the event `title`/`sub_title` via an alias table; date from `occurrence_datetime`
normalized to the **league-local calendar day**; block on `(date, team-pair)`. Home/away
is read from the **spine**; the YES side is resolved **separately** (each market's
`yes_sub_title` → team) so the fair posts on the correct contract. Doubleheaders assign
`gameNumber` by explicit "Game N" text, then **ordinal start-time order** (absolute G2
time is unreliable for straight DHs), behind a **one-to-one bijection guard** that refuses
to guess — unmapped fixtures emit nothing rather than risk cross-mapping G1's fair onto
the G2 contract.

## De-vig

American → implied probability, then two-way normalization. Default **multiplicative**;
`additive` and `shin` are pluggable. For a low-hold two-way book the methods differ by tens
of bps, but multiplicative slightly over-weights the favorite while Shin/additive reduce
favorite–longshot bias.

## Failover waterfall

**Pinnacle → Betfair Exchange → Circa → soft-book consensus**, ordered by sharpness (limits
taken, hold, line origination). Walk the list; use the first **live** book; switch back to
Pinnacle the instant it returns live. The sourcing book is stamped on every emit, so a
fallback is never silent.

## Stale — the dispersion band, not a clock

A quote is stale when **the market has moved away from it**, not when it crosses an arbitrary
age. All math is in log-odds so the band doesn't collapse on heavy favorites. Each refresh:

1. **Center** `c` — a freshness/sharpness-weighted **robust** consensus (weighted median) of
   the live books' log-odds fairs. Pinnacle/Betfair anchor; soft books inform.
2. **Scale** `s` — robust dispersion (`1.4826 × MAD`) of those same books: wide when books
   genuinely scatter, tight when clustered. Self-calibrating, no magic width.
3. **Drift** `δ̂` — normalized change in `c` over a short trailing window.
4. **Asymmetric half-widths**, keyed to the move: the **trailing** side (where a laggard
   falls) tightens (`w_trail = k·s·max(1−b·|δ̂|, floor)`); the **leading** side loosens
   (`w_lead = k·s·(1+a·|δ̂|)`); a flat market is symmetric. A book is live while
   `c − w_trail ≤ f_i ≤ c + w_lead`. `k·s` is floored (anti-flap) and ceilinged (guardrail),
   with **hysteresis** on re-entry.

Age only **weights** the consensus — it never gates. This one rule catches the frozen book
(it drifts off `c` as the market moves and is ejected — no separate guard), the trend-laggard
(trailing tighten), and the plain bad price. Below 3 live books the band isn't meaningful →
best-available (Pinnacle priority) + a loose absolute-age backstop (the only place wall-clock
age survives). No live book → `"no sportsbook fair"`. The posture is **binary**: the best live
book, or pull — publishing a fair we've flagged untrustworthy is how we get picked off.

**Known blind spot:** staleness is defined *relative to consensus*, so a synchronized error
(every book wrong together) won't flag. Acceptable for a quoting service — if every sharp book
agrees, that consensus *is* the best fair — but it's the one case this method structurally
cannot catch.

## Emit

Polling is decoupled from emitting: the odds poller refreshes book quotes every ~20s into a
cache (so we never discover a dead book at quote time); the emitter runs on a wall-clock 60s
tick, reads the latest cached fair, applies the band, and emits one record per fixture (with
`source_book`, `live_book_count`, `consensus_logodds`, `band_logodds`) until `status` leaves
the pre-game set. Every emit is appended to a JSONL audit log.

## Stack

Python 3.11, `asyncio` + `httpx`, `pydantic` v2. **Mock-first**: every external source is a
`Protocol` with a `Live*` and `Mock*` impl on the **same schema**, so live swap is one line
(`--mode {mock,live}`). Live adapters: StatsAPI (spine), Kalshi listing API, and OpticOdds
(sportsbook). Acceptance: `python -m mlb_fair.main --mode mock` runs a simulated slate — a
split DH, a straight DH, a mid-session Pinnacle outage (failover + switch-back), and an
all-books-stale window emitting `"no sportsbook fair"`.
