"""Does adding more Statcast metrics improve WIN-PROBABILITY (not totals)?

Builds progressive feature sets and measures OOS win prediction (home win = margin>0):
  CORE (xwOBA + xERA + park) -> +full offense -> +full pitching -> +bullpen -> FULL.
Metrics: AUC, log-loss, Brier, accuracy (5-fold CV). Benchmarked vs the Vegas no-vig home prob.
Reuses the raw-feature extraction in research_score_model. Same-season stats (live current-form
proxy) by default; pass prior=True for the lookahead-safe (pessimistic) view.

Usage:  python -m pipeline.research_winprob_features
"""

from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.model_selection import cross_val_predict
from sklearn.metrics import roc_auc_score, log_loss, brier_score_loss

from pipeline import research_score_model as M
from pipeline.utils import american_to_decimal  # noqa: F401  (kept for parity / future use)


def _cols(*prefixes):
    return [i for i, nm in enumerate(M.FEAT_NAMES) if any(nm.startswith(p) for p in prefixes)]


# progressive, cumulative feature sets (by column index into M.FEAT_NAMES)
def _build_sets():
    core = [M.FEAT_NAMES.index(n) for n in ("off_h_xwoba", "off_a_xwoba", "sp_h_xera", "sp_a_xera", "park")]
    off  = sorted(set(core) | set(_cols("off_h_", "off_a_")))
    pit  = sorted(set(off)  | set(_cols("sp_h_", "sp_a_")))
    bp   = sorted(set(pit)  | set(_cols("bp_h_", "bp_a_")))
    return [
        ("CORE (xwOBA+xERA+park)", core),
        ("+ full offense",         off),
        ("+ full pitching",        pit),
        ("+ bullpen (= FULL)",     bp),
    ]


def _eval(name, X, y, idx, clf):
    Xi = X[:, idx]
    p = cross_val_predict(clf, Xi, y, cv=5, method="predict_proba")[:, 1]
    auc = roc_auc_score(y, p); ll = log_loss(y, p); br = brier_score_loss(y, p)
    acc = ((p > 0.5).astype(int) == y).mean()
    print(f"  {name:26} k={len(idx):>2}  AUC={auc:.4f}  logloss={ll:.4f}  Brier={br:.4f}  acc={acc*100:.1f}%")
    return auc


def run(prior=False):
    tag = "PRIOR-season (lookahead-safe)" if prior else "SAME-season (live current-form proxy)"
    print("=" * 96)
    print(f"WIN-PROBABILITY vs # of Statcast features  —  OOS 5-fold CV, {tag}")
    print("=" * 96)
    rows = M._build_rows(prior=prior)
    X = np.array([r["X"] for r in rows], dtype=float)
    y = np.array([1 if r["margin"] > 0 else 0 for r in rows])
    veg = [r["vegas_total"] for r in rows]  # totals only; ML ceiling computed below if present
    print(f"\n  n={len(rows)} games | home-win rate={y.mean()*100:.1f}% | {X.shape[1]} raw features\n")

    lr = make_pipeline(SimpleImputer(), StandardScaler(), LogisticRegression(max_iter=2000, C=0.5))
    gbm = make_pipeline(SimpleImputer(),
                        GradientBoostingClassifier(n_estimators=300, max_depth=3, learning_rate=0.03, subsample=0.8))

    sets = _build_sets()
    for label, clf in [("LOGISTIC", lr), ("GBM", gbm)]:
        print(f"  --- {label} ---")
        prev = None
        for name, idx in sets:
            auc = _eval(name, X, y, idx, clf)
            if prev is not None:
                print(f"  {'':26}       delta AUC vs prev = {auc - prev:+.4f}")
            prev = auc
        print()

    # Vegas ML ceiling (no-vig home prob)
    def _novig(hm, am):
        def imp(o): return (-o)/(-o+100) if o < 0 else 100/(o+100)
        ih, ia = imp(hm), imp(am); return ih/(ih+ia)
    vy, vp = [], []
    for r in rows:
        hm, am = r.get("home_ml"), r.get("away_ml")
        if hm and am:
            vy.append(1 if r["margin"] > 0 else 0); vp.append(_novig(hm, am))
    if len(vy) > 100:
        vy, vp = np.array(vy), np.array(vp)
        print(f"  VEGAS no-vig ML (ceiling)  n={len(vy)}  AUC={roc_auc_score(vy, vp):.4f}  "
              f"logloss={log_loss(vy, vp):.4f}  Brier={brier_score_loss(vy, vp):.4f}")
    print("\n  Read: AUC rising as features add => more Statcast helps. Flat/falling => it doesn't.")
    print("=" * 96)


if __name__ == "__main__":
    run()
