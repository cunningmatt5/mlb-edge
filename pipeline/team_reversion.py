"""Live team-level mean-reversion signal — the "reversion juice" tier on UNDER 8.0 plays.

Overlay validated in research_team_reversion.py: when BOTH offenses are running hot vs their
own recent norm, the UNDER 8.0 has hit at a higher rate (top-20% reversion +12.1% vs +4.5%
base, monotonic, both metrics agree, 5/5 seasons — but underpowered, so this is a forward-
tracked EMERGING tier, not a proven standalone).

This module computes the same signal LIVE. 2026 has no player_game_logs.parquet, so it pulls
each team's per-game hitting from the MLB Stats API gameLog endpoint (one call per team) and
computes, ENTERING today (all games so far, today's not yet played):
  ops_hot  = OPS L5 − L20        runs_hot = runs/g L10 − season-to-date
  rev_score = z(mean both offenses' ops_hot) + z(mean both offenses' runs_hot)
standardised with the constants captured from the 2021-2025 research so live == backtest.

Run:  python -m pipeline.team_reversion [--season 2026]   (prints today's per-game tiers)
"""
from __future__ import annotations

import argparse
import logging
from datetime import date

import requests

from pipeline.statcast import _MLB_TEAM_IDS, MLB_STATS_BASE, _HEADERS, TIMEOUT

log = logging.getLogger(__name__)

# Calibration from research_team_reversion.build() over 2021-2025 — DO NOT hand-edit; regenerate
# if the metric definition changes (print df.game_ops_hot/.game_runs_hot mean/std + rev_score q80).
_OPS_MEAN, _OPS_STD   = 0.00077, 0.06453
_RUNS_MEAN, _RUNS_STD = 0.05805, 0.67208
_JUICE_THRESHOLD = 1.3717     # rev_score >= this = top-20% reversion (the +12.1% tier)
_WARM_THRESHOLD  = 0.6879     # top-33% (+7.8%) — a softer "leaning hot" flag
MIN_PRIOR = 20                # need this many completed games before the signal is stable

_signals_cache: dict | None = None
_signals_cache_key: tuple | None = None


def _team_game_ops_runs(team_id: int, season: int) -> list[tuple[str, float, float]]:
    """(date, runs, OPS) per completed game for a team, chronological. One API call."""
    try:
        resp = requests.get(
            f"{MLB_STATS_BASE}/teams/{team_id}/stats",
            params={"stats": "gameLog", "group": "hitting", "season": season, "gameType": "R"},
            headers=_HEADERS, timeout=TIMEOUT,
        )
        resp.raise_for_status()
        splits = resp.json().get("stats", [{}])[0].get("splits", [])
    except Exception as exc:
        log.warning("team_reversion: gameLog fetch failed for team %d: %s", team_id, exc)
        return []
    out = []
    for s in splits:
        st = s.get("stat", {})
        try:
            ab, bb = int(st["atBats"]), int(st["baseOnBalls"])
            h, d2, t3, hr = int(st["hits"]), int(st["doubles"]), int(st["triples"]), int(st["homeRuns"])
            runs = int(st["runs"])
            if ab <= 0:
                continue
            tb = h + d2 + 2 * t3 + 3 * hr
            ops = (h + bb) / (ab + bb) + tb / ab
            out.append((s.get("date", ""), float(runs), float(ops)))
        except (KeyError, ValueError, ZeroDivisionError, TypeError):
            continue
    out.sort(key=lambda x: x[0])
    return out


def _team_signal(games: list[tuple[str, float, float]]) -> dict | None:
    """ops_hot / runs_hot from a team's completed-game history (all of it = entering today)."""
    if len(games) < MIN_PRIOR:
        return None
    runs = [g[1] for g in games]
    ops = [g[2] for g in games]
    ops_l5 = sum(ops[-5:]) / 5
    ops_l20 = sum(ops[-20:]) / 20
    runs_l10 = sum(runs[-10:]) / 10
    runs_std = sum(runs) / len(runs)          # season-to-date
    return {"ops_hot": ops_l5 - ops_l20, "runs_hot": runs_l10 - runs_std, "n": len(games),
            # raw components so the UI can show WHAT drives the signal, not just a verdict.
            "ops_l5": ops_l5, "ops_l20": ops_l20, "runs_l10": runs_l10, "runs_std": runs_std}


def build_signals(season: int) -> dict[str, dict]:
    """{full_team_name: {ops_hot, runs_hot, n}} for every team. Cached per (season, day)."""
    global _signals_cache, _signals_cache_key
    key = (season, date.today().isoformat())
    if _signals_cache_key == key and _signals_cache is not None:
        return _signals_cache
    sig = {}
    seen_ids = set()
    for name, tid in _MLB_TEAM_IDS.items():
        if tid in seen_ids:      # Athletics alias shares id 133
            continue
        seen_ids.add(tid)
        s = _team_signal(_team_game_ops_runs(tid, season))
        if s:
            # map back to every name pointing at this id (so "Athletics" & "Oakland …" both resolve)
            for nm, i in _MLB_TEAM_IDS.items():
                if i == tid:
                    sig[nm] = s
    _signals_cache, _signals_cache_key = sig, key
    return sig


def game_reversion(home_team: str, away_team: str, signals: dict[str, dict]) -> dict | None:
    """Combine both offenses -> {rev_score, tier, teams:[away, home]} or None if data missing.

    tier: 'juice' (top-20% reversion, the +12.1% slice) | 'warm' (top-33%) | None.
    Each team carries its raw trailing components (ops L5 vs L20, runs/g L10 vs season) so the UI
    can show exactly WHICH offense is hot and on WHICH metric — not just the combined verdict.
    """
    h, a = signals.get(home_team), signals.get(away_team)
    if not h or not a:
        return None
    game_ops = (h["ops_hot"] + a["ops_hot"]) / 2
    game_runs = (h["runs_hot"] + a["runs_hot"]) / 2
    z_ops = (game_ops - _OPS_MEAN) / _OPS_STD
    z_runs = (game_runs - _RUNS_MEAN) / _RUNS_STD
    rev = z_ops + z_runs
    tier = "juice" if rev >= _JUICE_THRESHOLD else ("warm" if rev >= _WARM_THRESHOLD else None)

    def _team(name: str, s: dict) -> dict:
        return {
            "name": name,
            "ops_l5": round(s["ops_l5"], 3), "ops_l20": round(s["ops_l20"], 3),
            "ops_hot": round(s["ops_hot"], 3),
            "runs_l10": round(s["runs_l10"], 1), "runs_std": round(s["runs_std"], 1),
            "runs_hot": round(s["runs_hot"], 1),
        }

    return {
        "rev_score": round(rev, 2),
        "tier": tier,
        # away first, home second — matches the "Away @ Home" card layout.
        "teams": [_team(away_team, a), _team(home_team, h)],
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=date.today().year)
    args = ap.parse_args()
    sig = build_signals(args.season)
    print(f"team signals computed: {len(sig)} teams (>= {MIN_PRIOR} games)")
    hot = sorted(sig.items(), key=lambda kv: kv[1]["ops_hot"] + kv[1]["runs_hot"] / 10, reverse=True)
    print("\nhottest offenses (ops_hot / runs_hot):")
    for nm, s in hot[:8]:
        if nm == "Athletics":
            continue
        print(f"  {nm:24s} ops_hot {s['ops_hot']:+.3f}  runs_hot {s['runs_hot']:+.2f}")


if __name__ == "__main__":
    main()
