"""
Mean-reversion hitter signal — validation gauge (no API spend, no production change).

Question: among good, high-volume hitters, do those in a recent 5/10-game ACTUAL slump
(BA well below their season xBA) bounce back over the next 5/10 games MORE than a control
of non-slumping good hitters? Reversion is partly mechanical, so the test is flagged-vs-
CONTROL, not "did they bounce."

Read:
  - flagged forward BA ≈ control forward BA ≈ season xBA  → the slump carried NO forward
    info (pure noise / bad luck). If books shade a slumping star's HIT/TB line down, that's
    an exploitable recency-bias mispricing → worth pulling real prop lines to confirm.
  - flagged forward BA << control forward BA → the slump predicts continued struggle (real
    decline), market is right, no edge.

Honest limits: historical store has season expected + per-game actual but NO per-window
expected → luck filter is a proxy (good season xwOBA + recent actual far below season xBA).
No historical prop lines → this is a skill check / go-no-go, not an ROI proof.

Usage:  python pipeline/reversion_research.py
"""

from __future__ import annotations

import pickle
import statistics as st
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SEASONS = [2024, 2025]
MIN_GAMES = 80          # high-volume
MIN_XWOBA = 0.330       # "good hitter"
TRAIL = [5, 10]         # slump windows
FWD = [5, 10]           # forward windows
GAPS = [0.030, 0.050, 0.070]   # cold = trailing BA <= season xBA - gap
MIN_TRAIL_AB = 8        # ignore tiny-sample trailing windows
MIN_FWD_AB = 8


def _load(season: int):
    with open(ROOT / f"data/seasons/{season}/player_cache.pkl", "rb") as f:
        cache = pickle.load(f)
    expected = {
        v["mlbam_id"]: v for v in cache.values()
        if isinstance(v, dict) and v.get("mlbam_id") and v.get("xwoba") and not v.get("xwoba_against")
    }
    gl = pd.read_parquet(ROOT / f"data/seasons/{season}/player_game_logs.parquet")
    gl = gl[~gl["is_pitcher"]].copy()
    dates = pd.read_parquet(ROOT / f"data/seasons/{season}/games.parquet")[["game_pk", "date"]]
    gl = gl.merge(dates, on="game_pk", how="left").dropna(subset=["date"])
    gl = gl.sort_values(["player_id", "date", "game_pk"])
    return expected, gl


def _ba(games: list[dict], lo: int, hi: int) -> tuple[int, int]:
    """(H, AB) summed over games[lo:hi]."""
    H = sum(g["H"] for g in games[lo:hi])
    AB = sum(g["AB"] for g in games[lo:hi])
    return H, AB


def build_observations() -> list[dict]:
    """One row per eligible (player, date) with trailing/forward BA + season xBA."""
    obs = []
    for season in SEASONS:
        expected, gl = _load(season)
        # universe: good + high volume
        gp = gl.groupby("player_id").size()
        universe = {
            pid for pid in gp[gp >= MIN_GAMES].index
            if pid in expected and (expected[pid].get("xwoba") or 0) >= MIN_XWOBA
            and expected[pid].get("xba") is not None
        }
        print(f"  season {season}: universe = {len(universe)} good high-volume hitters")
        for pid, grp in gl.groupby("player_id"):
            if pid not in universe:
                continue
            games = grp[["H", "AB"]].to_dict("records")
            sx = float(expected[pid]["xba"])
            n = len(games)
            for w in TRAIL:
                for f in FWD:
                    for i in range(w - 1, n - f):           # i = last game of trailing window
                        tH, tAB = _ba(games, i - w + 1, i + 1)
                        fH, fAB = _ba(games, i + 1, i + 1 + f)
                        if tAB < MIN_TRAIL_AB or fAB < MIN_FWD_AB:
                            continue
                        nxt = games[i + 1]
                        obs.append({
                            "w": w, "f": f, "season_xba": sx,
                            "trail_ba": tH / tAB, "fwd_ba": fH / fAB,
                            "next_hit": 1 if (nxt["AB"] >= 1 and nxt["H"] >= 1) else 0,
                            "next_has_ab": 1 if nxt["AB"] >= 1 else 0,
                        })
    return obs


def run() -> None:
    print("=" * 92)
    print("MEAN-REVERSION HITTER GAUGE — do cold good-hitters bounce MORE than a control?")
    print("=" * 92)
    obs = build_observations()
    print(f"  total eligible (player,date,window) observations: {len(obs):,}\n")

    def mean(rows, key):
        vals = [r[key] for r in rows]
        return sum(vals) / len(vals) if vals else float("nan")

    for w in TRAIL:
        for f in FWD:
            cell = [o for o in obs if o["w"] == w and o["f"] == f]
            print(f"── trailing {w}g  →  forward {f}g " + "─" * 50)
            print(f"   {'gap Δ':<8}{'flagged n':>10}{'trail BA':>10}{'fwd BA':>9}"
                  f"{'ctrl fwd':>10}{'season xBA':>12}{'margin':>9}{'nextHit f/c':>14}")
            for gap in GAPS:
                flagged = [o for o in cell if o["trail_ba"] <= o["season_xba"] - gap]
                control = [o for o in cell if o["trail_ba"] > o["season_xba"] - gap]
                if not flagged:
                    print(f"   {gap:<8.3f}{'0':>10}")
                    continue
                f_fwd = mean(flagged, "fwd_ba")
                c_fwd = mean(control, "fwd_ba")
                margin = f_fwd - c_fwd
                # next-game hit rate among games with an AB
                fh = [o for o in flagged if o["next_has_ab"]]
                ch = [o for o in control if o["next_has_ab"]]
                f_hit = mean(fh, "next_hit") if fh else float("nan")
                c_hit = mean(ch, "next_hit") if ch else float("nan")
                print(f"   {gap:<8.3f}{len(flagged):>10}{mean(flagged,'trail_ba'):>10.3f}"
                      f"{f_fwd:>9.3f}{c_fwd:>10.3f}{mean(flagged,'season_xba'):>12.3f}"
                      f"{margin:>+9.3f}{f'{f_hit:.3f}/{c_hit:.3f}':>14}")
            print()

    print("READ: 'margin' = flagged forward BA − control forward BA. Near 0 (flagged fwd ≈")
    print("control fwd ≈ season xBA) → the slump is noise that fully reverts → potential")
    print("recency-bias mispricing worth pulling real prop lines to confirm. Strongly negative")
    print("→ slumps predict continued struggle, market is right, no edge. Compare nextHit f/c.")


if __name__ == "__main__":
    run()
