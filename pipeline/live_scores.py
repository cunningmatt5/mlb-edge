"""Fast live score patcher — updates score/inning fields in docs/games.json.

Run on a tight schedule (every 5 minutes) during game hours.
Reads existing games.json, fetches current linescores from MLB Stats API,
and patches only score-related fields without rebuilding predictions.

Also refreshes pitcher identity and lineup status for Preview-state games so
that scratches and newly-posted lineups are visible within 5 minutes rather
than waiting for the next full pipeline run.  When a pitcher change or new
lineup is detected, writes needs_rebuild=true to $GITHUB_OUTPUT so the
workflow can immediately dispatch morning_picks.yml for a full stat rebuild.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

MLB_API    = "https://statsapi.mlb.com/api/v1"
GAMES_PATH = Path(__file__).parent.parent / "docs" / "games.json"
TIMEOUT    = 15

log = logging.getLogger(__name__)


def fetch_linescores(game_pks: list[int]) -> dict[int, dict]:
    """Return {gamePk: score_patch} for the given game IDs via a single MLB API call."""
    if not game_pks:
        return {}

    try:
        resp = requests.get(
            f"{MLB_API}/schedule",
            params={
                "sportId": 1,
                "gamePks": ",".join(str(pk) for pk in game_pks),
                "hydrate": "linescore",
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("MLB API linescore fetch failed: %s", exc)
        return {}

    result: dict[int, dict] = {}
    for day in data.get("dates", []):
        for game in day.get("games", []):
            pk = game.get("gamePk")
            if not pk:
                continue

            status       = game.get("status", {})
            abstract     = status.get("abstractGameState", "Preview")
            linescore    = game.get("linescore", {})
            ls_teams     = linescore.get("teams", {})
            curr_inning  = linescore.get("currentInning")
            inning_ord   = linescore.get("currentInningOrdinal", "")
            is_top       = linescore.get("isTopInning")

            if curr_inning:
                half = "Top" if is_top else "Bot"
                inning_state = f"{half} {inning_ord}".strip()
            else:
                inning_state = None

            result[pk] = {
                "game_status":    abstract.lower(),
                "home_score":     ls_teams.get("home", {}).get("runs"),
                "away_score":     ls_teams.get("away", {}).get("runs"),
                "current_inning": curr_inning,
                "inning_state":   inning_state,
                "outs":           linescore.get("outs"),
            }

    return result


def refresh_preview_fields(game_pks: list[int]) -> dict[int, dict]:
    """Fetch updated probablePitcher and lineup IDs for Preview-state games.

    Returns {gamePk: {"home_sp_id", "home_sp_name", "away_sp_id", "away_sp_name",
                       "has_home_lineup", "has_away_lineup"}}.
    """
    if not game_pks:
        return {}

    try:
        resp = requests.get(
            f"{MLB_API}/schedule",
            params={
                "sportId": 1,
                "gamePks": ",".join(str(pk) for pk in game_pks),
                "hydrate": "probablePitcher,lineups",
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("MLB API preview refresh failed: %s", exc)
        return {}

    result: dict[int, dict] = {}
    for day in data.get("dates", []):
        for game in day.get("games", []):
            pk = game.get("gamePk")
            if not pk:
                continue
            home    = game.get("teams", {}).get("home", {})
            away    = game.get("teams", {}).get("away", {})
            home_sp = home.get("probablePitcher")
            away_sp = away.get("probablePitcher")
            lineups = game.get("lineups", {})
            result[pk] = {
                "home_sp_id":       home_sp["id"]              if home_sp else None,
                "home_sp_name":     home_sp.get("fullName", "") if home_sp else None,
                "away_sp_id":       away_sp["id"]              if away_sp else None,
                "away_sp_name":     away_sp.get("fullName", "") if away_sp else None,
                "has_home_lineup":  bool(lineups.get("homePlayers")),
                "has_away_lineup":  bool(lineups.get("awayPlayers")),
            }

    return result


def patch_live_scores() -> tuple[int, bool]:
    """Patch current linescore data and preview fields into docs/games.json.

    Returns (n_changed, needs_full_rebuild).
    needs_full_rebuild is True when a pitcher change or new lineup was detected,
    signalling the workflow to dispatch the full pipeline.
    """
    if not GAMES_PATH.exists():
        log.warning("games.json not found at %s", GAMES_PATH)
        return 0, False

    data  = json.loads(GAMES_PATH.read_text(encoding="utf-8"))
    games = data.get("games", [])

    if not games:
        log.info("No games in games.json — nothing to update")
        return 0, False

    # ── Score / inning patch (live + preview games) ───────────────────────────
    pks_to_check = [
        g["gamePk"] for g in games
        if g.get("gamePk") and g.get("game_status") != "final"
    ]
    if not pks_to_check:
        log.info("All games already final — skipping")
        return 0, False

    log.info("Fetching linescores for %d active game(s)...", len(pks_to_check))
    score_updates = fetch_linescores(pks_to_check)

    changed = 0
    for game in games:
        pk = game.get("gamePk")
        if pk not in score_updates:
            continue
        patch = score_updates[pk]
        if all(game.get(k) == v for k, v in patch.items()):
            continue
        game.update(patch)
        changed += 1

    # ── Preview-field refresh (pitcher + lineup status) ───────────────────────
    preview_pks = [
        g["gamePk"] for g in games
        if g.get("gamePk") and g.get("game_status") == "preview"
    ]

    needs_rebuild = False
    if preview_pks:
        log.info("Refreshing pitcher/lineup for %d preview game(s)...", len(preview_pks))
        preview_updates = refresh_preview_fields(preview_pks)

        for game in games:
            pk = game.get("gamePk")
            upd = preview_updates.get(pk)
            if not upd:
                continue

            # Pitcher change detection
            for side in ("home", "away"):
                new_id   = upd.get(f"{side}_sp_id")
                new_name = upd.get(f"{side}_sp_name")
                if new_id and new_id != game.get(f"{side}_sp_id"):
                    old_id = game.get(f"{side}_sp_id")
                    log.info(
                        "SP change detected for gamePk %s (%s): %s → %s (%s)",
                        pk, side, old_id, new_id, new_name,
                    )
                    game[f"{side}_sp_id"] = new_id
                    # Update name inside the sp stats object if present
                    sp_obj = game.get(f"{side}_sp")
                    if isinstance(sp_obj, dict):
                        sp_obj["name"]     = new_name or sp_obj.get("name", "")
                        sp_obj["mlbam_id"] = new_id
                    game["sp_changed"] = True
                    needs_rebuild = True
                    changed += 1

            # Lineup status: mark official when IDs first appear
            if (upd["has_home_lineup"] or upd["has_away_lineup"]) and game.get("lineup_status") == "tbd":
                log.info("Lineup posted for gamePk %s — marking official", pk)
                game["lineup_status"] = "official"
                needs_rebuild = True
                changed += 1

    if changed:
        data["generated_at"] = datetime.now(timezone.utc).isoformat()
        GAMES_PATH.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        log.info("Patched %d field(s), wrote games.json (needs_rebuild=%s)", changed, needs_rebuild)
    else:
        log.info("No changes detected")

    return changed, needs_rebuild


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    n, needs_rebuild = patch_live_scores()
    print(f"Updated {n} game(s)")
    if needs_rebuild:
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a") as f:
                f.write("needs_rebuild=true\n")
        else:
            print("needs_rebuild=true")
