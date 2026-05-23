"""Run retroactive signal scoring against historical game data.

Usage:
    python -m pipeline.backtest [--seasons 2023,2024]

Reads:  data/seasons/{year}/*.parquet + player_cache.pkl
Writes: data/backtest_results.parquet
"""
from __future__ import annotations

import argparse
import logging
import pickle
from pathlib import Path
from typing import Optional

import pandas as pd

from pipeline.analytics.game_totals import score_game_total
from pipeline.analytics.hit_props import score_hit_props
from pipeline.analytics.hr_props import score_hr_props
from pipeline.analytics.moneyline_f5 import score_moneyline_f5
from pipeline.analytics.strikeout_props import score_strikeout_props
from pipeline.analytics.team_totals import score_team_totals
from pipeline.analytics.total_bases import score_total_bases_props
from pipeline.analytics.walk_props import score_walk_props

DATA_DIR    = Path(__file__).parent.parent / "data"
SEASONS_DIR = DATA_DIR / "seasons"

log = logging.getLogger(__name__)

# Fallback grading thresholds (mirror resolver.py — used when no book line)
_GRADE: dict = {
    "K_PROP":    lambda a, d: "WIN" if a >= 6 else "LOSS",
    "HR_PROP":   lambda a, d: "WIN" if a >= 1 else "LOSS",
    "HIT_PROP":  lambda a, d: "WIN" if a >= 1 else "LOSS",
    "TB_PROP":   lambda a, d: "WIN" if a >= 2 else "LOSS",
    "WALK_PROP": lambda a, d: (
        "WIN" if (d == "UNDER" and a <= 1) or (d == "OVER" and a >= 2) else "LOSS"
    ),
}


def run_backtest(seasons: list[int] | None = None) -> pd.DataFrame:
    """Score all historical games and return results DataFrame."""
    if seasons is None:
        seasons = [2023, 2024]

    all_rows: list[dict] = []
    for season in seasons:
        season_dir = SEASONS_DIR / str(season)
        if not season_dir.exists():
            log.warning(
                "Season %d data not found at %s — run historical.py first", season, season_dir
            )
            continue
        rows = _backtest_season(season, season_dir)
        all_rows.extend(rows)
        log.info("Season %d: %d pick records generated", season, len(rows))

    if not all_rows:
        log.error("No results generated — check that historical data was pulled")
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    out_path = DATA_DIR / "backtest_results.parquet"
    df.to_parquet(out_path, index=False)
    log.info("Saved backtest_results.parquet: %d total rows", len(df))
    return df


# ---------------------------------------------------------------------------
# Season-level processing
# ---------------------------------------------------------------------------

def _backtest_season(season: int, season_dir: Path) -> list[dict]:
    games_df   = pd.read_parquet(season_dir / "games.parquet")
    logs_df    = pd.read_parquet(season_dir / "player_game_logs.parquet")
    lineups_df = pd.read_parquet(season_dir / "game_lineups.parquet")
    with open(season_dir / "player_cache.pkl", "rb") as f:
        cache: dict = pickle.load(f)

    # Pre-index DataFrames for fast per-game lookups
    logs_by_game    = {pk: g for pk, g in logs_df.groupby("game_pk")}
    lineups_by_game = {pk: g for pk, g in lineups_df.groupby("game_pk")}

    rows: list[dict] = []
    total = len(games_df)

    for i, game_row in enumerate(games_df.itertuples()):
        if i % 250 == 0:
            log.info("  Season %d: %d/%d games", season, i, total)

        game_pk  = game_row.game_pk
        game_log = logs_by_game.get(game_pk)
        if game_log is None:
            continue

        # Identify actual starting pitchers from boxscore (gameStarted=True)
        home_sp_id = _find_sp(game_log, "home")
        away_sp_id = _find_sp(game_log, "away")

        # Build batting lineups from lineup table
        lineup_group = lineups_by_game.get(game_pk, pd.DataFrame())
        home_lineup  = _get_lineup(lineup_group, "home")
        away_lineup  = _get_lineup(lineup_group, "away")

        game: dict = {
            "gamePk":       game_pk,
            "game_pk":      game_pk,
            "homeTeam":     game_row.home_team,
            "awayTeam":     game_row.away_team,
            "home_sp_id":   home_sp_id,
            "away_sp_id":   away_sp_id,
            "home_sp_name": cache.get(home_sp_id, {}).get("name", "") if home_sp_id else "",
            "away_sp_name": cache.get(away_sp_id, {}).get("name", "") if away_sp_id else "",
            "home_lineup":  home_lineup,
            "away_lineup":  away_lineup,
            "venue":        game_row.venue,
            "weather":      None,   # skip — not available for historical games
            "umpire":       "",     # skip — skip umpire modifier for backtest
        }

        # Run all scorers
        candidates: list[dict] = []
        candidates += score_strikeout_props(game, cache)
        candidates += score_hr_props(game, cache)
        candidates += score_hit_props(game, cache)
        candidates += score_total_bases_props(game, cache)
        candidates += score_game_total(game, cache)
        candidates += score_team_totals(game, cache)
        candidates += score_moneyline_f5(game, cache)
        candidates += score_walk_props(game, cache)

        for pick in candidates:
            if pick["signal"] < 5.0:
                continue
            outcome = _grade_pick(
                pick,
                home_score=game_row.home_score,
                away_score=game_row.away_score,
                game_log=game_log,
            )
            if outcome is None:
                continue
            rows.append({
                "game_pk":    game_pk,
                "date":       game_row.date,
                "season":     season,
                "bet_type":   pick["bet_type"],
                "subject":    pick.get("subject", ""),
                "subject_id": pick.get("subject_id"),
                "direction":  pick["direction"],
                "signal":     pick["signal"],
                "tier":       _assign_tier(pick["signal"]),
                "outcome":    outcome,
            })

    return rows


