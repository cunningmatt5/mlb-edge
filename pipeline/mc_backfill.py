"""
Backfill historical Monte Carlo outputs (2022-2025) so the MC divergence signal can
be validated against outcomes. MC output is not persisted anywhere, so this
reconstructs the inputs simulate_game() needs from the prior-season player caches +
recorded lineups and re-runs the simulator per finished game.

Fidelity caveats (documented, surfaced in the verdict):
  - Prior-season player_cache (lookahead-SAFE, point-in-time) — no in-season form.
  - Historical batters lack k_pct/bb_pct → MC uses league-average K/BB per batter.
  - Pitchers lack handedness → no platoon-split selection.
  - weather_modifier / rest_modifier = 0 (not reconstructable; MC applies them to win% only).
  => a CRUDER proxy of the live MC, not an exact match. 2026 not backfillable
     (no data/seasons/2026 cache); forward persistence in history.py covers it going forward.

Usage:
    python pipeline/mc_backfill.py                 # all of 2022-2025, n=2000
    python pipeline/mc_backfill.py --seasons 2024 --n 500 --limit 200   # quick pilot
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from multiprocessing import Pool

from pipeline.analytics.monte_carlo import simulate_game
from pipeline.backtest import load_full_historical_cache, load_season_lineups
from pipeline.predictor import _pitcher_score
from pipeline.park_factors import get_run_factor
from pipeline.odds import no_vig_prob

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
OUT = ROOT / "docs" / "mc_history.json"

# Per-worker globals (set by _init for each season pool)
_CACHE: dict = {}
_LINEUPS: dict = {}
_N = 2000


def _init(season: int, n: int) -> None:
    global _CACHE, _LINEUPS, _N
    _CACHE = load_full_historical_cache(season)   # prior-season cache (point-in-time)
    _LINEUPS = load_season_lineups(season)
    _N = n


def _sp_input(sp_id):
    """Shape an SP cache entry into what simulate_game reads, plus the raw entry for scoring."""
    e = _CACHE.get(sp_id) or {}
    return {"season": {"k_pct": e.get("k_pct"), "bb_pct": e.get("bb_pct")}, "throws": None}, e


def _mc_for_game(rec: dict) -> dict:
    cache = _CACHE
    gpk = rec["gamePk"]
    lu = _LINEUPS.get(gpk) or _LINEUPS.get(int(gpk)) or {}
    home_lineup = [cache[p] for p in lu.get("home", []) if p in cache]
    away_lineup = [cache[p] for p in lu.get("away", []) if p in cache]

    home_sp, home_sp_e = _sp_input(rec.get("home_sp_id"))
    away_sp, away_sp_e = _sp_input(rec.get("away_sp_id"))
    home_bp = cache.get(f"bullpen:{rec['home_team']}", {})
    away_bp = cache.get(f"bullpen:{rec['away_team']}", {})

    sig = {
        "pitcher_score_home": _pitcher_score(home_sp_e, home_bp),
        "pitcher_score_away": _pitcher_score(away_sp_e, away_bp),
        "bullpen_k_pct_home": home_bp.get("k_pct"),  "bullpen_bb_pct_home": home_bp.get("bb_pct"),
        "bullpen_xera_home":  home_bp.get("xera"),
        "bullpen_k_pct_away": away_bp.get("k_pct"),  "bullpen_bb_pct_away": away_bp.get("bb_pct"),
        "bullpen_xera_away":  away_bp.get("xera"),
        "weather_modifier": 0, "rest_modifier": 0,
    }
    try:
        park = float(get_run_factor(rec.get("venue", "")))
    except Exception:
        park = 100.0

    g = {
        "park_run_factor": park,
        "home_sp": home_sp, "away_sp": away_sp,
        "home_lineup": home_lineup, "away_lineup": away_lineup,
        "prediction": {"model_signals": sig},
    }
    mc = simulate_game(g, n=_N)

    vhp = None
    hml, aml = rec.get("home_ml"), rec.get("away_ml")
    if hml is not None and aml is not None:
        try:
            vhp = round(no_vig_prob(int(hml), int(aml))[0], 4)
        except Exception:
            vhp = None

    return {
        "gamePk": gpk, "season": rec["season"], "date": rec.get("date"),
        "mc_win_pct": mc["mc_win_pct"], "mc_total": mc["mc_total"],
        "mc_home_runs": mc["mc_home_runs"], "mc_away_runs": mc["mc_away_runs"],
        "lineup_ok": len(home_lineup) >= 3 and len(away_lineup) >= 3,
        "vegas_home_prob": vhp,
        "closing_total": rec.get("closing_total"),
        "home_ml": hml, "away_ml": aml,
        "over_price": rec.get("over_price"), "under_price": rec.get("under_price"),
        "predicted_total": rec.get("predicted_total"),
        "actual_winner": rec.get("actual_winner"), "actual_total": rec.get("actual_total"),
        "total_went_over": rec.get("total_went_over"),
    }


def _load_games(seasons: set[int]) -> dict[int, list[dict]]:
    """Finished games from backtest.json (has SP ids, venue, odds, outcomes)."""
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))["games"]
    by_season: dict[int, list[dict]] = {}
    for g in bt:
        s = g.get("season")
        if s not in seasons:
            continue
        if g.get("home_sp_id") is None or g.get("away_sp_id") is None:
            continue
        if g.get("home_score") is None or g.get("away_score") is None:
            continue
        by_season.setdefault(s, []).append(g)
    return by_season


def run(seasons: list[int], n: int, limit: int | None, workers: int) -> None:
    by_season = _load_games(set(seasons))
    all_records: list[dict] = []
    for season in sorted(by_season):
        games = by_season[season]
        if limit:
            games = games[:limit]
        t0 = time.time()
        if workers > 1:
            with Pool(workers, initializer=_init, initargs=(season, n)) as pool:
                recs = pool.map(_mc_for_game, games, chunksize=8)
        else:
            _init(season, n)
            recs = [_mc_for_game(g) for g in games]
        all_records.extend(recs)
        ok = sum(1 for r in recs if r["lineup_ok"])
        dt = time.time() - t0
        print(f"  {season}: {len(recs)} games  ({ok} with full lineups)  in {dt:.0f}s")

    OUT.write_text(json.dumps(all_records, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(all_records)} records → {OUT.relative_to(ROOT)}  (n={n} sims/game)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seasons", type=int, nargs="+", default=[2022, 2023, 2024, 2025])
    p.add_argument("--n", type=int, default=2000, help="MC iterations per game")
    p.add_argument("--limit", type=int, default=None, help="cap games/season (pilot)")
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args()
    run(args.seasons, args.n, args.limit, args.workers)
