"""MLB Edge — Regression Analysis

Finds the strongest predictors of MLB game outcomes across three phases:
  Phase 1: Composite signals (pitcher_score, lineup_score, comps, Vegas)
  Phase 2: Raw stat components (xERA, xwOBA, barrel%, etc.) from player cache
  Phase 3: ROI analysis with leave-one-year-out (LOYO) cross-validation

Usage:
    python -m pipeline.regression_analysis
    python -m pipeline.regression_analysis --out data/regression_report.json
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
from scipy.stats import pearsonr
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LinearRegression, LogisticRegression, LogisticRegressionCV
from sklearn.preprocessing import StandardScaler

ROOT          = Path(__file__).parent.parent
BACKTEST_PATH = ROOT / "docs" / "backtest.json"
HISTORY_PATH  = ROOT / "docs" / "history.json"
SEASONS_DIR   = ROOT / "data" / "seasons"

# Pitcher stats available in ALL seasons (2021-2025)
PITCHER_STATS = ["xera", "xwoba_against", "xba_against", "xslg_against"]
# Batter stats available in ALL seasons; lineup regression uses these
BATTER_STATS  = ["xwoba", "barrel_pct", "hard_hit_pct"]
# Only 2024-2025 have game_lineups.parquet
LINEUP_SEASONS = [2024, 2025]

# ── Math helpers ──────────────────────────────────────────────────────────────

def _logit(p: float) -> float:
    p = max(1e-6, min(1 - 1e-6, float(p)))
    return math.log(p / (1 - p))


def _american_to_decimal(odds: float) -> float:
    if odds >= 0:
        return 1 + odds / 100
    return 1 - 100 / odds


def _roi_from_games(games: list[dict]) -> dict:
    """Flat-$100 ROI betting the model's predicted side."""
    if not games:
        return {"n": 0, "win_pct": None, "roi_pct": None}
    total_staked = len(games) * 100
    total_return = 0.0
    wins = 0
    for g in games:
        side = g.get("bet_side") or ("home" if g["home_win_pct"] >= 0.5 else "away")
        won  = g["actual_winner"] == side
        if won:
            wins += 1
            ml = g["home_ml"] if side == "home" else g["away_ml"]
            total_return += (_american_to_decimal(ml) - 1) * 100
        else:
            total_return -= 100
    n = len(games)
    return {
        "n":       n,
        "win_pct": round(wins / n * 100, 1),
        "roi_pct": round(total_return / total_staked * 100, 2),
    }


def _bootstrap_brier_ci(y: np.ndarray, probs: np.ndarray, n: int = 500) -> tuple[float, float]:
    """95% bootstrap confidence interval for Brier score."""
    size = len(y)
    briers = []
    rng = np.random.default_rng(42)
    for _ in range(n):
        idx = rng.integers(0, size, size=size)
        briers.append(float(np.mean((probs[idx] - y[idx]) ** 2)))
    return (float(np.percentile(briers, 2.5)), float(np.percentile(briers, 97.5)))


# ── Data loading ──────────────────────────────────────────────────────────────

def load_backtest_priced() -> list[dict]:
    """Load 2022-2025 priced games (skip 2021 lookahead bias)."""
    bt = json.loads(BACKTEST_PATH.read_text(encoding="utf-8"))
    return [
        g for g in bt["games"]
        if g.get("season") in (2022, 2023, 2024, 2025)
        and g.get("home_implied_prob") is not None
        and g.get("pitcher_score_home") is not None
        and g.get("pitcher_score_away") is not None
        and g.get("comps_home_win_rate") is not None
        and g.get("model_edge_ml") is not None
        and g.get("actual_winner") in ("home", "away")
        and g.get("home_ml") is not None
        and g.get("away_ml") is not None
        and abs(g["home_ml"]) <= 600
    ]


def load_history_resolved() -> list[dict]:
    """Load 2026 resolved+priced games from history.json."""
    raw = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    records = raw if isinstance(raw, list) else raw.get("games", [])
    out = []
    for g in records:
        if (g.get("actual_winner") in ("home", "away")
                and g.get("home_ml") is not None
                and g.get("model_edge_ml") is not None
                and g.get("lineup_score_home") is not None
                and g.get("lineup_score_away") is not None
                and g.get("pitcher_score_home") is not None
                and abs(g["home_ml"]) <= 600):
            g = dict(g)
            # Derive no-vig Vegas home win prob: home_win_pct - model_edge_ml
            g["home_implied_prob"] = max(0.05, min(0.95,
                                        g["home_win_pct"] - g["model_edge_ml"]))
            g["closing_total"] = g.get("vegas_total")
            g["season"] = 2026
            out.append(g)
    return out


