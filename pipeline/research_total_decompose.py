"""Decompose the 2026 totals signal (IN-SAMPLE dev set) — what drives the model's total beating
the line, and can a cleaner re-weighting of the components beat the current AUC ~0.575?

Components available in history.json 2026: pitcher_score_home/away, lineup_score_home/away, plus
the current predicted_total (independent) deviation. We predict UNDER (actual < closing) and
report cross-validated AUC for each feature set. (2015-2025 stays sealed — this is dev only.)

Usage:  python -m pipeline.research_total_decompose
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score

ROOT = Path(__file__).parent.parent


def _load():
    raw = json.loads((ROOT / "docs" / "history.json").read_text())
    hist = raw if isinstance(raw, list) else raw.get("games", [])
    rows = []
    for r in hist:
        ct, pt, at = r.get("vegas_total"), r.get("predicted_total"), r.get("actual_total")
        ph, pa = r.get("pitcher_score_home"), r.get("pitcher_score_away")
        lh, la = r.get("lineup_score_home"), r.get("lineup_score_away")
        if None in (ct, pt, at, ph, pa, lh, la) or abs(at - ct) < 1e-9:
            continue
        rows.append({
            "under": 1 if at < ct else 0,
            "avg_pitch": (ph + pa) / 2, "pitch_diff": ph - pa,
            "avg_line": (lh + la) / 2, "line_diff": lh - la,
            "pred_dev": pt - ct,            # current model's deviation from the line
            "resid": at - ct,
        })
    return rows


def _auc(rows, feats):
    X = np.array([[r[f] for f in feats] for r in rows])
    y = np.array([r["under"] for r in rows])
    pipe = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
    scores = cross_val_score(pipe, X, y, cv=5, scoring="roc_auc")
    return scores.mean(), scores.std()


def run():
    rows = _load()
    y = np.array([r["under"] for r in rows])
    print("=" * 80)
    print(f"2026 TOTALS DECOMPOSITION (dev set, n={len(rows)}, under-rate={y.mean()*100:.1f}%)")
    print("=" * 80)

    # how much of the model's deviation is explained by pitcher/lineup (vs park/weather/level)?
    import numpy as _np
    dev = _np.array([r["pred_dev"] for r in rows])
    for f in ("avg_pitch", "avg_line", "pitch_diff", "line_diff"):
        v = _np.array([r[f] for r in rows])
        c = _np.corrcoef(dev, v)[0, 1]
        print(f"  corr(pred_dev, {f:11}) = {c:+.3f}")
    print("  (low corrs => the model's deviation is driven mostly by park/weather/level, not pitcher/lineup)\n")

    print("CV-AUC predicting UNDER (higher = more signal; 0.50 = none):")
    sets = [
        ("current model: pred_dev",            ["pred_dev"]),
        ("avg_pitch",                          ["avg_pitch"]),
        ("avg_line",                           ["avg_line"]),
        ("pitch_diff",                         ["pitch_diff"]),
        ("line_diff",                          ["line_diff"]),
        ("avg_pitch + avg_line",               ["avg_pitch", "avg_line"]),
        ("avg_pitch+avg_line+diffs",           ["avg_pitch", "avg_line", "pitch_diff", "line_diff"]),
        ("components + pred_dev (all)",         ["avg_pitch", "avg_line", "pitch_diff", "line_diff", "pred_dev"]),
    ]
    best = None
    for name, feats in sets:
        m, s = _auc(rows, feats)
        flag = ""
        if best is None or m > best[1]:
            best = (name, m)
        print(f"  {name:32} AUC={m:.3f} ± {s:.3f}")
    print(f"\n  best in-sample feature set: {best[0]} (AUC {best[1]:.3f}) vs current pred_dev")
    print("  NOTE: in-sample AUC; the real test is the sealed 2015-25 holdout (run once, later).")
    print("=" * 80)


if __name__ == "__main__":
    run()
