# Demo UI — reference

This is the source of truth for the interactive demo. **Read this before editing the
webapp** so changes land in the right place and stay consistent. When you change the
UI, update this file in the same edit.

## What the demo is

A thin FastAPI layer over the *real* service code (no logic is reimplemented for the
demo). It runs the mock slate through the actual pipeline and serves a single page:

```
mock fixtures ──▶ spine + registry ──▶ Kalshi field-level mapping ──▶ two-way de-vig
 (data/*.json)     (engine/registry)     (kalshi/mapping.py)           (odds/devig.py)
                                   │
                          webapp/app.py  build_slate()  ──▶  GET /api/slate (JSON)
                                   │
                          webapp/static/index.html  ──▶  renders + calls GET /api/devig live
```

## File map

| File | Role | Edit here when… |
|------|------|-----------------|
| `webapp/app.py` | FastAPI app: `build_slate()`, routes `/api/slate`, `/api/devig`, `/` | changing what data the UI receives, default odds, adding an endpoint |
| `webapp/static/index.html` | the whole frontend (inline CSS + vanilla JS, no build step) | any visual / interaction change |
| `webapp/__init__.py` | re-exports `app` so `uvicorn webapp:app` works | rarely |
| `api/index.py` | Vercel ASGI entry (adds `src/` + root to `sys.path`, imports `app`) | only for deploy-path issues |
| `vercel.json` | rewrites all routes → the function; `includeFiles` ships `src/`,`webapp/`,`data/` | adding bundled files/dirs |
| `requirements.txt` | Vercel runtime deps (fastapi, pydantic, tzdata) | adding a runtime dep used by the web path |

There is **no separate JS/CSS file** — it's all inline in `index.html`. Keep it that way
(zero build step is deliberate for an easy demo).

## Backend: `webapp/app.py`

- **`build_slate()`** — runs `MockSchedule` → `FixtureRegistry.upsert_spine` → `MockKalshiEvents`
  → `map_events` → `apply`, then shapes the result. Returns:
  ```jsonc
  {
    "slate_date": "2026-06-25",
    "spine_count": 6, "events_count": 8, "bound_count": 6, "quotable_count": 5,
    "spine":   [ { game_pk, away, home, game_number, double_header, status } ],
    "results": [ <row>, ... ]   // sorted: bound first, then pending, then unmatched
  }
  ```
  A `results` **row** is one Kalshi event's mapping outcome:
  - always: `event_ticker`, `status` (`bound|pending|unmatched`), `reason`, `title`
  - bound only: `game_pk`, `game_number`, `home_team`/`home_id`, `away_team`/`away_id`,
    `double_header` (`single|split-DH|straight-DH`), `spine_status`, `quotable`,
    `confidence` (`exact|ordinal|text`), `yes_team_id` (the representative/home YES side),
    `default_home_price`, `default_away_price` (American odds)
- **`DEFAULT_ODDS`** — seed American moneylines keyed by `game_pk`. Edit to change the
  starting prices shown on each card.
- **`GET /api/devig?home=&away=&method=`** — calls the real `devig()`; returns
  `{home, away, overround, method}` or `{error}` (HTTP 400) on bad input.
- **`SLATE_DATE`** — the single mock day everything is fetched for.

## Frontend: `webapp/static/index.html`

Layout (top → bottom), each tied to the function that builds it:

| UI section | DOM id | Built by |
|------------|--------|----------|
| Title + one-line description | — | static markup |
| Stat chips (date, spine, events, mapped, quotable) | `#chips` | `render()` |
| Controls: de-vig method `<select>` + badge legend | `#method` | static markup |
| **Bound game cards** | `#bound` | `render()` → `boundCard(row)` |
| **Fail-safe section** (pending/unmatched) | `#failsafe` (wrap `#failsafe-wrap`) | `render()` → `failCard(row)` |
| Collapsible spine table | `#spine` | `render()` |
| Footer (design notes + endpoint names) | — | static markup |

Key JS functions:
- **`render()`** — fetches `/api/slate` (the only call on load), fills every section, wires
  inputs, and triggers the first fair computation per card.
- **`boundCard(row)`** — HTML for one bound game: matchup, `gamePk · G#`, DH badge,
  confidence badge, and (if `!quotable`) a `not quotable · <status>` badge. Two `<input>`s
  (`.o-away`, `.o-home`) + a fair-bars block (`.bar-home`, `.bar-away`) + `.hold` line.
  The **YES** tag is placed on whichever side equals `yes_team_id` (currently the home side).
- **`failCard(row)`** — HTML for a pending/unmatched event (title, status badge, reason text).
- **`recompute(card)`** — reads the card's two odds inputs + current method, calls
  `/api/devig`, updates the two bars (width = probability) and the hold/`fair_yes` line.
- **`debounce(fn, ms)`** — 180 ms debounce on odds typing so we don't spam the endpoint.
- Method `<select>` `change` → recompute every card.

### Badge → CSS class map (for restyling)
`exact`→`.b-exact` (green) · `ordinal`→`.b-ordinal` (blue) · `text`→`.b-text` (purple) ·
DH type→`.b-dh` (grey) · not-quotable→`.b-noq` (amber) · pending→`.b-pending` (amber) ·
unmatched→`.b-unmatch` (red). Theme colors are CSS vars in `:root` at the top of `<style>`.

## Common edits — where to go

- **Change starting odds for a game** → `DEFAULT_ODDS` in `app.py`.
- **Add a field to each card** (e.g. venue, start time) → add it to the row dict in
  `build_slate()`, then read it in `boundCard()`.
- **Restyle / re-theme** → CSS vars in `:root`, or the per-component classes in `<style>`.
- **Change which side is YES / show both contracts** → `yes_team_id` logic in `build_slate()`
  and the `tag()`/`homeYes` logic in `boundCard()` + `recompute()`.
- **New endpoint / different data** → add a route in `app.py`, fetch it in `index.html`.
- **Show the ambiguous-DH bijection-guard case** → it isn't in the happy-path mock slate
  (it binds nothing by design); add an ambiguous pair to `data/mock_kalshi_events.json`
  or surface it as a separate canned example.

## Invariants to preserve

- The UI must keep calling the **real** `map_events` / `devig` — never hardcode results.
- Home/away comes from the spine; the YES side is a separate field. Don't conflate them.
- Fail-safe rows (`pending`/`unmatched`) must stay visible — they're the point of the demo.
- No build step / no frontend framework. Inline everything in `index.html`.

## Run / deploy

```bash
pip install -e ".[web]"
python -m uvicorn webapp:app --reload      # http://localhost:8000
vercel ; vercel --prod                      # deploy (config: vercel.json, api/index.py)
```
