"""Build and write the picks.json and trends.json output files."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone

log = logging.getLogger(__name__)

OUTPUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "docs", "picks.json"
)

TRENDS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "docs", "trends.json"
)


def build_game_block(
    game: dict,
    picks: list[dict],
    *,
    insights: dict | None = None,
    comps_count: int = 0,
    home_sp_stats: dict | None = None,
    away_sp_stats: dict | None = None,
    home_lineup_xwoba: float | None = None,
    away_lineup_xwoba: float | None = None,
    park_run_factor: float | None = None,
) -> dict:
    return {
        "gamePk":            game["gamePk"],
        "game_time":         game["gameTime"],
        "home_team":         game["homeTeam"],
        "away_team":         game["awayTeam"],
        "venue":             game.get("venue", ""),
        "home_sp":           game.get("home_sp_name", "TBD"),
        "away_sp":           game.get("away_sp_name", "TBD"),
        "home_sp_stats":     home_sp_stats or {},
        "away_sp_stats":     away_sp_stats or {},
        "home_lineup_xwoba": round(home_lineup_xwoba, 3) if home_lineup_xwoba else None,
        "away_lineup_xwoba": round(away_lineup_xwoba, 3) if away_lineup_xwoba else None,
        "park_run_factor":   park_run_factor,
        "comps_count":       comps_count,
        "insights":          insights,
        "picks":             picks,
    }


def build_output(game_blocks: list[dict], today: date) -> dict:
    pick_count = sum(len(g["picks"]) for g in game_blocks)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date":         today.isoformat(),
        "game_count":   len(game_blocks),
        "pick_count":   pick_count,
        "games":        game_blocks,
    }


def write_trends_json(trends: dict, dry_run: bool = False) -> None:
    payload = json.dumps(trends, indent=2, default=str)
    if dry_run:
        return
    os.makedirs(os.path.dirname(TRENDS_PATH), exist_ok=True)
    with open(TRENDS_PATH, "w", encoding="utf-8") as f:
        f.write(payload)
    log.info(
        "Wrote trends.json: %d pitcher signals, %d batter signals → %s",
        len(trends.get("pitchers", [])),
        len(trends.get("batters", [])),
        TRENDS_PATH,
    )


def write_picks_json(output: dict, dry_run: bool = False) -> None:
    payload = json.dumps(output, indent=2, default=str)
    if dry_run:
        print(payload)
        return
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(payload)
    log.info(
        "Wrote picks.json: %d games (%d picks) → %s",
        output["game_count"],
        output["pick_count"],
        OUTPUT_PATH,
    )
