"""Data-fitted model weights via logistic regression on 10,369 backtest games.

Answers: do the current pitcher-diff edge multipliers (0.25x / 0.5x / 1.0x)
match what the data actually supports?  Compares Brier scores before/after and
translates fitted coefficients back into edge_mult equivalents so we can decide
whether to update predictor.py.

Usage:
    python -m pipeline.fit_weights
    python -m pipeline.fit_weights --apply       # write new edge_mult to predictor.py
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows terminals with non-Unicode codepages
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from scipy.optimize import minimize

BACKTEST_PATH = Path(__file__).parent.parent / "docs" / "backtest.json"
PREDICTOR_PATH = Path(__file__).parent / "predictor.py"

# ── Math helpers ──────────────────────────────────────────────────────────────

def _logit(p: float) -> float:
    p = max(1e-6, min(1 - 1e-6, float(p)))
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, x))))


def _brier(probs: np.ndarray, actuals: np.ndarray) -> float:
    return float(np.mean((probs - actuals) ** 2))


# ── Data loading ──────────────────────────────────────────────────────────────

def _load() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Load backtest games; return (logit_vegas, pitcher_diff, y, current_probs)."""
    bt = json.loads(BACKTEST_PATH.read_text(encoding="utf-8"))
    games = [
        g for g in bt["games"]
        if g.get("home_implied_prob") is not None
        and g.get("pitcher_score_home") is not None
        and g.get("pitcher_score_away") is not None
        and g.get("actual_winner") in ("home", "away")
        and g.get("home_win_pct") is not None
    ]

    logit_vegas   = np.array([_logit(g["home_implied_prob"])   for g in games])
    pitcher_diff  = np.array([g["pitcher_score_home"] - g["pitcher_score_away"] for g in games])
    y             = np.array([1 if g["actual_winner"] == "home" else 0 for g in games], dtype=float)
    current_probs = np.array([g["home_win_pct"] for g in games])

    return logit_vegas, pitcher_diff, y, current_probs


# ── Scipy logistic regression ─────────────────────────────────────────────────

def _neg_log_loss(coef: np.ndarray, X: np.ndarray, y: np.ndarray) -> float:
    logits = X @ coef
    probs  = np.clip(1.0 / (1.0 + np.exp(-logits)), 1e-9, 1 - 1e-9)
    return -float(np.mean(y * np.log(probs) + (1 - y) * np.log(1 - probs)))


def _neg_log_loss_grad(coef: np.ndarray, X: np.ndarray, y: np.ndarray) -> np.ndarray:
    logits = X @ coef
    probs  = 1.0 / (1.0 + np.exp(-logits))
    err    = probs - y
    return X.T @ err / len(y)


def _fit(X: np.ndarray, y: np.ndarray, x0: np.ndarray | None = None) -> np.ndarray:
    if x0 is None:
        x0 = np.zeros(X.shape[1])
    res = minimize(
        _neg_log_loss, x0, args=(X, y),
        jac=_neg_log_loss_grad,
        method="L-BFGS-B",
        options={"maxiter": 2000, "ftol": 1e-12},
    )
    return res.x


def _probs_from_coef(coef: np.ndarray, X: np.ndarray) -> np.ndarray:
    logits = X @ coef
    return 1.0 / (1.0 + np.exp(-np.clip(logits, -20, 20)))


# ── Edge-mult grid search ─────────────────────────────────────────────────────