def load_player_caches() -> dict[int, dict]:
    """Load per-season player caches. Returns {season: {player_id: stats_dict}}."""
    caches: dict[int, dict] = {}
    for year in range(2021, 2026):
        path = SEASONS_DIR / str(year) / "player_cache.pkl"
        if path.exists():
            with open(path, "rb") as f:
                caches[year] = pickle.load(f)
    return caches


def load_game_lineups() -> dict[int, tuple[list[int], list[int]]]:
    """Load 2024+2025 game lineups. Returns {game_pk: (home_ids, away_ids)}."""
    out: dict[int, tuple[list[int], list[int]]] = {}
    for year in LINEUP_SEASONS:
        path = SEASONS_DIR / str(year) / "game_lineups.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df = df[df["batting_order"].between(1, 9)]
        for pk, grp in df.groupby("game_pk"):
            home_ids = grp[grp["side"] == "home"]["player_id"].tolist()
            away_ids = grp[grp["side"] == "away"]["player_id"].tolist()
            out[int(pk)] = (home_ids, away_ids)
    return out


# ── Feature helpers ───────────────────────────────────────────────────────────

def _get_stat(cache: dict, pid: int, key: str) -> float | None:
    player = cache.get(int(pid))
    if player is None:
        return None
    return player.get(key)


def _team_batter_avg(ids: list[int], cache: dict, key: str, min_found: int = 5) -> float | None:
    vals = [v for pid in ids if (v := _get_stat(cache, pid, key)) is not None]
    return float(np.mean(vals)) if len(vals) >= min_found else None


# ── Phase 1: Composite signal regression ─────────────────────────────────────

