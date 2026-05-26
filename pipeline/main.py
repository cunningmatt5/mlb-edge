"""MLB Edge — daily game intelligence pipeline.

Usage:
    python -m pipeline.main              # normal run, writes docs/games.json
    python -m pipeline.main --dry-run    # print JSON to stdout, no file write
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path

from pipeline.comps import load_comps_db
from pipeline.odds import fetch_mlb_game_lines, get_game_event
from pipeline.predictor import build_game
from pipeline.schedule import fetch_schedule
from pipeline.standings import fetch_team_records
from pipeline.statcast import build_player_cache
from pipeline.weather import fetch_game_weather

OUTPUT_DIR   = Path(__file__).parent.parent / "docs"
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

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
        log.info("No games with probable starters today — writing empty games.json")
        _write_output({"generated_at": datetime.now(timezone.utc).isoformat(),
                       "date": today.isoformat(), "game_count": 0, "games": []},
                      dry_run)
        return

    team_records = fetch_team_records(today.year)

    log.info("Fetching weather for %d games...", len(games))
    for game in games:
        game["weather"] = fetch_game_weather(game.get("venue", ""), game.get("gameTime", ""))

    log.info("Building player cache for %d games...", len(games))
    cache = build_player_cache(games)

    game_lines = fetch_mlb_game_lines(ODDS_API_KEY, today.isoformat())
    if game_lines:
        log.info("Odds: %d games from Pinnacle", len(game_lines))

    comps_db = load_comps_db()
    if comps_db:
        log.info("Comps database: %d historical games loaded", len(comps_db))

    game_objects: list[dict] = []
    for game in games:
        home = game.get("homeTeam", "")
        away = game.get("awayTeam", "")
        log.info("Building: %s @ %s", away, home)

        odds = get_game_event(game, game_lines) if game_lines else None
        game_obj = build_game(
            game=game,
            cache=cache,
            comps_db=comps_db,
            weather=game.get("weather"),
            odds=odds,
        )
        for side, id_key in [("away", "awayTeamId"), ("home", "homeTeamId")]:
            tid = game.get(id_key)
            if tid and tid in team_records:
                game_obj[f"{side}_record"] = team_records[tid]

        game_objects.append(game_obj)

        pred = game_obj["prediction"]
        log.info(
            "  %s @ %s: %s wins %.0f%% · %.1f-%.1f (total %.1f)",
            away, home,
            home if pred["home_win_pct"] >= 0.5 else away,
            max(pred["home_win_pct"], pred["away_win_pct"]) * 100,
            pred["predicted_away_runs"],
            pred["predicted_home_runs"],
            pred["predicted_total"],
        )

    game_objects.sort(key=lambda g: g.get("game_time_utc") or "")

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date":         today.isoformat(),
        "game_count":   len(game_objects),
        "games":        game_objects,
    }
    _write_output(output, dry_run)

    # Append today to history for later resolution
    if not dry_run:
        try:
            from pipeline.history import append_today, load_history, save_history
            history = load_history()
            history = append_today(history, game_objects, today.isoformat())
            save_history(history)
        except Exception as exc:
            log.warning("History update failed: %s", exc)

    # Generate player + game props picks (non-fatal — never breaks game predictions)
    if not dry_run:
        try:
            from pipeline.analytics.hr_props       import score_hr_props
            from pipeline.analytics.hit_props       import score_hit_props
            from pipeline.analytics.strikeout_props import score_strikeout_props
            from pipeline.analytics.total_bases     import score_total_bases
            from pipeline.analytics.team_totals     import score_team_totals
            from pipeline.analytics.game_totals     import score_game_total
            from pipeline.analytics.moneyline_f5    import score_moneyline_f5

            pick_games: list[dict] = []
            for game in games:  # original schedule dicts have SP IDs + lineup ID lists
                all_picks: list[dict] = []
                all_picks += score_hr_props(game, cache)
                all_picks += score_hit_props(game, cache)
                all_picks += score_strikeout_props(game, cache)
                all_picks += score_total_bases(game, cache)
                all_picks += score_team_totals(game, cache)
                all_picks += score_game_total(game, cache)
                all_picks += score_moneyline_f5(game, cache)
                all_picks.sort(key=lambda p: p["signal"], reverse=True)
                if all_picks:
                    pick_games.append({
                        "game_time": game.get("gameTime", ""),
                        "away_team": game.get("awayTeam", ""),
                        "home_team": game.get("homeTeam", ""),
                        "venue":     game.get("venue", ""),
                        "picks":     all_picks,
                    })

            picks_output = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "games": pick_games,
            }
            picks_path = OUTPUT_DIR / "picks.json"
            picks_path.write_text(json.dumps(picks_output, separators=(",", ":")), encoding="utf-8")
            log.info("Props: %d game cards, %d picks → picks.json",
                     len(pick_games), sum(len(g["picks"]) for g in pick_games))
        except Exception as exc:
            log.warning("Props analytics failed (non-fatal): %s", exc, exc_info=True)

    log.info("=== Done: %d games ===", len(game_objects))


def _write_output(data: dict, dry_run: bool) -> None:
    if dry_run:
        print(json.dumps(data, indent=2))
    else:
        out_path = OUTPUT_DIR / "games.json"
        out_path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        log.info("Wrote %s (%d bytes)", out_path, out_path.stat().st_size)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Print JSON to stdout instead of writing file")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
