# MLB Fair-Value Service

Live MLB fixture detection + sportsbook fair-value service for Kalshi market making
(Exercise 1 of a trading take-home). Detects new MLB fixtures the moment they list on
Kalshi and emits a de-vigged sportsbook fair per fixture, once per minute, until first pitch.

Full spec: @docs/PLAN_exercise1_engineering.md
Assignment: `docs/assignment.pdf` (read if you need the original wording)

## Commands
- Install: `pip install -e ".[dev]"`
- Test: `pytest -q`  (YOU MUST keep tests green before moving to the next phase)
- Run (mock end-to-end): `python -m mlb_fair.main --mode mock`
- Run (live): `python -m mlb_fair.main --mode live`  (needs `ODDS_API_KEY`)

## Architecture — the gamePk "spine"
We never match Kalshi directly to the sportsbook. Both map independently onto MLB
StatsAPI's `gamePk` (the canonical, immutable game key, which carries `gameNumber` +
`doubleHeader` natively). The engine joins on `gamePk`.

Layout (`src/mlb_fair/`):
- `models.py` — shared pydantic types (SpineGame, KalshiEvent/Market, BookQuote, DevigFair, EmitRecord)
- `config.py` — all tunables: cadences, band params, failover order
- `spine/` — StatsAPI client (live) + mock; shared parser. DONE.
- `engine/registry.py` — gamePk-keyed fixture store + block index + lifecycle. DONE.
- `odds/devig.py` — american→fair, 3 methods (multiplicative default). DONE.
- `kalshi/` — listing poller + field-level mapping to gamePk. TODO (P2).
- `odds/selection.py` — dispersion-band staleness + failover waterfall. TODO (P3b).
- `engine/fair_engine.py`, `engine/scheduler.py`, `emit/sink.py` — TODO (P4).

## Load-bearing invariants — IMPORTANT, do not "simplify" these away
- Identity comes from **fields, never Kalshi tickers** (their docs say ticker structure isn't a contract).
- Home/away is read from the **spine only**; the Kalshi YES side is resolved **separately** to a team
  so the fair posts on the correct contract side. These are two different joins — don't conflate them.
- Doubleheaders: assign gameNumber by explicit text → ordinal start-time order (NEVER absolute G2 time
  for traditional DHs). A **bijection guard** must refuse to guess on ambiguous DH ordering — emit
  nothing rather than risk cross-mapping G1's fair onto the G2 contract.
- Every uncertain match **fails safe to "unmatched, emit nothing,"** never to a guess.
- **Staleness is the dispersion band, NOT a wall-clock age.** A book is live while its de-vigged fair
  (in log-odds) sits inside an asymmetric band around the freshness/sharpness-weighted consensus of the
  live books. Width = robust dispersion (MAD/IQR) of live books × `band_k`. The band is **non-symmetric**,
  keyed to consensus drift: trailing side tightens, leading side loosens, symmetric when flat. Age only
  **weights** the consensus, it never gates. There is **NO "degraded but emit" tier** — best live book or
  "no sportsbook fair". Below 3 live books, fall back to best-available + a loose age backstop only.
  Do not re-introduce a frozen-book guard — the band subsumes it.
- Emit cadence is 60s (spec). Odds polling runs strictly faster (~20s) so we never discover a dead book
  at quote time. These are decoupled loops.

## Build status (work phases in order)
- DONE: P0 scaffold/models/config, P1 spine+registry, P3a devig. 11 tests passing.
- NEXT: **P2** Kalshi mapping. First dump a real Kalshi MLB event to confirm `strike_date` semantics
  and what's in `product_metadata`, then build the field-level join against mocks.
- THEN: P3b selection/band → P4 engine+scheduler+emit → P5 tests + README + DESIGN.md.

## Demo UI
- An interactive demo lives in `webapp/` (FastAPI over the real pipeline) + `api/`+`vercel.json`
  for Vercel. **Before editing the webapp, read `webapp/README.md`** — it maps every UI element
  to the code that produces it and lists where to make common changes. Keep that doc in sync with
  any UI change (same edit). The UI must always call the real `map_events`/`devig`, never hardcode.

## Conventions
- Python 3.11+, asyncio + httpx, pydantic v2. Mock-first: every external source is a Protocol with a
  Live* and Mock* impl using the **same schema**, so live swap is one line.
- Add a test with each module; commit per phase (clean diffs / rollback points).
- The DESIGN.md reasoning is already drafted inside @docs/PLAN_exercise1_engineering.md §6 — lift it,
  don't reinvent it.
