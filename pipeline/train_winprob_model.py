"""Train the curated-Statcast win-probability logistic and persist coefficients to JSON.

Fits on 2024-2025 same-season data (matches live, which uses current-season stats). Uses the SAME
feature extraction as the live predictor (pipeline.winprob_model.extract_features) so train/serve are
identical. Persists imputer means + scaler params + logistic coef/intercept (dependency-free predict).

Usage:  python -m pipeline.train_winprob_model
"""

from __future__ import annotations

import json

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.model_selection import cross_val_predict
from sklearn.metrics import roc_auc_score, log_loss

from pipeline.backtest import load_full_historical_cache, load_season_lineups
from pipeline.park_factors import get_run_factor
from pipeline.winprob_model import CURATED_FEATURES, MODEL_PATH, extract_features
import json as _json
from pathlib import Path

SEASONS = (2021, 2022, 2023, 2024, 2025)


def _same_season_cache(season):
    import pickle
    p = Path(__file__).parent.parent / "data" / "seasons" / str(season) / "player_cache.pkl"
    return pickle.load(open(p, "rb")) if p.exists() else {}


def _rows():
    bt = {int(g["gamePk"]): g for g in _json.loads((Path(__file__).parent.parent / "docs" / "backtest.json").read_text())["games"]
          if g.get("gamePk") and g.get("season") in SEASONS}
    X, y = [], []
    for season in SEASONS:
        lineups = load_season_lineups(season)
        cache = _same_season_cache(season)
        for pk, lu in lineups.items():
            g = bt.get(pk)
            if not g or g.get("home_score") is None or g.get("away_score") is None or not g.get("venue"):
                continue
            hp = [cache[i] for i in lu.get("home", []) if i in cache]
            ap = [cache[i] for i in lu.get("away", []) if i in cache]
            if not hp or not ap:
                continue
            try:
                park = float(get_run_factor(g["venue"]))
            except Exception:
                park = 100.0
            feats = extract_features(hp, ap, cache.get(g.get("home_sp_id")), cache.get(g.get("away_sp_id")),
                                     cache.get(f"bullpen:{g['home_team']}"), cache.get(f"bullpen:{g['away_team']}"), park)
            X.append([feats[f] for f in CURATED_FEATURES])
            y.append(1 if g["home_score"] > g["away_score"] else 0)
    return np.array(X, dtype=float), np.array(y)


def run():
    X, y = _rows()
    print(f"Training rows: n={len(y)}  home-win rate={y.mean()*100:.1f}%  features={len(CURATED_FEATURES)}")

    imp = SimpleImputer().fit(X)
    Xi = imp.transform(X)
    sc = StandardScaler().fit(Xi)
    Xs = sc.transform(Xi)
    clf = LogisticRegression(max_iter=2000, C=0.5).fit(Xs, y)

    # honest OOS metrics on the persisted pipeline
    from sklearn.pipeline import make_pipeline
    pipe = make_pipeline(SimpleImputer(), StandardScaler(), LogisticRegression(max_iter=2000, C=0.5))
    p = cross_val_predict(pipe, X, y, cv=5, method="predict_proba")[:, 1]
    auc, ll = roc_auc_score(y, p), log_loss(y, p)
    print(f"OOS 5-fold: AUC={auc:.4f}  log-loss={ll:.4f}")

    model = {
        "features": CURATED_FEATURES,
        "impute_means": imp.statistics_.tolist(),
        "scaler_mean": sc.mean_.tolist(),
        "scaler_scale": sc.scale_.tolist(),
        "coef": clf.coef_[0].tolist(),
        "intercept": float(clf.intercept_[0]),
        "trained_on": "2021-2025 same-season",
        "n": int(len(y)),
        "cv_auc": round(float(auc), 4),
        "cv_logloss": round(float(ll), 4),
    }
    MODEL_PATH.write_text(json.dumps(model, indent=2))
    print(f"Saved -> {MODEL_PATH}")

    # sanity: top |coef| features
    order = sorted(zip(CURATED_FEATURES, clf.coef_[0]), key=lambda t: -abs(t[1]))
    print("Top coefficients (standardized):")
    for nm, c in order[:8]:
        print(f"  {nm:16} {c:+.3f}")


if __name__ == "__main__":
    run()
