"""Fetch today's MLB schedule and probable starters from the MLB Stats API."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import requests

MLB_API = "https://statsapi.mlb.com/api/v1"
TIMEOUT = 20

log = logging.getLogger(__name__)


def get_team_rest_days(game_date: date) -> dict[str, int]:
    """Return {team_name: rest_days} for all teams playing on game_date.

    rest_days = days since last game. 0 = back-to-back, 1 = normal, 2+ = extra rest.
    Teams not found in the recent schedule default to 1 (normal rest).
    Looks back up to 5 days to catch teams returning from off-days.
    """
    rest_map: dict[str, date] = {}
    for delta in range(1, 6):
        check = game_date - timedelta(days=delta)
        url = f"{MLB_API}/schedule"
        params = {
            "sportId": 1,
            "date": check.strftime("%m/%d/%Y"),
        }
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            for day in data.get("dates", []):
                for raw in day.get("games", []):
                    status = raw.get("status", {})
                    if status.get("abstractGameState") not in ("Final", "Live"):
                        continue
                    home = raw.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
                    away = raw.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
                    for team in (home, away):
                        if team and team not in rest_map:
                            rest_map[team] = check
        except Exception:
            pass

    result: dict[str, int] = {}
    for team, last_played in rest_map.items():
        result[team] = (game_date - last_played).days - 1
    return result


def fetch_schedule(game_date: date) -> list[dict]:
    """Return a list of game dicts for games with both probable pitchers posted.

    Each dict has keys:
        gamePk, gameTime (ISO-8601 UTC), homeTeam, awayTeam, venue,
        home_sp_id, home_sp_name, away_sp_id, away_sp_name,
        home_lineup (list of MLBAM IDs, may be empty),
        away_lineup (list of MLBAM IDs, may be empty)
    """
    url = f"{MLB_API}/schedule"
    params = {
        "sportId": 1,
        "date": game_date.strftime("%m/%d/%Y"),
        "hydrate": "probablePitcher,lineups,venue,officials,linescore",
    }
    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("MLB schedule fetch failed: %s", exc)
        return []

    dates = data.get("dates", [])
    if not dates:
        return []

    games = []
    for raw in dates[0].get("games", []):
        parsed = _parse_game(raw)
        if parsed:
            games.append(parsed)

    # Attach rest days for each team
    try:
        rest_map = get_team_rest_days(game_date)
        for g in games:
            g["home_rest_days"] = rest_map.get(g["homeTeam"], 1)
            g["away_rest_days"] = rest_map.get(g["awayTeam"], 1)
    except Exception as exc:
        log.debug("Rest days lookup failed: %s", exc)
        for g in games:
            g.setdefault("home_rest_days", 1)
            g.setdefault("away_rest_days", 1)

    total_batters = sum(len(g.get("home_lineup", [])) + len(g.get("away_lineup", [])) for g in games)
    log.info("Schedule: %d games with probable starters for %s (%d batter IDs collected)",
             len(games), game_date, total_batters)
    return games


_SKIP_STATES = {"D", "C"}  # D = Postponed, C = Cancelled


def _parse_game(raw: dict) -> dict | None:
    """Parse a single game entry. Returns None if game is postponed/cancelled or either starter is missing."""
    status = raw.get("status", {})
    if status.get("codedGameState") in _SKIP_STATES:
        log.debug("Skipping game %s: %s", raw.get("gamePk"), status.get("detailedState"))
        return None

    home = raw.get("teams", {}).get("home", {})
    away = raw.get("teams", {}).get("away", {})

    home_sp = home.get("probablePitcher")
    away_sp = away.get("probablePitcher")
    if not home_sp or not away_sp:
        return None

    abstract_state = status.get("abstractGameState", "Preview")  # "Preview" | "Live" | "Final"
    linescore   = raw.get("linescore", {})
    ls_teams    = linescore.get("teams", {})
    curr_inning = linescore.get("currentInning")
    inning_ord  = linescore.get("currentInningOrdinal", "")
    is_top      = linescore.get("isTopInning")

    if curr_inning:
        inning_half = "Top" if is_top else "Bot"
        inning_state = f"{inning_half} {inning_ord}".strip()
    else:
        inning_state = None

    # Lineups are returned at the game level under raw["lineups"]["homePlayers"] /
    # ["awayPlayers"] — NOT nested inside teams.home.  The team-level "lineup" /
    # "battingOrder" keys do not exist in the schedule API response.
    game_lineups = raw.get("lineups", {})
    home_lineup_ids = [p["id"] for p in game_lineups.get("homePlayers", []) if "id" in p]
    away_lineup_ids = [p["id"] for p in game_lineups.get("awayPlayers", []) if "id" in p]

    return {
        "gamePk":          raw["gamePk"],
        "gameTime":        raw.get("gameDate", ""),
        "homeTeam":        home.get("team", {}).get("name", "Unknown"),
        "awayTeam":        away.get("team", {}).get("name", "Unknown"),
        "homeTeamId":      home.get("team", {}).get("id"),
        "awayTeamId":      away.get("team", {}).get("id"),
        "venue":           raw.get("venue", {}).get("name", "Unknown"),
        "home_sp_id":      home_sp["id"],
        "home_sp_name":    home_sp.get("fullName", ""),
        "home_sp_throws":  home_sp.get("pitchHand", {}).get("code"),
        "away_sp_id":      away_sp["id"],
        "away_sp_name":    away_sp.get("fullName", ""),
        "away_sp_throws":  away_sp.get("pitchHand", {}).get("code"),
        "home_lineup":     home_lineup_ids,
        "away_lineup":     away_lineup_ids,
        "umpire":          _extract_hp_umpire(raw),
        "game_status":     abstract_state.lower(),
        "home_score":      ls_teams.get("home", {}).get("runs"),
        "away_score":      ls_teams.get("away", {}).get("runs"),
        "current_inning":  curr_inning,
        "inning_state":    inning_state,
        "outs":            linescore.get("outs"),
    }


def _extract_hp_umpire(raw: dict) -> str:
    """Return the home plate umpire's full name, or empty string if not posted."""
    for official in raw.get("officials", []):
        if official.get("officialType") == "Home Plate":
            return official.get("official", {}).get("fullName", "")
    return ""


