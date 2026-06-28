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
# mock end-to-end (no API keys needed)
python -m mlb_fair.main --mode mock

# live (requires an odds aggregator key)
export ODDS_API_KEY=...
python -m mlb_fair.main --mode live
```

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

Foundation complete and tested (spine + registry + de-vig). Kalshi mapping, the
dispersion-band staleness/failover engine, and the emit scheduler are in progress —
see the phase checklist in `DEVELOPMENT.md`.
