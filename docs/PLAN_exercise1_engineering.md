# Exercise 1 — Live Fixture Detection & Fair-Value Service

**Build brief.** This is the spec. Build it phase by phase (phases at the bottom).
Default language **Python 3.11**, `asyncio` + `httpx`, `pydantic` v2. Mock-first with live adapters
behind interfaces so swapping to live is trivial. Everything below that says "justify" is grading
signal — the reasoning is already written here; lift it into `DESIGN.md`.

---

## 0. The core design decision: a canonical schedule "spine"

Neither Kalshi nor the odds feed gives a clean, stable game identifier that survives doubleheaders.
So we do **not** match Kalshi directly to the sportsbook. Instead we anchor both to a third source
that *does* have authoritative game identity: **MLB StatsAPI's schedule endpoint.**

```
GET https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD
    &hydrate=team,probablePitcher,linescore
```

For every game it returns (field names verified):
- `gamePk` — the canonical, immutable game key. **This is our join key.**
- `gameNumber` — 1 or 2 (the doubleheader sequence)
- `doubleHeader` — `"N"` single, `"S"` split-admission, `"Y"` traditional/straight
- `gameDate` — UTC ISO start time
- `teams.home/away.team.{id,name}` — stable MLB team IDs
- `teams.home/away.leagueRecord.{wins,losses,pct}` — **free input for Exercise 2**
- `status.detailedState` — Scheduled / Warmup / In Progress / Suspended / Final …
- `scheduledInnings` (usually 9; 7 for some DH games historically — read it, don't assume)

We pull a **rolling 6-day window** (today + 5 days; Kalshi lists ≤ ~5 days out) and refresh it on a
slow cadence (every 3–5 min — the schedule barely changes). The registry of active fixtures is keyed
by `gamePk`. Kalshi markets get mapped → `gamePk`; sportsbook games get mapped → `gamePk`; the fair
engine joins them on `gamePk`. Doubleheaders, suspensions, and start-time drift all become
properties of the spine row rather than parsing problems.

---

## 1. Fixture detection (Kalshi)

**API.** Public read, no auth. Base `https://api.elections.kalshi.com/trade-api/v2`
(the "elections" subdomain serves *all* markets). Hierarchy is **Series → Event → Market**.

- Discover the MLB series ticker(s) at startup via `GET /series?category=Sports` (or the search
  endpoint) and filter by title/tags. **Do not hardcode or parse tickers** — Kalshi's own docs warn
  ticker structure is not a contract. Cache the resolved series ticker(s).
- Poll `GET /events?series_ticker=<MLB>&with_nested_markets=true&status=open` (plus `unopened`/
  `initialized` so we see markets *before* they open for trading). Use `min_updated_ts` to poll for
  *changes* efficiently.

**Two distinct events to track per market:**
1. **Listed** — the event/market first appears (`status: initialized`, `created_time` set).
2. **Open for trading** — `status` → open / `open_time` reached. **Queue priority is about being
   ready at `open_time`**, so we want the fair computed and warm *before* this transition.

**Cadence + justification (for DESIGN.md).** Poll the MLB markets list every **2 seconds**.
Expected detection latency ≈ interval/2 ≈ 1s, worst case 2s. New-fixture listings are bursty (a slate
gets added at once), so a steady 2s poll is far under any sane rate limit while keeping detection lag
~1s. Since the spine already tells us *which* games should list in the next 5 days, optionally tighten
to **1s** during the daily batch window when we expect new `gamePk`s to appear on Kalshi, and relax
to 5–10s overnight. Kalshi has a WebSocket for *price* data, but new-market *listing* push isn't
reliably exposed — so we **poll for discovery**, then (optionally) subscribe WS for the order book
once a ticker is known. Detection lag is treated as a real cost: every poll logs `detected_at` vs the
market's `created_time`/`open_time` so latency is measurable.

**Map Kalshi market → `gamePk` (the highest-risk join — everything below fails safe to "unmatched,
emit nothing" rather than a wrong map).** We never match Kalshi to the sportsbook; both sides join to
the spine independently. Identity comes from **fields, never the ticker**:

- `yes_sub_title` / `no_sub_title` — for a moneyline these *are* the two team names (the YES/NO
  outcomes). Gives the team pair with no parsing.
- `strike_date` — date/time anchor.
- `rules_primary` — settlement-rules prose; the most precise identifier, and for a DH usually names the
  specific game or its official start.
- `product_metadata` — `{}` in the generic schema, but **dump a real MLB event first**: if Kalshi puts
  structured team/date/game-number fields here, key off that and most of the fuzzy logic disappears.

Pipeline:
1. **Build the Kalshi key.** Resolve `yes_sub_title`/`no_sub_title` → MLB `team_id`s via the alias
   table → an *unordered* team pair. Convert `strike_date` to the game's **local calendar date** (a
   7pm ET game is tomorrow in UTC; "same calendar day" for DHs is defined in local time — getting this
   wrong mis-blocks).
2. **Block against the spine** on `(local_date, {team_a_id, team_b_id})`. One spine game → assign its
   `gamePk`. Two → DH, see §4. Zero → **pending queue**, retry next cycle. Since we pull the schedule 6
   days out, the spine should already contain the game, so an unmatched Kalshi market is almost always
   an alias miss or refresh lag — make it **loud**, not silent.
3. **Resolve the YES side → team.** Take home/away from the **spine** (authoritative; never infer from
   Kalshi — this also handles rare `reverseHomeAwayStatus` makeups). But the contract pays on a specific
   team, so map `yes_sub_title` → `team_id` to decide whether `fair_yes` = `fair_home` or `fair_away`.
   **Skipping this posts a correct fair on the wrong side of the contract.**
4. **Cache** the `event_ticker ↔ gamePk` (+ YES-side) binding once it passes a confidence check; re-derive
   only on a cheap periodic re-validation, not every poll.

Fragile surfaces (all fail safe to unmatched): the **alias table** (watch the Athletics naming mess and
Kalshi abbreviations), **local-date normalization**, and the **assumption that `strike_date` is the
start time** — verify against a live MLB event before trusting; it may be a resolution timestamp.

---

## 2. Fair-value output

For each fixture with a Kalshi market, **every 60s until first pitch** emit one record:

```json
{
  "gamePk": 778123,
  "kalshi_event_ticker": "…",
  "home_team": "…", "away_team": "…",
  "game_number": 1,
  "emit_ts": "2026-06-25T17:30:00Z",
  "fair_home": 0.5412,
  "fair_away": 0.4588,
  "source_book": "pinnacle",
  "live_book_count": 4,
  "consensus_logodds": 0.165,
  "band_logodds": [-0.21, 0.19]   // [trailing edge, leading edge] used this tick
}
```
or, when no usable book exists:
```json
{ "gamePk": 778123, "emit_ts": "…", "fair": "no sportsbook fair", "reason": "all_books_stale" }
```

**Decouple polling from emitting.** The odds poller refreshes book quotes every ~20s into a cache;
the emitter runs on a wall-clock-aligned 60s tick, reads the latest cached fair per fixture, applies
the staleness rules (§3), and emits. Stop emitting for a `gamePk` when `status.detailedState` leaves
the pre-game set (Scheduled/Pre-Game/Warmup) → first pitch reached.

**De-vig math.** American odds → implied probability:
- `price > 0`: `p = 100 / (price + 100)`
- `price < 0`: `p = (-price) / ((-price) + 100)`
- decimal: `p = 1 / decimal`

Two-way **multiplicative** normalization (default):
```
p_home = imp_home / (imp_home + imp_away)
p_away = imp_away / (imp_home + imp_away)
overround = imp_home + imp_away - 1
```
Make the method **pluggable** (`devig.py`: `multiplicative | additive | shin`). Note in DESIGN.md:
for a 2-way, low-hold book like Pinnacle the methods differ by only tens of bps, but multiplicative
slightly over-weights the favorite; Shin and additive reduce favorite–longshot bias. Default
multiplicative; expose the flag.

---

## 3. Book selection, failover, and staleness

**Odds source.** Default **The Odds API v4** behind an `OddsSource` protocol:
```
GET https://api.the-odds-api.com/v4/sports/baseball_mlb/odds
    ?regions=us,eu&markets=h2h&oddsFormat=american&apiKey=…
```
Each event: `id`, `commence_time`, `home_team`, `away_team`, `bookmakers[]` →
`{key, title, last_update, markets[].h2h.outcomes[{name, price}]}`. **Pinnacle** lives in the `eu`
region (Business tier). Crucially there's a **per-bookmaker and per-market `last_update`** — that's
our staleness clock. The Odds API does **not** expose a game number, so DH disambiguation rides on
the spine (§4). (Alternatives to mention in DESIGN.md but not build: OpticOdds, which exposes a
stable `fixture_id`; Betfair Exchange `h2h_lay` as an ultra-sharp back/lay midpoint.)

**Failover waterfall (configurable; default order + justification).**
1. **Pinnacle** — sharpest, lowest vig, the reference de-vig source. Spec-mandated priority.
2. **Betfair Exchange** (back/lay midpoint) — no house margin, sharp.
3. **Circa** — sharp US book, high limits, originates lines.
4. **Soft-book consensus** — median de-vigged fair of {DraftKings, FanDuel, BetMGM, Caesars}. Liquid
   but softer; used only as a last resort, and as a *cross-check* for frozen-book detection.

Walk the list; use the first book that is **live** (see staleness below). The moment Pinnacle returns
live, **switch back to it** immediately. Always stamp `source_book` so a fallback is never silent.

**Definition of stale — dispersion-band, not a clock.** A quote is stale when the *market has moved
away from it*, not when it crosses an arbitrary age. At each odds refresh we build a reference from
the currently-live books and eject any book that falls outside a band around it. Age never gates;
it only **weights** (fresher quotes count more toward the reference). All math is in **log-odds**
(`logit(devigged_fair)`) so the band doesn't collapse on heavy favorites and behaves consistently
across pick'ems and lopsided games. Present results back in probability.

Per refresh:
1. **Center** `c` = freshness- and sharpness-weighted robust consensus (weighted median, or a
   weighted mean with robust down-weighting) of the live books' log-odds fairs. Robust so a couple of
   laggards can't define "normal." Pinnacle / Betfair anchor; soft books inform.
2. **Scale** `s` = robust dispersion of the live books' log-odds fairs — `1.4826 × MAD` (or IQR-based).
   This is the "reasonable range" derived from *live* dispersion: wide when books genuinely scatter on
   a volatile game, tight when they're clustered. Self-calibrating, no magic width.
3. **Drift** `δ` = change in `c` over a short trailing window (~60–90s), normalized by `s` → a unitless
   "how strong is the move, and which way."
4. **Asymmetric half-widths** (this is the non-symmetric part — keyed to the move, not a fixed side):
   - *trailing side* (opposite the drift — where a laggard falls): `w_trail = k·s · max(1 − b·|δ̂|, floor)`
     → **tightens** as the move strengthens, so a book lagging a trend is flagged fast.
   - *leading side* (direction of the drift): `w_lead = k·s · (1 + a·|δ̂|)` → **loosens**, because a book
     out ahead of the move is the freshest signal, not stale.
   - when `δ ≈ 0` (flat market) both collapse to `k·s` → **symmetric**: an offset is just noise, treat
     both sides alike.
   A book is live while `c − w_trail ≤ f_i ≤ c + w_lead` (mapping trailing/leading to the literal
   side opposite/with the drift).
5. **Guardrails.** `k·s` is floored (band can't collapse to zero and flap on micro-noise) and ceilinged
   (on a wildly scattered game it can't blow out to where staleness is meaningless — guardrail, not the
   mechanism; generous and tunable). **Hysteresis:** a book exits at the band edge but must re-enter by
   a small margin to rejoin, so it doesn't flap in and out.

**Cold start (`< 3` live books)** — the band isn't meaningful yet:
- 1 book → it *is* the fair, trivially live.
- 2 books → can't tell which is the outlier; take best-available (Pinnacle priority) with a **loose
  absolute-age backstop** as the only safety net. The age cap survives *only* here, as a degenerate-case
  floor — never as the main gate.
- The band engages at **≥ 3 live books**.

**Why this is better:** it catches the frozen book for free (a book stamping fake-fresh timestamps
drifts off `c` as the market moves and gets ejected — no separate guard needed), catches a book
lagging a real move (asymmetric trailing tighten), and catches a plain bad price. A coordinated
market-wide move re-centers `c` on the leaders and only true laggards fall out the trailing side, so
genuine repricing doesn't trigger a false-stale cascade.

**Known blind spot (state it in DESIGN.md):** staleness is defined *relative to consensus*, so a
**synchronized error** — every book wrong together — won't flag, since nothing falls outside the band.
That's acceptable for a quoting service (if every sharp book agrees, that consensus *is* the best fair),
but it's the one case this method structurally cannot catch.

If **no** book is live → emit `"no sportsbook fair"`. There is **no "degraded but emit anyway" tier** —
for queue-priority quoting, publishing a fair we've flagged as untrustworthy is how we get picked off.
The posture is binary: the best live book, or pull.

A book dropping mid-session must never crash the service or silently serve stale: wrap each source in
try/except, time out requests, keep last-good per book, and let the band + provenance stamp do the rest.

---

## 4. Doubleheader handling (the part naive builds break on)

The spine already labels DHs. Algorithm:

1. Build block key `(local_date, home_id, away_id)`. One spine game → single fixture, done.
2. Two spine games → DH. Assign each Kalshi market and each sportsbook event to a `gameNumber`,
   strongest signal first:
   - **Explicit game number:** regex `sub_title` / `rules_primary` for "Game 2" / "G2" (and check
     `product_metadata`). Map directly when present.
   - **Ordinal start-time match (fallback):** rank the (two) Kalshi markets by `strike_date`, rank the
     two spine games by `gameNumber`, pair in order. Same on the sportsbook side using `commence_time`.
   - For **split-admission** (`doubleHeader: "S"`) the two starts are hours apart (1pm / 7pm), so this
     is robust.
   - For **traditional/straight** (`doubleHeader: "Y"`) G2 starts ~30–45 min after G1 *ends*, so G2's
     absolute start **drifts**. Use **ordinal order only** — never absolute-time proximity.
   - **Bijection guard:** the two Kalshi markets must map one-to-one onto the two `gamePk`s. If the
     ordering is ambiguous (e.g. both markets still carry G1's `strike_date` because G2 isn't firm yet),
     **do not guess** — hold both unmapped and emit nothing until a disambiguating signal arrives.
     Cross-mapping G1's fair onto the G2 contract is the catastrophic error this whole design exists to
     prevent; a minute of silence is cheap by comparison.
3. **Suspended-then-resumed:** the spine keeps the **same `gamePk`** across suspension/resumption and
   reflects it in `status` (Suspended → Resumed). Never spawn a new fixture for a resumption; keep the
   original `gamePk` mapping. If a suspended game resumes as part of a later DH, the spine shows it —
   which is exactly why we key on `gamePk`, not `(teams, date)`.
4. **Start-time drift generally:** refresh the spine every few minutes so rain delays / G2 float
   propagate. Use `status.detailedState` (not the clock) to decide when to stop emitting.
5. **`reverseHomeAwayStatus`** (makeup at the other park) and similar: always read home/away from the
   spine, never infer.

---

## 5. Architecture, stack, file tree

- Python 3.11, `asyncio`, `httpx` (async), `pydantic` v2 models, `tenacity` for retries.
- In-memory registry + a JSONL audit log of every emit (`emits.jsonl`) and every detection latency.
- **Mock-first:** every external source is a `Protocol` with a `Live*` impl and a `Mock*` impl driven
  by recorded JSON fixtures. `--mode {mock,live}` flag. Mocks must use the **exact same schema** as
  live so swapping is a one-line change.

```
mlb-fair-service/
  README.md
  DESIGN.md                      # the ≤1-page design note (content drafted in §6)
  pyproject.toml
  .env.example                   # ODDS_API_KEY, etc.
  src/mlb_fair/
    config.py                    # thresholds, cadences, waterfall order
    models.py                    # Fixture, BookQuote, Fair, EmitRecord (pydantic)
    spine/
      statsapi.py                # schedule client (Live) + registry build
      mock.py
    kalshi/
      client.py                  # listing poller (Live) + series discovery
      mock.py
      mapping.py                 # kalshi event -> gamePk
      team_aliases.json
    odds/
      base.py                    # OddsSource Protocol
      the_odds_api.py            # Live
      mock.py
      devig.py                   # multiplicative | additive | shin
      selection.py               # waterfall + dispersion-band staleness (consensus, scale, asymmetric drift, hysteresis, cold-start)
    engine/
      registry.py                # gamePk-keyed fixture store + lifecycle
      fair_engine.py             # produce fair per fixture from selected book
      scheduler.py               # 60s wall-clock-aligned emit loop
    emit/
      sink.py                    # stdout JSON + JSONL (pluggable)
    main.py                      # wires pollers + scheduler; --mode flag
  data/
    mock_schedule.json
    mock_kalshi_events.json
    mock_odds.json
    mock_doubleheader.json       # a split + a traditional DH, plus a suspension
  tests/
    test_devig.py
    test_selection_failover.py   # pinnacle down -> betfair -> circa -> consensus -> back to pinnacle
    test_staleness.py            # band: frozen book ejected on market move, trend-laggard flagged on trailing side, cold-start fallback, hysteresis
    test_mapping_doubleheader.py # S vs Y, ordinal mapping, bijection guard refuses ambiguous, YES-side resolves to correct team, suspension keeps gamePk
    test_emit_no_fair.py
```

**Mock schemas to bundle** (keep identical to live):
- *spine*: list of `{gamePk, gameNumber, doubleHeader, gameDate, teams{home,away{id,name,record}}, status, scheduledInnings}`
- *kalshi*: `{events:[{event_ticker, series_ticker, title, sub_title, strike_date, markets:[{ticker, status, open_time, created_time, updated_time}]}]}`
- *odds*: The Odds API v4 shape above.

---

## 6. DESIGN.md content (draft — lift directly)

> **Detection cadence.** Poll Kalshi's MLB markets list every 2s (1s during the daily listing
> window), giving ~1s expected / 2s worst-case detection latency. Listings are bursty and the request
> cost is negligible against the queue-position edge; we measure latency by logging detection time vs
> the market's `created_time`/`open_time`.
>
> **Failover ordering.** Pinnacle → Betfair Exchange → Circa → soft-book consensus, ordered by
> sharpness (limits taken, hold, line origination). First fresh book wins; we switch back to Pinnacle
> the instant it returns fresh; the sourcing book is stamped on every emit.
>
> **Stale.** Defined relative to the live market, not a clock. Each refresh we build a
> freshness/sharpness-weighted robust consensus `c` of the live books (in log-odds) and a band whose
> width is a robust dispersion (MAD/IQR) of those same books — so the tolerance self-scales with how
> much books actually disagree. The band is **non-symmetric**, keyed to the consensus drift: during a
> move it tightens on the trailing side (laggards flagged fast) and loosens on the leading side (a book
> out ahead is the freshest signal); flat market → symmetric. A book outside its band is stale and
> drops out (with hysteresis on re-entry). This catches frozen books, trend-laggards, and bad prices in
> one rule; age only weights, never gates. Below 3 live books the band isn't meaningful → best-available
> + a loose age backstop. No book live → `"no sportsbook fair"` (no "degraded but emit" tier — we
> publish a book we stand behind or we pull). Blind spot: a synchronized error across all books won't
> flag, since nothing falls outside consensus.
>
> **Kalshi → sportsbook mapping.** We never join Kalshi to the book directly. Both are mapped to MLB
> StatsAPI's `gamePk` from *fields* (never the ticker): team pair from `yes/no_sub_title` via an alias
> table, date from `strike_date` normalized to local calendar date, blocked on `(date, teams)`. For
> doubleheaders we assign `gameNumber` by explicit text (`sub_title`/`rules_primary`) then ordinal
> start-time order (absolute G2 time is unreliable for straight DHs), with a one-to-one bijection guard
> that refuses to guess — unmapped fixtures emit nothing rather than risk cross-mapping G1's fair onto
> the G2 contract. Home/away always comes from the spine; the YES side is resolved separately so the
> fair is posted on the correct team. `gamePk` is immutable across suspensions, so resumed games never
> duplicate.

---

## 7. Build phases

- **P0** scaffold: repo, `pyproject`, config, pydantic models, all Protocols, mock JSON fixtures.
- **P1** spine client + registry (gamePk-keyed), rolling 6-day window.
- **P2** Kalshi client + series discovery + `mapping.py`. **First** dump a real MLB event/market to
  confirm `strike_date` semantics, `sub_title`/`rules_primary` content, and `product_metadata`, then
  build the field-level join (team-pair block, local-date, YES-side resolution, DH bijection guard)
  against mocks.
- **P3** odds client + `devig.py` + `selection.py` (waterfall, staleness, frozen-book).
- **P4** fair engine + scheduler + emit sink; `main.py` wiring; `--mode mock` runs end-to-end.
- **P5** tests (failover, staleness, DH, no-fair); README + DESIGN.md.

Acceptance: `python -m mlb_fair.main --mode mock` runs a simulated slate including one split-admission
DH, one straight DH, one mid-session Pinnacle outage, and one all-books-stale window, emitting once
per minute per fixture with correct provenance and at least one `"no sportsbook fair"`.
