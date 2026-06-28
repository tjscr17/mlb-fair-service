import asyncio
from mlb_fair.spine.mock import MockSchedule
from mlb_fair.engine.registry import FixtureRegistry
from mlb_fair.odds.devig import devig
from mlb_fair.kalshi.mock import MockKalshiEvents
from mlb_fair.kalshi.mapping import map_events, apply

print("=" * 72)
print("P1 - spine + registry (gamePk-keyed fixtures)")
print("=" * 72)
games = asyncio.run(MockSchedule().fetch("2026-06-25", "2026-06-25"))
reg = FixtureRegistry(); reg.upsert_spine(games)
print(f"{len(reg)} fixtures loaded\n")
for f in sorted(reg.all(), key=lambda x: (x.spine.game_date, x.spine.game_number)):
    s = f.spine
    dh = {"N": "single", "S": "split-DH", "Y": "straight-DH"}[s.double_header]
    print(f"  gamePk {s.game_pk}  {s.away.name} @ {s.home.name}  G{s.game_number} [{dh}]  {s.status}")
print("\nCubs@Brewers split-DH ->", [g.game_pk for g in reg.block('2026-06-25', frozenset({112, 158}))])

print("\n" + "=" * 72)
print("P3a - de-vig (American odds -> fair win prob), 3 methods")
print("=" * 72)
for m in ("multiplicative", "additive", "shin"):
    fr = devig(-150, +130, m)
    print(f"{m:14s} home {fr.home:.4f}  away {fr.away:.4f}  (hold {fr.overround*100:.2f}%)")

print("\n" + "=" * 72)
print("P2 - Kalshi -> gamePk mapping (fields, never tickers; fail-safe)")
print("=" * 72)
events = asyncio.run(MockKalshiEvents().fetch())
print(f"{len(events)} Kalshi events fetched\n")
results = map_events(events, reg)

icon = {"bound": "OK ", "pending": "...", "unmatched": "XX "}
for r in sorted(results, key=lambda r: (r.status, r.event_ticker)):
    if r.status == "bound":
        b = r.binding
        yes = ", ".join(f"{t.split('-')[-1]}=team{tid}" for t, tid in b.market_yes.items())
        print(f"  {icon[r.status]} {r.event_ticker:34s} -> gamePk {b.game_pk} G{b.game_number} "
              f"[{b.confidence:8s}] YES sides: {yes}")
    else:
        print(f"  {icon[r.status]} {r.event_ticker:34s} -> {r.status.upper()} ({r.reason})")

bound = apply(results, reg)
print(f"\napplied {bound} bindings into the registry; quotable (bound + pre-game) now:")
for f in reg.quotable():
    s = f.spine
    print(f"  gamePk {s.game_pk}  {s.away.name} @ {s.home.name} G{s.game_number}"
          f"  (YES posts on home team_id {f.binding.yes_team_id})")
