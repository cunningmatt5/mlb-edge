"""Backfill history.json with model predictions for all finished 2026 season games.

Uses the same Savant pitcher data as backtest.py (current-season stats, so there
is minor lookahead bias for early-season games — acceptable for record-keeping).
Lineup scores default to 0.5 (neutral) because historical batting orders are not
stored; win probability is therefore driven by pitcher quality and historical comps.

Only adds games not already present in history.json (keyed by gamePk), so live-
pipeline records with real lineup data are never overwritten.

Usage:
    python -m pipeline.backfill
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pipeline.backtest import (
    build_pitcher_cache,
    fetch_season_finished_games,
    score_game,
)
from pipeline.comps import load_comps_db
from pipeline.history import load_history, save_history

log = logging.getLogger(__name__)

SEASON   = 2026
DOCS_DIR = Path(__file__).parent.parent / "docs"


def run_backfill(season: int = SEASON) -> None:
    history      = load_history()
    existing_pks = {r["gamePk"] for r in history}
    log.info("Existing history: %d records (%d unique games)", len(history), len(existing_pks))

    games     = fetch_season_finished_games(season)
    new_games = [g for g in games if g["gamePk"] not in existing_pks]
    log.info("Season %d: %d finished games total, %d not yet in history",
             season, len(games), len(new_games))

    if not new_games:
        log.info("Nothing to backfill — all finished games already recorded")
        return

    sp_ids       = {g["home_sp_id"] for g in new_games} | {g["away_sp_id"] for g in new_games}
    pitcher_cache = build_pitcher_cache(sp_ids, season)

    comps_db = load_comps_db()
    log.info("Comps DB: %d records", len(comps_db))

    added = 0
    for i, g in enumerate(new_games, 1):
        try:
            result = score_game(g, pitcher_cache, comps_db)

            # Skip 0-0 results — postponed/suspended games
            if result["home_score"] == 0 and result["away_score"] == 0:
                continue
            # Skip true ties (extra-inning ties called for weather — very rare)
            if result["actual_winner"] == "tie":
                continue

            history.append({
                "date":                result["date"],
                "gamePk":              result["gamePk"],
                "home_team":           result["home_team"],
                "away_team":           result["away_team"],
                "predicted_winner":    result["predicted_winner"],
                "home_win_pct":        result["home_win_pct"],
                "predicted_total":     result.get("predicted_total"),
                "predicted_home_sp_id": g.get("home_sp_id"),
                "predicted_away_sp_id": g.get("away_sp_id"),
                "pitcher_score_home":  result["pitcher_score_home"],
                "pitcher_score_away":  result["pitcher_score_away"],
                "lineup_score_home":   0.5,
                "lineup_score_away":   0.5,
                "comps_home_win_rate": result.get("comps_home_win_rate"),
                "actual_winner":       result["actual_winner"],
                "home_score":          result["home_score"],
                "away_score":          result["away_score"],
                "actual_total":        result.get("actual_total"),
                "sp_scratched":        False,
                "backfilled":          True,
            })
            added += 1
        except Exception as exc:
            log.warning("Failed to score game %s (%s @ %s): %s",
                        g.get("gamePk"), g.get("away_team"), g.get("home_team"), exc)

        if i % 100 == 0:
            log.info("  Processed %d / %d games...", i, len(new_games))

    if added:
        # Keep chronological order so Record tab renders correctly
        history.sort(key=lambda r: (r["date"], r["gamePk"]))
        save_history(history)
        log.info("Backfill complete: added %d records (season %d, total history %d)",
                 added, season, len(history))
    else:
        log.info("No new records added")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    run_backfill()
