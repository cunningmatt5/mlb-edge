"""MLB Edge research — is there a backtestable First-5-innings (F5) UNDER edge?

F5 is almost pure starting pitcher (no bullpen/late-inning noise) — where the model is sharpest.
Joins the backfilled F5 closing line (data/f5_closing_odds_by_gamepk.json) to the F5 actual run
total (data/f5_results_by_gamepk.json) and backtests blind UNDER/OVER by F5 line x vig, PUSH-
CORRECTED (F5 lines like 4.0/5.0 push). Robustness floor mirrors research_under_8.py.

Prereq: run the F5 odds backfill (workflow "Backfill F5 Odds") + backfill_f5_results.py.

Usage:  python -m pipeline.research_f5
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.utils import american_to_decimal  # noqa: E402

ROOT = Path(__file__).parent.parent
ODDS = ROOT / "data" / "f5_closing_odds_by_gamepk.json"
RESULTS = ROOT / "data" / "f5_results_by_gamepk.json"
MIN_SLICE_N = 100
MIN_SEASON_N = 20
LINES = [("3.5", 3.5), ("4.0", 4.0), ("4.5", 4.5), ("5.0", 5.0), ("5.5+", 5.5)]


def _season_by_gamepk() -> dict[str, int]:
    out: dict[str, int] = {}
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))["games"]
    rows = list(bt)
    hp = ROOT / "docs" / "history.json"
    if hp.exists():
        raw = json.loads(hp.read_text(encoding="utf-8"))
        rows += raw if isinstance(raw, list) else raw.get("games", [])
    for g in rows:
        pk = g.get("gamePk")
        if pk is None:
            continue
        try:
            out[str(pk)] = int(g.get("season") or g.get("date", "")[:4])
        except (TypeError, ValueError):
            pass
    return out


def _load() -> list[dict]:
    odds = json.loads(ODDS.read_text(encoding="utf-8")).get("by_gamepk", {}) if ODDS.exists() else {}
    res  = json.loads(RESULTS.read_text(encoding="utf-8")).get("by_gamepk", {}) if RESULTS.exists() else {}
    season = _season_by_gamepk()
    rows = []
    for pk, o in odds.items():
        actual = res.get(pk)
        if actual is None:                       # no F5 result (game <5 innings) → skip
            continue
        if not all(k in o and o[k] is not None for k in ("f5_total", "f5_over_price", "f5_under_price")):
            continue
        rows.append({"gamePk": pk, "line": o["f5_total"], "over_price": o["f5_over_price"],
                     "under_price": o["f5_under_price"], "actual": float(actual),
                     "season": season.get(pk)})
    return rows


def _roi(rows: list[dict], pick_under: bool) -> dict:
    bets = wins = 0
    units = 0.0
    per_season: dict[int, list] = {}
    for r in rows:
        line, at = r["line"], r["actual"]
        if abs(at - line) < 1e-9:                # push → void
            continue
        won = (at < line) if pick_under else (at > line)
        price = r["under_price"] if pick_under else r["over_price"]
        u = (american_to_decimal(price) - 1) if won else -1.0
        bets += 1
        wins += 1 if won else 0
        units += u
        s = per_season.setdefault(r["season"], [0, 0.0])
        s[0] += 1
        s[1] += u
    if not bets:
        return {"n": 0, "win": None, "roi": None, "season_roi": {}}
    sr = {y: round(v[1] / v[0] * 100, 1) for y, v in sorted(per_season.items()) if y and v[0] >= MIN_SEASON_N}
    return {"n": bets, "win": round(wins / bets * 100, 1), "roi": round(units / bets * 100, 2), "season_roi": sr}


def _passes(res: dict) -> bool:
    counted = res.get("season_roi", {})
    losers = sum(1 for v in counted.values() if v <= 0)
    return res["n"] >= MIN_SLICE_N and len(counted) >= 2 and losers == 0


def _fmt(res, base=None):
    if not res["n"]:
        return "n=0"
    lift = f"  (lift {res['roi'] - base:+.1f})" if base is not None and res["roi"] is not None else ""
    return f"n={res['n']:>5} win={res['win']:>5.1f}% roi={res['roi']:>+7.2f}%{lift}{'  <- PASS' if _passes(res) else ''}"


def run():
    rows = _load()
    if not rows:
        print("No F5 rows — run the F5 odds backfill + backfill_f5_results.py first.")
        return
    seasons = sorted({r["season"] for r in rows if r["season"]})
    from collections import Counter
    linec = Counter(r["line"] for r in rows)
    print("=" * 92)
    print("F5 (first-5-innings) UNDER/OVER edge — blind, push-corrected")
    print("=" * 92)
    print(f"games with F5 line+result: {len(rows):,} | seasons {seasons}")
    print(f"F5 line distribution: {dict(sorted(linec.items()))}\n")

    def mask(lo):
        hi = lo + 0.25
        return [r for r in rows if (r['line'] >= 5.5 if lo == 5.5 else lo <= r['line'] < hi)]

    passes = []
    print("Blind UNDER / OVER by F5 line:")
    for lbl, ln in LINES:
        sub = mask(ln)
        u, o = _roi(sub, True), _roi(sub, False)
        print(f"  {lbl:>5}  UNDER {_fmt(u)}")
        print(f"  {lbl:>5}  OVER  {_fmt(o)}")
        if _passes(u): passes.append((f"UNDER F5 {lbl}", u))
        if _passes(o): passes.append((f"OVER F5 {lbl}", o))

    print("\nUNDER by vig band (all F5 lines pooled):")
    for label, lo, hi in [("std -110..-106", -110, -106), ("cheaper >-106", -105, 10000), ("vig-against <-110", -10000, -111)]:
        sub = [r for r in rows if lo <= r["under_price"] <= hi]
        print(f"  {label:>18}: {_fmt(_roi(sub, True))}")

    print("\n" + "=" * 92)
    if passes:
        print("SLICES CLEARING THE FLOOR (n>=100, >=2 seasons, 0 losing):")
        for label, res in passes:
            print(f"  PASS  {label}: roi {res['roi']:+.2f}% (n={res['n']}) seasons {res['season_roi']}")
    else:
        print("No F5 slice cleared the robustness floor.")
    print("=" * 92)


if __name__ == "__main__":
    run()
