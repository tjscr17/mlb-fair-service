# Demo UI — reference

This is the source of truth for the interactive demo. **Read this before editing the
webapp** so changes land in the right place and stay consistent. When you change the
UI, update this file in the same edit.

## What the demo is

A thin FastAPI layer over the *real* service code (no logic is reimplemented for the
demo). It runs the mock slate through the actual pipeline and serves a single page:

```
mock fixtures ──▶ spine + registry ──▶ Kalshi mapping ──▶ gamePk join ◀── odds mapping ◀── mock odds
 (data/*.json)     (engine/registry)    (kalshi/mapping)   (both sides)    (odds/mapping)   (data/mock_odds.json)
                                   │                                  │
                          webapp/app.py  _pipeline()  ──▶  GET /api/slate (list view)
                                   │                  ──▶  GET /api/game/{pk} (detail: links + per-book odds)
                                   │
                          webapp/static/index.html ──▶ list + live /api/devig + click-through detail modal
```
Both Kalshi *and* the sportsbook are joined to `gamePk` independently (never to each other) —
that's the design point the detail modal exists to show.

## File map

| File | Role | Edit here when… |
|------|------|-----------------|
| `webapp/app.py` | FastAPI app: `_pipeline()`, `build_slate()`, routes `/api/slate`, `/api/game/{pk}`, `/api/devig`, `/`; `BOOK_META`, `FAILOVER_PRIORITY` | changing data the UI receives, default odds, book links, adding an endpoint |
| `webapp/static/index.html` | the whole frontend (inline CSS + vanilla JS, no build step) — list view + detail modal | any visual / interaction change |
| `src/mlb_fair/odds/{base,mock,mapping}.py` | odds source (The Odds API shape), mock loader, and odds→gamePk join | changing how books are parsed/joined |
| `data/mock_odds.json` | mock sportsbook odds (The Odds API v4 shape) for the 6 games | adding/removing books or games, tweaking prices/timestamps |
| `src/mlb_fair/spine/statsapi.py` | **live** MLB schedule client (public, no key) | live spine fetch issues |
| `src/mlb_fair/kalshi/client.py` | **live** Kalshi listing client (series discovery + events poll) | live Kalshi fetch issues |
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
- **`GET /api/game/{game_pk}?method=`** — full detail for one mapped game (404 if the gamePk
  isn't a bound fixture). Returns: matchup + badges fields, `links` (`kalshi`, `mlb_gameday`,
  `statsapi`), `kalshi_markets` (per contract: `ticker`, `team`, `side`, `url`), `books`
  (per book: `home_price`/`away_price`, `last_update`, de-vigged `fair_home`/`fair_away`,
  `hold`, `region`, `url`), `consensus` (median fair), and `source_book` (first present in
  `FAILOVER_PRIORITY` — the "REF" book; full band-staleness selection is P3b).
- **`_pipeline(mode)` / `_apipeline(mode)`** — runs spine → Kalshi join → odds join once.
  `mode="mock"` uses bundled fixtures + the fixed `SLATE_DATE`; `mode="live"` uses
  `StatsApiSchedule` + `LiveKalshiEvents` over a today-anchored `window_days` range. Odds stay
  mock in both modes (no key yet) — so in **live** mode they don't join (different gamePks) and
  the detail view shows no books, by design. `/api/slate` and `/api/game` take `?mode=mock|live`
  (default `CONFIG.mode` from `MLB_MODE`); `_resolve_mode()` validates it. Live failures return
  502/`{error}` so the page degrades instead of 500ing.
- **`BOOK_META`** — per-book title/region/site link. **`FAILOVER_PRIORITY`** — sharpness order
  used only to pick the reference book for display.
- **`DEFAULT_ODDS`** — seed odds for the *list-view* cards (the modal uses real per-book odds).
- **`SLATE_DATE`** — the single mock day everything is fetched for.

## Frontend: `webapp/static/index.html`

Layout (top → bottom), each tied to the function that builds it:

| UI section | DOM id | Built by |
|------------|--------|----------|
| Title + one-line description | — | static markup |
| Stat chips (date, spine, events, mapped, quotable) | `#chips` | `render()` |
| Controls: data `mock|live` toggle + de-vig method `<select>` + legend | `#mode`, `#method` | static markup |
| **Bound game cards** | `#bound` | `render()` → `boundCard(row)` |
| **Fail-safe section** (pending/unmatched) | `#failsafe` (wrap `#failsafe-wrap`) | `render()` → `failCard(row)` |
| Collapsible spine table | `#spine` | `render()` |
| Footer (design notes + endpoint names) | — | static markup |
| **Detail modal** (overlay) | `#overlay` / `#modal` | `openModal(pk)` → `modalHtml(detail)` |

Each bound card has a **`view detail · N books ▸`** button (`[data-more]`) → `openModal(pk)`.

Key JS functions:
- **`init()` / `loadSlate()` / `currentMode()`** — `init()` wires the `#method` and `#mode`
  listeners once, then `loadSlate()` fetches `/api/slate?mode=…`, handles errors/empty slates, and
  calls `render()`. Changing `#mode` re-loads; changing `#method` recomputes cards + open modal.
- **`render()`** — fills every section from `SLATE`, wires per-card inputs + detail buttons, and
  triggers the first fair computation per card. (Global listeners live in `init()`, not here, so
  re-renders don't stack handlers.)
- **`boundCard(row)`** — HTML for one bound game: matchup, `gamePk · G#`, DH badge,
  confidence badge, and (if `!quotable`) a `not quotable · <status>` badge. Two `<input>`s
  (`.o-away`, `.o-home`) + a fair-bars block (`.bar-home`, `.bar-away`) + `.hold` line.
  The **YES** tag is placed on whichever side equals `yes_team_id` (currently the home side).
- **`failCard(row)`** — HTML for a pending/unmatched event (title, status badge, reason text).
- **`recompute(card)`** — reads the card's two odds inputs + current method, calls
  `/api/devig`, updates the two bars (width = probability) and the hold/`fair_yes` line.
- **`debounce(fn, ms)`** — 180 ms debounce on odds typing so we don't spam the endpoint.
- **`openModal(pk)` / `modalHtml(d)` / `closeModal()`** — fetch `/api/game/{pk}` and render the
  detail (links, Kalshi contracts, per-book odds table with the REF row highlighted + consensus
  row). Closes on ×, overlay click, or Esc. `MODAL_PK` tracks the open game.
- Method `<select>` `change` → recompute every card **and** re-fetch the open modal (keeps the
  per-book de-vig in sync with the selected method).

### Badge → CSS class map (for restyling)
`exact`→`.b-exact` (green) · `ordinal`→`.b-ordinal` (blue) · `text`→`.b-text` (purple) ·
DH type→`.b-dh` (grey) · not-quotable→`.b-noq` (amber) · pending→`.b-pending` (amber) ·
unmatched→`.b-unmatch` (red). Theme colors are CSS vars in `:root` at the top of `<style>`.

## Common edits — where to go

- **Change starting odds for a game** (list-view cards) → `DEFAULT_ODDS` in `app.py`.
- **Change per-book odds / add a book / add a game** (modal) → `data/mock_odds.json` (The Odds
  API shape); add display metadata + a posting link in `BOOK_META`.
- **Change the reference-book pick** → `FAILOVER_PRIORITY` in `app.py`.
- **Add a field to the detail modal** → add it to the `/api/game/{pk}` response in `app.py`,
  then read it in `modalHtml()`.
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
