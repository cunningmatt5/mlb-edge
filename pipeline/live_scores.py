"""Fast live score patcher — updates score/inning fields in docs/games.json.

Run on a tight schedule (every 5 minutes) during game hours.
Reads existing games.json, fetches current linescores from MLB Stats API,
and patches only score-related fields without rebuilding predictions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

MLB_API   = "https://statsapi.mlb.com/api/v1"
GAMES_PATH = Path(__file__).parent.parent / "docs" / "games.json"
TIMEOUT   = 15

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


def patch_live_scores() -> int:
    """Patch current linescore data into docs/games.json. Returns number of games changed."""
    if not GAMES_PATH.exists():
        log.warning("games.json not found at %s", GAMES_PATH)
        return 0

    data  = json.loads(GAMES_PATH.read_text(encoding="utf-8"))
    games = data.get("games", [])

    if not games:
        log.info("No games in games.json — nothing to update")
        return 0

    # Skip games already marked final — they won't change
    pks_to_check = [
        g["gamePk"] for g in games
        if g.get("gamePk") and g.get("game_status") != "final"
    ]
    if not pks_to_check:
        log.info("All games already final — skipping")
        return 0

    log.info("Fetching linescores for %d active game(s)...", len(pks_to_check))
    updates = fetch_linescores(pks_to_check)

    changed = 0
    for game in games:
        pk = game.get("gamePk")
        if pk not in updates:
            continue
        patch = updates[pk]

        if all(game.get(k) == v for k, v in patch.items()):
            continue

        game.update(patch)
        changed += 1

    if changed:
        data["generated_at"] = datetime.now(timezone.utc).isoformat()
        GAMES_PATH.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        log.info("Patched %d game(s), wrote games.json", changed)
    else:
        log.info("No score changes detected")

    return changed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    n = patch_live_scores()
    print(f"Updated {n} game(s)")
