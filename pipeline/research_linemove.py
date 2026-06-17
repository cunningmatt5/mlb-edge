"""MLB Edge research — does LINE MOVEMENT (open->close) predict outcomes?

Market-based signal (no model dependence). Joins free open/close ML + totals
(data/linemove_by_gamepk.json) to outcomes (docs/backtest.json) and tests:
  - Totals: follow the move (line drops -> UNDER, rises -> OVER) at the close price, push-aware.
  - Moneyline: back the steamed side (price shortened open->close).
Both with a fade check + by move magnitude. $0 (no Odds API).

Usage:  python -m pipeline.research_linemove
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.utils import american_to_decimal  # noqa: E402

ROOT = Path(__file__).parent.parent
LM = ROOT / "data" / "linemove_by_gamepk.json"
MIN_SLICE_N = 100
MIN_SEASON_N = 20


def _load() -> list[dict]:
    lm = json.loads(LM.read_text(encoding="utf-8")).get("by_gamepk", {})
    bt = {str(g["gamePk"]): g for g in json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))["games"] if g.get("gamePk")}
    rows = []
    for pk, o in lm.items():
        g = bt.get(pk)
        if not g or g.get("actual_winner") not in ("home", "away"):
            continue
        o = dict(o); o["season"] = g.get("season"); o["winner"] = g.get("actual_winner")
        o["actual_total"] = g.get("actual_total")
        rows.append(o)
    return rows


def _roi(bets: list[tuple]) -> dict:
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


def _passes(r):
    c = r.get("season_roi", {})
    return r["n"] >= MIN_SLICE_N and len(c) >= 3 and sum(1 for v in c.values() if v <= 0) <= 1


def _fmt(r):
    return "n=0" if not r["n"] else f"n={r['n']:>5} win={r['win']:>5.1f}% roi={r['roi']:>+7.2f}%{'  <- PASS' if _passes(r) else ''}"


def _implied(ml):
    if ml is None:
        return None
    dec = american_to_decimal(ml)
    return 1.0 / dec if dec else None


def run():
    rows = _load()
    if not rows:
        print("No line-move rows — run backfill_linemove.py first.")
        return
    seasons = sorted({r["season"] for r in rows if r["season"]})
    print("=" * 92)
    print("LINE MOVEMENT (open->close) — market-based signal, no model. $0.")
    print("=" * 92)
    print(f"games: {len(rows):,} | seasons {seasons}\n")
    passes = []

    # ---------- TOTALS: follow the move ----------
    def grade_total(r, side):  # side 'under'/'over'
        at, cl = r.get("actual_total"), r.get("close_total")
        if at is None or cl is None or abs(at - cl) < 1e-9:   # push/void
            return None
        won = (at < cl) if side == "under" else (at > cl)
        price = r.get("close_under") if side == "under" else r.get("close_over")
        return (won, price, r["season"])

    print("TOTALS — follow the move (line drops->UNDER, rises->OVER) by move size:")
    for lo, lbl in [(0.0, "any"), (0.5, ">=0.5"), (1.0, ">=1.0")]:
        follow, fade = [], []
        for r in rows:
            ot, cl = r.get("open_total"), r.get("close_total")
            if ot is None or cl is None:
                continue
            mv = cl - ot
            if abs(mv) <= lo - 1e-9 if lo else abs(mv) < 1e-9:
                continue
            side = "under" if mv < 0 else "over"
            b = grade_total(r, side)
            if b: follow.append(b)
            bf = grade_total(r, "over" if side == "under" else "under")
            if bf: fade.append(bf)
        rf, rfade = _roi(follow), _roi(fade)
        print(f"  move {lbl:>6}: follow {_fmt(rf)}   |   fade {_fmt(rfade)}")
        if _passes(rf): passes.append((f"totals follow move {lbl}", rf))
        if _passes(rfade): passes.append((f"totals FADE move {lbl}", rfade))
    print()

    # ---------- MONEYLINE: back the steamed side ----------
    print("MONEYLINE — back the steamed side (price shortened open->close) by implied-prob shift:")
    for lo, lbl in [(0.0, "any"), (0.03, ">=3pp"), (0.06, ">=6pp")]:
        steam, fade = [], []
        for r in rows:
            ho, hc = _implied(r.get("open_home_ml")), _implied(r.get("close_home_ml"))
            if ho is None or hc is None:
                continue
            shift = hc - ho            # +ve = home shortened (steam toward home)
            if abs(shift) < max(lo, 1e-9):
                continue
            if shift > 0:              # steam home
                steam.append((r["winner"] == "home", r.get("close_home_ml"), r["season"]))
                fade.append((r["winner"] == "away", r.get("close_away_ml"), r["season"]))
            else:                      # steam away
                steam.append((r["winner"] == "away", r.get("close_away_ml"), r["season"]))
                fade.append((r["winner"] == "home", r.get("close_home_ml"), r["season"]))
        rs, rf = _roi(steam), _roi(fade)
        print(f"  shift {lbl:>6}: steam {_fmt(rs)}   |   fade {_fmt(rf)}")
        if _passes(rs): passes.append((f"ML steam {lbl}", rs))
        if _passes(rf): passes.append((f"ML FADE steam {lbl}", rf))

    print("\n" + "=" * 92)
    if passes:
        print("SLICES CLEARING THE FLOOR (n>=100, >=3 seasons, <=1 losing):")
        for label, res in passes:
            print(f"  PASS  {label}: roi {res['roi']:+.2f}% (n={res['n']}) seasons {res['season_roi']}")
    else:
        print("No line-movement slice cleared the robustness floor.")
    print("=" * 92)


if __name__ == "__main__":
    run()
