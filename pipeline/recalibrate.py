"""Refit calibration.json using Platt scaling on backtest + live history data.

Reads docs/backtest.json (9,784+ games with Vegas closing lines) and
docs/history.json (live 2026 resolved games), then fits:

    P(home_wins) = sigmoid(a * logit(home_win_pct) + b)

via maximum-likelihood (binary cross-entropy), storing the Platt params
and calibration diagnostics in data/calibration.json.

Usage:
    python -m pipeline.recalibrate           # write new calibration.json
    python -m pipeline.recalibrate --dry-run # print params without writing
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR         = Path(__file__).parent.parent / "data"
DOCS_DIR         = Path(__file__).parent.parent / "docs"
CALIBRATION_PATH = DATA_DIR / "calibration.json"
BACKTEST_PATH    = DOCS_DIR / "backtest.json"
HISTORY_PATH     = DOCS_DIR / "history.json"

log = logging.getLogger(__name__)

# Reasonable fallback signal params for prop picks (midpoint = neutral, slope = moderate)
_SIGNAL_MIDPOINT_DEFAULT = 7.5
_SIGNAL_SLOPE_DEFAULT    = 0.45


# ---------------------------------------------------------------------------
# Math
# ---------------------------------------------------------------------------

def _logit(p: float) -> float:
    p = max(1e-7, min(1 - 1e-7, p))
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    x = max(-20.0, min(20.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def _fit_platt(
    probs: list[float],
    labels: list[float],
    lr: float = 0.02,
    iters: int = 5000,
) -> tuple[float, float]:
    """Fit Platt scaling params (a, b) by gradient descent on log-loss.

    Model: P(y=1) = sigmoid(a * logit(prob) + b)
    Minimises binary cross-entropy.  Well-calibrated input gives a≈1, b≈0.
    """
    if len(probs) < 30:
        return 1.0, 0.0

    a, b = 1.0, 0.0
    n = len(probs)
    for it in range(iters):
        ga = gb = 0.0
        for p, y in zip(probs, labels):
            lp   = _logit(p)
            pred = _sigmoid(a * lp + b)
            err  = pred - y
            ga  += err * lp
            gb  += err
        a -= lr * ga / n
        b -= lr * gb / n

        # Adaptive LR decay
        if it == 2000:
            lr *= 0.5

    return round(a, 6), round(b, 6)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_backtest_pairs() -> list[tuple[float, float]]:
    """Return (home_win_pct, home_won) pairs from docs/backtest.json."""
    if not BACKTEST_PATH.exists():
        log.warning("backtest.json not found at %s — skipping", BACKTEST_PATH)
        return []
    try:
        data = json.loads(BACKTEST_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Could not read backtest.json: %s", exc)
        return []

    pairs: list[tuple[float, float]] = []
    for g in data.get("games", []):
        hwp = g.get("home_win_pct")
        aw  = g.get("actual_winner")
        if hwp is None or aw in (None, "tie"):
            continue
        pairs.append((float(hwp), 1.0 if aw == "home" else 0.0))
    log.info("Backtest: %d graded games loaded", len(pairs))
    return pairs


def _load_history_pairs() -> list[tuple[float, float]]:
    """Return (home_win_pct, home_won) pairs from docs/history.json."""
    if not HISTORY_PATH.exists():
        log.warning("history.json not found at %s — skipping", HISTORY_PATH)
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Could not read history.json: %s", exc)
        return []

    pairs: list[tuple[float, float]] = []
    for r in data:
        hwp = r.get("home_win_pct")
        aw  = r.get("actual_winner")
        if hwp is None or aw in (None, "tie") or r.get("sp_scratched"):
            continue
        pairs.append((float(hwp), 1.0 if aw == "home" else 0.0))
    log.info("History: %d graded games loaded", len(pairs))
    return pairs


# ---------------------------------------------------------------------------
# Calibration metrics
# ---------------------------------------------------------------------------

def _brier_score(probs: list[float], labels: list[float]) -> float:
    if not probs:
        return 0.0
    return round(sum((p - y) ** 2 for p, y in zip(probs, labels)) / len(probs), 6)


def _calibration_curve(
    probs: list[float],
    labels: list[float],
    bins: int = 10,
) -> list[dict]:
    """Bucket model probabilities into equal-width bins and compute actual win rates."""
    width = 1.0 / bins
    curve = []
    for i in range(bins):
        lo = round(i * width, 3)
        hi = round((i + 1) * width, 3)
        bucket = [(p, y) for p, y in zip(probs, labels) if lo <= p < hi]
        if hi == 1.0:
            bucket = [(p, y) for p, y in zip(probs, labels) if p >= lo]
        n = len(bucket)
        if n == 0:
            continue
        mean_p  = round(sum(p for p, _ in bucket) / n, 4)
        win_rate = round(sum(y for _, y in bucket) / n, 4)
        curve.append({
            "bin":            f"{lo:.2f}-{hi:.2f}",
            "n":              n,
            "model_prob_mean": mean_p,
            "actual_win_rate": win_rate,
        })
    return curve


def _win_rates_by_prob_band(probs: list[float], labels: list[float]) -> dict:
    """Win rates in confidence buckets (based on max(p, 1-p))."""
    bands = [
        ("50-55%", 0.50, 0.55),
        ("55-60%", 0.55, 0.60),
        ("60-65%", 0.60, 0.65),
        ("65%+",   0.65, 1.00),
    ]
    result = {}
    for label, lo, hi in bands:
        bucket = [
            y for p, y in zip(probs, labels)
            if lo <= max(p, 1 - p) < hi or (hi == 1.00 and max(p, 1 - p) >= lo)
        ]
        n = len(bucket)
        result[label] = {
            "n":        n,
            "win_rate": round(sum(bucket) / n, 4) if n > 0 else None,
        }
    return result


# ---------------------------------------------------------------------------
# Main refit
# ---------------------------------------------------------------------------

def refit(dry_run: bool = False) -> None:
    bt_pairs   = _load_backtest_pairs()
    hist_pairs = _load_history_pairs()
    all_pairs  = bt_pairs + hist_pairs

    if len(all_pairs) < 30:
        log.warning(
            "Only %d graded games — insufficient for Platt fit (need ≥30). Skipping.",
            len(all_pairs),
        )
        return

    probs  = [p for p, _ in all_pairs]
    labels = [y for _, y in all_pairs]

    log.info(
        "Fitting Platt calibration on %d games (%d backtest + %d live history)",
        len(all_pairs), len(bt_pairs), len(hist_pairs),
    )

    a, b = _fit_platt(probs, labels)

    # Calibrated probabilities for diagnostics
    cal_probs = [_sigmoid(a * _logit(p) + b) for p in probs]

    brier_raw = _brier_score(probs, labels)
    brier_cal = _brier_score(cal_probs, labels)
    cal_curve = _calibration_curve(cal_probs, labels)
    win_rates  = _win_rates_by_prob_band(cal_probs, labels)

    # Overall win rate (predicted winner correct)
    decided = [(p, y) for p, y in zip(probs, labels)]
    correct = sum(1 for p, y in decided if (p >= 0.5) == (y > 0.5))
    overall_win_rate = round(correct / len(decided), 4)

    seasons_used = sorted({
        g.get("season")
        for g in json.loads(BACKTEST_PATH.read_text()).get("games", [])
        if g.get("season") is not None
    }) if BACKTEST_PATH.exists() else []
    if hist_pairs:
        seasons_used = sorted(set(seasons_used) | {2026})

    log.info(
        "Platt fit: a=%.6f b=%.6f | Brier raw=%.4f -> cal=%.4f | overall_wr=%.4f",
        a, b, brier_raw, brier_cal, overall_win_rate,
    )

    new_calibration = {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "seasons_used":    seasons_used,
        "total_graded":    len(all_pairs),
        "source_breakdown": {
            "historical_backtest": len(bt_pairs),
            "live_history":        len(hist_pairs),
        },
        "platt_params": {
            "a": a,
            "b": b,
        },
        "logistic_params": {
            "midpoint": _SIGNAL_MIDPOINT_DEFAULT,
            "slope":    _SIGNAL_SLOPE_DEFAULT,
        },
        "brier_score": {
            "raw_model":   brier_raw,
            "calibrated":  brier_cal,
        },
        "overall_win_rate": overall_win_rate,
        "win_rates_by_confidence": win_rates,
        "calibration_curve": cal_curve,
    }

    if dry_run:
        print(json.dumps(new_calibration, indent=2))
        log.info("Dry run — calibration.json not updated.")
        return

    CALIBRATION_PATH.write_text(
        json.dumps(new_calibration, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("calibration.json updated: %s", CALIBRATION_PATH)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Refit calibration from backtest + live history")
    parser.add_argument("--dry-run", action="store_true", help="Print params without writing")
    args = parser.parse_args()
    refit(dry_run=args.dry_run)
