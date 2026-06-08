"""
MLB Edge — Quantitative Edge Research
Finds multi-criteria profitable betting edges from backtest + closing-lines data.

Usage:
    python pipeline/edge_research.py
    python pipeline/edge_research.py --season-check   # break out ROI by clean season
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

# Windows consoles default to cp1252, which can't encode the ── box-drawing
# characters used throughout the report. Force UTF-8 so the documented
# `python pipeline/edge_research.py` invocation runs natively.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent

# ── Data Loading ──────────────────────────────────────────────────────────────

def american_to_decimal(odds: float) -> float:
    if odds >= 0:
        return 1 + odds / 100
    return 1 - 100 / odds


def load_backtest() -> pd.DataFrame:
    with open(ROOT / "docs" / "backtest.json") as f:
        data = json.load(f)
    games = data["games"]
    return pd.DataFrame(games)


def load_history_2026() -> pd.DataFrame:
    """Load 2026 resolved games from history.json (has odds + outcomes missing from backtest)."""
    hist_path = ROOT / "docs" / "history.json"
    if not hist_path.exists():
        return pd.DataFrame()
    raw = json.loads(hist_path.read_text(encoding="utf-8"))
    games = raw if isinstance(raw, list) else raw.get("games", [])
    df = pd.DataFrame(games)
    if df.empty:
        return df
    # Normalize to backtest field names
    df = df.rename(columns={"vegas_total": "closing_total"})
    df["season"] = 2026
    # Keep only records with complete odds + outcome
    needed = ["home_ml", "away_ml", "closing_total", "model_edge_ml", "actual_winner", "total_went_over"]
    df = df.dropna(subset=needed).copy()
    return df


def build_dataset() -> pd.DataFrame:
    """Load backtest (2022-2025) + history.json (2026) — full closing odds, model edge, outcomes."""
    bt = load_backtest()
    # 2021 has lookahead bias in pitcher caches; 2026 rows in backtest lack odds (use history instead)
    bt = bt[(bt["season"] >= 2022) & (bt["season"] <= 2025)].copy()
    bt = bt.dropna(subset=["home_ml", "closing_total", "model_edge_ml"]).copy()

    h26 = load_history_2026()
    df = pd.concat([bt, h26], ignore_index=True) if not h26.empty else bt

    # Filter out extreme/corrupted odds values
    df = df[
        (df["home_ml"] >= -600) & (df["home_ml"] <= 600) &
        (df["closing_total"] >= 5.0) & (df["closing_total"] <= 14.0)
    ].copy()

    # ── Derived fields ─────────────────────────────────────────────────────────
    df["pitcher_diff"]      = df["pitcher_score_home"] - df["pitcher_score_away"]
    df["avg_pitcher_score"] = (df["pitcher_score_home"] + df["pitcher_score_away"]) / 2
    df["total_deviation"]   = df["predicted_total"] - df["closing_total"]

    # Outcome flags (backtest already has total_went_over as bool)
    df["home_won"] = df["actual_winner"] == "home"
    df["away_won"] = df["actual_winner"] == "away"

    # Model-pick direction
    df["model_pick_away"] = df["home_win_pct"] < 0.5
    df["model_pick_home"] = df["home_win_pct"] >= 0.5

    # Totals direction
    df["model_under"] = df["total_deviation"] < 0
    df["model_over"]  = df["total_deviation"] > 0

    return df


# ── ROI Calculation ───────────────────────────────────────────────────────────

def roi_ml(df: pd.DataFrame, pick_away: bool) -> dict:
    """Compute moneyline ROI betting $100 per game on the given side."""
    if len(df) == 0:
        return {"n": 0, "win_pct": None, "roi_pct": None}
    if pick_away:
        wins = df["away_won"].sum()
        returns = df.apply(
            lambda r: (american_to_decimal(r["away_ml"]) - 1) * 100 if r["away_won"] else -100,
            axis=1,
        )
    else:
        wins = df["home_won"].sum()
        returns = df.apply(
            lambda r: (american_to_decimal(r["home_ml"]) - 1) * 100 if r["home_won"] else -100,
            axis=1,
        )
    n = len(df)
    total_roi = returns.sum() / (n * 100) * 100
    return {"n": n, "win_pct": round(wins / n * 100, 1), "roi_pct": round(total_roi, 2)}


def roi_totals(df: pd.DataFrame, pick_under: bool) -> dict:
    """Compute totals ROI betting $100 per game on OVER or UNDER."""
    if len(df) == 0:
        return {"n": 0, "win_pct": None, "roi_pct": None}
    # Cast to int to avoid Python bool bitwise ~ bug (not operator is safe, ~ is not)
    went_over = df["total_went_over"].astype(int)
    if pick_under:
        wins = (1 - went_over).sum()
        returns = df.apply(
            lambda r: (american_to_decimal(r["under_price"]) - 1) * 100
                      if not bool(r["total_went_over"]) else -100,
            axis=1,
        )
    else:
        wins = went_over.sum()
        returns = df.apply(
            lambda r: (american_to_decimal(r["over_price"]) - 1) * 100
                      if bool(r["total_went_over"]) else -100,
            axis=1,
        )
    n = len(df)
    total_roi = returns.sum() / (n * 100) * 100
    return {"n": n, "win_pct": round(wins / n * 100, 1), "roi_pct": round(total_roi, 2)}


# ── Report helpers ────────────────────────────────────────────────────────────

def flag(roi: float | None, n: int) -> str:
    if roi is None or n < 100:
        return "  (n<100)"
    if roi >= 10:
        return "  ← STRONG ✓"
    if roi >= 5:
        return "  ← implement?"
    if roi >= 2:
        return ""
    return "  ✗"


def row(label: str, res: dict, extra: str = "") -> str:
    if res["n"] == 0:
        return f"  {label:<50}  n=0"
    f = flag(res["roi_pct"], res["n"])
    return (
        f"  {label:<52}  n={res['n']:>5}  "
        f"win={res['win_pct']:>5.1f}%  ROI={res['roi_pct']:>+7.2f}%{f}{extra}"
    )


def season_breakdown(df: pd.DataFrame, pick_away: bool, is_totals: bool, pick_under: bool = True) -> str:
    parts = []
    for yr in sorted(df["season"].unique()):
        sub = df[df["season"] == yr]
        if is_totals:
            r = roi_totals(sub, pick_under)
        else:
            r = roi_ml(sub, pick_away)
        roi = r['roi_pct'] if r['roi_pct'] is not None else 0.0
        parts.append(f"{yr}:{r['n']}g/{'+' if roi >= 0 else ''}{roi:.1f}%" if r['n'] else f"{yr}:n=0")
    return "  seasons: " + "  ".join(parts)


# ── Main Analysis ─────────────────────────────────────────────────────────────

def run(season_check: bool = False) -> None:
    print("Loading data...")
    df = build_dataset()
    seasons = sorted(df["season"].unique())
    print(f"Dataset: {len(df):,} games ({seasons[0]}-{seasons[-1]}, seasons: {seasons})\n")

    MIN_N = 100

    # ═══════════════════════════════════════════════════════════════════════════
    # MONEYLINE CUTS
    # ═══════════════════════════════════════════════════════════════════════════
    print("=" * 80)
    print("MONEYLINE EDGE RESEARCH  (betting the model-favored side)")
    print("=" * 80)

    # Baseline splits
    print("\n── Baseline ──")
    all_away = df[df["model_pick_away"]]
    all_home = df[df["model_pick_home"]]
    print(row("All games — bet model-favored team (away)", roi_ml(all_away, pick_away=True)))
    print(row("All games — bet model-favored team (home)", roi_ml(all_home, pick_away=False)))

    # ML-A: Strong away model edge
    print("\n── ML-A: Model edge bands (betting model's pick) ──")
    for lo, hi, pick_away, label in [
        (-0.20, -0.15, True,  "model edge < -15% → bet away"),
        (-0.15, -0.10, True,  "model edge -15% to -10% → bet away"),
        (-0.10, -0.05, True,  "model edge -10% to -5% → bet away"),
        (-0.05,  0.00, True,  "model edge  -5% to  0% → bet away"),
        ( 0.00,  0.05, False, "model edge   0% to +5% → bet home"),
        ( 0.05,  0.10, False, "model edge  +5% to +10% → bet home"),
        ( 0.10,  0.20, False, "model edge > +10% → bet home"),
    ]:
        if pick_away:
            sub = df[(df["model_edge_ml"] >= lo) & (df["model_edge_ml"] < hi) & (df["model_pick_away"])]
        else:
            sub = df[(df["model_edge_ml"] >= lo) & (df["model_edge_ml"] < hi) & (df["model_pick_home"])]
        r = roi_ml(sub, pick_away=pick_away)
        print(row(f"  {label}", r))

    # ML-A consolidated: model picks away AND has ≥10% edge vs market
    print()
    strong_away = df[(df["model_edge_ml"] <= -0.10) & (df["model_pick_away"])]
    print(row("ML-A  Away edge ≤ -10% (model picks away)", roi_ml(strong_away, pick_away=True)))
    if season_check:
        print(season_breakdown(strong_away, pick_away=True, is_totals=False))

    # ML-B: + pitcher advantage
    print("\n── ML-B/D: + pitcher differential ──")
    for diff_thresh, label in [
        (-0.05, "ML-B  Away edge ≤-10% + pitcher_diff ≤ -0.05"),
        (-0.10, "ML-D  Away edge ≤-10% + pitcher_diff ≤ -0.10 (strong)"),
        (-0.15, "      Away edge ≤-10% + pitcher_diff ≤ -0.15 (elite gap)"),
    ]:
        sub = strong_away[strong_away["pitcher_diff"] <= diff_thresh]
        r = roi_ml(sub, pick_away=True)
        print(row(label, r))
        if season_check and r["n"] >= MIN_N:
            print(season_breakdown(sub, pick_away=True, is_totals=False))

    # ML-C: + away ML price band (underdog range)
    print("\n── ML-C: Away edge ≤-10% × ML price band ──")
    for ml_lo, ml_hi, label in [
        (100, 999, "away_ml +100 and above (away underdog)"),
        (100, 145, "away_ml +100 to +145 (light dog)"),
        (145, 200, "away_ml +145 to +200 (medium dog)"),
        (200, 999, "away_ml +200+ (big dog)"),
        (-120, 100, "away_ml -120 to +100 (near pick)"),
        (-999, -120, "away_ml < -120 (away is favorite)"),
    ]:
        sub = strong_away[(strong_away["away_ml"] >= ml_lo) & (strong_away["away_ml"] < ml_hi)]
        r = roi_ml(sub, pick_away=True)
        print(row(f"  {label}", r))
        if season_check and r["n"] >= MIN_N:
            print(season_breakdown(sub, pick_away=True, is_totals=False))

    # ML-E: + total line filter
    print("\n── ML-E: Away edge ≤-10% × total line ──")
    for tot_lo, tot_hi, label in [
        (0, 7.5,  "total < 7.5  (pitcher's game)"),
        (7.5, 8.5, "total 7.5-8.5"),
        (8.5, 9.5, "total 8.5-9.5"),
        (9.5, 99,  "total ≥ 9.5  (run-fest)"),
    ]:
        sub = strong_away[(strong_away["closing_total"] >= tot_lo) & (strong_away["closing_total"] < tot_hi)]
        r = roi_ml(sub, pick_away=True)
        print(row(f"  total {tot_lo}-{tot_hi}: {label}", r))

    # ML-F: Triple combo
    print("\n── ML-F: Triple combo (away edge + pitcher + total) ──")
    triple = strong_away[
        (strong_away["pitcher_diff"] <= -0.05) &
        (strong_away["closing_total"] <= 8.5)
    ]
    print(row("ML-F  Away edge ≤-10% + pitcher ≤-0.05 + total ≤ 8.5", roi_ml(triple, pick_away=True)))
    if season_check and len(triple) >= MIN_N:
        print(season_breakdown(triple, pick_away=True, is_totals=False))

    triple2 = strong_away[
        (strong_away["pitcher_diff"] <= -0.10) &
        (strong_away["closing_total"] <= 8.5)
    ]
    print(row("      Away edge ≤-10% + pitcher ≤-0.10 + total ≤ 8.5", roi_ml(triple2, pick_away=True)))

    # ML-G: Tight ML game + pitcher edge (no model-edge filter)
    print("\n── ML-G: Tight ML game + pitcher edge ──")
    tight_ml = df[(df["away_ml"] >= -120) & (df["away_ml"] <= 120)]
    for diff_thresh, label in [
        (-0.05, "tight ML (-120 to +120) + pitcher_diff ≤ -0.05 (away SP better)"),
        (-0.10, "tight ML (-120 to +120) + pitcher_diff ≤ -0.10"),
        ( 0.05, "tight ML (-120 to +120) + pitcher_diff ≥ +0.05 (home SP better)"),
        ( 0.10, "tight ML (-120 to +120) + pitcher_diff ≥ +0.10"),
    ]:
        if diff_thresh < 0:
            sub = tight_ml[tight_ml["pitcher_diff"] <= diff_thresh]
            r = roi_ml(sub, pick_away=True)
        else:
            sub = tight_ml[tight_ml["pitcher_diff"] >= diff_thresh]
            r = roi_ml(sub, pick_away=False)
        print(row(f"  {label}", r))
        if season_check and r["n"] >= MIN_N:
            print(season_breakdown(sub, pick_away=(diff_thresh < 0), is_totals=False))

    # ML-H: ML bands across all model-pick games
    print("\n── ML-H: Away model pick × ML price band (all games) ──")
    for ml_lo, ml_hi, label in [
        (130, 999, "away +130 and above"),
        (100, 130, "away +100 to +130"),
        (-115, 100, "near pick-em (-115 to +100)"),
        (-150, -115, "away -115 to -150 (slight fav)"),
        (-999, -150, "away > -150 (big favorite)"),
    ]:
        sub = all_away[(df["away_ml"] >= ml_lo) & (df["away_ml"] < ml_hi)]
        r = roi_ml(sub, pick_away=True)
        print(row(f"  {label}", r))
        if season_check and r["n"] >= MIN_N:
            print(season_breakdown(sub, pick_away=True, is_totals=False))

    # ML-I: Home edge reversal
    print("\n── ML-I: Home model edge ≥ +10% — fade or follow? ──")
    strong_home = df[df["model_edge_ml"] >= 0.10]
    print(row("  Bet home (follow the model)",  roi_ml(strong_home, pick_away=False)))
    print(row("  Bet away (fade the model)",     roi_ml(strong_home, pick_away=True)))

    # ML-J: Both aces (avg pitcher score) × tight game
    print("\n── ML-J: Average pitcher quality × game tightness ──")
    for avg_thresh, label in [(0.55, "avg_sp ≥ 0.55"), (0.60, "avg_sp ≥ 0.60"), (0.65, "avg_sp ≥ 0.65")]:
        ace_games = df[df["avg_pitcher_score"] >= avg_thresh]
        tight_ace = ace_games[(ace_games["away_ml"] >= -115) & (ace_games["away_ml"] <= 115)]
        print(row(f"  {label} + tight (-115/+115) → model pick", roi_ml(
            tight_ace[tight_ace["model_pick_away"]], pick_away=True)))

    # ═══════════════════════════════════════════════════════════════════════════
    # TOTALS CUTS
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("TOTALS EDGE RESEARCH")
    print("=" * 80)

    # TOT-A: Baselines
    print("\n── TOT-A: Baseline ──")
    model_under = df[df["model_under"]]
    model_over  = df[df["model_over"]]
    print(row("TOT-A  Model UNDER all games", roi_totals(model_under, pick_under=True)))
    print(row("       Model OVER  all games", roi_totals(model_over,  pick_under=False)))
    print(row("       All games UNDER (blind)", roi_totals(df, pick_under=True)))
    print(row("       All games OVER  (blind)", roi_totals(df, pick_under=False)))

    # TOT-B: UNDER + pitcher quality
    print("\n── TOT-B: Model UNDER × pitcher quality ──")
    for thresh, label in [(0.50, "avg_sp ≥ 0.50"), (0.55, "avg_sp ≥ 0.55"), (0.60, "avg_sp ≥ 0.60"), (0.65, "avg_sp ≥ 0.65")]:
        sub = model_under[model_under["avg_pitcher_score"] >= thresh]
        r = roi_totals(sub, pick_under=True)
        print(row(f"  UNDER + {label}", r))
        if season_check and r["n"] >= MIN_N:
            print(season_breakdown(sub, pick_away=False, is_totals=True, pick_under=True))

    # TOT-C: UNDER + high total line + elite pitching
    print("\n── TOT-C: Model UNDER × total line band × pitcher quality ──")
    for tot_lo, tot_hi in [(0, 7.5), (7.5, 8.5), (8.5, 9.0), (9.0, 9.5), (9.5, 99)]:
        for sp_thresh in [0.0, 0.55, 0.60]:
            sub = model_under[
                (model_under["closing_total"] >= tot_lo) &
                (model_under["closing_total"] < tot_hi) &
                (model_under["avg_pitcher_score"] >= sp_thresh)
            ]
            sp_str = f" + avg_sp≥{sp_thresh}" if sp_thresh > 0 else ""
            label = f"UNDER total {tot_lo}-{tot_hi}{sp_str}"
            r = roi_totals(sub, pick_under=True)
            if r["n"] >= 30:  # lower threshold to show picture
                print(row(f"  {label}", r))

    # TOT-E: UNDER + strong model deviation
    print("\n── TOT-E/H: Strong model deviation from Vegas total ──")
    for dev, label in [
        (-0.50, "UNDER + model sees 0.5+ under Vegas"),
        (-0.75, "UNDER + model sees 0.75+ under Vegas"),
        (-1.00, "UNDER + model sees 1.0+ under Vegas"),
        ( 0.50, "OVER  + model sees 0.5+ over Vegas"),
        ( 0.75, "OVER  + model sees 0.75+ over Vegas"),
        ( 1.00, "OVER  + model sees 1.0+ over Vegas"),
    ]:
        if dev < 0:
            sub = df[df["total_deviation"] <= dev]
            r = roi_totals(sub, pick_under=True)
        else:
            sub = df[df["total_deviation"] >= dev]
            r = roi_totals(sub, pick_under=False)
        print(row(f"  {label}", r))
        if season_check and r["n"] >= MIN_N:
            print(season_breakdown(sub, pick_away=False, is_totals=True, pick_under=(dev < 0)))

    # TOT-F: UNDER by total line band (solo) + blind comparison
    print("\n── TOT-F: UNDER by total line band (model filter vs blind) ──")
    for lo, hi in [(0, 7.0), (7.0, 7.5), (7.5, 8.0), (8.0, 8.5), (8.5, 9.0), (9.0, 9.5), (9.5, 99)]:
        sub_model = model_under[(model_under["closing_total"] >= lo) & (model_under["closing_total"] < hi)]
        sub_blind = df[(df["closing_total"] >= lo) & (df["closing_total"] < hi)]
        r_model = roi_totals(sub_model, pick_under=True)
        r_blind = roi_totals(sub_blind, pick_under=True)
        pct_covered = f"({r_model['n']}/{r_blind['n']} = {r_model['n']/r_blind['n']*100:.0f}%)" if r_blind['n'] else ""
        print(row(f"  model UNDER, line {lo}-{hi} {pct_covered}", r_model))
        print(row(f"  blind UNDER, line {lo}-{hi}", r_blind))
        print()
    if season_check:
        print("\n  Season breakdown for UNDER 8.0-8.5 (model filter):")
        sub = model_under[(model_under["closing_total"] >= 8.0) & (model_under["closing_total"] < 8.5)]
        print(season_breakdown(sub, pick_away=False, is_totals=True, pick_under=True))
        print("  Season breakdown for UNDER 9.0-9.5 (model filter):")
        sub = model_under[(model_under["closing_total"] >= 9.0) & (model_under["closing_total"] < 9.5)]
        print(season_breakdown(sub, pick_away=False, is_totals=True, pick_under=True))
        print("  Season breakdown for UNDER 8.0-8.5 (BLIND):")
        sub = df[(df["closing_total"] >= 8.0) & (df["closing_total"] < 8.5)]
        print(season_breakdown(sub, pick_away=False, is_totals=True, pick_under=True))
        print("  Season breakdown for UNDER 9.0-9.5 (BLIND):")
        sub = df[(df["closing_total"] >= 9.0) & (df["closing_total"] < 9.5)]
        print(season_breakdown(sub, pick_away=False, is_totals=True, pick_under=True))

    # TOT-G: OVER + weak pitching matchup
    print("\n── TOT-G: Model OVER + weak pitcher matchup ──")
    for thresh, label in [(0.45, "avg_sp ≤ 0.45"), (0.40, "avg_sp ≤ 0.40"), (0.35, "avg_sp ≤ 0.35")]:
        sub = model_over[model_over["avg_pitcher_score"] <= thresh]
        r = roi_totals(sub, pick_under=False)
        print(row(f"  OVER + {label} (both SPs soft)", r))

    # TOT-I: UNDER correlated with away model edge
    print("\n── TOT-I: Correlated UNDER + away model edge (same direction) ──")
    for edge_thresh, label in [
        (-0.05, "UNDER + away model edge ≤ -5%"),
        (-0.10, "UNDER + away model edge ≤ -10%"),
    ]:
        sub = model_under[model_under["model_edge_ml"] <= edge_thresh]
        r = roi_totals(sub, pick_under=True)
        print(row(f"  {label}", r))

    # TOT-J: UNDER when under_price is near flat/positive
    print("\n── TOT-J: Model UNDER × closing UNDER price ──")
    for price_lo, price_hi, label in [
        (-100, 999, "UNDER priced near even or plus (+)"),
        (-105, 999, "UNDER priced -105 or better"),
        (-110, -106, "UNDER priced -106 to -110 (standard vig)"),
        (-120, -111, "UNDER priced -111 to -120 (vig against)"),
        (-999, -120, "UNDER priced worse than -120"),
    ]:
        sub = model_under[
            (model_under["under_price"] >= price_lo) &
            (model_under["under_price"] <= price_hi)
        ]
        r = roi_totals(sub, pick_under=True)
        print(row(f"  {label}", r))

    # ═══════════════════════════════════════════════════════════════════════════
    # COMBINATION SUMMARY — best multi-criteria edges
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("COMBINATION SUMMARY — Flagging edges with ROI ≥ 5%, n ≥ 100")
    print("=" * 80)

    candidates = []

    combos = [
        # label, filter_func, is_totals, pick_under
        ("ML: Away edge ≤-10%",
         lambda d: d[d["model_edge_ml"] <= -0.10], False, None),
        ("ML: Away edge ≤-10% + pitcher ≤-0.05",
         lambda d: d[(d["model_edge_ml"] <= -0.10) & (d["pitcher_diff"] <= -0.05)], False, None),
        ("ML: Away edge ≤-10% + pitcher ≤-0.10",
         lambda d: d[(d["model_edge_ml"] <= -0.10) & (d["pitcher_diff"] <= -0.10)], False, None),
        ("ML: Away edge ≤-10% + pitcher ≤-0.05 + total ≤8.5",
         lambda d: d[(d["model_edge_ml"] <= -0.10) & (d["pitcher_diff"] <= -0.05) & (d["closing_total"] <= 8.5)], False, None),
        ("ML: Away edge ≤-10% + away_ml in [+100, +145]",
         lambda d: d[(d["model_edge_ml"] <= -0.10) & (d["away_ml"] >= 100) & (d["away_ml"] <= 145)], False, None),
        ("ML: Away edge ≤-10% + away_ml in [+100, +200]",
         lambda d: d[(d["model_edge_ml"] <= -0.10) & (d["away_ml"] >= 100) & (d["away_ml"] <= 200)], False, None),
        ("ML: Tight (-115/+115) + pitcher ≤-0.05 (away SP)",
         lambda d: d[(d["away_ml"] >= -115) & (d["away_ml"] <= 115) & (d["pitcher_diff"] <= -0.05)], False, None),
        ("ML: Tight (-120/+120) + pitcher ≤-0.10 (away SP)",
         lambda d: d[(d["away_ml"] >= -120) & (d["away_ml"] <= 120) & (d["pitcher_diff"] <= -0.10)], False, None),
        ("TOT: Model UNDER (all)",
         lambda d: d[d["model_under"]], True, True),
        ("TOT: UNDER + avg_sp ≥ 0.55",
         lambda d: d[d["model_under"] & (d["avg_pitcher_score"] >= 0.55)], True, True),
        ("TOT: UNDER + avg_sp ≥ 0.60",
         lambda d: d[d["model_under"] & (d["avg_pitcher_score"] >= 0.60)], True, True),
        ("TOT: UNDER + total ≥ 8.5 + avg_sp ≥ 0.55",
         lambda d: d[d["model_under"] & (d["closing_total"] >= 8.5) & (d["avg_pitcher_score"] >= 0.55)], True, True),
        ("TOT: UNDER + total ≥ 8.5 + avg_sp ≥ 0.60",
         lambda d: d[d["model_under"] & (d["closing_total"] >= 8.5) & (d["avg_pitcher_score"] >= 0.60)], True, True),
        ("TOT: UNDER + dev ≤ -0.75",
         lambda d: d[d["model_under"] & (d["total_deviation"] <= -0.75)], True, True),
        ("TOT: UNDER + dev ≤ -0.50 + avg_sp ≥ 0.55",
         lambda d: d[d["model_under"] & (d["total_deviation"] <= -0.50) & (d["avg_pitcher_score"] >= 0.55)], True, True),
        ("TOT: OVER + avg_sp ≤ 0.40",
         lambda d: d[d["model_over"] & (d["avg_pitcher_score"] <= 0.40)], True, False),
        ("TOT: OVER + dev ≥ +0.75",
         lambda d: d[d["model_over"] & (d["total_deviation"] >= 0.75)], True, False),
    ]

    for label, fn, is_totals, pick_under in combos:
        sub = fn(df)
        if is_totals:
            r = roi_totals(sub, pick_under=pick_under)
        else:
            r = roi_ml(sub, pick_away=True)
        if r["n"] >= 100 and r["roi_pct"] is not None and r["roi_pct"] >= 3:
            candidates.append((label, r))

    candidates.sort(key=lambda x: x[1]["roi_pct"], reverse=True)

    print()
    print(f"  {'Label':<55}  {'n':>6}  {'Win%':>6}  {'ROI%':>8}")
    print("  " + "-" * 80)
    for label, r in candidates:
        f = "  ★ IMPLEMENT" if r["roi_pct"] >= 5 else "  · consider"
        print(f"  {label:<55}  {r['n']:>6}  {r['win_pct']:>5.1f}%  {r['roi_pct']:>+7.2f}%{f}")

    if season_check:
        print("\n── Season-by-season for top candidates ──")
        for label, r in candidates[:8]:
            if r["roi_pct"] < 5:
                continue
            fn_match = next(fn for lbl, fn, *_ in combos if lbl == label)
            is_tot   = next(it for lbl, _, it, *_ in combos if lbl == label)
            pu       = next(pu for lbl, _, _, pu in combos if lbl == label)
            sub = fn_match(df)
            print(f"\n  {label} (ROI {r['roi_pct']:+.1f}%, n={r['n']})")
            for yr in sorted(df["season"].unique()):
                s = sub[sub["season"] == yr]
                if is_tot:
                    yr_r = roi_totals(s, pick_under=pu)
                else:
                    yr_r = roi_ml(s, pick_away=True)
                sign = "+" if yr_r["roi_pct"] and yr_r["roi_pct"] >= 0 else ""
                print(f"    {yr}: n={yr_r['n']:>4}  win={yr_r['win_pct'] or 0:>5.1f}%  ROI={sign}{yr_r['roi_pct'] or 0:.1f}%")

    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season-check", action="store_true",
                        help="Break out ROI by season for top candidates")
    args = parser.parse_args()
    run(season_check=args.season_check)
