# MLB Fair-Value Service

Live MLB fixture detection & sportsbook fair-value service for Kalshi market making.
Detects new MLB fixtures as they list on Kalshi and emits a de-vigged sportsbook fair
for each, once per minute, until first pitch.

See `docs/PLAN_exercise1_engineering.md` for the full design and `DEVELOPMENT.md` for the
current build status.

## Setup

```bash
pip install -e ".[dev]"
```

## Run

```bash
# mock end-to-end (no API keys needed): a simulated slate with a split DH, a straight
# DH, a mid-session Pinnacle outage (failover + switch-back), and an all-books-stale
# window emitting "no sportsbook fair". Emits to stdout + emits.jsonl.
python -m mlb_fair.main --mode mock
python -m mlb_fair.main --mode mock --ticks 6 --interval 1   # bounded run

# live (StatsAPI + Kalshi are public; sportsbook odds need an OpticOdds key)
export OPTIC_ODDS_API_KEY=...   # PowerShell: $env:OPTIC_ODDS_API_KEY="..."  (or a .env file)
python -m mlb_fair.main --mode live --ticks 1
```

Each emit carries provenance: `source_book`, `live_book_count`, `consensus_logodds`,
and the `band_logodds` band used that tick (see `DESIGN.md`).

## Demo UI

An interactive page that runs the mock slate through the real pipeline (gamePk
spine → Kalshi field-level mapping → two-way de-vig). Edit any moneyline to watch
its fair recompute; doubleheaders show their disambiguation confidence
(`exact` / `ordinal` / `text`) and the fail-safe (`pending` / `unmatched`) cases.

```bash
pip install -e ".[web]"
python -m uvicorn webapp:app --reload      # -> http://localhost:8000
```

Deploy to Vercel (config in `vercel.json` + `api/index.py`, deps in `requirements.txt`):

```bash
npm i -g vercel    # if needed
vercel             # first run links/creates the project; deploys a preview
vercel --prod      # promote to production
```

## Test

```bash
pytest -q
```

## Status

All phases implemented end-to-end (P0–P5): gamePk spine + registry, Kalshi → gamePk
mapping (DH bijection + YES-side), de-vig, the dispersion-band staleness + failover
selector, and the fair engine + 60s emit scheduler + JSONL sink, wired in `main.py`.
Live adapters: StatsAPI, Kalshi, OpticOdds. 47 tests passing. See `DESIGN.md` for the
reasoning and `DEVELOPMENT.md` for the phase map. (Rough-draft quality — band
parameters and the live emit loop are tuned for the mock acceptance run.)
