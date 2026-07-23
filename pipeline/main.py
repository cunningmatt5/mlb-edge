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
    signal_to_model_prob,
)
from pipeline.predictor import build_game
from pipeline.schedule import fetch_schedule, fetch_recent_lineup_ids
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

    # For games with TBD lineups, fetch each team's last 10 completed lineups as a proxy
    tbd_games = [g for g in games if not g.get("home_lineup") or not g.get("away_lineup")]
    if tbd_games:
        log.info("Fetching proxy lineups for %d games with TBD lineups...", len(tbd_games))
        proxy_map = fetch_recent_lineup_ids(tbd_games)
        for g in games:
            if not g.get("home_lineup"):
                g["home_lineup_proxy"] = proxy_map.get(g.get("homeTeam", ""), [])
            if not g.get("away_lineup"):
                g["away_lineup_proxy"] = proxy_map.get(g.get("awayTeam", ""), [])

    # Stamp lineup_status on each game dict so prop picks can be tagged downstream
    for g in games:
        _is_proxy = bool(g.get("home_lineup_proxy") or g.get("away_lineup_proxy"))
        _has_lineup = bool(g.get("home_lineup") and g.get("away_lineup"))
        g["lineup_status"] = "official" if (_has_lineup and not _is_proxy) else ("proxy" if _is_proxy else "tbd")

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

    # Build a pre-game odds cache so live/final games always show the closing line
    # (not in-game Pinnacle odds that reflect the current score).
    # Primary source: previous games.json when game was in "preview" status.
    # Fallback: opening_lines.json (has total/ML but no over/under prices).
    pre_game_odds: dict[int, dict] = {}
    try:
        _old_path = OUTPUT_DIR / "games.json"
        if _old_path.exists():
            _old_data = json.loads(_old_path.read_text(encoding="utf-8"))
            if _old_data.get("date") == today.isoformat():
                for _og in _old_data.get("games", []):
                    _pk = _og.get("gamePk")
                    if _pk and _og.get("odds") and _og.get("game_status") == "preview":
                        pre_game_odds[_pk] = _og["odds"]
    except Exception as _exc:
        log.debug("Could not load pre-game odds from existing games.json: %s", _exc)

    # Fallback: opening_lines.json for games not yet captured as preview-state odds
    _today_str = today.isoformat()
    for _pk_str, _ol in opening_lines.items():
        if _ol.get("date") != _today_str:
            continue
        try:
            _pk = int(_pk_str)
        except (ValueError, TypeError):
            continue
        if _pk not in pre_game_odds and _ol.get("home_ml") is not None:
            pre_game_odds[_pk] = {
                "home_ml": _ol["home_ml"],
                "away_ml": _ol["away_ml"],
                "total":   _ol["total"],
            }

    log.info("Pre-game odds cache: %d game(s) locked for live/final display", len(pre_game_odds))

    comps_db = load_comps_db()
    if comps_db:
        log.info("Comps database: %d historical games loaded", len(comps_db))

    game_objects: list[dict] = []
    for game in games:
        home = game.get("homeTeam", "")
        away = game.get("awayTeam", "")
        log.info("Building: %s @ %s", away, home)

        pk = game.get("gamePk")
        status = game.get("game_status", "preview")

        # Lock odds to pre-game closing line once a game starts — never show live in-game lines
        using_locked_odds = status in ("live", "final") and pk in pre_game_odds
        if using_locked_odds:
            odds = pre_game_odds[pk]
            log.debug("Using locked pre-game odds for %s @ %s (status=%s)", away, home, status)
        else:
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

        # Line movement: only compute for preview games.
        # For live/final games the current API line is in-game; don't overwrite locked odds.
        if not using_locked_odds and opening_lines and game_lines:
            movement = compute_line_movement(game, game_lines, opening_lines)
            if movement:
                game["line_movement"] = movement
                game_obj.setdefault("odds", {})["line_movement"] = movement

        # Attach validated edge conditions (multi-season backtest findings)
        try:
            from pipeline.analytics.edge_detector import detect_edges
            _odds             = game_obj.get("odds") or {}
            _closing_total    = _odds.get("total")
            _predicted_total  = (game_obj.get("prediction") or {}).get("predicted_total")
            game_obj["edge_conditions"] = detect_edges(
                _closing_total, _predicted_total, _odds.get("under_price"))
        except Exception as exc:
            log.warning("Edge detection failed for %s @ %s (non-fatal): %s",
                        away, home, exc, exc_info=True)
            game_obj["edge_conditions"] = []

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
