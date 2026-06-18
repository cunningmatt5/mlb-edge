"""Holdout gate for the locked totals model (under ~ avg_pitcher + avg_lineup), fit on 2026.

A TRUE sealed test (2015-2020) needs caches we'd have to build (multi-hour). This runs the only
years regenerable from disk — 2024-2025 (game_lineups.parquet + prior caches exist). It's
out-of-sample for the 2026-fit re-weighting but same-era, so treat it as a FAIL-FAST gate:
if the model can't predict 2024-25 totals OOS-of-fit, don't bother building 2015-2020.

Usage:  python -m pipeline.research_holdout_totals
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

from pipeline.backtest import load_full_historical_cache, load_season_lineups
from pipeline.predictor import _lineup_score
from pipeline.utils import american_to_decimal

ROOT = Path(__file__).parent.parent


def _fit_2026():
    raw = json.loads((ROOT / "docs" / "history.json").read_text())
    hist = raw if isinstance(raw, list) else raw.get("games", [])
    X, y = [], []
    for r in hist:
        ph, pa, lh, la = r.get("pitcher_score_home"), r.get("pitcher_score_away"), r.get("lineup_score_home"), r.get("lineup_score_away")
        ct, at = r.get("vegas_total"), r.get("actual_total")
        if None in (ph, pa, lh, la, ct, at) or abs(at - ct) < 1e-9:
            continue
        X.append([(ph + pa) / 2, (lh + la) / 2]); y.append(1 if at < ct else 0)
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)).fit(np.array(X), np.array(y))
    return model


def _regen_lineup(season):
    """{gamePk: (lineup_home, lineup_away)} from game_lineups + prior-season cache."""
    lineups = load_season_lineups(season)
    cache = load_full_historical_cache(season)   # prior-season (lookahead-safe)
    out = {}
    for pk, lu in lineups.items():
        hp = [cache[i] for i in lu.get("home", []) if i in cache]
        ap = [cache[i] for i in lu.get("away", []) if i in cache]
        if hp and ap:
            out[pk] = (_lineup_score(hp), _lineup_score(ap))
    return out


def run():
    model = _fit_2026()
    bt = {str(g["gamePk"]): g for g in json.loads((ROOT / "docs" / "backtest.json").read_text())["games"] if g.get("gamePk")}
    print("=" * 84)
    print("HOLDOUT GATE — locked totals model (avg_pitch+avg_lineup, fit on 2026) on 2024-2025")
    print("=" * 84)
    allrows = []
    for season in (2024, 2025):
        lu = _regen_lineup(season)
        n = 0
        rows = []
        for pk, (lh, la) in lu.items():
            g = bt.get(str(pk))
            if not g or g.get("pitcher_score_home") is None or g.get("closing_total") is None \
               or g.get("under_price") is None or g.get("over_price") is None or g.get("actual_total") is None:
                continue
            avg_p = (g["pitcher_score_home"] + g["pitcher_score_away"]) / 2
            avg_l = (lh + la) / 2
            rows.append({"season": season, "avg_p": avg_p, "avg_l": avg_l,
                         "ct": g["closing_total"], "up": g["under_price"], "op": g["over_price"], "at": g["actual_total"]})
            n += 1
        print(f"  {season}: regenerated lineup for {len(lu)} games, matched {n} with odds+pitcher")
        allrows += rows

    if not allrows:
        print("  No rows — caches/lineups missing. (Pre-2024 needs a cache build.)"); return

    X = np.array([[r["avg_p"], r["avg_l"]] for r in allrows])
    y = np.array([1 if r["at"] < r["ct"] else 0 for r in allrows])  # under
    p_under = model.predict_proba(X)[:, 1]
    nonpush = np.array([abs(r["at"] - r["ct"]) > 1e-9 for r in allrows])
    auc = roc_auc_score(y[nonpush], p_under[nonpush])
    print(f"\n  AUC (predict UNDER), 2024-25 OOS: {auc:.3f}  (2026 in-sample was ~0.60; 0.50 = no signal)")

    # Betting test: bet the model's lean (under if p_under>0.5 else over) at std-vig, push-corrected.
    def roi(std_only):
        n = w = 0; u = 0.0; per = {}
        for r, pu in zip(allrows, p_under):
            if abs(r["at"] - r["ct"]) < 1e-9: continue
            lean_under = pu > 0.5
            price = r["up"] if lean_under else r["op"]
            if std_only and not (-110 <= price <= -106): continue
            won = (r["at"] < r["ct"]) if lean_under else (r["at"] > r["ct"])
            pr = (american_to_decimal(price) - 1) if won else -1.0
            n += 1; w += 1 if won else 0; u += pr
            d = per.setdefault(r["season"], [0, 0.0]); d[0] += 1; d[1] += pr
        if not n: return
        sr = {s: round(v[1]/v[0]*100, 1) for s, v in sorted(per.items())}
        print(f"  bet model lean ({'std-vig' if std_only else 'all-vig'}): n={n} win={w/n*100:.1f}% ROI={u/n*100:+.2f}%  seasons {sr}")
    roi(False)
    roi(True)
    print("=" * 84)
    print("Read: AUC>~0.55 + positive ROI => signal may generalize -> worth building 2015-20 caches.")
    print("      AUC~0.50 / negative ROI => 2026 was in-sample noise -> stop.")


if __name__ == "__main__":
    run()
