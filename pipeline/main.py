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
from pipeline.formatter import build_game_block, build_output, write_picks_json
from pipeline.schedule import fetch_schedule
from pipeline.statcast import build_player_cache

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

    games = fetch_schedule(today)
    if not games:
        log.info("No games with probable starters today — writing empty picks.json")
        write_picks_json(build_output([], today), dry_run=dry_run)
        return

    log.info("Building player cache for %d games...", len(games))
    cache = build_player_cache(games)

    game_blocks = []
    total_candidates = 0

    for game in games:
        home = game.get("homeTeam", "")
        away = game.get("awayTeam", "")
        log.info("Analyzing: %s @ %s", away, home)

        candidates: list[dict] = []
        candidates += score_strikeout_props(game, cache)
        candidates += score_hr_props(game, cache)
        candidates += score_hit_props(game, cache)
        candidates += score_game_total(game, cache)
        candidates += score_moneyline_f5(game, cache)

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
