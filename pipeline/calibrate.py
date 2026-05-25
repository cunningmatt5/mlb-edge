"""Calibrate predictor.py constants against the historical comps database.

Usage:
    python -m pipeline.calibrate

Reads data/game_comps.json (7,303 historical games from 2023-2025).
Each record has a 7-dim feature vector:
    [0] home_sp xFIP-like normalized (0=elite, 1=terrible)
    [1] home_sp SIERA normalized
    [2] away_sp xFIP-like normalized (0=elite, 1=terrible)
    [3] away_sp SIERA normalized
    [4] home lineup xwOBA normalized (0=poor, 1=elite)
    [5] away lineup xwOBA normalized
    [6] park_factor normalized (0=pitcher park, 1=hitter park)

Fits LEAGUE_AVG_RUNS, pitcher_weight, and lineup_weight to minimize
RMSE against actual total_runs, then prints recommended values.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

log = logging.getLogger(__name__)

COMPS_PATH = Path(__file__).parent.parent / "data" / "game_comps.json"

# Park factor normalized [0,1] → actual factor [88, 118] → mult [0.88, 1.18]
_PARK_LO = 88.0
_PARK_HI = 118.0


def _model_total(params: list[float], features: list[float]) -> float:
    """Mirror of predictor._predicted_runs() mapped onto comps feature indices.

    Sign convention (important — features are NOT the same scale as predictor scores):
      comps features[0/2] = xFIP normalized (0=elite/low xFIP, 1=terrible/high xFIP)
      predictor away_pitcher_score = inverted (0=terrible, 1=elite)

      Equivalence:
        predictor: home_runs ∝ 1 − (away_pitcher_score − 0.5) * w_pit
                             = 1 − ((1 − features[2]) − 0.5) * w_pit
                             = 1 + (features[2] − 0.5) * w_pit   ← uses +, not −

      So + sign for pitcher terms here is CORRECT even though predictor.py uses −.
      A bad away pitcher (features[2]=0.8) adds +0.3*w_pit → more home runs ✓
    """
    baseline, w_pit, w_lin = params
    park_mult = (_PARK_LO + features[6] * (_PARK_HI - _PARK_LO)) / 100.0

    home_runs = baseline * (
        1.0
        + (features[4] - 0.5) * w_lin   # home offense edge (higher xwOBA = more runs)
        + (features[2] - 0.5) * w_pit   # away pitcher "badness" (higher xFIP = more runs)
    ) * park_mult

    away_runs = baseline * (
        1.0
        + (features[5] - 0.5) * w_lin   # away offense edge
        + (features[0] - 0.5) * w_pit   # home pitcher "badness"
    ) * park_mult

    return home_runs + away_runs


def _rmse_loss(params: list[float], games: list[dict]) -> float:
    errs = [
        (_model_total(params, g["features"]) - g["total_runs"]) ** 2
        for g in games
        if len(g.get("features", [])) >= 7
    ]
    return float(np.mean(errs)) if errs else 1e9


def calibrate() -> tuple[float, float, float]:
    """Return (league_avg_runs, pitcher_weight, lineup_weight) fitted to comps data."""
    if not COMPS_PATH.exists():
        raise FileNotFoundError(
            f"game_comps.json not found at {COMPS_PATH}. "
            "Run: python -m pipeline.comps --build"
        )

    with open(COMPS_PATH, encoding="utf-8") as f:
        games = json.load(f)

    valid = [g for g in games if len(g.get("features", [])) >= 7 and g.get("total_runs") is not None]
    log.info("Loaded %d valid games from comps DB (of %d total)", len(valid), len(games))

    if len(valid) < 100:
        raise ValueError(f"Too few valid games ({len(valid)}) for calibration.")

    # Baseline guess matches current hardcoded values
    x0 = [4.5, 0.6, 0.6]
    bounds = [(3.5, 5.5), (0.1, 2.0), (0.1, 2.0)]

    result = minimize(
        _rmse_loss,
        x0=x0,
        args=(valid,),
        method="Nelder-Mead",
        options={"xatol": 1e-5, "fatol": 1e-5, "maxiter": 10000},
    )

    opt_baseline, opt_pit, opt_lin = result.x

    # Compute baseline RMSE for comparison
    baseline_rmse = np.sqrt(_rmse_loss(x0, valid))
    opt_rmse = np.sqrt(_rmse_loss(result.x, valid))
    mean_total = np.mean([g["total_runs"] for g in valid])
    baseline_bias = np.mean([_model_total(x0, g["features"]) - g["total_runs"] for g in valid])
    opt_bias = np.mean([_model_total(result.x, g["features"]) - g["total_runs"] for g in valid])

    print("\n" + "=" * 60)
    print("MLBEdge Calibration Results")
    print("=" * 60)
    print(f"Games analyzed:     {len(valid):,}")
    print(f"Mean actual total:  {mean_total:.2f} runs")
    print()
    print(f"{'':20s}  {'Baseline':>10s}  {'Optimized':>10s}")
    print(f"{'LEAGUE_AVG_RUNS':20s}  {x0[0]:>10.4f}  {opt_baseline:>10.4f}")
    print(f"{'pitcher_weight':20s}  {x0[1]:>10.4f}  {opt_pit:>10.4f}")
    print(f"{'lineup_weight':20s}  {x0[2]:>10.4f}  {opt_lin:>10.4f}")
    print()
    print(f"{'RMSE':20s}  {baseline_rmse:>10.3f}  {opt_rmse:>10.3f}")
    print(f"{'Bias (pred-actual)':20s}  {baseline_bias:>+10.3f}  {opt_bias:>+10.3f}")
    print("=" * 60)
    print()
    print("Paste into pipeline/predictor.py lines 15-16:")
    print(f"  LEAGUE_AVG_RUNS = {opt_baseline:.3f}")
    print(f"  # pitcher_weight = {opt_pit:.3f}, lineup_weight = {opt_lin:.3f}")
    print()
    print("Update _predicted_runs() weight coefficient from 0.6 to:")
    print(f"  pitcher factor: {opt_pit:.3f}")
    print(f"  lineup factor:  {opt_lin:.3f}")
    print("=" * 60 + "\n")

    return float(opt_baseline), float(opt_pit), float(opt_lin)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    calibrate()