def _grid_search_edge_mult(
    logit_vegas: np.ndarray,
    pitcher_diff: np.ndarray,
    y: np.ndarray,
    vegas_gt_054: np.ndarray,
) -> dict:
    """Brute-force search over (away_mult, consensus_home_mult, other_home_mult)."""
    best_brier = 1e9
    best = (1.0, 0.25, 0.5)

    away_mask          = pitcher_diff < 0
    consensus_home     = (pitcher_diff > 0) & vegas_gt_054
    other_home         = (pitcher_diff > 0) & ~vegas_gt_054

    for away_m in np.arange(0.0, 2.6, 0.1):
        for ch_m in np.arange(0.0, 1.1, 0.1):
            for oh_m in np.arange(0.0, 1.1, 0.1):
                mult = np.where(away_mask, away_m,
                       np.where(consensus_home, ch_m, oh_m))
                logits = logit_vegas + pitcher_diff * mult
                probs  = np.clip(1.0 / (1.0 + np.exp(-logits)), 1e-9, 1 - 1e-9)
                b = float(np.mean((probs - y) ** 2))
                if b < best_brier:
                    best_brier = b
                    best = (away_m, ch_m, oh_m)

    away_m, ch_m, oh_m = best
    mult = np.where(away_mask, away_m,
           np.where(consensus_home, ch_m, oh_m))
    logits = logit_vegas + pitcher_diff * mult
    probs  = np.clip(1.0 / (1.0 + np.exp(-logits)), 1e-9, 1 - 1e-9)

    return {
        "away_mult":           round(away_m, 2),
        "consensus_home_mult": round(ch_m, 2),
        "other_home_mult":     round(oh_m, 2),
        "brier":               round(best_brier, 6),
        "probs":               probs,
    }


# ── Apply changes to predictor.py ────────────────────────────────────────────

