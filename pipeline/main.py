"""MLB Edge — morning analytics pipeline entry point.

Usage:
    python -m pipeline.main              # normal run, writes docs/picks.json
    python -m pipeline.main --dry-run    # print JSON to stdout, no file write
"""

from __future__ import annotations

import argparse
import logging
import os
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
from pipeline.odds import (
    compute_ev,
    fetch_mlb_game_lines,
    fetch_mlb_props,
    get_event_id,
    match_game_line,
    match_prop_line,
)
from pipeline.weather import fetch_game_weather

SIGNAL_THRESHOLD = 5.0   # Appealing floor — anything below is not surfaced

TIER_ELITE     = 8.0
TIER_GREAT     = 6.5
TIER_APPEALING = 5.0

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
MIN_EDGE     = 0.03   # minimum edge vs Pinnacle no-vig to surface a pick
FETCH_PROPS  = os.environ.get("ODDS_FETCH_PROPS", "").lower() == "true"  # paid tier only


def _assign_tier(signal: float) -> str:
    if signal >= TIER_ELITE:
        return "ELITE"
    if signal >= TIER_GREAT:
        return "GREAT"
    return "APPEALING"

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

    # --- Fetch today's game-level odds (free tier: h2h, totals, team_totals) ---
    game_lines = fetch_mlb_game_lines(ODDS_API_KEY, today.isoformat())
    if game_lines:
        log.info("Odds: matched %d games from Pinnacle", len(game_lines))

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
        for pick in qualifying:
            pick["tier"] = _assign_tier(pick["signal"])

        # --- Match odds lines and compute edge ---
        _PROP_TYPES = {"K_PROP", "HR_PROP", "HIT_PROP", "TB_PROP", "WALK_PROP"}
        prop_lines: dict = {}
        if FETCH_PROPS and game_lines:
            event_id = get_event_id(game, game_lines)
            if event_id:
                prop_lines = fetch_mlb_props(ODDS_API_KEY, event_id)

        for pick in qualifying:
            if pick["bet_type"] in _PROP_TYPES:
                matched = match_prop_line(pick, prop_lines) if prop_lines else None
            else:
                matched = match_game_line(pick, game, game_lines)
            if matched:
                pick["odds"] = compute_ev(pick, matched)
                pick["has_line"] = True
            else:
                pick["odds"] = None
                pick["has_line"] = False

        # Keep picks with sufficient edge OR no line matched (never suppress blind)
        qualifying = [
            p for p in qualifying
            if not p["has_line"] or p["odds"]["edge_pct"] >= MIN_EDGE
        ]

        # Sort: lined picks first (edge desc), then unpriced picks (signal desc)
        qualifying.sort(key=lambda p: (
            0 if p["has_line"] else 1,
            -p["odds"]["edge_pct"] if p["has_line"] else 0.0,
            -p["signal"],
        ))

        tier_counts = {t: sum(1 for p in qualifying if p.get("tier") == t)
                       for t in ("ELITE", "GREAT", "APPEALING")}
        lined = sum(1 for p in qualifying if p.get("has_line"))
        log.info(
            "  %s @ %s: %d candidates → Elite %d / Great %d / Appealing %d (%d lined)",
            away, home, len(candidates),
            tier_counts["ELITE"], tier_counts["GREAT"], tier_counts["APPEALING"], lined,
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
