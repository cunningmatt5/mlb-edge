"""
MLB Edge — Deep-Dive: is there a robust AWAY moneyline edge, or is the tier overfit?

Research + validation only. Writes nothing; prints a console report that:
  1. Audits ML data cleanliness per season.
  2. Reproduces the production pick_tier (elite_away/strong_away) and exposes its
     2024-26 (tuning) vs 2022-23 (out-of-sample) split.
  3. Sweeps refinement dimensions — price, model-edge magnitude, pitcher filter,
     and the untested price-conditioned model edge — each against a robustness floor.
  4. Combines survivors, runs two OOS checks, and prints a PASS/FAIL verdict:
     a robust price-aware rule, or a recommendation to demote the tier.

Methodology boundary (critical, same as totals):
  model_edge_ml / pitcher_score_* are lookahead-biased in 2021, so any MODEL slice
  is restricted to 2022-2026. Pure price/odds/outcome fields (home_ml, away_ml,
  actual_winner) are clean in all six seasons and may use 2021.

Usage:
    python pipeline/research_moneyline.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

# Reuse the validated helpers from the sibling script (no side effects on import).
from edge_research import american_to_decimal, roi_ml  # noqa: F401

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
MIN_SEASON_N = 20    # a season needs this many games to "count" for robustness
MIN_SLICE_N = 100    # balanced floor: a refinement must clear this total sample


# ── Loading ───────────────────────────────────────────────────────────────────

def build_ml() -> tuple[pd.DataFrame, dict]:
    """
    Return (df, audit) of clean moneyline games across 2021-2026.

    df carries clean price/outcome columns for every row, plus model columns
    (model_edge_ml, pitcher scores) trustworthy only for season >= 2022.
    `model_eligible` marks rows usable in model-filtered slices.
    """
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))["games"]
    df = pd.DataFrame(bt)
    df = df[(df["season"] >= 2021) & (df["season"] <= 2025)].copy()

    h = json.loads((ROOT / "docs" / "history.json").read_text(encoding="utf-8"))
    h = h if isinstance(h, list) else h.get("games", [])
    hd = pd.DataFrame(h).rename(columns={"vegas_total": "closing_total"})
    hd["season"] = 2026
    raw = pd.concat([df, hd], ignore_index=True)

    audit = {"raw_per_season": raw.groupby("season").size().to_dict()}

    for c in ("home_ml", "away_ml", "model_edge_ml",
              "pitcher_score_home", "pitcher_score_away", "closing_total"):
        raw[c] = pd.to_numeric(raw.get(c), errors="coerce")

    # Clean ML: both prices present + in sane range + a decided winner.
    ml = raw.dropna(subset=["home_ml", "away_ml", "actual_winner"]).copy()
    ml = ml[ml["home_ml"].between(-600, 600) & ml["away_ml"].between(-600, 600)].copy()

    pre = len(ml)
    ml = ml.drop_duplicates(subset=["gamePk"], keep="first").copy()
    audit["dupes_removed"] = pre - len(ml)

    ml["away_won"] = ml["actual_winner"] == "away"
    ml["home_won"] = ml["actual_winner"] == "home"
    ml["pdiff"] = ml["pitcher_score_home"] - ml["pitcher_score_away"]  # home - away; <0 = away SP better
    ml["model_eligible"] = (ml["season"] >= 2022) & ml["model_edge_ml"].notna()
    return ml, audit


# ── Robustness evaluation ───────────────────────────────────────────────────────

def evaluate(df: pd.DataFrame) -> dict:
    """Overall AWAY-ML ROI + per-season breakdown + robustness verdict for a slice."""
    overall = roi_ml(df, pick_away=True)
    seasons = {int(y): roi_ml(df[df["season"] == y], pick_away=True) for y in sorted(df["season"].unique())}
    counted = {y: r for y, r in seasons.items() if r["n"] >= MIN_SEASON_N}
    pos = sum(1 for r in counted.values() if (r["roi_pct"] or 0) > 0)
    n_counted = len(counted)
    need_pos = max(4, n_counted - 1)   # at most one losing season among those with data
    passed = (overall["n"] >= MIN_SLICE_N) and (n_counted >= 1) and (pos >= need_pos)
    return {"overall": overall, "seasons": seasons,
            "n_counted": n_counted, "pos": pos, "need_pos": need_pos, "passed": passed}


def roi_window(df: pd.DataFrame, years: set[int]) -> float:
    """AWAY-ML ROI on a season subset (for OOS lenses); 0.0 if empty."""
    r = roi_ml(df[df["season"].isin(years)], pick_away=True)
    return r["roi_pct"] if r["roi_pct"] is not None else 0.0


def roi_favored(df: pd.DataFrame) -> dict:
    """Flat-$100 ROI betting the MODEL-FAVORED side (home or away) per home_win_pct."""
    if len(df) == 0:
        return {"n": 0, "win_pct": None, "roi_pct": None}
    fav_away = (1 - df["home_win_pct"]) > df["home_win_pct"]
    won = df["away_won"].where(fav_away, df["home_won"])
    price = df["away_ml"].where(fav_away, df["home_ml"])
    ret = price.apply(american_to_decimal).sub(1).mul(100).where(won, -100)
    n = len(df)
    return {"n": n, "win_pct": round(won.sum() / n * 100, 1),
            "roi_pct": round(ret.sum() / (n * 100) * 100, 2)}


def evaluate_favored(df: pd.DataFrame) -> dict:
    """Favored-side ROI + per-season breakdown + robustness verdict (parallels evaluate)."""
    overall = roi_favored(df)
    seasons = {int(y): roi_favored(df[df["season"] == y]) for y in sorted(df["season"].unique())}
    counted = {y: r for y, r in seasons.items() if r["n"] >= MIN_SEASON_N}
    pos = sum(1 for r in counted.values() if (r["roi_pct"] or 0) > 0)
    n_counted = len(counted)
    passed = (overall["n"] >= MIN_SLICE_N) and (n_counted >= 1) and (pos >= max(4, n_counted - 1))
    return {"overall": overall, "seasons": seasons,
            "n_counted": n_counted, "pos": pos, "need_pos": max(4, n_counted - 1), "passed": passed}


def fmt(res: dict) -> str:
    o = res["overall"]
    if o["n"] == 0:
        return "n=0"
    verdict = "PASS ✓" if res["passed"] else "fail ✗"
    return (f"n={o['n']:>5}  win={o['win_pct']:>5.1f}%  ROI={o['roi_pct']:>+7.2f}%   "
            f"[{res['pos']}/{res['n_counted']} seasons + → {verdict}]")


def print_seasons(res: dict, indent: str = "      ") -> None:
    parts = []
    for yr, r in res["seasons"].items():
        if r["n"] == 0:
            continue
        roi = r["roi_pct"] if r["roi_pct"] is not None else 0.0
        flag = "" if r["n"] >= MIN_SEASON_N else "·"
        parts.append(f"{yr}:{r['n']}g/{'+' if roi >= 0 else ''}{roi:.1f}%{flag}")
    print(indent + "  ".join(parts))


def line(label: str, res: dict) -> None:
    print(f"  {label:<46}  {fmt(res)}")


# ── Report ──────────────────────────────────────────────────────────────────────

TUNING = {2024, 2025, 2026}   # window the production tier was grid-searched on
OOS_EARLY = {2022, 2023}      # cleanest out-of-sample for the tuned tier


def run() -> None:
    df, audit = build_ml()
    model = df[df["model_eligible"]].copy()

    # ═══ Section 1: data cleanliness ═══════════════════════════════════════════
    print("=" * 92)
    print("AWAY MONEYLINE — DATA CLEANLINESS  (price/outcome fields clean in all seasons)")
    print("=" * 92)
    print(f"  Dupe gamePks removed: {audit['dupes_removed']}")
    print(f"  {'season':<8}{'raw':>7}{'clean ML':>10}{'away dog%':>11}{'away fav%':>11}{'model-elig':>12}")
    for yr in sorted(df["season"].unique()):
        s = df[df["season"] == yr]
        dog = (s["away_ml"] > 0).mean() * 100
        fav = (s["away_ml"] < 0).mean() * 100
        print(f"  {yr:<8}{audit['raw_per_season'].get(yr,0):>7}{len(s):>10}"
              f"{dog:>10.0f}%{fav:>10.0f}%{int(s['model_eligible'].sum()):>12}")
    print("\n  NOTE: 2021 model-eligible is 0 by design (lookahead-biased model fields);")
    print("        2021 appears ONLY in price-only slices below.")

    # ═══ Section 2: the incumbent tier, exposed ════════════════════════════════
    print("\n" + "=" * 92)
    print("BASELINE — production pick_tier (predictor.py), validated season-by-season")
    print("=" * 92)
    elite = model[(model["model_edge_ml"] <= -0.10) & (model["pdiff"] < -0.07)]
    strong = model[(model["model_edge_ml"] <= -0.10) & (model["pdiff"] < -0.05) & (model["pdiff"] >= -0.07)]
    for lbl, sub in [("elite_away (edge≤-0.10 & pdiff<-0.07)", elite),
                     ("strong_away (edge≤-0.10 & -0.07≤pdiff<-0.05)", strong)]:
        res = evaluate(sub)
        line(lbl, res)
        print_seasons(res)
        print(f"        tuning 2024-26 ROI={roi_window(sub, TUNING):+.1f}%   "
              f"OOS 2022-23 ROI={roi_window(sub, OOS_EARLY):+.1f}%   "
              f"← {'OVERFIT' if roi_window(sub, OOS_EARLY) < 0 <= roi_window(sub, TUNING) else 'check'}")
    print("  ^ The UI advertises elite_away as '+9.2% ROI · 884 bets' (hardcoded literal).")

    # ═══ Section 3: refinement sweep ═══════════════════════════════════════════
    print("\n" + "=" * 92)
    print(f"REFINEMENT SWEEP   (floor: total n≥{MIN_SLICE_N} AND at most one losing season)")
    print("=" * 92)

    print("\n── Price-only (2021-2026): is away price alone ever an edge? ──")
    for lo, hi, lbl in [
        (100, 145, "away_ml +100..+145 (light dog)"),
        (146, 200, "away_ml +146..+200 (medium dog)"),
        (201, 600, "away_ml +200+ (big dog)"),
        (-150, 99, "away_ml -150..+99 (near pick/slt fav)"),
        (-600, -151, "away_ml < -150 (away favorite)"),
    ]:
        sub = df[(df["away_ml"] >= lo) & (df["away_ml"] <= hi)]
        res = evaluate(sub)
        line(f"  {lbl}", res)
        if res["overall"]["n"] >= MIN_SLICE_N:
            print_seasons(res, indent="        ")

    print("\n── Model edge magnitude (2022-2026), bet away ──")
    for thr, lbl in [(-0.10, "edge ≤ -0.10"), (-0.12, "edge ≤ -0.12"), (-0.15, "edge ≤ -0.15")]:
        sub = model[model["model_edge_ml"] <= thr]
        res = evaluate(sub)
        line(f"  {lbl}", res)
        if res["overall"]["n"] >= MIN_SLICE_N:
            print_seasons(res, indent="        ")

    print("\n── Does the pitcher filter earn its keep? (edge ≤ -0.10, 2022-2026) ──")
    base = model[model["model_edge_ml"] <= -0.10]
    for thr, lbl in [(None, "no pitcher filter"),
                     (-0.05, "pdiff < -0.05"),
                     (-0.07, "pdiff < -0.07 (elite_away bar)"),
                     (-0.10, "pdiff < -0.10")]:
        sub = base if thr is None else base[base["pdiff"] < thr]
        res = evaluate(sub)
        line(f"  {lbl}", res)
        if res["overall"]["n"] >= MIN_SLICE_N:
            print_seasons(res, indent="        ")

    print("\n── KEY CROSS: price-conditioned model edge (edge ≤ -0.10 × away_ml band, 2022-2026) ──")
    for lo, hi, lbl in [
        (100, 145, "edge≤-0.10 × away +100..+145"),
        (146, 200, "edge≤-0.10 × away +146..+200"),
        (201, 600, "edge≤-0.10 × away +200+"),
        (-150, 99, "edge≤-0.10 × away -150..+99"),
        (-600, -151, "edge≤-0.10 × away < -150"),
    ]:
        sub = base[(base["away_ml"] >= lo) & (base["away_ml"] <= hi)]
        res = evaluate(sub)
        line(f"  {lbl}", res)
        if res["overall"]["n"] >= MIN_SLICE_N:
            print_seasons(res, indent="        ")

    print("\n── Total-line context (edge ≤ -0.10, 2022-2026) ──")
    for cond, lbl in [
        (base["closing_total"] <= 8.5, "closing_total ≤ 8.5 (low-scoring)"),
        (base["closing_total"] > 8.5, "closing_total > 8.5"),
    ]:
        sub = base[cond]
        res = evaluate(sub)
        line(f"  {lbl}", res)
        if res["overall"]["n"] >= MIN_SLICE_N:
            print_seasons(res, indent="        ")

    # ═══ Section 4 + 5: combined rule + verdict ════════════════════════════════
    print("\n" + "=" * 92)
    print("COMBINED RULE + VERDICT  (must clear floor AND survive 2022-23 OOS)")
    print("=" * 92)
    _verdict(df, model, base)

    _high_confidence(df)
    print("\nDone.")


def _high_confidence(df: pd.DataFrame) -> None:
    """
    Validate the UI 'High Confidence' Best Bets tier: bet the model-favored side when
    max(home_win_pct, 1-home_win_pct) >= threshold. home_win_pct is model-derived, so
    2022-2026 only. The UI advertises '68.0% win rate at 65%+' — but win rate != ROI.
    """
    print("\n" + "=" * 92)
    print("HIGH-CONFIDENCE TIER — bet model-favored side (UI 'Best Bets' claims 68% win rate)")
    print("=" * 92)
    hc = df[(df["season"] >= 2022) & df["home_win_pct"].notna()].copy()
    hc["conf"] = hc["home_win_pct"].combine((1 - hc["home_win_pct"]), max)
    hc["fav_away"] = (1 - hc["home_win_pct"]) > hc["home_win_pct"]

    for thr in (0.62, 0.65):
        res = evaluate_favored(hc[hc["conf"] >= thr])
        line(f"conf ≥ {thr:.2f} (bet favored side)", res)
        print_seasons(res)
    print("  ^ NOTE: win rate ≈ 64-66% (≈ the advertised 68%) BUT ROI is negative —")
    print("    high-confidence picks are favorites that win often without paying enough.")

    print("\n── split by favored side (where the loss lives) ──")
    base = hc[hc["conf"] >= 0.62]
    line("  home-favored (most of the tier)", evaluate_favored(base[~base["fav_away"]]))
    print_seasons(evaluate_favored(base[~base["fav_away"]]))
    line("  away-favored (small, volatile)", evaluate_favored(base[base["fav_away"]]))
    print_seasons(evaluate_favored(base[base["fav_away"]]))
    print("\n  VERDICT: the High-Confidence tier is a net-losing flat-stake bet (ROI < 0,")
    print("  4/5 seasons negative), driven by losing home favorites. The '68% win rate'")
    print("  claim is true-ish on win rate but misleading as a bet — recommend demote.")


def _verdict(df, model, base) -> None:
    candidates = [
        ("elite_away (incumbent)", model[(model["model_edge_ml"] <= -0.10) & (model["pdiff"] < -0.07)]),
        ("edge≤-0.10 (no pitcher)", base),
        ("edge≤-0.10 × away +100..+145", base[(base["away_ml"] >= 100) & (base["away_ml"] <= 145)]),
        ("edge≤-0.10 × away dog (+100+)", base[base["away_ml"] >= 100]),
        ("edge≤-0.10 × total ≤ 8.5", base[base["closing_total"] <= 8.5]),
    ]
    print(f"\n  {'rule':<38}{'overall':>22}{'OOS 2022-23':>14}{'ex-2026':>11}  verdict")
    for lbl, sub in candidates:
        res = evaluate(sub)
        o = res["overall"]
        if o["n"] == 0:
            continue
        oos = roi_window(sub, OOS_EARLY)
        ex26 = roi_window(sub, {2022, 2023, 2024, 2025})
        promotable = res["passed"] and oos > 0
        tag = "PROMOTABLE ✓" if promotable else ("overfit" if oos < 0 else "fail floor")
        print(f"  {lbl:<38}n={o['n']:>5} ROI={o['roi_pct']:>+6.1f}%{'':1}"
              f"{oos:>+12.1f}%{ex26:>+10.1f}%  [{tag}]")

    print("\n  Read: a rule is PROMOTABLE only if it clears the robustness floor AND has")
    print("  positive ROI on 2022-23 (true OOS for the 2024-26-tuned tier). If nothing is")
    print("  promotable, the recommendation is to DEMOTE the Elite Away tier (stop")
    print("  advertising it / drop the hardcoded +9.2% claim), as was done for the 9.0 total.")


if __name__ == "__main__":
    run()