def phase1_composite(bt: list[dict], hist: list[dict]) -> dict:
    print("\n" + "=" * 70)
    print("  PHASE 1: COMPOSITE SIGNAL REGRESSION")
    print("=" * 70)

    # ── Dataset A: pitcher + comps + Vegas (backtest 2022-2025) ──────────
    print(f"\n  Dataset A — backtest 2022-2025:  n = {len(bt):,} games")

    ya  = np.array([1 if g["actual_winner"] == "home" else 0 for g in bt], dtype=float)
    lv  = np.array([_logit(g["home_implied_prob"]) for g in bt])
    pd_ = np.array([g["pitcher_score_home"] - g["pitcher_score_away"] for g in bt])
    cw  = np.array([g["comps_home_win_rate"] for g in bt])
    me  = np.array([g["model_edge_ml"] for g in bt])

    # Vegas-only baseline
    X_vegas = StandardScaler().fit_transform(lv.reshape(-1, 1))
    lr_v = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
    lr_v.fit(X_vegas, ya)
    p_vegas  = lr_v.predict_proba(X_vegas)[:, 1]
    brier_v  = float(np.mean((p_vegas - ya) ** 2))

    # Full Dataset A model (all 4 signals)
    Xa = StandardScaler().fit_transform(np.column_stack([lv, pd_, cw, me]))
    lr_a = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
    lr_a.fit(Xa, ya)
    pa = lr_a.predict_proba(Xa)[:, 1]
    brier_a  = float(np.mean((pa - ya) ** 2))
    delta_a  = (brier_a - brier_v) * 1000

    feat_a = ["logit_vegas", "pitcher_diff", "comps_win_rate", "model_edge_ml"]
    coef_a = lr_a.coef_[0]

    print(f"\n  Brier score  Vegas-only baseline: {brier_v:.6f}")
    print(f"  Brier score  full model (A):      {brier_a:.6f}  ({delta_a:+.2f} mBrier vs Vegas-only)")

    print(f"\n  Standardized coefficients (Dataset A):")
    print(f"    {'Feature':<22} {'Coef':>10}")
    print("    " + "-" * 34)
    for name, coef in sorted(zip(feat_a, coef_a), key=lambda x: -abs(x[1])):
        print(f"    {name:<22} {coef:>+10.4f}")

    # ── Dataset B: adds lineup_diff (history.json 2026) ─────────────────
    print(f"\n  Dataset B — history.json 2026:  n = {len(hist):,} games")

    yb  = np.array([1 if g["actual_winner"] == "home" else 0 for g in hist], dtype=float)
    lvb = np.array([_logit(g["home_implied_prob"]) for g in hist])
    pdb = np.array([g["pitcher_score_home"] - g["pitcher_score_away"] for g in hist])
    cwb = np.array([g["comps_home_win_rate"] for g in hist])
    meb = np.array([g["model_edge_ml"] for g in hist])
    ld  = np.array([g["lineup_score_home"] - g["lineup_score_away"] for g in hist])

    Xb = StandardScaler().fit_transform(np.column_stack([lvb, pdb, cwb, meb, ld]))
    lr_b = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
    lr_b.fit(Xb, yb)
    pb = lr_b.predict_proba(Xb)[:, 1]
    brier_b  = float(np.mean((pb - yb) ** 2))
    ci_lo, ci_hi = _bootstrap_brier_ci(yb, pb)

    feat_b = ["logit_vegas", "pitcher_diff", "comps_win_rate", "model_edge_ml", "lineup_diff"]
    coef_b = lr_b.coef_[0]

    print(f"  Brier score: {brier_b:.6f}  [bootstrap 95% CI: {ci_lo:.6f} – {ci_hi:.6f}]")

    print(f"\n  Standardized coefficients (Dataset B — includes lineup_diff):")
    print(f"    {'Feature':<22} {'Coef':>10}  Notes")
    print("    " + "-" * 48)
    for name, coef in sorted(zip(feat_b, coef_b), key=lambda x: -abs(x[1])):
        note = "<-- lineup signal" if name == "lineup_diff" else ""
        print(f"    {name:<22} {coef:>+10.4f}  {note}")

    # ── Pearson correlations (Dataset B) ────────────────────────────────
    print(f"\n  Pearson r vs. home_won  (Dataset B, n={len(hist)}):")
    print(f"    {'Signal':<22} {'r':>8}  {'p-value':>10}  Significant?")
    print("    " + "-" * 56)
    corr_results = {}
    for name, vals in [
        ("logit_vegas",    lvb),
        ("model_edge_ml",  meb),
        ("pitcher_diff",   pdb),
        ("lineup_diff",    ld),
        ("comps_win_rate", cwb),
    ]:
        r, p = pearsonr(vals, yb)
        corr_results[name] = round(float(r), 4)
        sig = "YES" if p < 0.05 else "no"
        print(f"    {name:<22} {r:>+8.4f}  {p:>10.4f}  {sig}")

    # ── Run total prediction (error vs. Vegas) ───────────────────────────
    print(f"\n  Run total prediction (target = actual_total - closing_total):")
    hist_totals = [g for g in hist if g.get("closing_total") and g.get("actual_total")]
    total_corr = {}
    if hist_totals:
        terr = np.array([g["actual_total"] - g["closing_total"] for g in hist_totals])
        print(f"    n = {len(hist_totals)},  mean error = {terr.mean():+.2f},  std = {terr.std():.2f}")
        print(f"    {'Signal':<22} {'Pearson r':>10}  {'p-value':>10}")
        print("    " + "-" * 46)
        for name, vals in [
            ("pitcher_diff",  np.array([g["pitcher_score_home"] - g["pitcher_score_away"] for g in hist_totals])),
            ("lineup_diff",   np.array([g["lineup_score_home"]  - g["lineup_score_away"]  for g in hist_totals])),
            ("model_edge_ml", np.array([g["model_edge_ml"] for g in hist_totals])),
        ]:
            r, p = pearsonr(vals, terr)
            total_corr[name] = round(float(r), 4)
            print(f"    {name:<22} {r:>+10.4f}  {p:>10.4f}")

    # ── ROI simulation (Dataset A) ───────────────────────────────────────
    print(f"\n  ROI simulation — betting model's pick (Dataset A, flat $100):")
    print(f"    {'Filter':<38} {'n':>6}  {'Win%':>6}  {'ROI':>8}")
    print("    " + "-" * 64)
    roi_results: dict[str, dict] = {}
    subsets = [
        ("All games",                 bt),
        ("|edge| > 0.03",             [g for g in bt if abs(g["model_edge_ml"]) > 0.03]),
        ("|edge| > 0.08",             [g for g in bt if abs(g["model_edge_ml"]) > 0.08]),
        ("Away edge  (edge <= -0.05)",[g for g in bt if g["model_edge_ml"] <= -0.05]),
        ("Home edge  (edge >= +0.05)",[g for g in bt if g["model_edge_ml"] >= 0.05]),
        ("Pitcher diff > +0.10",      [g for g in bt if g["pitcher_score_home"] - g["pitcher_score_away"] > 0.10]),
        ("Pitcher diff < -0.10",      [g for g in bt if g["pitcher_score_home"] - g["pitcher_score_away"] < -0.10]),
    ]
    for label, subset in subsets:
        r = _roi_from_games(subset)
        if r["n"] >= 50:
            flag = "  *" if r.get("roi_pct") is not None and r["roi_pct"] >= 5.0 else ""
            roi_results[label] = r
            print(f"    {label:<38} {r['n']:>6}  {r['win_pct']:>5.1f}%  {r['roi_pct']:>+7.2f}%{flag}")

    return {
        "dataset_a_n":    len(bt),
        "brier_vegas":    round(brier_v, 6),
        "brier_full_a":   round(brier_a, 6),
        "delta_mbrier_a": round(delta_a, 3),
        "coef_a": {n: round(float(c), 4) for n, c in zip(feat_a, coef_a)},
        "dataset_b_n":  len(hist),
        "brier_full_b": round(brier_b, 6),
        "brier_b_ci":   [round(ci_lo, 6), round(ci_hi, 6)],
        "coef_b": {n: round(float(c), 4) for n, c in zip(feat_b, coef_b)},
        "pearson_b": corr_results,
        "total_corr": total_corr,
    }


