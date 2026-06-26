# Exercise 2 — Live MLB Win-Probability Model

**Build brief.** This is the spec. Default **Python 3.11**, LightGBM, scikit-learn,
pandas/numpy, matplotlib, `pybaseball`. We only ever predict at half-inning breaks (game state stable,
bases empty, 0 outs), so there is no mid-inning modeling. Everything below that says "justify" is
grading signal — the reasoning is drafted here; lift it into `MODEL_NOTE.md`.

---

## 0. Functional form (the headline decision)

**Additive-in-log-odds model anchored to the pregame win probability:**

```
logit(P_home_win) = logit(pregame_WP_home) + g(state)
```

Implement `g(state)` as a **gradient-boosted classifier (LightGBM)** trained with binary log-loss,
with `logit(pregame_WP_home)` supplied as the model's **`init_score` (offset)**. The trees then learn
*only the in-game adjustment* on top of the market's pregame estimate. Final probabilities pass
through **isotonic calibration** fit on a holdout.

**Why this form (for MODEL_NOTE.md):**
- **Bounded output.** A sigmoid of a real-valued log-odds is strictly in (0,1) by construction; no
  clipping needed (we still clamp to [ε, 1−ε] defensively for blowouts).
- **Pregame consistency.** At the opening state (top 1, 0–0, full game remaining) the adjustment
  `g ≈ 0`, so the model collapses to the pregame WP — exactly the input the firm provides and trusts.
  The offset *guarantees* the anchor instead of hoping the model relearns it.
- **The value of a run changes across innings and lead sizes** — this is what `g` encodes. Features
  `run_diff`, `half_innings_remaining`, and their **interaction** let the model learn that a 1-run
  lead is worth far more in the 9th than the 1st, and the nonlinearity in `run_diff` captures
  diminishing marginal value of extra runs (the +1→+2 jump matters more than +7→+8). We add
  **monotone constraints** (P non-decreasing in `run_diff`; appropriate sign on innings features) so
  extrapolation into sparse extreme states stays sane.

**Alternative considered (build it as a baseline + document it):** a transparent **parametric
"remaining-runs" model**. Back out each team's expected runs per remaining half-inning from the
pregame WP and a league run environment, then compute `P(home runs > away runs)` over the remaining
half-innings using an empirical per-inning run distribution — handling walk-off (home stops batting
once ahead in the bottom of the 9th+) and the extra-innings ghost-runner scoring bump directly.
*Pros:* fully interpretable, run-value-by-inning emerges from first principles, perfect monotonicity,
no training data needed (great cold-start / sanity check). *Cons:* needs a run-distribution
assumption and a team-strength back-out; absorbs idiosyncratic features less gracefully.
**Decision:** ship the GBM as primary (data-driven, produces the required calibration artifacts
cleanly); include the parametric model as (a) a documented alternative, (b) a calibration baseline,
and (c) optionally a *feature* into the GBM. Note that blending the two, or feeding parametric WP as a
GBM feature, is the production-grade move.

---

## 1. Inputs and features (all zero-latency at an inning break — justify each)

**Required (from the spec):** pregame WP (home) → used as the offset; home & away team; home & away
record; half-inning (e.g. "top 6"); home score; away score.

**Core derived:**
- `run_diff = home_score - away_score`
- `half_innings_remaining` — from inning, half, and `scheduledInnings`
- `is_home_batting_next` — true when the bottom is about to start; encodes the **last-licks / walk-off**
  advantage
- `run_diff * half_innings_remaining` — explicit interaction (trees get it implicitly, but adding it
  helps the model and the parametric baseline)

**Optional zero-latency adds (justify availability in MODEL_NOTE.md):**
- **Pythagorean expectation / season run differential** from records — from standings, static,
  pregame.
- **Starter-pulled flag + current pitcher** — readable from the live boxscore/feed at the break with
  no meaningful latency (the feed already reflects the pitching change once the inning ends).
- **Bullpen quality** (team bullpen ERA / aggregate reliever rate) — season stat, precomputed,
  pregame.
- **Park factor** — static seasonal table keyed by venue; known pregame.
- **Rest days / travel** — from the schedule; pregame.
- **Rule-era / ghost-runner indicator** — known from the date; important for extra-innings calibration.

Anything requiring mid-play scraping or a slow source is excluded by design.

**Pregame WP for *training labels*.** At inference the firm supplies pregame WP. For training we must
reconstruct it so the offset's distribution matches inference. Use **log5** from each team's season
record entering the game plus a home-field constant (~0.54 baseline home win rate):
`P = (H - H*A) / (H + A - 2*H*A)` then apply HFA. If historical closing-line odds are available,
prefer the de-vigged closing WP (sharper). **Leakage caution:** use records *entering* that game, not
end-of-season.

---

## 2. Data pipeline

- Source: **Retrosheet** play-by-play via `pybaseball`/chadwick. For each game, reconstruct the state
  at **every half-inning break**: `(inning, half_about_to_start, home_score, away_score, …)` plus the
  final label `home_win ∈ {0,1}`.
- Span: **2021–2024 regular season** (~4 seasons × ~2,430 games × ~18 breaks ≈ ~170k rows) — ample for
  LightGBM and post-2020 so the **ghost-runner** extra-innings rule is represented. Note era effects
  (pitch clock + shift ban from 2023): keep recent, optionally add an era feature.
- **Splits — critical:** split by **game** (group split), not by row — states within a game share the
  same label and leak. Hold out the **most recent season** (train ≤2023, validate 2024) to test
  temporal generalization, and report a game-grouped CV too.
