"""Soft-book / line-value backtest ($0) — do edges live BETWEEN books, not vs the sharp line?

Free multi-book SBR dataset (6 retail books: DK/FD/Caesars/Bet365/BetMGM/BetRivers, no Pinnacle).
For each game we compute the 6-book NO-VIG consensus fair prob per side, and the BEST available
price across books. If the best price's implied prob is below consensus fair, betting it is +EV by
the consensus. Backtest whether that actually wins (line-shopping / soft-outlier edge), by edge
bucket + season. ML has no push.

Usage:  python -m pipeline.research_softbook
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pipeline.utils import american_to_decimal

ROOT = Path(__file__).parent.parent


def _norm(s):
    n = re.sub(r"[^a-z0-9]", "", str(s).lower()); return "athletics" if n.endswith("athletics") else n


def _imp(odds):  # American -> implied prob (with vig)
    return 1.0 / american_to_decimal(odds)


def _outcomes():
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text())["games"]
    out = {}
    for g in bt:
        d = (g.get("date") or "")[:10]
        if d and g.get("home_team") and g.get("away_team") and g.get("actual_winner") in ("home", "away"):
            out[(d, _norm(g["home_team"]), _norm(g["away_team"]))] = (g["actual_winner"], g.get("season"))
    return out


def run():
    data = json.loads((ROOT / "data" / "external" / "mlb_odds_dataset.json").read_text())
    out = _outcomes()
    # observations: (edge, best_odds, won, season)
    obs = []
    shop = []   # (won_at_consensus_avg_decimal, won_at_best_decimal, won, season) for uplift
    for date, games in data.items():
        for g in games:
            key = (date[:10], _norm((g.get("gameView", {}).get("homeTeam") or {}).get("fullName", "")),
                   _norm((g.get("gameView", {}).get("awayTeam") or {}).get("fullName", "")))
            res = out.get(key)
            if not res:
                continue
            winner, season = res
            ho, ao = [], []
            for bm in g.get("odds", {}).get("moneyline", []):
                cl = bm.get("currentLine") or {}
                if cl.get("homeOdds") is not None and cl.get("awayOdds") is not None:
                    ho.append(cl["homeOdds"]); ao.append(cl["awayOdds"])
            if len(ho) < 3:           # need a few books to shop
                continue
            # 6-book no-vig consensus fair prob per side
            fh = sum(_imp(h) / (_imp(h) + _imp(a)) for h, a in zip(ho, ao)) / len(ho)
            fa = 1 - fh
            best_h, best_a = max(ho), max(ao)             # best price per side
            for side, fair, best, won in [("home", fh, best_h, winner == "home"),
                                          ("away", fa, best_a, winner == "away")]:
                edge = fair - _imp(best)                  # +ve => best price beats fair => +EV
                obs.append((edge, best, won, season))
            # line-shop uplift: bet consensus favorite, at avg vs best price
            fav_home = fh >= 0.5
            avg_dec = (sum(american_to_decimal(o) for o in (ho if fav_home else ao)) / len(ho))
            best_dec = american_to_decimal(best_h if fav_home else best_a)
            won_fav = winner == ("home" if fav_home else "away")
            shop.append((avg_dec, best_dec, won_fav, season))

    print("=" * 90)
    print(f"SOFT-BOOK / LINE-VALUE (ML) — {len(obs)//2} games, 6 retail books, no-vig consensus = fair")
    print("=" * 90)

    print("\n[A] Bet a side at BEST price when it beats consensus fair, by edge bucket (push-free):")
    print(f"  {'edge bucket':<16} {'n':>6} {'win%':>6} {'ROI%':>8}  stability")
    buckets = [("<=0 (no value)", -9, 0), ("0-1%", 0, 0.01), ("1-2%", 0.01, 0.02),
               ("2-3%", 0.02, 0.03), ("3-5%", 0.03, 0.05), ("5%+", 0.05, 9)]
    for lbl, lo, hi in buckets:
        sub = [(b, won, s) for (e, b, won, s) in obs if lo <= e < hi]
        if len(sub) < 50:
            print(f"  {lbl:<16} n={len(sub):>5} (thin)"); continue
        n = len(sub); w = sum(1 for _, won, _ in sub if won)
        u = sum((american_to_decimal(b) - 1) if won else -1 for b, won, _ in sub)
        per = {}
        for b, won, s in sub:
            d = per.setdefault(s, [0, 0.0]); d[0] += 1; d[1] += (american_to_decimal(b) - 1) if won else -1
        sr = {s: round(v[1]/v[0]*100, 1) for s, v in sorted(per.items()) if v[0] >= 30}
        pos = sum(1 for r in sr.values() if r > 0)
        print(f"  {lbl:<16} {n:>6} {w/n*100:>5.1f}% {u/n*100:>+7.2f}%  {pos}/{len(sr)} seasons+  {sr}")

    print("\n[B] Line-shopping uplift — bet consensus favorite at AVG price vs BEST price:")
    for lbl, idx in [("at consensus AVG price", 0), ("at BEST available price", 1)]:
        n = len(shop); w = sum(1 for r in shop if r[2])
        u = sum((r[idx] - 1) if r[2] else -1 for r in shop)
        print(f"  {lbl:<26} n={n} win={w/n*100:.1f}% ROI={u/n*100:+.2f}%")
    print("\n  (uplift = BEST minus AVG ROI = pure line-shopping value, no model needed)")
    print("=" * 90)


if __name__ == "__main__":
    run()
