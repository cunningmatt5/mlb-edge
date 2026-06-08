"""
MLB Edge — Validate the Monte Carlo divergence signal (2022-2025).

Consumes docs/mc_history.json (produced by mc_backfill.py) and asks the same
question we asked of the Elite Away / High Confidence tiers: does the MC's
divergence from Vegas actually predict outcomes profitably, or is "divergence is
signal" an unvalidated claim?

Two signals:
  - TOTALS: mc_total - closing_total  → OVER/UNDER (MC runs systematically below
    Vegas, so the question is whether the DEGREE of divergence predicts UNDERs).
  - MONEYLINE: mc_win_pct - vegas_home_prob → favored side.

Fidelity caveat (printed in the verdict): the historical MC is a CRUDER proxy of
the live MC — prior-season stats (point-in-time, not lookahead), league-average
batter K/BB, no platoon splits, weather/rest=0. A null result is suggestive but
not fully damning; a positive result needs confirmation on clean forward data
(now being persisted via history.py).

Usage:
    python pipeline/research_mc.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from edge_research import american_to_decimal, roi_ml, roi_totals  # noqa: F401

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
MIN_SEASON_N = 20
MIN_SLICE_N = 100


def load() -> pd.DataFrame:
    raw = json.loads((ROOT / "docs" / "mc_history.json").read_text(encoding="utf-8"))
    df = pd.DataFrame(raw)
    for c in ("mc_total", "closing_total", "mc_win_pct", "vegas_home_prob",
              "predicted_total", "home_ml", "away_ml", "over_price", "under_price",
              "actual_total"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    # totals need under/over price + outcome; ML needs odds + winner
    df["away_won"] = df["actual_winner"] == "away"
    df["home_won"] = df["actual_winner"] == "home"
    df["mc_tot_dev"]    = df["mc_total"] - df["closing_total"]
    df["model_tot_dev"] = df["predicted_total"] - df["closing_total"]
    df["mc_ml_dev"]     = df["mc_win_pct"] - df["vegas_home_prob"]
    return df


# ── robustness eval (totals + ML favored-side) ──────────────────────────────────

def _floor(seasons: dict) -> tuple[int, int, bool]:
    counted = {y: r for y, r in seasons.items() if r["n"] >= MIN_SEASON_N}
    pos = sum(1 for r in counted.values() if (r["roi_pct"] or 0) > 0)
    nc = len(counted)
    # Only 2024-25 have real lineups → a 2-season window. Require ALL seasons
    # positive when nc<=3; allow one down year only with 4+ seasons.
    need = nc if nc <= 3 else nc - 1
    return pos, nc, (nc >= 2 and pos >= need)


def evaluate(df: pd.DataFrame, kind: str, **kw) -> dict:
    fn = (lambda d: roi_totals(d, pick_under=kw["pick_under"])) if kind == "tot" \
        else (lambda d: roi_ml(d, pick_away=kw["pick_away"]))
    overall = fn(df)
    seasons = {int(y): fn(df[df["season"] == y]) for y in sorted(df["season"].unique())}
    pos, nc, floor_ok = _floor(seasons)
    passed = overall["n"] >= MIN_SLICE_N and floor_ok
    return {"overall": overall, "seasons": seasons, "pos": pos, "nc": nc, "passed": passed}


def fmt(res: dict) -> str:
    o = res["overall"]
    if o["n"] == 0:
        return "n=0"
    return (f"n={o['n']:>5}  win={o['win_pct']:>5.1f}%  ROI={o['roi_pct']:>+7.2f}%   "
            f"[{res['pos']}/{res['nc']} sea+ → {'PASS ✓' if res['passed'] else 'fail ✗'}]")


def seasons_line(res: dict, indent="        ") -> None:
    parts = []
    for yr, r in res["seasons"].items():
        if r["n"] == 0:
            continue
        roi = r["roi_pct"] or 0.0
        parts.append(f"{yr}:{r['n']}g/{'+' if roi >= 0 else ''}{roi:.1f}%")
    print(indent + "  ".join(parts))


def line(lbl: str, res: dict) -> None:
    print(f"  {lbl:<44}  {fmt(res)}")


# ── report ──────────────────────────────────────────────────────────────────────

def run() -> None:
    df = load()
    tot = df.dropna(subset=["mc_total", "closing_total", "under_price", "over_price", "total_went_over"])
    tot = tot[tot["lineup_ok"]] if "lineup_ok" in tot else tot
    ml = df.dropna(subset=["mc_win_pct", "vegas_home_prob", "home_ml", "away_ml"])
    ml = ml[ml["home_ml"].between(-600, 600) & ml["away_ml"].between(-600, 600)]
    if "lineup_ok" in ml:
        ml = ml[ml["lineup_ok"]]   # exclude placeholder-lineup sims (2022-23 lack lineup data)

    print("=" * 92)
    print("MC DIVERGENCE VALIDATION (2022-2025)  —  is 'divergence is signal' actually true?")
    print("=" * 92)
    print(f"  totals-usable: {len(tot)}  |  ML-usable: {len(ml)}")
    print(f"  mc_total mean={df['mc_total'].mean():.2f}  closing mean={df['closing_total'].mean():.2f}"
          f"  → systematic MC lean={df['mc_tot_dev'].mean():+.2f} runs")
    print(f"  mc_win_pct mean={df['mc_win_pct'].mean():.3f}  vegas_home mean={df['vegas_home_prob'].mean():.3f}"
          f"  → systematic ML lean={df['mc_ml_dev'].mean():+.3f}")
    for yr in sorted(df["season"].unique()):
        s = df[df["season"] == yr]
        print(f"    {yr}: {len(s)} games  mc_lean={s['mc_tot_dev'].mean():+.2f}  lineup_ok={int(s['lineup_ok'].sum())}")

    # ── TOTALS ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 92)
    print("TOTALS — bet UNDER/OVER by MC-vs-Vegas total divergence")
    print("=" * 92)
    print("\n  baseline (blind, all games):")
    line("blind UNDER", evaluate(tot, "tot", pick_under=True))
    line("blind OVER",  evaluate(tot, "tot", pick_under=False))

    print("\n  UNDER by MC divergence band (mc_total - closing_total):")
    for lo, hi, lbl in [(-99, -3.0, "dev ≤ -3.0 (MC way below)"),
                        (-3.0, -2.0, "dev -3.0..-2.0"),
                        (-2.0, -1.0, "dev -2.0..-1.0"),
                        (-1.0, 0.0, "dev -1.0..0"),
                        (0.0, 99, "dev ≥ 0 (MC above Vegas)")]:
        sub = tot[(tot["mc_tot_dev"] > lo) & (tot["mc_tot_dev"] <= hi)]
        res = evaluate(sub, "tot", pick_under=True)
        line(f"  {lbl}", res)
        if res["overall"]["n"] >= MIN_SLICE_N:
            seasons_line(res)

    print("\n  OVER when MC is ABOVE Vegas (the rarer direction):")
    for lo, lbl in [(0.5, "dev ≥ +0.5"), (1.0, "dev ≥ +1.0")]:
        sub = tot[tot["mc_tot_dev"] >= lo]
        line(f"  {lbl}", evaluate(sub, "tot", pick_under=False))

    # ── MONEYLINE ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 92)
    print("MONEYLINE — bet the side MC favors vs Vegas no-vig (mc_win_pct - vegas_home_prob)")
    print("=" * 92)
    print("\n  bet HOME when MC above Vegas by band:")
    for lo, lbl in [(0.05, "mc-veg ≥ +0.05"), (0.10, "mc-veg ≥ +0.10"), (0.15, "mc-veg ≥ +0.15")]:
        sub = ml[ml["mc_ml_dev"] >= lo]
        res = evaluate(sub, "ml", pick_away=False)
        line(f"  {lbl}", res)
        if res["overall"]["n"] >= MIN_SLICE_N:
            seasons_line(res)
    print("\n  bet AWAY when MC below Vegas by band (note systematic away-lean):")
    for hi, lbl in [(-0.05, "mc-veg ≤ -0.05"), (-0.10, "mc-veg ≤ -0.10"), (-0.15, "mc-veg ≤ -0.15")]:
        sub = ml[ml["mc_ml_dev"] <= hi]
        res = evaluate(sub, "ml", pick_away=True)
        line(f"  {lbl}", res)
        if res["overall"]["n"] >= MIN_SLICE_N:
            seasons_line(res)

    # ── REDUNDANCY vs primary model ─────────────────────────────────────────────
    print("\n" + "=" * 92)
    print("REDUNDANCY — is the MC total signal just restating the primary model's deviation?")
    print("=" * 92)
    corr = tot[["mc_tot_dev", "model_tot_dev"]].corr().iloc[0, 1]
    print(f"  corr(mc_total_dev, model_predicted_total_dev) = {corr:.3f}")
    print("  UNDER when BOTH MC and model are >=1 run below Vegas:")
    both = tot[(tot["mc_tot_dev"] <= -1.0) & (tot["model_tot_dev"] <= -1.0)]
    line("  both ≤ -1.0 → UNDER", evaluate(both, "tot", pick_under=True))
    mc_only = tot[(tot["mc_tot_dev"] <= -1.0) & (tot["model_tot_dev"] > -1.0)]
    line("  MC≤-1 but model>-1 → UNDER", evaluate(mc_only, "tot", pick_under=True))

    print("\n" + "=" * 92)
    print("VERDICT")
    print("=" * 92)
    print("  Read: a signal is promotable only if a divergence band clears the floor")
    print("  (n≥100, ≥3 of 4 seasons +) AND beats the blind baseline. Caveat: historical")
    print("  MC is a CRUDER proxy (prior-season stats, league-avg batter K/BB, no platoon,")
    print("  weather=0) — a null result is suggestive; a positive needs forward-clean")
    print("  confirmation (mc_win_pct/mc_total now persisted in history.py).")
    print("\nDone.")


if __name__ == "__main__":
    run()
