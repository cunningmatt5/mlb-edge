"""DIAGNOSTIC GATE — does the model carry predictive signal BEYOND the market line?

If not, improving the current architecture is futile (the inputs are public/already priced).
Two rigorous tests with sklearn:
  1. Moneyline (2021-26): does model home_win_pct improve out-of-sample log-loss over market-only?
  2. Totals (2026 only, where predicted_total is independent of Vegas): does the model's
     deviation (predicted_total - closing_total) predict under/over beyond the line?
$0. Reuses docs/backtest.json + history.json.

Usage:  python -m pipeline.research_model_diagnostic
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.model_selection import cross_val_predict

ROOT = Path(__file__).parent.parent


def _no_vig_home(home_ml, away_ml):
    def imp(o): return (-o) / (-o + 100) if o < 0 else 100 / (o + 100)
    ih, ia = imp(home_ml), imp(away_ml)
    return ih / (ih + ia)


def _load():
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text())["games"]
    rows = list(bt)
    hp = ROOT / "docs" / "history.json"
    hist = []
    if hp.exists():
        raw = json.loads(hp.read_text())
        hist = raw if isinstance(raw, list) else raw.get("games", [])
    return rows, hist


def ml_test(rows, hist):
    X_mkt, X_mdl, y = [], [], []
    for g in rows:
        if g.get("actual_winner") not in ("home", "away"): continue
        wp, imp = g.get("home_win_pct"), g.get("home_implied_prob")
        if wp is None or imp is None: continue
        X_mkt.append([imp]); X_mdl.append([imp, wp]); y.append(1 if g["actual_winner"] == "home" else 0)
    for r in hist:
        if r.get("actual_winner") not in ("home", "away"): continue
        wp = r.get("home_win_pct"); hm, am = r.get("home_ml"), r.get("away_ml")
        if wp is None or hm is None or am is None: continue
        imp = _no_vig_home(hm, am)
        X_mkt.append([imp]); X_mdl.append([imp, wp]); y.append(1 if r["actual_winner"] == "home" else 0)
    X_mkt, X_mdl, y = np.array(X_mkt), np.array(X_mdl), np.array(y)
    print(f"\n[1] MONEYLINE marginal value (n={len(y)}, 2021-26)")
    p_mkt = cross_val_predict(LogisticRegression(), X_mkt, y, cv=5, method="predict_proba")[:, 1]
    p_mdl = cross_val_predict(LogisticRegression(), X_mdl, y, cv=5, method="predict_proba")[:, 1]
    ll_mkt, ll_mdl = log_loss(y, p_mkt), log_loss(y, p_mdl)
    coef = LogisticRegression().fit(X_mdl, y).coef_[0]
    print(f"  out-of-sample log-loss: market-only={ll_mkt:.5f}  market+model={ll_mdl:.5f}  (lower=better)")
    print(f"  improvement from adding model: {ll_mkt - ll_mdl:+.5f}  ({'HELPS' if ll_mdl < ll_mkt - 1e-4 else 'no improvement'})")
    print(f"  coef [market_implied, model_winpct] = [{coef[0]:.2f}, {coef[1]:.2f}]  (model coef ~0 => no marginal info)")


def totals_test(rows, hist):
    # deviation independence by year (sanity)
    print("\n[2] TOTALS — predicted_total independence (stdev of predicted-closing by year):")
    byyr = {}
    for g in rows:
        ct, pt, s = g.get("closing_total"), g.get("predicted_total"), g.get("season")
        if ct and pt and s: byyr.setdefault(s, []).append(pt - ct)
    for r in hist:
        ct, pt = r.get("vegas_total"), r.get("predicted_total")
        if ct and pt: byyr.setdefault(2026, []).append(pt - ct)
    for s in sorted(byyr):
        a = np.array(byyr[s]); print(f"    {s}: n={len(a):>4} stdev(dev)={a.std():.3f} mean={a.mean():+.3f}")

    # 2026 only: does deviation predict under?
    dev, y = [], []
    for r in hist:
        ct, pt, at = r.get("vegas_total"), r.get("predicted_total"), r.get("actual_total")
        if ct is None or pt is None or at is None or abs(at - ct) < 1e-9: continue
        dev.append([pt - ct]); y.append(1 if at < ct else 0)   # 1 = under hit
    dev, y = np.array(dev), np.array(y)
    print(f"\n  2026 totals signal test (n={len(y)} non-push):")
    if len(y) < 100 or len(set(y)) < 2:
        print("    insufficient data"); return
    p = cross_val_predict(LogisticRegression(), dev, y, cv=5, method="predict_proba")[:, 1]
    base = np.full(len(y), y.mean())
    ll_base, ll_dev = log_loss(y, base), log_loss(y, p)
    auc = roc_auc_score(y, dev[:, 0] * 1.0)   # more-negative dev should -> under; check sign via -dev
    coef = LogisticRegression().fit(dev, y).coef_[0][0]
    print(f"    log-loss: baseline(under-rate)={ll_base:.5f}  deviation-model={ll_dev:.5f}  ({'HELPS' if ll_dev < ll_base - 1e-4 else 'no improvement'})")
    print(f"    logistic coef(deviation->under) = {coef:+.3f}  (negative = more model-under -> more actual-under = RIGHT sign)")
    print(f"    AUC(-deviation -> under) = {roc_auc_score(y, -dev[:,0]):.3f}  (>0.5 = signal; 0.5 = none)")
    # binned lift
    q = np.quantile(dev[:, 0], [0, .2, .4, .6, .8, 1.0])
    print("    under-rate by model-deviation quintile (most-under -> most-over):")
    for i in range(5):
        m = (dev[:, 0] >= q[i]) & (dev[:, 0] <= q[i + 1] if i == 4 else dev[:, 0] < q[i + 1])
        if m.sum(): print(f"      dev [{q[i]:+.2f},{q[i+1]:+.2f}]: n={m.sum():>3} under-rate={y[m].mean()*100:.1f}%")


def run():
    print("=" * 88)
    print("DIAGNOSTIC GATE — does the model beat the market line? (out-of-sample)")
    print("=" * 88)
    rows, hist = _load()
    ml_test(rows, hist)
    totals_test(rows, hist)
    print("\n" + "=" * 88)


if __name__ == "__main__":
    run()
