"""
MLB Edge — Validating the UNDER_MODEL_DEV edge for conservative Kelly sizing.

Research + validation only. Writes nothing; prints a console report and a
RECOMMENDED kelly_win_prob to wire into edge_detector.py.

Methodology boundary (critical — this is why the script is 2026-only):
  The model-dev edge fires when the model's predicted_total diverges >=0.75 BELOW
  the Vegas total. In 2021-2025 predicted_total was Vegas-anchored (lookahead-biased,
  ~zero deviation), so backtest.json has essentially NO qualifying games — the edge
  is un-backtestable before 2026 by construction. The only honest sample is 2026
  resolved picks in history.json (single season, small n). The realized win rate is
  therefore NOT trustworthy at face value; we size Kelly off a CONSERVATIVE estimate
  (Wilson lower bound + a haircut), never the raw rate.

Trigger reproduced from edge_detector.detect_edges():
  closing_total <= 9.0  AND  (predicted_total - closing_total) <= -0.75

Usage:
    python pipeline/research_model_dev.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd

# Reuse the validated ROI helper + American-odds conversion from the sibling script.
from edge_research import american_to_decimal, roi_totals  # noqa: F401

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
DEV_THRESHOLD = -0.75   # predicted_total - closing_total <= this
MAX_LINE = 9.0          # edge only fires at closing_total <= 9.0
TOL = 1e-6

# Vig bands mirror edge_detector._VIG_* (std favorable zone is [-110, -106]).
VIG_STD_LO, VIG_STD_HI = -110, -106
VIG_SUPPRESS = -120     # worse than this → unprofitable, suppressed (no Kelly)

# Kelly conservatism knobs.
WILSON_Z = 1.2816       # ~90% one-sided lower bound — pulls small-n rates toward breakeven
HAIRCUT = 0.05          # subtract from the raw rate as an alternative conservative estimate
PROB_CEILING = 0.62     # never wire a win prob above this for a single-season edge


# ── Loading ───────────────────────────────────────────────────────────────────

def build_model_dev() -> pd.DataFrame:
    """Return resolved 2026 games that trigger the model-dev edge, with clean odds."""
    raw = json.loads((ROOT / "docs" / "history.json").read_text(encoding="utf-8"))
    games = raw if isinstance(raw, list) else raw.get("games", [])
    df = pd.DataFrame(games)
    if df.empty:
        return df
    df = df.rename(columns={"vegas_total": "closing_total"})

    for col in ("closing_total", "under_price", "predicted_total"):
        df[col] = pd.to_numeric(df.get(col), errors="coerce")
    df = df.dropna(subset=["closing_total", "under_price", "predicted_total", "total_went_over"]).copy()
    df = df.drop_duplicates(subset=["gamePk"], keep="first").copy()

    df["total_went_over"] = df["total_went_over"].astype(bool)
    df["deviation"] = df["predicted_total"] - df["closing_total"]

    # Apply the exact edge_detector trigger.
    df = df[(df["closing_total"] <= MAX_LINE + TOL) & (df["deviation"] <= DEV_THRESHOLD + TOL)].copy()
    return df


# ── Conservative win-prob estimation ──────────────────────────────────────────

def wilson_lower_bound(wins: int, n: int, z: float = WILSON_Z) -> float | None:
    """Lower bound of a binomial proportion CI — conservative for small n."""
    if n == 0:
        return None
    phat = wins / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def breakeven_prob(under_prices: pd.Series) -> float:
    """Win prob needed to break even at the median UNDER price in a slice."""
    med = under_prices.median()
    return 1.0 / american_to_decimal(med)


def conservative_prob(sub: pd.DataFrame) -> dict:
    """Recommended conservative Kelly win prob for a slice (or None if no edge survives)."""
    n = len(sub)
    if n == 0:
        return {"n": 0, "raw": None, "wilson": None, "be": None, "rec": None}
    wins = int((~sub["total_went_over"]).sum())
    raw = wins / n
    wilson = wilson_lower_bound(wins, n)
    be = breakeven_prob(sub["under_price"])
    # Take the more conservative of (Wilson LB) and (raw - haircut), cap at ceiling.
    cand = min(wilson, raw - HAIRCUT)
    rec = round(min(cand, PROB_CEILING), 3)
    # Only an edge if the conservative estimate still clears breakeven.
    if rec <= be:
        rec = None
    return {"n": n, "wins": wins, "raw": round(raw, 3), "wilson": round(wilson, 3),
            "be": round(be, 3), "rec": rec}


# ── Report ────────────────────────────────────────────────────────────────────

def _line(label: str, sub: pd.DataFrame) -> None:
    r = roi_totals(sub, pick_under=True)
    if r["n"] == 0:
        print(f"  {label:<40}  n=0")
        return
    print(f"  {label:<40}  n={r['n']:>4}  win={r['win_pct']:>5.1f}%  ROI={r['roi_pct']:>+7.2f}%")


def run() -> None:
    df = build_model_dev()
    print("=" * 84)
    print("UNDER_MODEL_DEV — 2026-only validation (single season, small n — read with care)")
    print("=" * 84)
    if df.empty:
        print("  No qualifying games found in history.json. Nothing to validate.")
        return

    # ── Overall + splits ──────────────────────────────────────────────────────
    print("\nOVERALL")
    _line("all qualifying games", df)

    print("\nBY VIG BAND (UNDER price)")
    std = df[(df["under_price"] >= VIG_STD_LO) & (df["under_price"] <= VIG_STD_HI)]
    cheap = df[df["under_price"] > VIG_STD_HI]
    mild = df[(df["under_price"] < VIG_STD_LO) & (df["under_price"] >= VIG_SUPPRESS)]
    heavy = df[df["under_price"] < VIG_SUPPRESS]
    _line("std vig (-110..-106)", std)
    _line("cheaper than std (> -106)", cheap)
    _line("mild vig-against (-111..-120)", mild)
    _line("heavy vig (< -120, SUPPRESSED)", heavy)

    print("\nBY LINE")
    for ln in (8.0, 8.5, 9.0):
        _line(f"closing_total = {ln}", df[abs(df["closing_total"] - ln) < TOL])

    print("\nBY DEVIATION MAGNITUDE")
    for thr in (-0.75, -1.0, -1.5):
        _line(f"deviation <= {thr}", df[df["deviation"] <= thr + TOL])

    # ── Recommended Kelly win prob ────────────────────────────────────────────
    print("\n" + "=" * 84)
    print("RECOMMENDED kelly_win_prob  (conservative: min(Wilson-LB, raw-haircut), capped)")
    print("=" * 84)
    print(f"  knobs: Wilson z={WILSON_Z} (~90% one-sided), haircut={HAIRCUT}, ceiling={PROB_CEILING}")

    # Sizable population = everything except heavy-vig (which we suppress anyway).
    sizable = df[df["under_price"] >= VIG_SUPPRESS]
    std_est = conservative_prob(std)
    off_est = conservative_prob(df[(df["under_price"] >= VIG_SUPPRESS)
                                   & ~((df["under_price"] >= VIG_STD_LO) & (df["under_price"] <= VIG_STD_HI))])
    all_est = conservative_prob(sizable)

    for label, est in [("std-vig subset", std_est), ("off-vig subset", off_est),
                       ("all sizable (non-heavy)", all_est)]:
        if est["n"] == 0:
            print(f"  {label:<26}  n=0")
            continue
        rec = est["rec"]
        rec_s = f"{rec:.3f}" if rec is not None else "None (no edge after conservatism)"
        print(f"  {label:<26}  n={est['n']:>4}  raw={est['raw']:.3f}  "
              f"wilson={est['wilson']:.3f}  breakeven={est['be']:.3f}  →  REC={rec_s}")

    # Wiring guidance: prefer per-tier when each has enough n; else fall back to all-sizable.
    print("\n  WIRING GUIDANCE")
    std_rec = std_est["rec"] if std_est["n"] >= 15 else all_est["rec"]
    base = std_rec if std_rec is not None else all_est["rec"]
    if base is None:
        print("    Conservative estimate does NOT clear breakeven → DO NOT Kelly-size yet.")
    else:
        off_rec = round(max(base - 0.02, (off_est["be"] or 0) + 0.001), 3)
        print(f"    _MODELDEV_KELLY_WP_STD = {base:.3f}")
        print(f"    _MODELDEV_KELLY_WP_OFF = {off_rec:.3f}   (std − 0.02, mirroring the 8.0 gap)")
        print(f"    (heavy vig < {VIG_SUPPRESS} → None, no stake; same as the 8.0 edge)")

    print("\nDone.")


if __name__ == "__main__":
    run()
