"""Refit calibration.json from live 2026 resolved game history.

Reads history.json, excludes SP-scratched games and unresolved records,
and updates the logistic params + win-rate bands.

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

from pipeline.history import load_history

DATA_DIR       = Path(__file__).parent.parent / "data"
CALIBRATION_PATH = DATA_DIR / "calibration.json"

log = logging.getLogger(__name__)

# Signal bands for win-rate reporting
_BANDS = [
    ("5.0-5.9", 5.0, 6.0),
    ("6.0-6.9", 6.0, 7.0),
    ("7.0-7.9", 7.0, 8.0),
    ("8.0-8.9", 8.0, 9.0),
    ("9.0+",    9.0, 99.0),
]

# Tier cutoffs
_ELITE_CUTOFF = 8.0
_GREAT_CUTOFF = 6.5


def _logistic(x: float, midpoint: float, slope: float) -> float:
    return 1.0 / (1.0 + math.exp(-(x - midpoint) / slope))


def _fit_logistic(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    """Fit logistic curve (midpoint, slope) by gradient descent on log-loss.

    pairs: list of (signal_value, 1-if-correct 0-if-not)
    Returns (midpoint, slope) that minimises binary cross-entropy.
    """
    if not pairs:
        return 11.0, 0.0803

    # Initialise near calibration.json defaults
    mp, sl = 11.0, 0.0803
    lr = 0.05
    for _ in range(2000):
        grad_mp = grad_sl = 0.0
        for x, y in pairs:
            p  = _logistic(x, mp, sl)
            p  = max(1e-9, min(1 - 1e-9, p))
            dL = p - y  # dLoss / d(logit)
            grad_mp += dL * (-1.0 / sl)
            grad_sl += dL * (-(x - mp) / (sl ** 2))
        n = len(pairs)
        mp -= lr * grad_mp / n
        sl -= lr * grad_sl / n
        sl  = max(0.01, sl)  # keep slope positive

    return round(mp, 4), round(sl, 6)


def _win_rates_by_band(records: list[dict]) -> dict:
    result = {}
    for label, lo, hi in _BANDS:
        bucket = [
            r for r in records
            if r.get("home_win_pct") is not None
            and lo <= _record_max_conf(r) < hi
        ]
        n = len(bucket)
        correct = sum(1 for r in bucket if r.get("actual_winner") == r.get("predicted_winner"))
        result[label] = {
            "n":        n,
            "win_rate": round(correct / n, 4) if n > 0 else None,
        }
    return result


def _record_max_conf(r: dict) -> float:
    hwp = r.get("home_win_pct") or 0.5
    return max(hwp, 1.0 - hwp) * 10  # convert probability to ~signal scale


def _win_rates_by_tier(records: list[dict]) -> dict:
    def _tier(r):
        conf = _record_max_conf(r)
        if conf >= _ELITE_CUTOFF:
            return "ELITE"
        if conf >= _GREAT_CUTOFF:
            return "GREAT"
        return "APPEALING"

    tiers: dict[str, list] = {"ELITE": [], "GREAT": [], "APPEALING": []}
    for r in records:
        tiers[_tier(r)].append(r)

    result = {}
    for tier, bucket in tiers.items():
        n = len(bucket)
        correct = sum(1 for r in bucket if r.get("actual_winner") == r.get("predicted_winner"))
        result[tier] = {
            "n":        n,
            "win_rate": round(correct / n, 4) if n > 0 else None,
        }
    return result


def refit(dry_run: bool = False) -> None:
    history = load_history()
    resolved = [
        r for r in history
        if r.get("actual_winner") not in (None, "tie")
        and not r.get("sp_scratched")
    ]
    n_total     = len(history)
    n_resolved  = len(resolved)
    n_scratched = sum(1 for r in history if r.get("sp_scratched"))
    log.info(
        "History: %d total, %d resolved non-tie non-scratched, %d SP-scratched",
        n_total, n_resolved, n_scratched,
    )

    if n_resolved < 30:
        log.warning(
            "Only %d resolved records — insufficient for reliable refit (need ≥30). "
            "Calibration unchanged.",
            n_resolved,
        )
        return

    # Build (signal_proxy, correct) pairs for logistic fit
    pairs: list[tuple[float, float]] = []
    for r in resolved:
        hwp = r.get("home_win_pct")
        if hwp is None:
            continue
        conf_prob  = max(hwp, 1.0 - hwp)
        signal_est = conf_prob * 10  # rough proxy for signal value
        correct    = 1.0 if r["actual_winner"] == r["predicted_winner"] else 0.0
        pairs.append((signal_est, correct))

    mp, sl = _fit_logistic(pairs)
    overall_wr = sum(1 for r in resolved if r["actual_winner"] == r["predicted_winner"]) / n_resolved

    log.info(
        "Refit complete: midpoint=%.4f slope=%.6f overall_win_rate=%.4f (n=%d)",
        mp, sl, overall_wr, n_resolved,
    )

    band_rates = _win_rates_by_band(resolved)
    tier_rates = _win_rates_by_tier(resolved)

    new_calibration = {
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "seasons_used":     [2026],
        "total_graded":     n_resolved,
        "logistic_params":  {"midpoint": mp, "slope": sl},
        "win_rates": {
            "by_signal_band": band_rates,
            "by_tier":        tier_rates,
        },
        "tier_recommendations": {
            "elite_cutoff": _ELITE_CUTOFF,
            "great_cutoff": _GREAT_CUTOFF,
        },
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
    parser = argparse.ArgumentParser(description="Refit calibration.json from 2026 history")
    parser.add_argument("--dry-run", action="store_true", help="Print new params without writing")
    args = parser.parse_args()
    refit(dry_run=args.dry_run)
