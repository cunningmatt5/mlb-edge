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
from pipeline.odds import (
    fetch_mlb_game_lines, fetch_mlb_props, get_game_event,
    match_game_line, match_prop_line, compute_ev,
    load_opening_lines, save_opening_lines, record_opening_lines, compute_line_movement,
)
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

    # Opening line tracking — record first-seen lines and compute movement
    opening_lines: dict = {}
    if game_lines:
        opening_lines = load_opening_lines()
        if record_opening_lines(games, game_lines, opening_lines):
            save_opening_lines(opening_lines)
            log.info("Opening lines: recorded first-seen lines for today")

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

        # Attach line movement if significant movement detected vs. opening
        if opening_lines and game_lines:
            movement = compute_line_movement(game, game_lines, opening_lines)
            if movement:
                game_obj.setdefault("odds", {})["line_movement"] = movement

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

    # Re-attach any games that left preview state (probable pitcher cleared by MLB API)
    # and were therefore filtered out of fetch_schedule(). Preserve this morning's
    # prediction and refresh only the score/inning fields.
    try:
        old_path = OUTPUT_DIR / "games.json"
        if old_path.exists():
            old_data = json.loads(old_path.read_text(encoding="utf-8"))
            if old_data.get("date") == today.isoformat():
                old_by_pk = {g["gamePk"]: g for g in old_data.get("games", [])}
                new_pks   = {g["gamePk"] for g in game_objects}
                dropped   = [pk for pk in old_by_pk if pk not in new_pks]
                if dropped:
                    log.info("Re-attaching %d live/final game(s) from existing games.json", len(dropped))
                    from pipeline.live_scores import fetch_linescores
                    score_updates = fetch_linescores(dropped)
                    for pk in dropped:
                        preserved = dict(old_by_pk[pk])
                        if pk in score_updates:
                            preserved.update(score_updates[pk])
                        game_objects.append(preserved)
                    game_objects.sort(key=lambda g: g.get("game_time_utc") or "")
    except Exception as exc:
        log.warning("Live game merge failed (non-fatal): %s", exc)

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
            from pipeline.analytics.total_bases     import score_total_bases_props as score_total_bases
            from pipeline.analytics.team_totals     import score_team_totals
            from pipeline.analytics.game_totals     import score_game_total
            from pipeline.analytics.moneyline_f5    import score_moneyline_f5

            _GAME_LEVEL_TYPES = {"TOTAL", "TEAM_TOTAL", "ML_F5"}

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

                # Attach EV calculations where Pinnacle lines are available
                if game_lines:
                    game_event = get_game_event(game, game_lines)
                    prop_lines = fetch_mlb_props(ODDS_API_KEY, game_event.get("event_id")) if game_event else {}
                    for pick in all_picks:
                        try:
                            if pick["bet_type"] in _GAME_LEVEL_TYPES:
                                matched = match_game_line(pick, game, game_lines)
                            else:
                                matched = match_prop_line(pick, prop_lines)
                            if matched:
                                ev = compute_ev(pick, matched)
                                pick["odds"] = {
                                    "has_line":     True,
                                    "line":         ev.get("line"),
                                    "over_price":   ev["over_price"],
                                    "under_price":  ev["under_price"],
                                    "edge_pct":     ev["edge_pct"],
                                    "model_prob":   ev["model_prob"],
                                    "implied_prob": ev["implied_prob"],
                                }
                        except Exception:
                            pass

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
