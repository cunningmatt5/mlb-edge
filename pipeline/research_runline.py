"""MLB Edge research — is there a backtestable RUN LINE (±1.5) edge?

Joins the free run-line odds (data/runline_by_gamepk.json) to the final margin + model signals
(docs/backtest.json) and backtests: (a) blind favorite -1.5 / dog +1.5, and (b) backing the
MODEL's favored side on the run line, bucketed by model edge. ±1.5 never pushes. $0 (no Odds API).

Prereq: download the dataset + run backfill_runline.py.

Usage:  python -m pipeline.research_runline
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.utils import american_to_decimal  # noqa: E402

ROOT = Path(__file__).parent.parent
RL = ROOT / "data" / "runline_by_gamepk.json"
MIN_SLICE_N = 100
MIN_SEASON_N = 20


def _load() -> list[dict]:
    rl = json.loads(RL.read_text(encoding="utf-8")).get("by_gamepk", {})
    bt = {str(g["gamePk"]): g for g in json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))["games"] if g.get("gamePk")}
    rows = []
    for pk, o in rl.items():
        if abs(o.get("run_line", 0) - 1.5) > 1e-9:        # standard run line only
            continue
        g = bt.get(pk)
        if not g or g.get("home_score") is None or g.get("away_score") is None:
            continue
        if g.get("home_spread") is not None:
            pass
        margin = g["home_score"] - g["away_score"]        # home - away
        rows.append({
            "pk": pk, "season": g.get("season"), "margin": margin,
            "home_spread": o["home_spread"], "home_rl": o["home_rl_price"], "away_rl": o["away_rl_price"],
            "edge": g.get("model_edge_ml"), "home_ml": g.get("home_ml"), "away_ml": g.get("away_ml"),
        })
    return rows


def _roi(bets: list[tuple]) -> dict:
    """bets = list of (won: bool, price: int, season: int). ±1.5 has no push."""
    n = w = 0
    units = 0.0
    per: dict[int, list] = {}
    for won, price, season in bets:
        if price is None:
            continue
        u = (american_to_decimal(price) - 1) if won else -1.0
        n += 1; w += 1 if won else 0; units += u
        s = per.setdefault(season, [0, 0.0]); s[0] += 1; s[1] += u
    if not n:
        return {"n": 0, "win": None, "roi": None, "season_roi": {}}
    sr = {y: round(v[1] / v[0] * 100, 1) for y, v in sorted(per.items()) if y and v[0] >= MIN_SEASON_N}
    return {"n": n, "win": round(w / n * 100, 1), "roi": round(units / n * 100, 2), "season_roi": sr}


def _passes(r: dict) -> bool:
    c = r.get("season_roi", {})
    return r["n"] >= MIN_SLICE_N and len(c) >= 3 and sum(1 for v in c.values() if v <= 0) <= 1


def _fmt(r):
    return "n=0" if not r["n"] else f"n={r['n']:>5} win={r['win']:>5.1f}% roi={r['roi']:>+7.2f}%{'  <- PASS' if _passes(r) else ''}"


def _home_fav(row) -> bool:
    return row["home_spread"] < 0          # home laying -1.5


def run():
    rows = _load()
    if not rows:
        print("No run-line rows — download dataset + run backfill_runline.py first.")
        return
    seasons = sorted({r["season"] for r in rows if r["season"]})
    print("=" * 92)
    print("RUN LINE (±1.5) edge — free SBR data, model-conditioned. No push at 1.5.")
    print("=" * 92)
    print(f"games: {len(rows):,} | seasons {seasons}\n")

    # Grading helpers: home -1.5 covers iff margin>=2; away +1.5 covers iff margin<=1 (complementary).
    def home_minus15(r): return r["margin"] >= 2
    def away_minus15(r): return r["margin"] <= -2

    passes = []

    # --- Blind: favorite -1.5 and dog +1.5 ---
    favm15, dogp15 = [], []
    for r in rows:
        if _home_fav(r):
            favm15.append((home_minus15(r), r["home_rl"], r["season"]))
            dogp15.append((not home_minus15(r), r["away_rl"], r["season"]))
        else:
            favm15.append((away_minus15(r), r["away_rl"], r["season"]))
            dogp15.append((not away_minus15(r), r["home_rl"], r["season"]))
    print("BLIND:")
    print(f"  favorite -1.5 : {_fmt(_roi(favm15))}")
    print(f"  underdog +1.5 : {_fmt(_roi(dogp15))}")
    print()

    # --- Model-conditioned: back the model's favored side on the run line ---
    # model_side = home if edge>=0 else away. Bet that side: -1.5 if it's the favorite, else +1.5.
    def model_side_bet(r):
        if r["edge"] is None:
            return None
        model_home = r["edge"] >= 0
        if model_home:
            won = home_minus15(r) if _home_fav(r) else (r["margin"] >= -1)   # home -1.5 or home +1.5
            price = r["home_rl"]
        else:
            won = away_minus15(r) if (not _home_fav(r)) else (r["margin"] <= 1)  # away -1.5 or away +1.5
            price = r["away_rl"]
        return (won, price, r["season"])

    print("MODEL-CONDITIONED — back the model's favored team on the run line, by |edge|:")
    for lo, hi, lbl in [(0.0, 0.03, "0-3%"), (0.03, 0.06, "3-6%"), (0.06, 0.10, "6-10%"), (0.10, 9, "10%+")]:
        sub = [model_side_bet(r) for r in rows if r["edge"] is not None and lo <= abs(r["edge"]) < hi]
        sub = [b for b in sub if b]
        res = _roi(sub)
        print(f"  |edge| {lbl:>6}: {_fmt(res)}")
        if _passes(res): passes.append((f"model run-line |edge| {lbl}", res))
    print()

    # --- Split: lay -1.5 with model-liked favorites vs take +1.5 with model-liked dogs ---
    lay, take = [], []
    for r in rows:
        b = model_side_bet(r)
        if not b:
            continue
        model_home = r["edge"] >= 0
        model_is_fav = (model_home and _home_fav(r)) or ((not model_home) and (not _home_fav(r)))
        (lay if model_is_fav else take).append(b)
    print("MODEL-CONDITIONED split:")
    print(f"  lay -1.5 (model likes the favorite): {_fmt(_roi(lay))}")
    print(f"  take +1.5 (model likes the dog)    : {_fmt(_roi(take))}")
    if _passes(_roi(lay)): passes.append(("lay -1.5 model-fav", _roi(lay)))
    if _passes(_roi(take)): passes.append(("take +1.5 model-dog", _roi(take)))

    print("\n" + "=" * 92)
    if passes:
        print("SLICES CLEARING THE FLOOR (n>=100, >=3 seasons, <=1 losing):")
        for label, res in passes:
            print(f"  PASS  {label}: roi {res['roi']:+.2f}% (n={res['n']}) seasons {res['season_roi']}")
    else:
        print("No run-line slice cleared the robustness floor.")
    print("=" * 92)


if __name__ == "__main__":
    run()
