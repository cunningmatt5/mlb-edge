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

# Ensure the project root is importable so this runs both as a script
# (`python pipeline/research_model_dev.py`) and as a module (`python -m ...`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Reuse the validated ROI helper + American-odds conversion from the sibling script.
from pipeline.edge_research import american_to_decimal, roi_totals  # noqa: F401,E402

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


def validate_multiseason() -> None:
    """Out-of-sample check: does 'model projects below Vegas' predict UNDERs across seasons?

    Combines 2024-2025 from docs/backtest.json (now lineup-aware, so deviations are real)
    with 2026 from docs/history.json, and reports UNDER ROI by deviation threshold × season.

    IMPORTANT (2026-06-16 finding): the backtest uses prior-season stats, so its deviations
    top out near -0.5 — there are ~0 games at the live -0.75 trigger in 2024-2025, and at
    shallow thresholds the model filter is no better than the blind under. The model-dev
    edge is therefore NOT validatable out-of-sample; it lives only in 2026 live data (where
    CLV ~64% is the one independent sign it's real). Re-run this as 2026+ data accrues.
    """
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))
    bt = bt["games"] if isinstance(bt, dict) else bt
    hp = ROOT / "docs" / "history.json"
    hist = json.loads(hp.read_text(encoding="utf-8")) if hp.exists() else []
    hist = hist if isinstance(hist, list) else hist.get("games", [])

    rows = []
    for g in bt:
        s = g.get("season")
        if s not in (2024, 2025):
            continue
        ct, pt, up, tgo = g.get("closing_total"), g.get("predicted_total"), g.get("under_price"), g.get("total_went_over")
        if None not in (ct, pt, up, tgo):
            rows.append((s, ct, pt - ct, up, tgo))
    for g in hist:
        if g.get("actual_winner") not in ("home", "away"):
            continue
        ct, pt, up, tgo = g.get("vegas_total"), g.get("predicted_total"), g.get("under_price"), g.get("total_went_over")
        if None not in (ct, pt, up, tgo):
            rows.append((2026, ct, pt - ct, up, tgo))

    def roi(sub):
        if not sub:
            return "n=0"
        u = sum((american_to_decimal(r[3]) - 1) if not r[4] else -1 for r in sub)
        return f"n={len(sub)} roi={100*u/len(sub):+.1f}%"

    print("=" * 84)
    print("MULTI-SEASON OOS CHECK — model-below-Vegas (line ≤9.0) → UNDER, by season")
    print("=" * 84)
    print(f"  {'threshold':<14}{'2024':<22}{'2025':<22}{'2026':<22}")
    for thr in (-0.25, -0.35, -0.50, -0.75):
        cells = [roi([r for r in rows if r[0] == s and r[1] <= 9.0 and r[2] <= thr]) for s in (2024, 2025, 2026)]
        print(f"  dev≤{thr:<10}{cells[0]:<22}{cells[1]:<22}{cells[2]:<22}")
    print("\n  baseline (all unders, line ≤9.0):")
    for s in (2024, 2025, 2026):
        print(f"    {s}: {roi([r for r in rows if r[0] == s and r[1] <= 9.0])}")
    print("\n  VERDICT: not OOS-validatable — backtest deviations can't reach the live -0.75")
    print("  trigger; shallow-threshold proxy ≈ baseline. Keep model-dev conservative/emerging.")
    print("\nDone.")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Model-dev edge research")
    p.add_argument("--multiseason", action="store_true",
                   help="Out-of-sample 2024-2026 directional validation (vs default 2026 Kelly research)")
    args = p.parse_args()
    validate_multiseason() if args.multiseason else run()