- **Synthetic fallback:** include a generator that simulates games from a simple per-inning run model
  and emits the same state rows, so the full train→calibrate→eval pipeline always produces artifacts
  even if the Retrosheet download is unavailable in the sandbox. This protects the one-shot; document
  it as a fallback, not the real result.

---

## 3. Calibration deliverables (required)

On the holdout:
- **Brier score**, plus **Brier skill score** vs two baselines: (a) constant pregame-WP, (b) an
  empirical *state-only* WP table (the classic "two average teams" lookup). Beating both is the bar.
- **Reliability curve:** bucket predicted WP into ~15 bins, plot predicted vs realized with bin
  counts; also report **ECE**. Save `reliability.png` + `reliability_bins.csv`.
- Show calibration **before vs after** isotonic.
- Persist `metrics.json` (brier, brier_skill, ece, logloss) and the fitted model + isotonic to
  `artifacts/`.

---

## 4. Edge-case note (draft — lift to MODEL_NOTE.md)

- **Extra innings.** The ghost-runner rule (runner on 2nd to start each half, permanent since 2020/23)
  elevates scoring; the model must be era-aware (post-2020 training, plus the rule indicator). Entering
  an extra inning `run_diff = 0` and the home side batting in the bottom carries a real walk-off edge.
  Data is sparse here → monotone constraints + the parametric backbone keep WP sane.
- **Walk-offs.** Home bats last in the bottom of the 9th+. Tied entering the bottom 9th, home WP
  should sit **> 50%** (last licks). The `is_home_batting_next` feature plus the fact that home can win
  without completing the inning pushes WP up; we sanity-check (tied, bottom 9 ≈ 0.53–0.56). We predict
  *at the break before* the half, and a walk-off resolves mid-half — so we never trade it, consistent
  with "trade only at breaks."
- **Large leads late.** WP must saturate toward 0/1 (e.g., +5 entering the 9th ≈ >0.99). Monotone
  constraints + isotonic prevent under-confidence; clamp to [ε, 1−ε] since trees won't hit exactly 1.

Add these as **tests**: pregame consistency, walk-off > 0.5, blowout monotonicity, saturation.

---

## 5. Known blind spots (draft — lift to MODEL_NOTE.md)

- No batter/pitcher matchup, platoon, or specific reliever quality beyond aggregate ERA → misses
  quality-of-opposition at the plate and bullpen mismatch.
- Bullpen **fatigue/availability** (who's already thrown, back-to-backs) not captured by season ERA.
- No live weather/wind beyond a static park factor.
- **Pregame WP is load-bearing** — garbage in, garbage out; the model inherits any bias in the
  supplied pregame estimate.
- Injuries/ejections, pinch-hit/defensive subs, manager tendencies, umpire strike-zone effects.
- Regime shifts (rule changes) make older data less representative → recent-era training + era flag.
- Small-sample extreme states → high variance, leaned on constraints/backbone.
- Tuned for regulation+extras regular season; **postseason** bullpen usage and leverage differ → flag
  if applied to playoffs.
- (Note: base-out/2-strike context is absent — but irrelevant here, since at an inning break bases are
  empty and outs are 0. This is a property of the trade timing, not a gap.)

---

## 6. File tree

```
mlb-winprob/
  README.md
  MODEL_NOTE.md                  # functional form + edge cases + blind spots (drafted above)
  pyproject.toml
  src/winprob/
    data/
      retrosheet.py              # fetch + build inning-break states
      synthetic.py               # fallback game simulator (same row schema)
      pregame_wp.py              # log5 / odds-derived pregame WP (offset)
      features.py                # derive run_diff, innings_remaining, interactions, etc.
    model/
      form.py                    # logit-offset LightGBM wrapper + monotone constraints
      parametric.py              # remaining-runs MC (baseline + alternative)
      calibrate.py               # isotonic
      train.py
      predict.py                 # inference entry point (signature below)
    eval/
      metrics.py                 # brier, brier_skill, ece, logloss
      reliability.py             # reliability.png + reliability_bins.csv
  artifacts/                     # model.txt, isotonic.pkl, metrics.json, reliability.png/csv
  tests/
    test_pregame_consistency.py  # top 1, 0-0 ~ pregame_WP
    test_edge_cases.py           # walk-off>0.5, blowout monotonicity, extras saturation
    test_predict_shapes.py
```

**Inference signature** `predict.py` must expose (matches the required inputs exactly):
```python
def predict(
    pregame_wp_home: float,
    home_team: str, away_team: str,
    home_record: tuple[int, int], away_record: tuple[int, int],
    half_inning: str,            # e.g. "top 6" / "bottom 9"
    home_score: int, away_score: int,
    *,                            # optional zero-latency extras
    starter_pulled: bool | None = None,
    bullpen_era_home: float | None = None,
    bullpen_era_away: float | None = None,
    park_factor: float | None = None,
    rest_days_home: int | None = None,
    rest_days_away: int | None = None,
) -> float:                       # returns P(home wins) in (0,1)
    ...
```

---

## 7. Build phases

- **P0** scaffold + `synthetic.py` so the pipeline runs without external data.
- **P1** `retrosheet.py` state builder + `pregame_wp.py` (log5) + `features.py`.
- **P2** `form.py` (offset GBM + monotone) + `train.py` with game/season splits.
- **P3** `calibrate.py` (isotonic) + `eval/` (Brier, Brier-skill, reliability, ECE) → artifacts.
- **P4** `parametric.py` baseline + comparison row in metrics.
- **P5** edge-case tests + README + MODEL_NOTE.md.

Acceptance: `python -m winprob.train` produces `artifacts/metrics.json`, `reliability.png`, a saved
model, and beats both baselines on holdout Brier; all edge-case tests pass; `predict(...)` returns a
calibrated WP for the required inputs and ≈ pregame_WP at the opening state.