def _apply_edge_mults(away: float, consensus_home: float, other_home: float) -> bool:
    """Patch edge_mult values in predictor.py. Returns True if changed."""
    src = PREDICTOR_PATH.read_text(encoding="utf-8")

    # Match the three-branch if/elif/else block inside _win_probability
    old_block = (
        "        if raw_edge > 0 and vegas_home_prob > 0.54:\n"
        "            edge_mult = 0.25  # consensus home — dampened (both model and Vegas agree)\n"
        "        elif raw_edge < 0:\n"
        "            edge_mult = 1.0   # away edge — full weight (model disagrees with Vegas)\n"
        "        else:\n"
        "            edge_mult = 0.5   # home edge without strong consensus"
    )
    new_block = (
        f"        if raw_edge > 0 and vegas_home_prob > 0.54:\n"
        f"            edge_mult = {consensus_home:.2f}  # consensus home — dampened (both model and Vegas agree)\n"
        f"        elif raw_edge < 0:\n"
        f"            edge_mult = {away:.2f}   # away edge — full weight (model disagrees with Vegas)\n"
        f"        else:\n"
        f"            edge_mult = {other_home:.2f}   # home edge without strong consensus"
    )

    if old_block not in src:
        print("  [WARN] Could not locate edge_mult block in predictor.py — patch skipped.")
        return False

    new_src = src.replace(old_block, new_block, 1)
    PREDICTOR_PATH.write_text(new_src, encoding="utf-8")
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def run(apply: bool = False) -> None:
    print(f"\n{'='*64}")
    print("  DATA-FITTED MODEL WEIGHTS  (logistic regression on backtest)")
    print(f"{'='*64}\n")

    # ── Load data ────────────────────────────────────────────────────────────
    logit_vegas, pitcher_diff, y, current_probs = _load()
    n = len(y)
    print(f"Loaded {n:,} resolved, priced games from backtest.json\n")

    vegas_home_prob = np.array([_sigmoid(lv) for lv in logit_vegas])
    vegas_gt_054    = vegas_home_prob > 0.54

    # ── Brier scores: baselines ───────────────────────────────────────────────
    brier_vegas   = _brier(vegas_home_prob, y)
    brier_current = _brier(current_probs, y)

    print("BRIER SCORES (lower = better calibrated; Brier = mean squared error)")
    print(f"  Vegas-only (home_implied_prob):   {brier_vegas:.6f}")
    print(f"  Current model (home_win_pct):     {brier_current:.6f}")
    delta_current = (brier_current - brier_vegas) * 1000
    direction = "better" if delta_current < 0 else "worse"
    print(f"  Current vs. Vegas delta:          {delta_current:+.2f} mBrier ({direction} than Vegas)\n")

    # ── Model 1: Vegas-only logistic (intercept + logit_vegas) ───────────────
    ones = np.ones(n)
    X_v  = np.column_stack([ones, logit_vegas])
    coef_v = _fit(X_v, y)

    probs_v = _probs_from_coef(coef_v, X_v)
    brier_v_fit = _brier(probs_v, y)

    # ── Model 2: Joint symmetric (intercept + logit_vegas + pitcher_diff) ────
    X_j  = np.column_stack([ones, logit_vegas, pitcher_diff])
    coef_j = _fit(X_j, y)

    probs_j = _probs_from_coef(coef_j, X_j)
    brier_j = _brier(probs_j, y)

    # ── Model 3: Asymmetric (pitcher_diff gets different coef for away edge) ─
    away_diff = np.where(pitcher_diff < 0, pitcher_diff, 0.0)
    X_a  = np.column_stack([ones, logit_vegas, pitcher_diff, away_diff])
    coef_a = _fit(X_a, y)

    probs_a = _probs_from_coef(coef_a, X_a)
    brier_a = _brier(probs_a, y)

    print("FITTED LOGISTIC MODELS")
    print(f"  Vegas-only logistic:    Brier = {brier_v_fit:.6f}  (intercept={coef_v[0]:+.4f}, b_vegas={coef_v[1]:+.4f})")
    print(f"  Symmetric joint:        Brier = {brier_j:.6f}  (b_pitcher_diff={coef_j[2]:+.4f})")
    print(f"  Asymmetric joint:       Brier = {brier_a:.6f}  (b_pitcher={coef_a[2]:+.4f}, b_away_bonus={coef_a[3]:+.4f})\n")

    # ── Translate coefficients to edge_mult equivalents ───────────────────────
    b_vegas   = coef_j[1]   # coefficient on logit(vegas)
    b_pitcher = coef_j[2]   # symmetric pitcher_diff coefficient

    # Current model: logit_out = 1.0 * logit_vegas + pitcher_diff * edge_mult
    # Fitted model:  logit_out = b0 + b_vegas * logit_vegas + b_pitcher * pitcher_diff
    # Normalized implied edge_mult = b_pitcher / b_vegas (if b_vegas ~ 1.0, this ≈ b_pitcher)
    implied_mult_symmetric = b_pitcher / b_vegas if abs(b_vegas) > 0.01 else b_pitcher

    b_away_bonus = coef_a[3]
    b_pitcher_a  = coef_a[2]
    b_vegas_a    = coef_a[1]

    implied_away_mult = (b_pitcher_a + b_away_bonus) / b_vegas_a if abs(b_vegas_a) > 0.01 else (b_pitcher_a + b_away_bonus)
    implied_home_mult = b_pitcher_a / b_vegas_a if abs(b_vegas_a) > 0.01 else b_pitcher_a

    print("EDGE MULTIPLIER TRANSLATION (current -> data-implied)")
    print(f"  Vegas-logit coefficient b_vegas = {b_vegas:+.4f} (ideal = 1.00)")
    print(f"  Symmetric pitcher_diff coef    = {b_pitcher:+.4f}")
    print(f"  Implied single edge_mult        = {implied_mult_symmetric:+.4f}")
    print()
    print(f"  Asymmetric model:")
    print(f"    Away-edge implied mult        = {implied_away_mult:.4f}  (current: 1.00)")
    print(f"    Home-edge implied mult        = {implied_home_mult:.4f}  (current: 0.25–0.50)")
    print()

    # ── Grid search for optimal piecewise edge_mults ──────────────────────────
    print("GRID SEARCH: optimal piecewise edge_mult (0.1-step, away×26 × home×11 × other×11)")
    grid = _grid_search_edge_mult(logit_vegas, pitcher_diff, y, vegas_gt_054)
    brier_grid = grid["brier"]
    print(f"  Best piecewise:  away={grid['away_mult']:.2f}  consensus_home={grid['consensus_home_mult']:.2f}  other_home={grid['other_home_mult']:.2f}")
    print(f"  Brier (grid):    {brier_grid:.6f}\n")

    # ── Summary table ─────────────────────────────────────────────────────────
    print("SUMMARY TABLE")
    print(f"  {'Model':<35} {'Brier':>10}  {'vs Vegas':>10}")
    rows = [
        ("Vegas-only (baseline)",             brier_vegas),
        ("Current model (predictor.py)",      brier_current),
        ("Fitted symmetric logistic",         brier_j),
        ("Fitted asymmetric logistic",        brier_a),
        ("Grid-search piecewise mult",        brier_grid),
    ]
    for label, b in rows:
        delta = (b - brier_vegas) * 1000
        sign  = "+" if delta >= 0 else ""
        print(f"  {label:<35} {b:.6f}  {sign}{delta:.2f} mBrier")
    print()

    # ── Improvement analysis ──────────────────────────────────────────────────
    best_fitted_brier = min(brier_j, brier_a, brier_grid)
    improvement       = (brier_current - best_fitted_brier) * 1000  # mBrier
    threshold_mbrier  = 3.0  # plan: update if >0.003 Brier improvement

    print("RECOMMENDATION")
    if improvement >= threshold_mbrier:
        print(f"  [APPLY] Fitted model improves Brier by {improvement:.2f} mBrier (threshold: {threshold_mbrier:.0f}).")
        print(f"  Recommended edge_mult update:")
        print(f"    away edge:       1.00 → {grid['away_mult']:.2f}")
        print(f"    consensus home:  0.25 → {grid['consensus_home_mult']:.2f}")
        print(f"    other home:      0.50 → {grid['other_home_mult']:.2f}")
        if apply:
            changed = _apply_edge_mults(
                grid["away_mult"],
                grid["consensus_home_mult"],
                grid["other_home_mult"],
            )
            if changed:
                print(f"\n  [OK] predictor.py updated. Re-run backtest to validate ROI impact.")
            else:
                print(f"\n  [WARN] Auto-patch failed — update predictor.py manually.")
        else:
            print(f"\n  Re-run with --apply to write these values to predictor.py.")
    else:
        print(f"  [NO CHANGE] Best fitted model improves Brier by only {improvement:.2f} mBrier")
        print(f"  (threshold: {threshold_mbrier:.0f} mBrier). Current weights are near-optimal.")
        print(f"  No changes to predictor.py.")

    # ── Elite Away subset analysis ────────────────────────────────────────────
    bt_raw = json.loads(BACKTEST_PATH.read_text(encoding="utf-8"))
    combo_games = [
        g for g in bt_raw["games"]
        if g.get("home_implied_prob") is not None
        and g.get("model_edge_ml") is not None
        and g.get("pitcher_score_home") is not None
        and g.get("pitcher_score_away") is not None
        and g.get("actual_winner") in ("home", "away")
        and g.get("home_win_pct") is not None
        and g["model_edge_ml"] <= -0.10
        and (g["pitcher_score_home"] - g["pitcher_score_away"]) < -0.05
    ]
    if combo_games:
        ea_y       = np.array([1 if g["actual_winner"] == "home" else 0 for g in combo_games], dtype=float)
        ea_current = np.array([g["home_win_pct"] for g in combo_games])
        ea_brier_current = _brier(ea_current, ea_y)
        ea_probs_j = _probs_from_coef(coef_j, np.column_stack([
            np.ones(len(combo_games)),
            [_logit(g["home_implied_prob"]) for g in combo_games],
            [g["pitcher_score_home"] - g["pitcher_score_away"] for g in combo_games],
        ]))
        ea_brier_j = _brier(ea_probs_j, ea_y)
        ea_wr = float(ea_y.mean())
        print(f"\nELITE AWAY SUBSET (n={len(combo_games)}, actual away win rate={1-ea_wr:.1%})")
        print(f"  Current model Brier:  {ea_brier_current:.6f}")
        print(f"  Fitted model Brier:   {ea_brier_j:.6f}")
        print(f"  Delta:                {(ea_brier_current - ea_brier_j)*1000:+.2f} mBrier")

    print(f"\n{'='*64}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fit optimal edge multipliers from backtest data")
    parser.add_argument("--apply", action="store_true",
                        help="Write recommended edge_mult values to predictor.py")
    args = parser.parse_args()
    run(apply=args.apply)
