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
    return 1.0 / (1.0 + math.exp(-x))

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


def regression_analysis(games: list[dict]) -> dict:
    """Logistic regression: actual_winner_home ~ home_implied_prob + pitcher_diff.

    If pitcher_diff coefficient is near zero → Vegas already prices pitcher quality.
    """
    X = [[g["home_implied_prob"], g["pitcher_diff"]] for g in games]
    y = [g["actual_winner_home"] for g in games]

    # Vegas-only model
    coef_vegas = _fit_logistic([[g["home_implied_prob"]] for g in games], y,
                               lr=0.1, epochs=3000)
    # Joint model
    coef_joint = _fit_logistic(X, y, lr=0.05, epochs=3000)

    # Log-loss for each model
    def log_loss(coef, X_data, y_data):
        ll = 0.0
        n_feat = len(X_data[0])
        for xi, yi in zip(X_data, y_data):
            p = _sigmoid(coef[0] + sum(coef[j+1]*xi[j] for j in range(n_feat)))
            p = max(1e-9, min(1 - 1e-9, p))
            ll -= yi * math.log(p) + (1 - yi) * math.log(1 - p)
        return ll / len(y_data)

    ll_vegas = log_loss(coef_vegas, [[g["home_implied_prob"]] for g in games], y)
    ll_joint = log_loss(coef_joint, X, y)
    ll_naive = -math.log(0.5)  # baseline: always predict 50%

    return {
        "n_games":              len(games),
        "vegas_only_log_loss":  round(ll_vegas, 6),
        "joint_log_loss":       round(ll_joint, 6),
        "naive_log_loss":       round(ll_naive, 6),
        "pitcher_diff_coef":    round(coef_joint[2], 4),
        "implied_prob_coef":    round(coef_joint[1], 4),
        "intercept":            round(coef_joint[0], 4),
        "interpretation": (
            "pitcher_diff adds meaningful signal" if abs(coef_joint[2]) > 0.3
            else "pitcher_diff adds little beyond Vegas (coef near 0)"
        ),
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

    print(f"\nLOGISTIC REGRESSION")
    print(f"  Vegas-only log-loss:  {reg['vegas_only_log_loss']:.6f}")
    print(f"  Joint log-loss:       {reg['joint_log_loss']:.6f}")
    print(f"  Naive baseline:       {reg['naive_log_loss']:.6f}")
    print(f"  home_implied_prob coef: {reg['implied_prob_coef']:+.4f}")
    print(f"  pitcher_diff coef:      {reg['pitcher_diff_coef']:+.4f}")
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
