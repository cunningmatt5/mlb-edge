"""Build and write the picks.json output file."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone

log = logging.getLogger(__name__)

OUTPUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "docs", "picks.json"
)


def build_game_block(game: dict, picks: list[dict]) -> dict:
    return {
        "gamePk": game["gamePk"],
        "game_time": game["gameTime"],
        "home_team": game["homeTeam"],
        "away_team": game["awayTeam"],
        "venue": game.get("venue", ""),
        "home_sp": game.get("home_sp_name", "TBD"),
        "away_sp": game.get("away_sp_name", "TBD"),
        "picks": sorted(picks, key=lambda p: p["signal"], reverse=True),
    }


def build_output(game_blocks: list[dict], today: date) -> dict:
    pick_count = sum(len(g["picks"]) for g in game_blocks)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": today.isoformat(),
        "pick_count": pick_count,
        "games": game_blocks,
    }


def write_picks_json(output: dict, dry_run: bool = False) -> None:
    payload = json.dumps(output, indent=2, default=str)
    if dry_run:
        print(payload)
        return
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(payload)
    log.info(
        "Wrote picks.json: %d picks across %d games → %s",
        output["pick_count"],
        len(output["games"]),
        OUTPUT_PATH,
    )
