"""MLB Edge — morning analytics pipeline entry point.

Usage:
    python -m pipeline.main              # normal run, writes docs/picks.json
    python -m pipeline.main --dry-run    # print JSON to stdout, no file write
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from pipeline.analytics.game_totals import score_game_total
from pipeline.analytics.hit_props import score_hit_props
from pipeline.analytics.hr_props import score_hr_props
from pipeline.analytics.moneyline_f5 import score_moneyline_f5
from pipeline.analytics.strikeout_props import score_strikeout_props
from pipeline.analytics.team_totals import score_team_totals
from pipeline.analytics.total_bases import score_total_bases_props
from pipeline.analytics.walk_props import score_walk_props
from pipeline.formatter import build_game_block, build_output, write_picks_json
from pipeline.resolver import archive_picks, load_history, resolve_pending, save_history
from pipeline.schedule import fetch_schedule
from pipeline.statcast import build_player_cache
from pipeline.weather import fetch_game_weather

SIGNAL_THRESHOLD = 7.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main(dry_run: bool = False) -> None:
    today = date.today()
    log.info("=== MLB Edge pipeline starting for %s ===", today)

    # --- Grade any pending picks from previous days ---
    history = load_history()
    resolve_pending(history)

    games = fetch_schedule(today)
    if not games:
        log.info("No games with probable starters today — writing empty picks.json")
        write_picks_json(build_output([], today), dry_run=dry_run)
        if not dry_run:
            save_history(history)
        return

    # --- Enrich each game with weather data ---
    log.info("Fetching weather for %d games...", len(games))
    for game in games:
        game["weather"] = fetch_game_weather(game.get("venue", ""), game.get("gameTime", ""))
        if game["weather"] and not game["weather"].get("dome"):
            wmod = game["weather"].get("wind_speed_mph")
            log.debug("  %s: wind=%s mph, temp=%s°F, blowing_out=%s",
                      game.get("venue"), wmod,
                      game["weather"].get("temp_f"),
                      game["weather"].get("blowing_out"))

    log.info("Building player cache for %d games...", len(games))
    cache = build_player_cache(games)

    game_blocks = []
    total_candidates = 0

    for game in games:
        home = game.get("homeTeam", "")
        away = game.get("awayTeam", "")
        umpire = game.get("umpire", "")
        log.info("Analyzing: %s @ %s (umpire: %s)", away, home, umpire or "TBD")

        candidates: list[dict] = []
        candidates += score_strikeout_props(game, cache)
        candidates += score_hr_props(game, cache)
        candidates += score_hit_props(game, cache)
        candidates += score_total_bases_props(game, cache)
        candidates += score_game_total(game, cache)
        candidates += score_team_totals(game, cache)
        candidates += score_moneyline_f5(game, cache)
        candidates += score_walk_props(game, cache)

        total_candidates += len(candidates)
        qualifying = [c for c in candidates if c["signal"] >= SIGNAL_THRESHOLD]

        log.info(
            "  %s @ %s: %d candidates, %d above threshold",
            away, home, len(candidates), len(qualifying),
        )

        if qualifying:
            game_blocks.append(build_game_block(game, qualifying))

    output = build_output(game_blocks, today)
    write_picks_json(output, dry_run=dry_run)

    # --- Archive today's picks and save history ---
    if not dry_run:
        archive_picks(history, game_blocks, today.isoformat())
        save_history(history)

    log.info(
        "=== Done: %d picks across %d games (from %d candidates) ===",
        output["pick_count"],
        len(game_blocks),
        total_candidates,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print JSON to stdout instead of writing file")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
