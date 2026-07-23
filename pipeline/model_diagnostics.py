"""Model diagnostics — quantify signal value vs. Vegas closing lines.

Runs logistic regression, calibration analysis, and ROI attribution on
docs/backtest.json + docs/history.json to answer the core question:
do our model signals add predictive value AFTER controlling for Vegas?

Usage:
    python -m pipeline.model_diagnostics
    python -m pipeline.model_diagnostics --out docs/diagnostics.json
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from pathlib import Path

log = logging.getLogger(__name__)

BACKTEST_PATH = Path(__file__).parent.parent / "docs" / "backtest.json"
HISTORY_PATH  = Path(__file__).parent.parent / "docs" / "history.json"


# ── Math helpers ──────────────────────────────────────────────────────────────

def _logit(p: float) -> float:
    p = max(1e-6, min(1 - 1e-6, p))
    return math.log(p / (1 - p))

def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))

def _ml_units(odds: int, won: bool) -> float:
    ret = odds / 100 if odds > 0 else 100 / abs(odds)
    return ret if won else -1.0


# ── Logistic regression (gradient descent, no scipy dependency) ───────────────

def _fit_logistic(X: list[list[float]], y: list[int],
                  lr: float = 0.05, epochs: int = 2000) -> list[float]:
    """Fit logistic regression via gradient descent. Returns coefficients [b0, b1, ...]."""
    n_feat = len(X[0])
    coef   = [0.0] * (n_feat + 1)          # intercept first

    for _ in range(epochs):
        grad = [0.0] * len(coef)
        for xi, yi in zip(X, y):
            p = _sigmoid(coef[0] + sum(coef[j + 1] * xi[j] for j in range(n_feat)))
            err = p - yi
            grad[0] += err
            for j in range(n_feat):
                grad[j + 1] += err * xi[j]
        for j in range(len(coef)):
            coef[j] -= lr * grad[j] / len(X)

    return coef


# ── Load & normalise games ────────────────────────────────────────────────────

def _load_games() -> list[dict]:
    games: list[dict] = []

    bt = json.loads(BACKTEST_PATH.read_text(encoding="utf-8"))
    for g in bt.get("games", []):
        if g.get("home_ml") is None or g.get("home_implied_prob") is None:
            continue
        if g.get("actual_winner") not in ("home", "away"):
            continue
        games.append({
            "home_win_pct":       float(g["home_win_pct"]),
            "home_implied_prob":  float(g["home_implied_prob"]),
            "pitcher_diff":       float(g.get("pitcher_score_home", 0.5))
                                  - float(g.get("pitcher_score_away", 0.5)),
            "model_edge_ml":      float(g.get("model_edge_ml") or 0),
            "home_ml":            int(g["home_ml"]),
            "away_ml":            int(g["away_ml"]),
            "closing_total":      g.get("closing_total"),
            "predicted_total":    g.get("predicted_total"),
            "over_price":         g.get("over_price", -110),
            "under_price":        g.get("under_price", -110),
            "total_went_over":    g.get("total_went_over"),
            "actual_winner_home": 1 if g["actual_winner"] == "home" else 0,
            "bet_side":           g.get("bet_side", "home"),
            "bet_won":            bool(g.get("bet_won", False)),
            "correct":            bool(g.get("correct", False)),
            "season":             g.get("season"),
            "source":             "backtest",
        })

    if HISTORY_PATH.exists():
        hist = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        for g in hist:
            if g.get("home_ml") is None or g.get("model_edge_ml") is None:
                continue
            if g.get("actual_winner") not in ("home", "away"):
                continue
            from pipeline.odds import no_vig_prob
            try:
                himp, _ = no_vig_prob(int(g["home_ml"]), int(g["away_ml"]))
            except Exception:
                continue
            home_wp = float(g["home_win_pct"])
            games.append({
                "home_win_pct":       home_wp,
                "home_implied_prob":  himp,
                "pitcher_diff":       float(g.get("pitcher_score_home", 0.5))
                                      - float(g.get("pitcher_score_away", 0.5)),
                "model_edge_ml":      float(g.get("model_edge_ml") or 0),
                "home_ml":            int(g["home_ml"]),
                "away_ml":            int(g["away_ml"]),
                "closing_total":      g.get("vegas_total"),
                "predicted_total":    g.get("predicted_total"),
                "over_price":         g.get("over_price", -110),
                "under_price":        g.get("under_price", -110),
                "total_went_over":    g.get("total_went_over"),
                "actual_winner_home": 1 if g["actual_winner"] == "home" else 0,
                "bet_side":           "home" if home_wp >= 0.5 else "away",
                "bet_won":            (g["actual_winner"] == "home") == (home_wp >= 0.5),
                "correct":            g.get("predicted_winner") == g.get("actual_winner"),
                "season":             (g.get("date") or "")[:4] or "2026",
                "source":             "history",
            })

    return games


# ── Analysis functions ────────────────────────────────────────────────────────

def calibration_analysis(games: list[dict], n_bins: int = 10) -> list[dict]:
    """Group by decile of model home_win_pct; compare to actual win rate."""
    sorted_g = sorted(games, key=lambda g: g["home_win_pct"])
    bin_size  = len(sorted_g) // n_bins
    rows = []
    for i in range(n_bins):
        chunk = sorted_g[i * bin_size : (i + 1) * bin_size]
        if not chunk:
            continue
        mean_pred  = sum(g["home_win_pct"] for g in chunk) / len(chunk)
        actual_wr  = sum(g["actual_winner_home"] for g in chunk) / len(chunk)
        mean_vegas = sum(g["home_implied_prob"] for g in chunk) / len(chunk)
        rows.append({
            "bin":         i + 1,
            "n":           len(chunk),
            "pred_prob":   round(mean_pred, 4),
            "actual_wr":   round(actual_wr, 4),
            "vegas_prob":  round(mean_vegas, 4),
            "model_err":   round(mean_pred - actual_wr, 4),
            "vegas_err":   round(mean_vegas - actual_wr, 4),
        })
    return rows


def _standardize(cols: list[list[float]]) -> tuple[list[list[float]], list[float], list[float]]:
    """Z-score each feature column. Returns (standardized_rows, means, stds).

    Features on very different scales (home_implied_prob ∈ [0,1] vs pitcher_diff ∈ ~±0.3)
    make plain gradient descent under-converge at a shared learning rate — which is how a
    *nested* joint model can post a worse in-sample log-loss than Vegas-only (statistically
    impossible for a properly fit MLE). Standardizing puts both features on comparable
    footing so the comparison is honest.
    """
    n_feat = len(cols[0])
    means  = [sum(r[j] for r in cols) / len(cols) for j in range(n_feat)]
    stds   = []
    for j in range(n_feat):
        var = sum((r[j] - means[j]) ** 2 for r in cols) / len(cols)
        stds.append(math.sqrt(var) or 1.0)
    std_rows = [[(r[j] - means[j]) / stds[j] for j in range(n_feat)] for r in cols]
    return std_rows, means, stds


def _log_loss(coef: list[float], X_data: list[list[float]], y_data: list[int]) -> float:
    ll = 0.0
    n_feat = len(X_data[0])
    for xi, yi in zip(X_data, y_data):
        p = _sigmoid(coef[0] + sum(coef[j + 1] * xi[j] for j in range(n_feat)))
        p = max(1e-9, min(1 - 1e-9, p))
        ll -= yi * math.log(p) + (1 - yi) * math.log(1 - p)
    return ll / len(y_data)


def _cv_log_loss(cols: list[list[float]], y: list[int], k: int = 5,
                 lr: float = 0.3, epochs: int = 3000) -> float:
    """K-fold cross-validated log-loss. Standardization is fit on each train fold only
    (no leakage). This measures out-of-sample fit — the only fair test of whether an added
    feature helps, since in-sample log-loss always favors the model with more parameters.
    """
    n = len(cols)
    fold_losses = []
    for f in range(k):
        test_idx  = set(range(f, n, k))                       # strided folds
        train     = [cols[i] for i in range(n) if i not in test_idx]
        train_y   = [y[i]    for i in range(n) if i not in test_idx]
        test      = [cols[i] for i in range(n) if i in test_idx]
        test_y    = [y[i]    for i in range(n) if i in test_idx]
        if not test or not train:
            continue
        n_feat = len(train[0])
        means  = [sum(r[j] for r in train) / len(train) for j in range(n_feat)]
        stds   = []
        for j in range(n_feat):
            var = sum((r[j] - means[j]) ** 2 for r in train) / len(train)
            stds.append(math.sqrt(var) or 1.0)
        std_train = [[(r[j] - means[j]) / stds[j] for j in range(n_feat)] for r in train]
        std_test  = [[(r[j] - means[j]) / stds[j] for j in range(n_feat)] for r in test]
        coef = _fit_logistic(std_train, train_y, lr=lr, epochs=epochs)
        fold_losses.append(_log_loss(coef, std_test, test_y))
    return sum(fold_losses) / len(fold_losses) if fold_losses else float("nan")


def regression_analysis(games: list[dict]) -> dict:
    """Does pitcher_diff add signal *beyond* the Vegas line?

    Fits two logistic models — Vegas-only (home_implied_prob) and joint
    (+ pitcher_diff) — on standardized features, and compares them by 5-fold
    cross-validated log-loss. The honest test is out-of-sample: if the joint
    model's CV log-loss doesn't beat Vegas-only, pitcher_diff adds nothing the
    market hasn't already priced, regardless of how large its fitted coefficient is.
    """
    vegas_cols = [[g["home_implied_prob"]] for g in games]
    joint_cols = [[g["home_implied_prob"], g["pitcher_diff"]] for g in games]
    y = [g["actual_winner_home"] for g in games]

    # Standardized full-data fits (for reporting coefficients on a comparable scale).
    std_joint, _, _ = _standardize(joint_cols)
    coef_joint = _fit_logistic(std_joint, y, lr=0.3, epochs=3000)

    # In-sample log-loss (kept for continuity, but not the basis for the verdict —
    # in-sample always favors the model with more parameters).
    std_vegas, _, _ = _standardize(vegas_cols)
    coef_vegas = _fit_logistic(std_vegas, y, lr=0.3, epochs=3000)
    ll_vegas_in = _log_loss(coef_vegas, std_vegas, y)
    ll_joint_in = _log_loss(coef_joint, std_joint, y)

    # Out-of-sample: 5-fold CV log-loss is the verdict.
    cv_vegas = _cv_log_loss(vegas_cols, y)
    cv_joint = _cv_log_loss(joint_cols, y)
    cv_delta = cv_vegas - cv_joint          # > 0 means joint predicts better OOS
    ll_naive = -math.log(0.5)               # baseline: always predict 50%

    # Verdict is gated on real out-of-sample improvement, not coefficient magnitude.
    # ~0.0005 log-loss ≈ the noise floor at this sample size.
    if cv_delta >= 0.0005:
        interpretation = (f"pitcher_diff adds signal beyond Vegas "
                          f"(CV log-loss {cv_delta:+.5f} better)")
    elif cv_delta <= -0.0005:
        interpretation = (f"pitcher_diff HURTS out-of-sample — Vegas already prices it "
                          f"(CV log-loss {cv_delta:+.5f})")
    else:
        interpretation = ("pitcher_diff adds nothing beyond Vegas "
                          f"(CV log-loss flat, {cv_delta:+.5f})")

    return {
        "n_games":               len(games),
        "coef_space":            "standardized (z-scored features)",
        "vegas_only_log_loss":   round(ll_vegas_in, 6),   # in-sample
        "joint_log_loss":        round(ll_joint_in, 6),   # in-sample
        "cv_vegas_log_loss":     round(cv_vegas, 6),      # out-of-sample (5-fold)
        "cv_joint_log_loss":     round(cv_joint, 6),      # out-of-sample (5-fold)
        "cv_log_loss_delta":     round(cv_delta, 6),      # >0 = joint better OOS
        "naive_log_loss":        round(ll_naive, 6),
        "pitcher_diff_coef":     round(coef_joint[2], 4),
        "implied_prob_coef":     round(coef_joint[1], 4),
        "intercept":             round(coef_joint[0], 4),
        "interpretation":        interpretation,
    }


def edge_asymmetry(games: list[dict]) -> dict:
    """ROI by edge sign: negative edge (away lean) vs. positive edge (home lean)."""
    buckets = {
        "strong_away": {"range": "< -0.10", "n": 0, "wins": 0, "units": 0.0},
        "mild_away":   {"range": "-0.10 to -0.03", "n": 0, "wins": 0, "units": 0.0},
        "neutral":     {"range": "-0.03 to +0.03", "n": 0, "wins": 0, "units": 0.0},
        "mild_home":   {"range": "+0.03 to +0.10", "n": 0, "wins": 0, "units": 0.0},
        "strong_home": {"range": "> +0.10",  "n": 0, "wins": 0, "units": 0.0},
    }
    for g in games:
        e = g["model_edge_ml"]
        if   e < -0.10: b = buckets["strong_away"]
        elif e < -0.03: b = buckets["mild_away"]
        elif e <  0.03: b = buckets["neutral"]
        elif e <  0.10: b = buckets["mild_home"]
        else:           b = buckets["strong_home"]

        won  = g["bet_won"]
        side = g["bet_side"]
        odds = g["home_ml"] if side == "home" else g["away_ml"]
        b["n"]     += 1
        if won: b["wins"] += 1
        b["units"] += _ml_units(odds, won)

    rows = []
    for name, b in buckets.items():
        if b["n"] == 0:
            continue
        roi = b["units"] / b["n"] * 100
        rows.append({
            "bucket":   name,
            "range":    b["range"],
            "n":        b["n"],
            "win_pct":  round(b["wins"] / b["n"] * 100, 1),
            "roi":      round(roi, 2),
            "units":    round(b["units"], 2),
        })
    return {"buckets": rows}


def roi_by_pitcher_diff(games: list[dict]) -> list[dict]:
    """ROI sliced by pitcher score differential quintile."""
    sorted_g = sorted(games, key=lambda g: g["pitcher_diff"])
    q_size    = len(sorted_g) // 5
    rows = []
    for i in range(5):
        chunk = sorted_g[i * q_size : (i + 1) * q_size]
        if not chunk:
            continue
        units = sum(_ml_units(g["home_ml"] if g["bet_side"] == "home" else g["away_ml"],
                              g["bet_won"]) for g in chunk)
        wins  = sum(1 for g in chunk if g["bet_won"])
        p_lo  = chunk[0]["pitcher_diff"]
        p_hi  = chunk[-1]["pitcher_diff"]
        rows.append({
            "quintile":    i + 1,
            "pitcher_diff_range": f"{p_lo:.3f} to {p_hi:.3f}",
            "n":           len(chunk),
            "win_pct":     round(wins / len(chunk) * 100, 1),
            "roi":         round(units / len(chunk) * 100, 2),
            "units":       round(units, 2),
        })
    return rows


def totals_accuracy(games: list[dict]) -> dict:
    """Directional accuracy and ROI for predicted total vs. Vegas total."""
    over_games  = [g for g in games if g.get("closing_total") and g.get("predicted_total")
                   and g.get("total_went_over") is not None
                   and (g["predicted_total"] - g["closing_total"]) > 0.5]
    under_games = [g for g in games if g.get("closing_total") and g.get("predicted_total")
                   and g.get("total_went_over") is not None
                   and (g["predicted_total"] - g["closing_total"]) < -0.5]

    def _bucket_stats(subset, bet_over: bool):
        if not subset:
            return {"n": 0, "hit_pct": 0, "roi": 0, "units": 0}
        hits  = sum(1 for g in subset if bool(g["total_went_over"]) == bet_over)
        units = sum(_ml_units(g.get("over_price", -110) if bet_over else g.get("under_price", -110),
                              bool(g["total_went_over"]) == bet_over) for g in subset)
        return {
            "n":       len(subset),
            "hit_pct": round(hits / len(subset) * 100, 1),
            "roi":     round(units / len(subset) * 100, 2),
            "units":   round(units, 2),
        }

    return {
        "model_over":  _bucket_stats(over_games,  bet_over=True),
        "model_under": _bucket_stats(under_games, bet_over=False),
    }


def season_breakdown(games: list[dict]) -> list[dict]:
    by_season: dict[str, dict] = {}
    for g in games:
        s = str(g.get("season") or "?")
        if s not in by_season:
            by_season[s] = {"n": 0, "correct": 0, "units": 0.0, "bets": 0}
        by_season[s]["n"] += 1
        if g["correct"]: by_season[s]["correct"] += 1
        odds = g["home_ml"] if g["bet_side"] == "home" else g["away_ml"]
        by_season[s]["units"] += _ml_units(odds, g["bet_won"])
        by_season[s]["bets"]  += 1

    rows = []
    for s, d in sorted(by_season.items()):
        acc = round(d["correct"] / d["n"] * 100, 1) if d["n"] else 0
        roi = round(d["units"] / d["bets"] * 100, 2) if d["bets"] else 0
        rows.append({
            "season":   s,
            "n":        d["n"],
            "accuracy": acc,
            "roi":      roi,
            "units":    round(d["units"], 2),
        })
    return rows


# ── Main ──────────────────────────────────────────────────────────────────────

def run_diagnostics() -> dict:
    log.info("Loading games...")
    games = _load_games()
    log.info("Loaded %d priced, resolved games", len(games))

    log.info("Running calibration analysis...")
    calib = calibration_analysis(games)

    log.info("Running logistic regression...")
    reg = regression_analysis(games)

    log.info("Running edge asymmetry analysis...")
    asym = edge_asymmetry(games)

    log.info("Running pitcher diff ROI analysis...")
    pdiff = roi_by_pitcher_diff(games)

    log.info("Running totals accuracy analysis...")
    totals = totals_accuracy(games)

    log.info("Running season breakdown...")
    seasons = season_breakdown(games)

    results = {
        "n_games":           len(games),
        "calibration":       calib,
        "regression":        reg,
        "edge_asymmetry":    asym,
        "pitcher_diff_roi":  pdiff,
        "totals_accuracy":   totals,
        "season_breakdown":  seasons,
    }

    # ── Print readable summary ────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  MODEL DIAGNOSTICS  ({len(games):,} games)")
    print(f"{'='*60}\n")

    print("CALIBRATION (model predicted prob vs. actual win rate)")
    print(f"  {'Bin':>4} {'N':>6} {'Pred%':>7} {'Actual%':>8} {'Vegas%':>8} {'ModelErr':>9} {'VegasErr':>9}")
    for r in calib:
        print(f"  {r['bin']:>4} {r['n']:>6} {r['pred_prob']*100:>6.1f}%"
              f" {r['actual_wr']*100:>7.1f}%  {r['vegas_prob']*100:>7.1f}%"
              f"  {r['model_err']*100:>+8.1f}%  {r['vegas_err']*100:>+8.1f}%")

    print(f"\nLOGISTIC REGRESSION — does pitcher_diff beat Vegas out-of-sample?")
    print(f"  In-sample log-loss     vegas-only {reg['vegas_only_log_loss']:.6f}"
          f"  joint {reg['joint_log_loss']:.6f}")
    print(f"  5-fold CV log-loss     vegas-only {reg['cv_vegas_log_loss']:.6f}"
          f"  joint {reg['cv_joint_log_loss']:.6f}  (delta {reg['cv_log_loss_delta']:+.6f})")
    print(f"  Naive baseline:        {reg['naive_log_loss']:.6f}")
    print(f"  coefs (standardized):  home_implied_prob {reg['implied_prob_coef']:+.4f}"
          f"   pitcher_diff {reg['pitcher_diff_coef']:+.4f}")
    print(f"  => {reg['interpretation']}")

    print(f"\nEDGE ASYMMETRY (ROI by model_edge_ml bucket)")
    print(f"  {'Bucket':>15} {'Range':>20} {'N':>6} {'Win%':>6} {'ROI':>8} {'Units':>8}")
    for r in asym["buckets"]:
        sign = "+" if r["roi"] >= 0 else ""
        print(f"  {r['bucket']:>15} {r['range']:>20} {r['n']:>6} "
              f"{r['win_pct']:>5.1f}%  {sign}{r['roi']:>6.2f}%  {sign}{r['units']:>6.2f}")

    print(f"\nPITCHER DIFF ROI (by quintile)")
    print(f"  {'Q':>3} {'Range':>20} {'N':>6} {'Win%':>6} {'ROI':>8}")
    for r in pdiff:
        sign = "+" if r["roi"] >= 0 else ""
        print(f"  {r['quintile']:>3} {r['pitcher_diff_range']:>20} "
              f"{r['n']:>6} {r['win_pct']:>5.1f}%  {sign}{r['roi']:>6.2f}%")

    print(f"\nTOTALS ACCURACY")
    t = totals
    print(f"  Model Over  — N={t['model_over']['n']:,}  "
          f"Hit%={t['model_over']['hit_pct']}  ROI={t['model_over']['roi']:+.2f}%")
    print(f"  Model Under — N={t['model_under']['n']:,}  "
          f"Hit%={t['model_under']['hit_pct']}  ROI={t['model_under']['roi']:+.2f}%")

    print(f"\nSEASON BREAKDOWN")
    print(f"  {'Season':>7} {'N':>6} {'Acc%':>6} {'ROI':>8} {'Units':>8}")
    for r in seasons:
        print(f"  {r['season']:>7} {r['n']:>6} {r['accuracy']:>5.1f}%"
              f"  {r['roi']:>+7.2f}%  {r['units']:>+7.2f}")

    print(f"\n{'='*60}\n")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    parser = argparse.ArgumentParser(description="Run model diagnostics against backtest data")
    parser.add_argument("--out", default=None, help="Optional path to write JSON results")
    args = parser.parse_args()

    results = run_diagnostics()

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"Results written to {out_path}")
