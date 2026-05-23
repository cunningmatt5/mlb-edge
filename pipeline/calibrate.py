"""Compute win rates and fit logistic signal→probability curve.

Usage:
    python -m pipeline.calibrate [--seasons 2023,2024]

Reads:  data/backtest_results.parquet
Writes: data/calibration.json

The logistic model is:  P(win) = 1 / (1 + exp(-(signal - midpoint) * slope))
Fitted parameters replace the hardcoded defaults (midpoint=7.5, slope=0.45)
in pipeline/odds.py.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

DATA_DIR = Path(__file__).parent.parent / "data"

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Logistic model
# ---------------------------------------------------------------------------

def _logistic(signal: np.ndarray, midpoint: float, slope: float) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-(signal - midpoint) * slope))


# ---------------------------------------------------------------------------
# Main calibration function
# ---------------------------------------------------------------------------

def compute_calibration(results_df: pd.DataFrame) -> dict:
    """Compute win rates and fit logistic parameters. Saves data/calibration.json."""
    graded = results_df[results_df["outcome"].isin(["WIN", "LOSS"])].copy()
    graded["win"] = (graded["outcome"] == "WIN").astype(int)

    total = len(graded)
    log.info("Calibrating from %d graded picks", total)

    # --- Win rates by signal band ---
    bands = [
        ("5.0-5.9", 5.0, 6.0),
        ("6.0-6.9", 6.0, 7.0),
        ("7.0-7.9", 7.0, 8.0),
        ("8.0-8.9", 8.0, 9.0),
        ("9.0+",    9.0, 11.0),
    ]
    by_band: dict = {}
    fit_signals:  list[float] = []
    fit_winrates: list[float] = []
    fit_weights:  list[float] = []

    for label, lo, hi in bands:
        subset = graded[(graded["signal"] >= lo) & (graded["signal"] < hi)]
        n  = len(subset)
        wr = float(subset["win"].mean()) if n > 0 else None
        by_band[label] = {"n": n, "win_rate": round(wr, 4) if wr is not None else None}
        if n >= 10 and wr is not None:
            fit_signals.append((lo + hi) / 2.0)
            fit_winrates.append(wr)
            fit_weights.append(float(n))

    # --- Win rates by bet type ---
    by_type: dict = {}
    for bt, sub in graded.groupby("bet_type"):
        n  = len(sub)
        wr = float(sub["win"].mean()) if n > 0 else None
        by_type[str(bt)] = {"n": n, "win_rate": round(wr, 4) if wr is not None else None}

    # --- Win rates by tier ---
    by_tier: dict = {}
    for tier, sub in graded.groupby("tier"):
        n  = len(sub)
        wr = float(sub["win"].mean()) if n > 0 else None
        by_tier[str(tier)] = {"n": n, "win_rate": round(wr, 4) if wr is not None else None}

    # --- Fit logistic curve ---
    midpoint, slope = 7.5, 0.45  # defaults if fit fails
    if len(fit_signals) >= 3:
        try:
            sigma = [1.0 / w for w in fit_weights]
            popt, _ = curve_fit(
                _logistic,
                fit_signals,
                fit_winrates,
                p0=[7.5, 0.45],
                sigma=sigma,
                absolute_sigma=False,
                bounds=([4.0, 0.05], [11.0, 2.0]),
                maxfev=5000,
            )
            midpoint, slope = float(popt[0]), float(popt[1])
            log.info("Logistic fit: midpoint=%.4f  slope=%.4f", midpoint, slope)
        except Exception as exc:
            log.warning("Curve fit failed: %s — keeping defaults (7.5, 0.45)", exc)
    else:
        log.warning(
            "Only %d signal bands have ≥10 samples — curve fit skipped, using defaults",
            len(fit_signals),
        )

    # --- Tier cutoff recommendations ---
    elite_cutoff = _find_crossing(fit_signals, fit_winrates, 0.55, default=8.0)
    great_cutoff = _find_crossing(fit_signals, fit_winrates, 0.50, default=6.5)

    cal = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seasons_used": sorted([int(s) for s in graded["season"].unique().tolist()]),
        "total_graded": total,
        "logistic_params": {
            "midpoint": round(midpoint, 4),
            "slope":    round(slope, 4),
        },
        "win_rates": {
            "by_signal_band": by_band,
            "by_bet_type":    by_type,
            "by_tier":        by_tier,
        },
        "tier_recommendations": {
            "elite_cutoff": round(elite_cutoff, 2),
            "great_cutoff": round(great_cutoff, 2),
        },
    }

    out_path = DATA_DIR / "calibration.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cal, indent=2))
    log.info("Saved calibration.json")
    return cal


def _find_crossing(signals: list, winrates: list, threshold: float, default: float) -> float:
    """Return the first signal value where win_rate >= threshold."""
    for s, wr in sorted(zip(signals, winrates)):
        if wr >= threshold:
            return s
    return default


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Fit calibration curve from backtest results")
    parser.add_argument("--seasons", help="(informational only — reads backtest_results.parquet)")
    args = parser.parse_args()

    results_path = DATA_DIR / "backtest_results.parquet"
    if not results_path.exists():
        print(f"ERROR: {results_path} not found — run pipeline.backtest first")
        raise SystemExit(1)

    df  = pd.read_parquet(results_path)
    cal = compute_calibration(df)

    print("\nCalibration complete")
    print(f"  Graded picks : {cal['total_graded']:,}")
    print(f"  Seasons      : {cal['seasons_used']}")
    print(f"  Logistic fit : midpoint={cal['logistic_params']['midpoint']}  slope={cal['logistic_params']['slope']}")
    print(f"  Tier recs    : elite≥{cal['tier_recommendations']['elite_cutoff']}  great≥{cal['tier_recommendations']['great_cutoff']}")
    print("\nWin rates by signal band:")
    for band, v in cal["win_rates"]["by_signal_band"].items():
        wr = f"{v['win_rate']:.1%}" if v["win_rate"] else "—"
        print(f"  {band}: {wr} ({v['n']:,} picks)")
