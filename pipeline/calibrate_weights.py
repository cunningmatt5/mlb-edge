"""Grid search to find optimal pick-tier thresholds using backtest.json.

Optimizes:
  - min_edge: minimum |model_edge_ml| to trigger a pick (currently 0.10)
  - min_pitcher_diff: minimum |pitcher_score_diff| required alongside the edge (currently 0.00 for
    strong_away/strong_home; 0.05 for elite_away)

Objective: maximize total units won on hold-out (2024-2026) while training on 2021-2023.
Requires N >= 50 qualifying games in the hold-out set to report a bucket.

Usage:
    python -m pipeline.calibrate_weights
"""

from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

BACKTEST_PATH = Path(__file__).parent.parent / "docs" / "backtest.json"
MIN_HOLDOUT_N = 50


def _ml_units(ml: int) -> float:
    """Return units won per $1 bet at American moneyline odds."""
    if ml >= 0:
        return ml / 100.0
    return 100.0 / abs(ml)


def _roi_for_games(games: list[dict], bet_away: bool) -> tuple[float, int]:
    """Return (total_units, n_bets) for the given games betting the given side."""
    total = 0.0
    for g in games:
        if bet_away:
            ml = g.get("away_ml")
            won = g.get("actual_winner") == "away"
        else:
            ml = g.get("home_ml")
            won = g.get("actual_winner") == "home"
        if ml is None:
            continue
        total += _ml_units(int(ml)) if won else -1.0
    return total, len(games)