# ── Phase 2: Raw stat regression ──────────────────────────────────────────────

def phase2_raw_stats(bt: list[dict], caches: dict[int, dict], lineups: dict) -> dict:
    print("\n" + "=" * 70)
    print("  PHASE 2: RAW STAT REGRESSION (FEATURE IMPORTANCE)")
    print("=" * 70)

    # ── Build SP differential feature matrix ─────────────────────────────
    rows = []
    for g in bt:
        season = g.get("season")
        cache  = caches.get(season)
        if cache is None:
            continue
        hsp = g.get("home_sp_id")
        asp = g.get("away_sp_id")
        if not hsp or not asp:
            continue
        row: dict = {
            "home_won": 1 if g["actual_winner"] == "home" else 0,
            "season":   season,
            "gamePk":   g["gamePk"],
        }
        for stat in PITCHER_STATS:
            h = _get_stat(cache, hsp, stat)
            a = _get_stat(cache, asp, stat)
            row[f"{stat}_diff"] = (h - a) if (h is not None and a is not None) else None
        rows.append(row)

    df_sp = pd.DataFrame(rows)
    diff_cols = [f"{s}_diff" for s in PITCHER_STATS]
    df_clean = df_sp.dropna(subset=diff_cols).copy()

    cov_n = len(df_clean)
    cov_t = len(bt)
    print(f"\n  SP stat coverage: {cov_n:,} / {cov_t:,} games ({cov_n/cov_t*100:.0f}%)")
    print(f"  (using {len(PITCHER_STATS)} universal stats: {', '.join(PITCHER_STATS)})")

    y_sp = df_clean["home_won"].values
    X_sp = StandardScaler().fit_transform(df_clean[diff_cols].values)

    # ── Pearson correlations ─────────────────────────────────────────────
    print(f"\n  Pearson r vs. home_won  (note: xERA / xwOBA_against are 'bad' stats):")
    print(f"    positive r = higher home stat → home wins more; negative = home advantage when stat is lower")
    print(f"    {'Stat differential':<30} {'r':>8}  {'p-value':>10}  Significant?")
    print("    " + "-" * 56)
    pearson_results: dict[str, float] = {}
    for col in diff_cols:
        r, p = pearsonr(df_clean[col].values, y_sp)
        pearson_results[col] = round(float(r), 4)
        sig = "YES" if p < 0.05 else "no"
        print(f"    {col:<30} {r:>+8.4f}  {p:>10.4f}  {sig}")

    # ── Lasso logistic (L1) feature selection ────────────────────────────
    print(f"\n  Lasso feature selection (LogisticRegressionCV, L1 penalty, 5-fold CV):")
    lasso_cv = LogisticRegressionCV(
        penalty="l1", solver="liblinear", cv=5,
        max_iter=2000, random_state=42, refit=True,
    )
    lasso_cv.fit(X_sp, y_sp)
    lasso_coef = {col: round(float(c), 4) for col, c in zip(diff_cols, lasso_cv.coef_[0])}

    print(f"    Best C = {lasso_cv.C_[0]:.4f}")
    print(f"    {'Stat':<30} {'Coef':>10}  Selected?")
    print("    " + "-" * 50)
    for col, coef in sorted(lasso_coef.items(), key=lambda x: -abs(x[1])):
        selected = "YES" if abs(coef) > 1e-6 else "zeroed out"
        print(f"    {col:<30} {coef:>+10.4f}  {selected}")

    # ── RandomForest permutation importance ──────────────────────────────
    print(f"\n  RandomForest permutation importance (200 trees, 20 repeats):")
    rng = np.random.default_rng(42)
    n_test = min(2000, len(y_sp) // 5)
    idx_all  = np.arange(len(y_sp))
    test_idx = rng.choice(idx_all, n_test, replace=False)
    train_idx = np.setdiff1d(idx_all, test_idx)

    rf = RandomForestClassifier(n_estimators=200, max_depth=5, random_state=42, n_jobs=-1)
    rf.fit(X_sp[train_idx], y_sp[train_idx])
    perm = permutation_importance(
        rf, X_sp[test_idx], y_sp[test_idx], n_repeats=20, random_state=42, n_jobs=-1
    )
    rf_imp = {col: round(float(v), 5) for col, v in zip(diff_cols, perm.importances_mean)}

    print(f"    {'Stat':<30} {'Perm imp':>10}  Lasso conflict?")
    print("    " + "-" * 54)
    for col, imp in sorted(rf_imp.items(), key=lambda x: -x[1]):
        conflict = "  (Lasso zeroed!)" if abs(lasso_coef[col]) < 1e-6 and imp > 0.001 else ""
        print(f"    {col:<30} {imp:>+10.5f}{conflict}")

    # ── Consensus ranking ────────────────────────────────────────────────
    combined = {col: abs(lasso_coef[col]) + rf_imp[col] for col in diff_cols}
    print(f"\n  CONSENSUS RANKING (|Lasso coef| + RF permutation importance):")
    print(f"    {'Rank':<5} {'Stat':<30} {'Lasso':>8}  {'RF imp':>8}  {'Score':>8}")
    print("    " + "-" * 64)
    for rank, (col, score) in enumerate(sorted(combined.items(), key=lambda x: -x[1]), 1):
        print(f"    {rank:<5} {col:<30} {lasso_coef[col]:>+8.4f}  {rf_imp[col]:>8.5f}  {score:>8.4f}")

    # ── Batter lineup regression (2024-2025 only) ─────────────────────────
    bat_results: dict[str, float] = {}
    if lineups:
        print(f"\n  Batter lineup regression (2024-2025, game_lineups.parquet):")
        bat_rows = []
        for g in bt:
            if g.get("season") not in LINEUP_SEASONS:
                continue
            cache = caches.get(g["season"])
            if cache is None:
                continue
            lu = lineups.get(g.get("gamePk"))
            if lu is None:
                continue
            home_ids, away_ids = lu
            row_bat: dict = {"home_won": 1 if g["actual_winner"] == "home" else 0}
            ok = True
            for stat in BATTER_STATS:
                h = _team_batter_avg(home_ids, cache, stat)
                a = _team_batter_avg(away_ids, cache, stat)
                if h is None or a is None:
                    ok = False
                    break
                row_bat[f"{stat}_diff"] = h - a
            if ok:
                bat_rows.append(row_bat)

        if bat_rows:
            bat_df = pd.DataFrame(bat_rows)
            y_bat  = bat_df["home_won"].values
            bat_cols = [f"{s}_diff" for s in BATTER_STATS]
            print(f"    Games with complete lineup data: {len(bat_df):,}")
            print(f"    {'Stat differential':<30} {'Pearson r':>10}  {'p-value':>10}  Significant?")
            print("    " + "-" * 60)
            for col in bat_cols:
                if col in bat_df.columns:
                    r, p = pearsonr(bat_df[col].values, y_bat)
                    bat_results[col] = round(float(r), 4)
                    sig = "YES" if p < 0.05 else "no"
                    print(f"    {col:<30} {r:>+10.4f}  {p:>10.4f}  {sig}")
        else:
            print("    No complete lineup data found after filtering.")

    return {
        "sp_coverage":     cov_n,
        "pearson":         pearson_results,
        "lasso":           lasso_coef,
        "rf_importance":   rf_imp,
        "consensus":       {col: round(score, 4) for col, score in combined.items()},
        "batter_pearson":  bat_results,
    }


# ── Phase 3: ROI analysis with LOYO ──────────────────────────────────────────

def phase3_roi(bt: list[dict], hist: list[dict]) -> dict:
    print("\n" + "=" * 70)
    print("  PHASE 3: ROI ANALYSIS (LEAVE-ONE-YEAR-OUT CROSS-VALIDATION)")
    print("=" * 70)
    print("  (2021 excluded — lookahead bias; 2026 used as holdout only)\n")

    years = [2022, 2023, 2024, 2025]
    by_yr = {yr: [g for g in bt if g["season"] == yr] for yr in years}
    for yr, gs in by_yr.items():
        print(f"  {yr}: {len(gs):,} games", end="   ")
    print()

    # ── 3a: ROI by pitcher_diff quartile (LOYO) ──────────────────────────
    print(f"\n  3a. ROI by pitcher_diff quartile  (LOYO — quartile bounds from training years):")
    print(f"       {'Quartile':<16}  {'Overall':>9}  {'2022':>7}  {'2023':>7}  {'2024':>7}  {'2025':>7}  {'Consistent?':>12}")
    print("       " + "-" * 76)

    qlabels = ["Q1 (away_sp better)", "Q2", "Q3", "Q4 (home_sp better)"]
    q_results: dict[str, dict] = {}

    for qi in range(4):
        pd_bounds_all = np.percentile(
            [g["pitcher_score_home"] - g["pitcher_score_away"] for g in bt], [25, 50, 75]
        )
        lo_glob = (-99, pd_bounds_all[0], pd_bounds_all[1], pd_bounds_all[2])[qi]
        hi_glob = (pd_bounds_all[0], pd_bounds_all[1], pd_bounds_all[2], 99)[qi]
        overall = _roi_from_games([g for g in bt if lo_glob <= (g["pitcher_score_home"] - g["pitcher_score_away"]) < hi_glob])

        yr_rois: dict[int, dict] = {}
        for test_yr in years:
            train = [g for yr2, gs in by_yr.items() for g in gs if yr2 != test_yr]
            train_pd = [g["pitcher_score_home"] - g["pitcher_score_away"] for g in train]
            bds = np.percentile(train_pd, [25, 50, 75])
            lo = (-99, bds[0], bds[1], bds[2])[qi]
            hi = (bds[0], bds[1], bds[2], 99)[qi]
            yr_sub = [g for g in by_yr[test_yr] if lo <= (g["pitcher_score_home"] - g["pitcher_score_away"]) < hi]
            yr_rois[test_yr] = _roi_from_games(yr_sub)

        consistent = sum(1 for r in yr_rois.values() if r.get("roi_pct") is not None and r["roi_pct"] > 0)
        loyo_flag = f"  {consistent}/4"
        label = qlabels[qi]
        yr_str = "  ".join(
            f"{yr_rois[yr]['roi_pct']:>+6.1f}%" if yr_rois[yr]["roi_pct"] is not None else "    N/A"
            for yr in years
        )
        overall_str = f"{overall['roi_pct']:>+8.2f}%" if overall["roi_pct"] is not None else "      N/A"
        q_results[label] = {
            "overall_roi": overall["roi_pct"],
            "yearly":      {yr: yr_rois[yr]["roi_pct"] for yr in years},
            "consistent":  consistent,
        }
        print(f"       {label:<16}  {overall_str}  {yr_str}  {loyo_flag}")

    # ── 3b: ROI by model_edge_ml bucket (LOYO) ───────────────────────────
    print(f"\n  3b. ROI by model_edge_ml bucket:")
    edge_buckets = [
        ("strong_away", lambda g: g["model_edge_ml"] <= -0.10),
        ("mild_away",   lambda g: -0.10 < g["model_edge_ml"] <= -0.03),
        ("neutral",     lambda g: -0.03 < g["model_edge_ml"] < 0.03),
        ("mild_home",   lambda g: 0.03 <= g["model_edge_ml"] < 0.10),
        ("strong_home", lambda g: g["model_edge_ml"] >= 0.10),
    ]
    print(f"    {'Bucket':<14}  {'n':>5}  {'Win%':>6}  {'ROI':>8}  {'2022':>7}  {'2023':>7}  {'2024':>7}  {'2025':>7}  {'LOYO':>6}")
    print("    " + "-" * 82)
    edge_results: dict[str, dict] = {}
    for bname, bfn in edge_buckets:
        subset = [g for g in bt if bfn(g)]
        overall = _roi_from_games(subset)
        yr_rois = {}
        for yr in years:
            yr_rois[yr] = _roi_from_games([g for g in by_yr[yr] if bfn(g)])
        consistent = sum(1 for r in yr_rois.values() if r.get("roi_pct") is not None and r["roi_pct"] > 0)
        edge_results[bname] = {"n": overall["n"], "roi": overall["roi_pct"], "consistent": consistent}
        yr_str = "  ".join(
            f"{yr_rois[yr]['roi_pct']:>+6.1f}%" if yr_rois[yr]["roi_pct"] is not None else "    N/A"
            for yr in years
        )
        win_str = f"{overall['win_pct']:>5.1f}%" if overall["win_pct"] is not None else "   N/A"
        roi_str = f"{overall['roi_pct']:>+7.2f}%"
        loyo_str = f"{consistent}/4"
        print(f"    {bname:<14}  {overall['n']:>5}  {win_str}  {roi_str}  {yr_str}  {loyo_str}")

    # ── 3c: 2-signal interaction grid ────────────────────────────────────
    print(f"\n  3c. 2-signal grid: pitcher_diff x model_edge_ml  (n>=80 & ROI>=+5% = STRONG *)")
    pd_all = [g["pitcher_score_home"] - g["pitcher_score_away"] for g in bt]
    p33, p66 = np.percentile(pd_all, [33, 66])
    pitcher_bands = [
        ("pd_low",  f"pd<{p33:.2f}",          lambda g: (g["pitcher_score_home"] - g["pitcher_score_away"]) < p33),
        ("pd_mid",  f"{p33:.2f}<=pd<{p66:.2f}", lambda g: p33 <= (g["pitcher_score_home"] - g["pitcher_score_away"]) < p66),
        ("pd_high", f"pd>={p66:.2f}",           lambda g: (g["pitcher_score_home"] - g["pitcher_score_away"]) >= p66),
    ]
    edge_bands = [
        ("away_edge",  "edge<=-0.05",   lambda g: g["model_edge_ml"] <= -0.05),
        ("neutral",    "-0.05<e<0.05",  lambda g: -0.05 < g["model_edge_ml"] < 0.05),
        ("home_edge",  "edge>=+0.05",   lambda g: g["model_edge_ml"] >= 0.05),
    ]
    header = f"    {'pitcher_diff':<14}" + "".join(f"  {eb[1]:<20}" for eb in edge_bands)
    print(header)
    print("    " + "-" * (14 + 22 * 3))
    grid_results: dict[str, dict] = {}
    for pb_name, pb_label, pb_fn in pitcher_bands:
        row_str = f"    {pb_label:<14}"
        for eb_name, eb_label, eb_fn in edge_bands:
            subset = [g for g in bt if pb_fn(g) and eb_fn(g)]
            r = _roi_from_games(subset)
            key = f"{pb_name}x{eb_name}"
            grid_results[key] = {"n": r["n"], "roi": r["roi_pct"]}
            if r["n"] >= 80 and r["roi_pct"] is not None:
                star = " *" if r["roi_pct"] >= 5.0 else ""
                cell = f"n={r['n']:>4} {r['roi_pct']:>+.1f}%{star}"
            else:
                cell = f"n={r['n']:>4} {'n<80':>6}" if r["n"] > 0 else f"{'n<80':>14}"
            row_str += f"  {cell:<20}"
        print(row_str)

    # ── 3d: 2026 holdout — lineup_diff quartile ───────────────────────────
    print(f"\n  3d. 2026 holdout — ROI by lineup_diff quartile (no LOYO — single season):")
    hist_ld = [g["lineup_score_home"] - g["lineup_score_away"] for g in hist]
    ld_q    = np.percentile(hist_ld, [25, 50, 75])
    ld_labels = ["Q1 (away lineup)", "Q2", "Q3", "Q4 (home lineup)"]
    ld_results: dict[str, dict] = {}
    for qi, label in enumerate(ld_labels):
        lo = (-99, ld_q[0], ld_q[1], ld_q[2])[qi]
        hi = (ld_q[0], ld_q[1], ld_q[2], 99)[qi]
        sub = [g for g in hist if lo <= (g["lineup_score_home"] - g["lineup_score_away"]) < hi]
        r = _roi_from_games(sub)
        ld_results[label] = r
        win_s = f"{r['win_pct']:.1f}%" if r["win_pct"] is not None else "  N/A"
        roi_s = f"{r['roi_pct']:>+7.2f}%" if r["roi_pct"] is not None else "      N/A"
        print(f"    {label:<18}  n={r['n']:>4}  win={win_s}  ROI={roi_s}")

    # ── 3e: Season robustness for the best bucket ─────────────────────────
    print(f"\n  3e. Season robustness — best model_edge_ml bucket:")
    best_bucket = max(edge_results, key=lambda k: edge_results[k]["roi"] or -99)
    best_fn     = next(fn for nm, fn in ((b, f) for b, f in edge_buckets) if nm == best_bucket)
    print(f"    Signal: model_edge_ml = '{best_bucket}'")
    print(f"    {'Year':<8}  {'n':>6}  {'Win%':>6}  {'ROI':>8}")
    print("    " + "-" * 34)
    robust_data: dict[str, dict] = {}
    for yr in years:
        sub = [g for g in by_yr[yr] if best_fn(g)]
        r   = _roi_from_games(sub)
        robust_data[str(yr)] = r
        win_s = f"{r['win_pct']:.1f}%" if r["win_pct"] is not None else "  N/A"
        roi_s = f"{r['roi_pct']:>+7.2f}%" if r["roi_pct"] is not None else "      N/A"
        print(f"    {yr:<8}  {r['n']:>6}  {win_s}  {roi_s}")
    # 2026 holdout
    sub26 = [g for g in hist if best_fn(g)]
    r26   = _roi_from_games(sub26)
    robust_data["2026_holdout"] = r26
    win_s = f"{r26['win_pct']:.1f}%" if r26["win_pct"] is not None else "  N/A"
    roi_s = f"{r26['roi_pct']:>+7.2f}%" if r26["roi_pct"] is not None else "      N/A"
    print(f"    {'2026 (holdout)':<8}  {r26['n']:>6}  {win_s}  {roi_s}")

    return {
        "pitcher_quartile_roi": q_results,
        "edge_bucket_roi":      edge_results,
        "grid_roi":             grid_results,
        "lineup_quartile_2026": {k: v for k, v in ld_results.items()},
        "best_bucket_robust":   {"bucket": best_bucket, "years": robust_data},
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run(out: str | None = None) -> None:
    print("\n" + "=" * 70)
    print("  MLB EDGE  —  REGRESSION ANALYSIS")
    print("  Finds the strongest predictors of game outcomes")
    print("=" * 70 + "\n")

    print("Loading data...")
    bt      = load_backtest_priced()
    hist    = load_history_resolved()
    caches  = load_player_caches()
    lineups = load_game_lineups()
    print(f"  Backtest 2022-2025 (priced): {len(bt):,} games")
    print(f"  History.json 2026 (resolved+priced): {len(hist):,} games")
    print(f"  Player caches loaded: seasons {sorted(caches.keys())}")
    print(f"  Game lineups (2024-2025): {len(lineups):,} games")

    r1 = phase1_composite(bt, hist)
    r2 = phase2_raw_stats(bt, caches, lineups)
    r3 = phase3_roi(bt, hist)

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"  Phase 1: Dataset A Brier {r1['brier_full_a']:.6f} "
          f"vs Vegas-only {r1['brier_vegas'  ]:.6f} "
          f"({r1['delta_mbrier_a']:+.2f} mBrier)")
    print(f"           Dataset B (2026) Brier {r1['brier_full_b']:.6f} "
          f"[CI {r1['brier_b_ci'][0]:.5f}–{r1['brier_b_ci'][1]:.5f}]")
    print(f"           Strongest composite signal: "
          f"{max(r1['pearson_b'], key=lambda k: abs(r1['pearson_b'][k]))} "
          f"(r = {max(r1['pearson_b'].values(), key=abs):+.4f})")
    top_sp = max(r2["pearson"], key=lambda k: abs(r2["pearson"][k]))
    print(f"  Phase 2: Strongest SP raw stat: {top_sp} (r = {r2['pearson'][top_sp]:+.4f})")
    top_lasso = max(r2["lasso"], key=lambda k: abs(r2["lasso"][k]))
    print(f"           Top Lasso feature:    {top_lasso} (coef = {r2['lasso'][top_lasso]:+.4f})")
    best_e  = max(r3["edge_bucket_roi"], key=lambda k: r3["edge_bucket_roi"][k]["roi"] or -99)
    best_r  = r3["edge_bucket_roi"][best_e]["roi"]
    best_c  = r3["edge_bucket_roi"][best_e]["consistent"]
    print(f"  Phase 3: Best edge bucket: {best_e} ROI={best_r:+.2f}% ({best_c}/4 years positive)")
    strong = [(k, v) for k, v in r3["grid_roi"].items() if v["n"] >= 80 and (v["roi"] or 0) >= 5.0]
    if strong:
        print(f"           STRONG GRID CELLS ({len(strong)}):")
        for key, val in sorted(strong, key=lambda x: -(x[1]["roi"] or 0)):
            print(f"             {key}: n={val['n']}  ROI={val['roi']:>+.1f}%")
    else:
        print(f"           No grid cells with n>=80 AND ROI>=+5%")

    if out:
        Path(out).write_text(
            json.dumps({"phase1": r1, "phase2": r2, "phase3": r3}, indent=2),
            encoding="utf-8",
        )
        print(f"\n  Results written to {out}")

    print("\n" + "=" * 70 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Regression analysis on MLB prediction signals")
    parser.add_argument("--out", default=None, help="Optional path to write JSON results to")
    args = parser.parse_args()
    run(out=args.out)
