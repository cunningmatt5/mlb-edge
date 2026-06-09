"""
FREE skill gauge for team totals (no API spend) — go/no-go before buying real lines.

We have team-total OUTCOMES (actual team runs) but no historical lines. So this
reconstructs 2024-25 games (real lineups exist for those seasons), runs the ACTUAL
score_team_totals() model to get its OVER/UNDER leans, derives a SYNTHETIC team line
from the closing game total + no-vig ML split, and grades the leans vs actual runs at
a flat -110.

This is a NECESSARY-condition check: a synthetic line is fair-ish, so if the model's
lean can't beat it, there is no hope of beating the real market line (which carries
vig + shading). If it DOES show skill, that justifies the credit spend to pull real
lines and test true profitability.

Usage:
    python pipeline/team_total_gauge.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pipeline.analytics.team_totals import score_team_totals
from pipeline.backtest import load_full_historical_cache, load_season_lineups
from pipeline.odds import no_vig_prob

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
SEASONS = [2024, 2025]            # only seasons with real historical lineups
WIN_UNITS = 100 / 110             # flat -110 payout
MARGIN_K = 5.0                    # run-margin per (win_prob-0.5); clamp ±2


def _synthetic_lines(game_total: float, home_ml, away_ml) -> tuple[float, float]:
    """(home_team_line, away_team_line) split of the game total using the no-vig ML."""
    try:
        p_home = no_vig_prob(int(home_ml), int(away_ml))[0]
    except Exception:
        p_home = 0.5
    m = max(-2.0, min(2.0, MARGIN_K * (p_home - 0.5)))   # expected run margin (home-away)
    return (game_total + m) / 2.0, (game_total - m) / 2.0


def run() -> None:
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))["games"]
    # rows: (season, direction, signal, won)
    rows: list[tuple] = []
    per_season_games = {s: 0 for s in SEASONS}
    blind_n = blind_under = 0    # base rate: ALL team-games vs the same synthetic line

    for season in SEASONS:
        cache = load_full_historical_cache(season)       # prior-season, point-in-time
        lineups = load_season_lineups(season)
        if not cache or not lineups:
            print(f"  {season}: no cache/lineups — skipped")
            continue
        for g in bt:
            if g.get("season") != season:
                continue
            if g.get("home_score") is None or g.get("closing_total") is None:
                continue
            if g.get("home_ml") is None or g.get("away_ml") is None:
                continue
            lu = lineups.get(g.get("gamePk")) or {}
            if len(lu.get("home", [])) < 3 or len(lu.get("away", [])) < 3:
                continue
            gd = {
                "homeTeam": g["home_team"], "awayTeam": g["away_team"],
                "home_sp_id": g.get("home_sp_id"), "away_sp_id": g.get("away_sp_id"),
                "venue": g.get("venue", ""),
                "home_lineup": lu.get("home", []), "away_lineup": lu.get("away", []),
            }
            try:
                picks = score_team_totals(gd, cache)
            except Exception:
                continue
            per_season_games[season] += 1
            hl, al = _synthetic_lines(float(g["closing_total"]), g["home_ml"], g["away_ml"])
            runs = {"home": g["home_score"], "away": g["away_score"]}
            line = {"home": hl, "away": al}
            for side in ("home", "away"):            # blind base rate (both teams, every game)
                blind_n += 1
                blind_under += 1 if runs[side] < line[side] else 0
            for p in picks:
                side = p["subject_side"]
                r, ln, d = runs[side], line[side], p["direction"]
                won = (r < ln) if d == "UNDER" else (r > ln)
                rows.append((season, d, p["signal"], won))

    if not rows:
        print("No leans graded — aborting.")
        return

    print("=" * 84)
    print("TEAM-TOTAL SKILL GAUGE (2024-25, model leans vs SYNTHETIC line, flat -110)")
    print("=" * 84)
    print(f"  games scored: {per_season_games}  |  total model leans graded: {len(rows)}")
    print(f"  break-even win rate at -110 = 52.4%")
    blind_rate = blind_under / blind_n * 100 if blind_n else 0
    model_under = [r for r in rows if r[1] == "UNDER"]
    mu_rate = sum(1 for r in model_under if r[3]) / len(model_under) * 100 if model_under else 0
    print(f"  BLIND base rate (all team-games UNDER same line): {blind_rate:.1f}%  (n={blind_n})")
    print(f"  MODEL UNDER-lean win rate: {mu_rate:.1f}%  →  SKILL GAP = {mu_rate - blind_rate:+.1f} pp")
    print(f"  ^ the gap (model − blind) is the true skill measure; it's invariant to the")
    print(f"    synthetic line's bias. A gap near 0 means NO skill regardless of ROI below.\n")

    def stats(rs):
        n = len(rs)
        if n == 0:
            return 0, None, None
        w = sum(1 for _ in rs if _[3])
        units = w * WIN_UNITS - (n - w)
        return n, round(w / n * 100, 1), round(units / n * 100, 1)

    def show(label, rs):
        n, wr, roi = stats(rs)
        flag = "" if n < 100 else (" ← +" if (roi or 0) > 0 else " ✗")
        print(f"  {label:<34} n={n:>5}  win={wr if wr is not None else 0:>5.1f}%  ROI={roi if roi is not None else 0:>+6.1f}%{flag}")

    for d in ("UNDER", "OVER"):
        sub = [r for r in rows if r[1] == d]
        print(f"── {d} leans ──")
        show(f"  all {d}", sub)
        for lo, hi, lbl in [(5, 6, "signal 5-6"), (6, 7, "signal 6-7"), (7, 11, "signal 7+")]:
            band = [r for r in sub if lo <= r[2] < hi]
            show(f"  {lbl}", band)
            if len(band) >= 100:
                parts = []
                for s in SEASONS:
                    n, wr, roi = stats([r for r in band if r[0] == s])
                    if n:
                        parts.append(f"{s}:{n}g/{'+' if (roi or 0) >= 0 else ''}{roi}%")
                print("        " + "  ".join(parts))
        print()

    print("VERDICT: if no direction/tier clears ~52.4% (and ideally rises with signal),")
    print("the model has no team-level skill vs a fair line → do NOT buy real lines.")
    print("If a tier shows real lift, it justifies pulling real lines to test profitability.")
    print("\nDone.")


if __name__ == "__main__":
    run()