def main() -> None:
    data = json.loads(BACKTEST_PATH.read_text(encoding="utf-8"))
    all_games = [g for g in data["games"] if "model_edge_ml" in g and g.get("home_ml") and g.get("away_ml")]

    train = [g for g in all_games if g.get("season", 0) <= 2023]
    holdout = [g for g in all_games if g.get("season", 0) >= 2024]

    print(f"Train: {len(train)} games (2021-2023)  |  Hold-out: {len(holdout)} games (2024-2026)")
    print()

    # Pre-compute pitcher_score_diff for each game
    for g in all_games:
        g["_pdiff"] = round(g["pitcher_score_home"] - g["pitcher_score_away"], 4)

    # Parameter grid
    edge_thresholds = [0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13]
    pitcher_thresholds = [0.00, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08]

    # --- Away picks grid search ---
    print("=" * 72)
    print("AWAY PICKS — grid search (model_edge_ml <= -min_edge AND pitcher_diff < -min_pdiff)")
    print("=" * 72)
    print(f"{'min_edge':>10} {'min_pdiff':>10} {'train_n':>8} {'train_roi%':>12} {'hold_n':>8} {'hold_roi%':>12} {'hold_units':>12}")
    print("-" * 72)

    best_away: list[dict] = []
    for min_edge, min_pdiff in product(edge_thresholds, pitcher_thresholds):
        def _qualifies_away(g: dict) -> bool:
            edge = g.get("model_edge_ml", 0)
            return edge <= -min_edge and g["_pdiff"] < -min_pdiff

        tr_games = [g for g in train if _qualifies_away(g)]
        ho_games = [g for g in holdout if _qualifies_away(g)]
        if len(ho_games) < MIN_HOLDOUT_N:
            continue

        tr_units, tr_n = _roi_for_games(tr_games, bet_away=True)
        ho_units, ho_n = _roi_for_games(ho_games, bet_away=True)
        tr_roi = 100.0 * tr_units / tr_n if tr_n else 0.0
        ho_roi = 100.0 * ho_units / ho_n if ho_n else 0.0

        print(f"{min_edge:>10.2f} {min_pdiff:>10.2f} {tr_n:>8} {tr_roi:>+12.2f} {ho_n:>8} {ho_roi:>+12.2f} {ho_units:>+12.2f}")
        best_away.append({"min_edge": min_edge, "min_pdiff": min_pdiff, "ho_roi": ho_roi, "ho_n": ho_n, "ho_units": ho_units})

    print()

    # --- Home picks grid search ---
    print("=" * 72)
    print("HOME PICKS — grid search (model_edge_ml >= +min_edge AND pitcher_diff > +min_pdiff)")
    print("=" * 72)
    print(f"{'min_edge':>10} {'min_pdiff':>10} {'train_n':>8} {'train_roi%':>12} {'hold_n':>8} {'hold_roi%':>12} {'hold_units':>12}")
    print("-" * 72)

    best_home: list[dict] = []
    for min_edge, min_pdiff in product(edge_thresholds, pitcher_thresholds):
        def _qualifies_home(g: dict) -> bool:
            edge = g.get("model_edge_ml", 0)
            return edge >= min_edge and g["_pdiff"] > min_pdiff

        tr_games = [g for g in train if _qualifies_home(g)]
        ho_games = [g for g in holdout if _qualifies_home(g)]
        if len(ho_games) < MIN_HOLDOUT_N:
            continue

        tr_units, tr_n = _roi_for_games(tr_games, bet_away=False)
        ho_units, ho_n = _roi_for_games(ho_games, bet_away=False)
        tr_roi = 100.0 * tr_units / tr_n if tr_n else 0.0
        ho_roi = 100.0 * ho_units / ho_n if ho_n else 0.0

        print(f"{min_edge:>10.2f} {min_pdiff:>10.2f} {tr_n:>8} {tr_roi:>+12.2f} {ho_n:>8} {ho_roi:>+12.2f} {ho_units:>+12.2f}")
        best_home.append({"min_edge": min_edge, "min_pdiff": min_pdiff, "ho_roi": ho_roi, "ho_n": ho_n, "ho_units": ho_units})

    print()

    # Summary: top 5 away and home combos by hold-out ROI
    print("=" * 72)
    print("TOP 5 AWAY COMBOS BY HOLD-OUT ROI")
    print("=" * 72)
    for row in sorted(best_away, key=lambda x: x["ho_roi"], reverse=True)[:5]:
        print(f"  edge>={row['min_edge']:.2f}, pdiff>{row['min_pdiff']:.2f} => "
              f"hold-out ROI {row['ho_roi']:+.2f}% on {row['ho_n']} games ({row['ho_units']:+.2f}u)")

    print()
    print("TOP 5 HOME COMBOS BY HOLD-OUT ROI")
    print("=" * 72)
    for row in sorted(best_home, key=lambda x: x["ho_roi"], reverse=True)[:5]:
        print(f"  edge>={row['min_edge']:.2f}, pdiff>{row['min_pdiff']:.2f} => "
              f"hold-out ROI {row['ho_roi']:+.2f}% on {row['ho_n']} games ({row['ho_units']:+.2f}u)")

    print()

    # --- Current baseline (model_edge >= 0.10, no pdiff gate) ---
    print("=" * 72)
    print("CURRENT BASELINE (edge >= 0.10, no pitcher diff gate)")
    print("=" * 72)
    curr_away_ho = [g for g in holdout if g.get("model_edge_ml", 0) <= -0.10]
    curr_home_ho = [g for g in holdout if g.get("model_edge_ml", 0) >= 0.10]
    aw_u, aw_n = _roi_for_games(curr_away_ho, bet_away=True)
    hm_u, hm_n = _roi_for_games(curr_home_ho, bet_away=False)
    print(f"  Away: {aw_n} games, ROI {100*aw_u/aw_n:+.2f}%, {aw_u:+.2f}u")
    print(f"  Home: {hm_n} games, ROI {100*hm_u/hm_n:+.2f}%, {hm_u:+.2f}u")

    # --- Proposed filter (edge >= 0.10, pdiff >= 0.03) ---
    print()
    print("PROPOSED FILTER (edge >= 0.10, pdiff >= 0.03)")
    print("=" * 72)
    prop_away_ho = [g for g in holdout if g.get("model_edge_ml", 0) <= -0.10 and g["_pdiff"] < -0.03]
    prop_home_ho = [g for g in holdout if g.get("model_edge_ml", 0) >= 0.10 and g["_pdiff"] > 0.03]
    aw_u, aw_n = _roi_for_games(prop_away_ho, bet_away=True)
    hm_u, hm_n = _roi_for_games(prop_home_ho, bet_away=False)
    if aw_n:
        print(f"  Away: {aw_n} games, ROI {100*aw_u/aw_n:+.2f}%, {aw_u:+.2f}u")
    if hm_n:
        print(f"  Home: {hm_n} games, ROI {100*hm_u/hm_n:+.2f}%, {hm_u:+.2f}u")


if __name__ == "__main__":
    sys.exit(main())
