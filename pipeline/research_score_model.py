"""Pure input -> score prediction model (NO odds, NO edge-hunting).

Goal: from historical LINEUPS + STATCAST only, how well can we predict a game's runs?
Validated by out-of-sample R^2 / MAE / correlation, benchmarked against:
  - the naive league-average baseline (R^2 = 0 by construction), and
  - the Vegas CLOSING total, the sharpest run estimate that exists = our practical ceiling.

Features (all our own data, $0): per-side RAW offense aggregates (xwOBA/xSLG/wRC+/barrel/EV/...),
per-side starter raw Statcast (xERA/xFIP/SIERA/xwOBA-against/K%/...), per-side bullpen (prior
season), and park run factor. Targets: total runs, margin (home-away), per-team runs.

2024-2025 have real lineups (game_lineups.parquet) + prior-season Statcast caches; lineup/pitcher
stats are pulled lookahead-safe from the PRIOR season cache (same rule as the holdout gate).

Usage:  python -m pipeline.research_score_model
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.model_selection import cross_val_predict
from sklearn.metrics import r2_score, mean_absolute_error

from pipeline.backtest import load_full_historical_cache, load_season_lineups
from pipeline.park_factors import get_run_factor

ROOT = Path(__file__).parent.parent
SEASONS = (2024, 2025)

BAT = ["xwoba", "xslg", "woba", "wrc_plus", "barrel_pct", "hard_hit_pct", "avg_ev", "contact_pct", "xba"]
PIT = ["xera", "xfip", "siera", "xwoba_against", "xslg_against", "barrel_pct_against",
       "k_pct", "bb_pct", "hr9", "swstr_pct", "fb_mph", "gb_pct"]
BP  = ["xera", "k_pct", "bb_pct", "whiff_pct"]


def _agg(players, field):
    vals = [p[field] for p in players if p.get(field) is not None]
    return sum(vals) / len(vals) if vals else np.nan


def _side_feats(players, sp, bullpen):
    f = [_agg(players, k) for k in BAT]
    f += [(sp.get(k) if sp else None) for k in PIT]
    f += [(bullpen.get(k) if bullpen else None) for k in BP]
    return [np.nan if v is None else float(v) for v in f]


FEAT_NAMES = ([f"off_h_{k}" for k in BAT] + [f"sp_h_{k}" for k in PIT] + [f"bp_h_{k}" for k in BP]
              + [f"off_a_{k}" for k in BAT] + [f"sp_a_{k}" for k in PIT] + [f"bp_a_{k}" for k in BP]
              + ["park"])


def _load_same_season_cache(season):
    import pickle
    p = ROOT / "data" / "seasons" / str(season) / "player_cache.pkl"
    return pickle.load(open(p, "rb")) if p.exists() else {}


def _build_rows(prior=True):
    bt = {int(g["gamePk"]): g for g in json.loads((ROOT / "docs" / "backtest.json").read_text())["games"]
          if g.get("gamePk") and g.get("season") in SEASONS}
    rows = []
    for season in SEASONS:
        lineups = load_season_lineups(season)
        cache = load_full_historical_cache(season) if prior else _load_same_season_cache(season)
        matched = 0
        for pk, lu in lineups.items():
            g = bt.get(pk)
            if not g or g.get("home_score") is None or g.get("away_score") is None or not g.get("venue"):
                continue
            hp = [cache[i] for i in lu.get("home", []) if i in cache]
            ap = [cache[i] for i in lu.get("away", []) if i in cache]
            if not hp or not ap:
                continue
            h_sp = cache.get(g.get("home_sp_id"))
            a_sp = cache.get(g.get("away_sp_id"))
            h_bp = cache.get(f"bullpen:{g['home_team']}")
            a_bp = cache.get(f"bullpen:{g['away_team']}")
            try:
                park = float(get_run_factor(g["venue"])) / 100.0
            except Exception:
                park = 1.0
            feats = _side_feats(hp, h_sp, h_bp) + _side_feats(ap, a_sp, a_bp) + [park]
            hs, as_ = float(g["home_score"]), float(g["away_score"])
            rows.append({"X": feats, "home_runs": hs, "away_runs": as_,
                         "total": hs + as_, "margin": hs - as_, "vegas_total": g.get("closing_total")})
            matched += 1
        print(f"  {season}: {len(lineups)} games w/ lineups, matched {matched}")
    return rows


def _report(name, y, pred, vegas=None):
    line = f"  {name:22} R2={r2_score(y, pred):+.3f}  MAE={mean_absolute_error(y, pred):.3f}  r={np.corrcoef(y, pred)[0,1]:+.3f}"
    if vegas is not None:
        mask = np.array([v is not None for v in vegas])
        if mask.sum() > 50:
            yv = y[mask]; vv = np.array([float(v) for v in np.array(vegas)[mask]])
            line += f"   | Vegas: R2={r2_score(yv, vv):+.3f} MAE={mean_absolute_error(yv, vv):.3f} r={np.corrcoef(yv, vv)[0,1]:+.3f}"
    print(line)


def run():
    print("=" * 96)
    print("PURE INPUT -> SCORE MODEL  (raw lineups + Statcast, no odds) — OOS 5-fold CV on 2024-25")
    print("=" * 96)
    rows = _build_rows()
    if len(rows) < 200:
        print("  Not enough rows."); return

    X = np.array([r["X"] for r in rows], dtype=float)
    vegas = [r["vegas_total"] for r in rows]
    print(f"\n  n={len(rows)} games | {X.shape[1]} raw features | NaN cells: {np.isnan(X).mean()*100:.1f}%")
    print(f"  actual total mean={np.mean([r['total'] for r in rows]):.2f} sd={np.std([r['total'] for r in rows]):.2f}\n")

    ridge = make_pipeline(SimpleImputer(), StandardScaler(), Ridge(alpha=5.0))
    gbm = make_pipeline(SimpleImputer(),
                        GradientBoostingRegressor(n_estimators=300, max_depth=3, learning_rate=0.03, subsample=0.8))

    for label, model in [("RIDGE", ridge), ("GBM", gbm)]:
        print(f"  --- {label} ---")
        for tgt, vbench in [("total", vegas), ("margin", None)]:
            y = np.array([r[tgt] for r in rows])
            pred = cross_val_predict(model, X, y, cv=5)
            _report(tgt, y, pred, vbench)
        # per-team -> derived total/margin
        yh = np.array([r["home_runs"] for r in rows]); ya = np.array([r["away_runs"] for r in rows])
        ph = cross_val_predict(model, X, yh, cv=5); pa = cross_val_predict(model, X, ya, cv=5)
        _report("total (from teams)", np.array([r["total"] for r in rows]), ph + pa, vegas)
        _report("margin (from teams)", np.array([r["margin"] for r in rows]), ph - pa)
        print()

    # GBM feature importances (which inputs actually drive runs)
    imp_model = make_pipeline(SimpleImputer(),
                              GradientBoostingRegressor(n_estimators=300, max_depth=3, learning_rate=0.03, subsample=0.8))
    imp_model.fit(X, np.array([r["total"] for r in rows]))
    imps = imp_model.named_steps["gradientboostingregressor"].feature_importances_
    top = sorted(zip(FEAT_NAMES, imps), key=lambda t: -t[1])[:12]
    print("  Top total-run drivers (GBM importance):")
    for nm, v in top:
        print(f"    {nm:24} {v:.3f}")
    print("\n  Read: compare our total R2 to Vegas's (~0.11). Closing that gap from inputs = the goal.")
    print("=" * 96)


if __name__ == "__main__":
    run()