# ---------------------------------------------------------------------------
# Grading helpers
# ---------------------------------------------------------------------------

def _grade_pick(
    pick: dict,
    home_score,
    away_score,
    game_log: pd.DataFrame,
) -> Optional[str]:
    bet_type   = pick["bet_type"]
    direction  = pick["direction"]
    subject_id = pick.get("subject_id")

    if bet_type in ("K_PROP", "WALK_PROP"):
        if not subject_id:
            return None
        # Cast to float to handle int vs float64 pandas column type mismatch
        rows = game_log[(game_log["player_id"].astype(float) == float(subject_id)) & game_log["is_pitcher"]]
        if rows.empty:
            return None
        actual = float(rows.iloc[0]["K"] if bet_type == "K_PROP" else rows.iloc[0]["P_BB"])
        return _GRADE[bet_type](actual, direction)

    if bet_type in ("HR_PROP", "HIT_PROP", "TB_PROP"):
        if not subject_id:
            return None
        # Cast to float to handle int vs float64 pandas column type mismatch
        rows = game_log[(game_log["player_id"].astype(float) == float(subject_id)) & ~game_log["is_pitcher"]]
        if rows.empty:
            return None
        col = {"HR_PROP": "HR", "HIT_PROP": "H", "TB_PROP": "TB"}[bet_type]
        actual = float(rows.iloc[0][col])
        return _GRADE[bet_type](actual, direction)

    if home_score is None or away_score is None:
        return None

    hs = float(home_score)
    as_ = float(away_score)

    if bet_type == "TOTAL":
        total = hs + as_
        return "WIN" if (direction == "OVER" and total > 8) or (direction == "UNDER" and total < 9) else "LOSS"

    if bet_type == "TEAM_TOTAL":
        side = pick.get("subject_side", "home")
        runs = hs if side == "home" else as_
        if direction == "OVER":
            return "WIN" if runs >= 5 else "LOSS"
        return "WIN" if runs <= 3 else "LOSS"

    if bet_type == "ML_F5":
        # Approximation: use full-game result (F5 data not available historically)
        if hs == as_:
            return "LOSS"
        if direction == "HOME":
            return "WIN" if hs > as_ else "LOSS"
        return "WIN" if as_ > hs else "LOSS"

    return None


def _find_sp(game_log: pd.DataFrame, side: str) -> Optional[int]:
    """Return player_id of the actual starting pitcher for one side."""
    mask = game_log["is_pitcher"] & game_log["game_started"] & (game_log["side"] == side)
    rows = game_log[mask]
    if rows.empty:
        return None
    return int(rows.iloc[0]["player_id"])


def _get_lineup(lineup_group: pd.DataFrame, side: str) -> list[int]:
    """Return batting-order IDs for one side, sorted by order."""
    if lineup_group.empty:
        return []
    sub = lineup_group[lineup_group["side"] == side].sort_values("batting_order")
    return [int(pid) for pid in sub["player_id"].tolist()]


def _assign_tier(signal: float) -> str:
    if signal >= 8.0:
        return "ELITE"
    if signal >= 6.5:
        return "GREAT"
    return "APPEALING"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Run retroactive signal backtest")
    parser.add_argument("--seasons", default="2023,2024", help="Comma-separated years")
    args = parser.parse_args()
    seasons = [int(s.strip()) for s in args.seasons.split(",")]
    run_backtest(seasons)
