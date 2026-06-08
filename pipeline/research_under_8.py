"""
MLB Edge — Deep-Dive: refining the UNDER total=8.0 edge.

Research + validation only. Writes nothing; prints a console report that:
  1. Audits data cleanliness at the 8.0 line (per season).
  2. Establishes the baselines we must beat (blind 2021-2026, model-filtered 2022-2026).
  3. Sweeps refinement dimensions, each judged against a robustness floor.
  4. Combines the survivors into a refined rule and prints a PASS/FAIL verdict.

Methodology boundary (critical):
  predicted_total / pitcher_score_* are computed with season-wide (lookahead-biased)
  stats in 2021, so any MODEL-derived slice is restricted to 2022-2026. Pure
  line/odds/outcome fields (closing_total, under_price, total_went_over) are clean
  in all six seasons and may use 2021.

Usage:
    python pipeline/research_under_8.py
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
LINE = 8.0
TOL = 1e-6
MIN_SEASON_N = 20   # a season needs this many games at the slice to "count" for robustness
MIN_SLICE_N = 100   # balanced floor: a refinement must clear this total sample


# ── Loading ───────────────────────────────────────────────────────────────────

def _load_backtest_8() -> pd.DataFrame:
    """2021-2025 games at the 8.0 line from backtest.json (clean odds + outcomes)."""
    data = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))
    df = pd.DataFrame(data["games"])
    df = df[(df["season"] >= 2021) & (df["season"] <= 2025)].copy()
    return df


def _load_history_8() -> pd.DataFrame:
    """2026 games from history.json (backtest.json has no 2026 odds). vegas_total -> closing_total."""
    raw = json.loads((ROOT / "docs" / "history.json").read_text(encoding="utf-8"))
    games = raw if isinstance(raw, list) else raw.get("games", [])
    df = pd.DataFrame(games)
    if df.empty:
        return df
    df = df.rename(columns={"vegas_total": "closing_total"})
    df["season"] = 2026
    return df


def build_8_line() -> tuple[pd.DataFrame, dict]:
    """
    Return (df, audit) for closing_total == 8.0 across 2021-2026.

    df carries the clean blind columns for every row, plus model columns
    (predicted_total, pitcher scores) which are only trustworthy for season >= 2022.
    `model_eligible` marks rows usable in model-filtered slices.
    audit holds per-season cleanliness counts for the report.
    """
    bt = _load_backtest_8()
    h26 = _load_history_8()
    raw = pd.concat([bt, h26], ignore_index=True) if not h26.empty else bt

    audit = {"raw_per_season": raw.groupby("season").size().to_dict()}

    # Required-for-blind fields + the 8.0 line itself.
    for col in ("closing_total", "under_price", "total_went_over"):
        raw[col] = pd.to_numeric(raw[col], errors="coerce") if col != "total_went_over" else raw[col]
    blind = raw.dropna(subset=["closing_total", "under_price", "total_went_over"]).copy()
    blind = blind[abs(blind["closing_total"] - LINE) < TOL].copy()

    # Sanity filter: keep home_ml when present and in [-600, 600]; never drop for a missing ML.
    if "home_ml" in blind.columns:
        hm = pd.to_numeric(blind["home_ml"], errors="coerce")
        blind = blind[hm.isna() | ((hm >= -600) & (hm <= 600))].copy()

    # Dedupe on gamePk (defensive — backtest/history shouldn't overlap, but doubleheaders + reloads happen).
    pre = len(blind)
    blind = blind.drop_duplicates(subset=["gamePk"], keep="first").copy()
    audit["dupes_removed"] = pre - len(blind)

    # Normalize outcome + odds dtypes.
    blind["went_over"] = blind["total_went_over"].astype(bool)
    blind["under_price"] = pd.to_numeric(blind["under_price"], errors="coerce")
    blind["home_ml"] = pd.to_numeric(blind.get("home_ml"), errors="coerce")
    blind["month"] = pd.to_datetime(blind["date"], errors="coerce").dt.month

    # Model-derived columns (trustworthy 2022+ only).
    blind["predicted_total"] = pd.to_numeric(blind.get("predicted_total"), errors="coerce")
    psh = pd.to_numeric(blind.get("pitcher_score_home"), errors="coerce")
    psa = pd.to_numeric(blind.get("pitcher_score_away"), errors="coerce")
    blind["avg_pitcher_score"] = (psh + psa) / 2
    blind["total_deviation"] = blind["predicted_total"] - LINE
    blind["model_under"] = blind["total_deviation"] < 0
    blind["model_eligible"] = (blind["season"] >= 2022) & blind["predicted_total"].notna()

    return blind, audit


# ── Robustness evaluation ───────────────────────────────────────────────────────

def evaluate(df: pd.DataFrame) -> dict:
    """Overall UNDER ROI + per-season breakdown + robustness verdict for a slice."""
    overall = roi_totals(df, pick_under=True)
    seasons = {}
    for yr in sorted(df["season"].unique()):
        seasons[int(yr)] = roi_totals(df[df["season"] == yr], pick_under=True)

    # Seasons with a meaningful sample, and how many of those were profitable.
    counted = {y: r for y, r in seasons.items() if r["n"] >= MIN_SEASON_N}
    pos = sum(1 for r in counted.values() if (r["roi_pct"] or 0) > 0)
    n_counted = len(counted)

    # Balanced floor: at most one losing season among those with data, min 4 positive, n >= 100.
    need_pos = max(4, n_counted - 1)
    passed = (overall["n"] >= MIN_SLICE_N) and (n_counted >= 1) and (pos >= need_pos)

    return {
        "overall": overall, "seasons": seasons,
        "n_counted": n_counted, "pos": pos, "need_pos": need_pos, "passed": passed,
    }


def fmt_overall(res: dict) -> str:
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
        sign = "+" if roi >= 0 else ""
        flag = "" if r["n"] >= MIN_SEASON_N else "·"  # · = below counting threshold
        parts.append(f"{yr}:{r['n']}g/{sign}{roi:.1f}%{flag}")
    print(indent + "  ".join(parts))


def line(label: str, res: dict) -> None:
    print(f"  {label:<46}  {fmt_overall(res)}")


# ── Report ──────────────────────────────────────────────────────────────────────

def run() -> None:
    df, audit = build_8_line()
    blind = df
    model = df[df["model_eligible"]].copy()

    # ═══ Section 1: data cleanliness ═══════════════════════════════════════════
    print("=" * 92)
    print(f"UNDER total = {LINE}  —  DATA CLEANLINESS  (line-based fields are clean in all seasons)")
    print("=" * 92)
    print(f"  Dupe gamePks removed: {audit['dupes_removed']}")
    print(f"  {'season':<8}{'raw':>8}{'@8.0 blind':>14}{'under_price':>14}"
          f"{'mean vig':>12}{'med vig':>10}{'model-elig':>12}")
    for yr in sorted(blind["season"].unique()):
        s = blind[blind["season"] == yr]
        raw_n = audit["raw_per_season"].get(yr, 0)
        vig = s["under_price"]
        me = int(s["model_eligible"].sum())
        print(f"  {yr:<8}{raw_n:>8}{len(s):>14}{s['under_price'].notna().sum():>14}"
              f"{vig.mean():>12.1f}{vig.median():>10.1f}{me:>12}")
    print(f"\n  NOTE: 2021 model-eligible is 0 by design — predicted_total/pitcher scores are")
    print(f"        lookahead-biased in 2021, so 2021 appears ONLY in blind (line-based) slices.")

    # ═══ Section 2: baselines ══════════════════════════════════════════════════
    print("\n" + "=" * 92)
    print("BASELINES — the bar to beat")
    print("=" * 92)
    base_blind = evaluate(blind)
    line("BLIND  UNDER @ 8.0  (2021-2026)", base_blind)
    print_seasons(base_blind)
    model_under = model[model["model_under"]]
    base_model = evaluate(model_under)
    line("MODEL  UNDER @ 8.0 + model leans under (2022-2026)", base_model)
    print_seasons(base_model)
    print("  ^ current edge_detector.py UNDER_LINE_8_0 rule is the MODEL line above.")

    # ═══ Section 3: refinement sweep ═══════════════════════════════════════════
    print("\n" + "=" * 92)
    print(f"REFINEMENT SWEEP   (floor: total n≥{MIN_SLICE_N} AND at most one losing season)")
    print("=" * 92)

    print("\n── Blind-eligible (2021-2026) ──")

    print("\n  under_price band (is the edge concentrated in cheap UNDERs?):")
    for lo, hi, lbl in [
        (-100, 9999, "under_price ≥ -100 (even or plus)"),
        (-105, -100, "under_price -101..-105"),
        (-110, -106, "under_price -106..-110 (std vig)"),
        (-120, -111, "under_price -111..-120"),
        (-9999, -121, "under_price worse than -120"),
    ]:
        sub = blind[(blind["under_price"] >= lo) & (blind["under_price"] <= hi)]
        res = evaluate(sub)
        line(f"  {lbl}", res)
        if res["overall"]["n"] >= MIN_SLICE_N:
            print_seasons(res, indent="        ")

    print("\n  calendar (no park/weather field exists historically; month is the proxy):")
    for lo, hi, lbl in [
        (3, 5, "Mar-May (cold/early)"),
        (6, 7, "Jun-Jul (warm)"),
        (8, 10, "Aug-Oct (late)"),
    ]:
        sub = blind[(blind["month"] >= lo) & (blind["month"] <= hi)]
        res = evaluate(sub)
        line(f"  {lbl}", res)
        if res["overall"]["n"] >= MIN_SLICE_N:
            print_seasons(res, indent="        ")

    print("\n  home_ml context (favorite/dog shape of the game):")
    for lo, hi, lbl in [
        (-9999, -150, "home favorite ≤ -150"),
        (-150, -110, "home small fav -110..-150"),
        (-110, 110, "near pick'em -110..+110"),
        (110, 9999, "home dog ≥ +110"),
    ]:
        sub = blind[blind["home_ml"].notna() & (blind["home_ml"] > lo) & (blind["home_ml"] <= hi)]
        res = evaluate(sub)
        line(f"  {lbl}", res)
        if res["overall"]["n"] >= MIN_SLICE_N:
            print_seasons(res, indent="        ")

    print("\n── Model-filtered (2022-2026 only) ──")

    print("\n  lift from the model-under filter:")
    line("  blind @8.0, 2022-2026 (no model filter)", evaluate(model))
    line("  + model leans under (predicted < 8.0)", evaluate(model[model["model_under"]]))

    print("\n  model deviation magnitude (predicted_total below 8.0):")
    for thr, lbl in [(-0.25, "dev ≤ -0.25"), (-0.50, "dev ≤ -0.50"), (-0.75, "dev ≤ -0.75")]:
        sub = model[model["total_deviation"] <= thr]
        res = evaluate(sub)
        line(f"  {lbl}", res)
        if res["overall"]["n"] >= MIN_SLICE_N:
            print_seasons(res, indent="        ")

    print("\n  starting-pitcher quality (avg of both SP scores), model-under games:")
    mu = model[model["model_under"]]
    for thr, lbl in [(0.50, "avg_sp ≥ 0.50"), (0.55, "avg_sp ≥ 0.55"), (0.60, "avg_sp ≥ 0.60")]:
        sub = mu[mu["avg_pitcher_score"] >= thr]
        res = evaluate(sub)
        line(f"  {lbl}", res)
        if res["overall"]["n"] >= MIN_SLICE_N:
            print_seasons(res, indent="        ")

    # ═══ Section 4 + 5: combined rule + verdict ════════════════════════════════
    print("\n" + "=" * 92)
    print("COMBINED REFINED RULE + VERDICT")
    print("=" * 92)
    _verdict(blind, model, base_blind, base_model)

    print("\nDone.")


def _verdict(blind, model, base_blind, base_model) -> None:
    """
    Pick the single best independently-passing refinement per family, then report a
    sensible combination. Selection is justified on 2021-2025; 2026 is checked as
    quasi-out-of-sample (a rule that only works because of 2026 is flagged).
    """
    candidates = []

    # Blind candidates worth combining (defined by clean fields only).
    candidates.append(("BLIND base", blind, "blind"))
    candidates.append(("BLIND + under_price ≤ -111 (vig-against)",
                       blind[blind["under_price"] <= -111], "blind"))

    # Model candidates.
    mu = model[model["model_under"]]
    candidates.append(("MODEL under", mu, "model"))
    candidates.append(("MODEL under + avg_sp ≥ 0.55", mu[mu["avg_pitcher_score"] >= 0.55], "model"))
    candidates.append(("MODEL dev ≤ -0.50", model[model["total_deviation"] <= -0.50], "model"))

    print(f"\n  {'candidate rule':<46}  overall          ex-2026 (OOS check)")
    for lbl, sub, fam in candidates:
        res = evaluate(sub)
        ex26 = roi_totals(sub[sub["season"] != 2026], pick_under=True)
        o = res["overall"]
        if o["n"] == 0:
            continue
        tag = "PASS ✓" if res["passed"] else "fail ✗"
        ex_roi = ex26["roi_pct"] if ex26["roi_pct"] is not None else 0.0
        oos = "holds" if ex_roi > 0 else "COLLAPSES w/o 2026"
        print(f"  {lbl:<46}  n={o['n']:>5} ROI={o['roi_pct']:>+6.1f}% [{tag}]   "
              f"ex26 ROI={ex_roi:>+6.1f}% ({oos})")

    print(f"\n  Baselines for reference:")
    print(f"    blind 2021-2026 : ROI={base_blind['overall']['roi_pct']:+.2f}%  "
          f"({'PASS' if base_blind['passed'] else 'fail'})")
    print(f"    model 2022-2026 : ROI={base_model['overall']['roi_pct']:+.2f}%  "
          f"({'PASS' if base_model['passed'] else 'fail'})")
    print("\n  Read the table: a refined rule is only a real improvement if it (a) clears the")
    print("  robustness floor [PASS], (b) beats the relevant baseline ROI, and (c) still holds")
    print("  ex-2026. Anything that only passes because of 2026 is NOT promotable yet.")


if __name__ == "__main__":
    run()
