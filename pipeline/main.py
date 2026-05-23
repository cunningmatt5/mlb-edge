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
from pipeline.comps import build_game_profile, compute_insights, find_similar_games, load_comps_db
from pipeline.formatter import build_game_block, build_output, write_picks_json
from pipeline.odds import (
    _norm_team,
    compute_ev,
    fetch_mlb_game_lines,
    fetch_mlb_props,
    get_event_id,
    get_game_event,
    match_game_line,
    match_prop_line,
)
from pipeline.park_factors import get_run_factor
from pipeline.resolver import archive_picks, load_history, resolve_pending, save_history
from pipeline.schedule import fetch_schedule
from pipeline.scorer import lineup_weighted_mean
from pipeline.statcast import build_player_cache
from pipeline.weather import fetch_game_weather

SIGNAL_THRESHOLD = 5.0

TIER_ELITE     = 8.0
TIER_GREAT     = 6.5
TIER_APPEALING = 5.0

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
MIN_EDGE     = 0.03
FETCH_PROPS  = os.environ.get("ODDS_FETCH_PROPS", "").lower() == "true"


def _assign_tier(signal: float) -> str:
    if signal >= TIER_ELITE:
        return "ELITE"
    if signal >= TIER_GREAT:
        return "GREAT"
    return "APPEALING"


def _sp_stats(sp: dict) -> dict:
    return {k: sp.get(k) for k in ("xfip", "siera", "k_pct", "stuff_plus", "bb_pct", "hr_per_9")}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main(dry_run: bool = False) -> None:
    today = date.today()
    log.info("=== MLB Edge pipeline starting for %s ===", today)

    history = load_history()
    resolve_pending(history)

    games = fetch_schedule(today)
    if not games:
        log.info("No games with probable starters today — writing empty picks.json")
        write_picks_json(build_output([], today), dry_run=dry_run)
        if not dry_run:
            save_history(history)
        return

    log.info("Fetching weather for %d games...", len(games))
    for game in games:
        game["weather"] = fetch_game_weather(game.get("venue", ""), game.get("gameTime", ""))

    log.info("Building player cache for %d games...", len(games))
    cache = build_player_cache(games)

    game_lines = fetch_mlb_game_lines(ODDS_API_KEY, today.isoformat())
    if game_lines:
        log.info("Odds: matched %d games from Pinnacle", len(game_lines))

    comps_db = load_comps_db()
    if comps_db:
        log.info("Comps database: %d historical games loaded", len(comps_db))

    game_blocks = []
    total_candidates = 0

    for game in games:
        home   = game.get("homeTeam", "")
        away   = game.get("awayTeam", "")
        umpire = game.get("umpire", "")
        log.info("Analyzing: %s @ %s (umpire: %s)", away, home, umpire or "TBD")

        # --- Comps insights ---
        profile = build_game_profile(game, cache) if comps_db else None
        insights   = None
        comps_count = 0
        if profile and comps_db:
            event = get_game_event(game, game_lines)
            if event:
                markets = event.get("markets", {})
                totals  = markets.get("totals", [])
                h2h     = markets.get("h2h", [])

                over_out  = next((o for o in totals if o.get("name") == "Over"),  None)
                under_out = next((o for o in totals if o.get("name") == "Under"), None)
                total_line  = over_out.get("point")  if over_out  else None
                over_price  = over_out["price"]       if over_out  else None
                under_price = under_out["price"]      if under_out else None

                norm_home = _norm_team(home)
                norm_away = _norm_team(away)
                home_out  = next((o for o in h2h if _norm_team(o.get("name", "")) == norm_home), None)
                away_out  = next((o for o in h2h if _norm_team(o.get("name", "")) == norm_away), None)
                home_price = home_out["price"] if home_out else None
                away_price = away_out["price"] if away_out else None

                similar     = find_similar_games(profile, comps_db, n=30)
                comps_count = len(similar)
                insights    = compute_insights(
                    similar, total_line, over_price, under_price, home_price, away_price
                )

        # --- SP stats ---
        home_sp_id = game.get("home_sp_id")
        away_sp_id = game.get("away_sp_id")
        home_sp_stats = _sp_stats(cache.get(home_sp_id, {}) if home_sp_id else {})
        away_sp_stats = _sp_stats(cache.get(away_sp_id, {}) if away_sp_id else {})

        # --- Lineup xwoba ---
        home_players = [cache[b] for b in game.get("home_lineup", []) if b in cache]
        away_players = [cache[b] for b in game.get("away_lineup", []) if b in cache]
        home_lineup_xwoba = lineup_weighted_mean(home_players, "xwoba")
        away_lineup_xwoba = lineup_weighted_mean(away_players, "xwoba")

        # --- Park factor ---
        try:
            park_run_factor = float(get_run_factor(game.get("venue", "")))
        except Exception:
            park_run_factor = None

        # --- Props scoring (secondary section) ---
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

        qualifying = [
            p for p in qualifying
            if not p["has_line"] or p["odds"]["edge_pct"] >= MIN_EDGE
        ]

        qualifying.sort(key=lambda p: (
            0 if p["has_line"] else 1,
            -p["odds"]["edge_pct"] if p["has_line"] else 0.0,
            -p["signal"],
        ))

        has_insights = insights and (insights.get("total") or insights.get("moneyline"))
        log.info(
            "  %s @ %s: insights=%s comps=%d picks=%d",
            away, home,
            "yes" if has_insights else "no",
            comps_count,
            len(qualifying),
        )

        game_blocks.append(build_game_block(
            game,
            qualifying,
            insights=insights,
            comps_count=comps_count,
            home_sp_stats=home_sp_stats,
            away_sp_stats=away_sp_stats,
            home_lineup_xwoba=home_lineup_xwoba,
            away_lineup_xwoba=away_lineup_xwoba,
            park_run_factor=park_run_factor,
        ))

    output = build_output(game_blocks, today)
    write_picks_json(output, dry_run=dry_run)

    if not dry_run:
        archive_picks(history, game_blocks, today.isoformat())
        save_history(history)

    log.info(
        "=== Done: %d games (%d picks from %d candidates) ===",
        len(game_blocks),
        output["pick_count"],
        total_candidates,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print JSON to stdout instead of writing file")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
