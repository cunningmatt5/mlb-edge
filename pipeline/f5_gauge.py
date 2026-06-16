"""
FREE skill gauge for first-5-innings (F5) totals — go/no-go before buying real F5 lines.

Mirrors team_total_gauge: a synthetic F5 line (full-game closing total × the empirical
F5 fraction) is ~fair, so if the model's F5-UNDER lean beats the BLIND base rate at the
SAME line, that's genuine skill — the gap cancels the synthetic line's bias. A gap near 0
means NO F5 skill regardless of any ROI, and we should NOT spend credits on real F5 lines.

Actual F5 runs come from the MLB schedule linescore (innings 1-5, free). The model signal
is SP-suppression only (F5 is starter-dominated) from well-covered historical stats
(xfip, xera, barrel%-against). Seasons 2024-2025 (prior-season pitcher caches available).

Usage:  python pipeline/f5_gauge.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402
import statistics as st  # noqa: E402

from pipeline.backtest import load_historical_pitcher_cache, load_closing_lines, MLB_API, TIMEOUT  # noqa: E402
from pipeline.scorer import normalize  # noqa: E402

SEASONS = [2024, 2025]


def fetch_season_f5(season: int) -> dict[int, dict]:
    """{gamePk: {sp_home, sp_away, f5, full}} from one schedule+linescore call."""
    r = requests.get(
        f"{MLB_API}/schedule",
        params={"sportId": 1, "startDate": f"03/01/{season}", "endDate": f"10/31/{season}",
                "hydrate": "probablePitcher,linescore", "gameType": "R"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    out: dict[int, dict] = {}
    for dt in r.json().get("dates", []):
        for g in dt.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            ls = g.get("linescore", {})
            inn = ls.get("innings", [])
            if len(inn) < 5:
                continue   # rain-shortened / suspended < 5 innings — exclude
            f5 = sum((i.get("home", {}).get("runs") or 0) + (i.get("away", {}).get("runs") or 0) for i in inn[:5])
            full = (ls.get("teams", {}).get("home", {}).get("runs") or 0) + \
                   (ls.get("teams", {}).get("away", {}).get("runs") or 0)
            teams = g.get("teams", {})
            out[g["gamePk"]] = {
                "sp_home": teams.get("home", {}).get("probablePitcher", {}).get("id"),
                "sp_away": teams.get("away", {}).get("probablePitcher", {}).get("id"),
                "f5": f5, "full": full,
            }
    return out


def _supp(sp: dict | None) -> float:
    """SP run-suppression on [0,1] from well-covered stats; 1 = elite. None → 0.5."""
    if not sp:
        return 0.5
    parts = []
    if sp.get("xfip") is not None:
        parts.append(1.0 - normalize(sp["xfip"], 2.5, 5.5))
    if sp.get("xera") is not None:
        parts.append(1.0 - normalize(sp["xera"], 2.5, 5.5))
    if sp.get("barrel_pct_against") is not None:
        parts.append(1.0 - normalize(sp["barrel_pct_against"], 0.03, 0.15))
    return sum(parts) / len(parts) if parts else 0.5


def run() -> None:
    rows = []  # (f5, full, supp, closing_total)
    for s in SEASONS:
        data = fetch_season_f5(s)
        sp_ids = {d["sp_home"] for d in data.values() if d["sp_home"]} | \
                 {d["sp_away"] for d in data.values() if d["sp_away"]}
        cache = load_historical_pitcher_cache(sp_ids, s)
        cl = load_closing_lines(s)
        for pk, d in data.items():
            ct = (cl.get(pk) or {}).get("closing_total")
            try:
                ct = float(ct) if ct is not None else None
            except (TypeError, ValueError):
                ct = None
            sc = (_supp(cache.get(d["sp_home"], {})) + _supp(cache.get(d["sp_away"], {}))) / 2
            rows.append((d["f5"], d["full"], sc, ct))
        print(f"  season {s}: {len(data)} final games")

    # Empirical F5 fraction → fair synthetic F5 line per game.
    full_mean = st.mean(r[1] for r in rows if r[1])
    f5_mean = st.mean(r[0] for r in rows)
    frac = f5_mean / full_mean
    sized = [r for r in rows if r[3]]   # games with a full-game closing total
    print("\n" + "=" * 78)
    print("F5 SKILL GAUGE — does model SP-suppression predict F5 UNDERs vs a fair line?")
    print("=" * 78)
    print(f"  games: {len(rows)} ({len(sized)} with full-game closing total)")
    print(f"  mean F5 runs = {f5_mean:.2f} | mean full = {full_mean:.2f} | F5 fraction = {frac:.3f}")
    print(f"  synthetic F5 line = full_game_closing_total × {frac:.3f}")

    def under_rate(sub):
        u = [1 if r[0] < r[3] * frac else 0 for r in sub]
        return (len(u), 100 * sum(u) / len(u)) if u else (0, 0.0)

    blind_n, blind_rate = under_rate(sized)
    print(f"\n  BLIND base rate (all games, F5 < synthetic line): {blind_rate:.1f}%  (n={blind_n})")

    # Model leans F5-under = high combined SP suppression. Show by tier + the skill gap.
    print("\n  By SP-suppression tier (model F5-UNDER lean strengthens with suppression):")
    cuts = sorted(sized, key=lambda r: r[2])
    print(f"    {'tier':<22}{'n':>6}{'F5-under%':>12}{'skill gap':>12}")
    for lo, hi, lbl in [(0.60, 1.01, "supp ≥ 0.60 (strong)"),
                        (0.55, 1.01, "supp ≥ 0.55"),
                        (0.50, 1.01, "supp ≥ 0.50")]:
        sub = [r for r in sized if lo <= r[2] < hi]
        n, rate = under_rate(sub)
        gap = rate - blind_rate
        print(f"    {lbl:<22}{n:>6}{rate:>11.1f}%{gap:>+11.1f}")

    print("\n  READ: the SKILL GAP (model − blind, same line) is the true signal — invariant")
    print("  to the synthetic line's bias. Gap ≈ 0 → no F5 skill → do NOT buy real F5 lines.")
    print("  A clearly positive gap on the strong-suppression tier → justifies pulling real")
    print("  F5 closing lines to validate a true F5 UNDER edge.\n")


if __name__ == "__main__":
    run()
