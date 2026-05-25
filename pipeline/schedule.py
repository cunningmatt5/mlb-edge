"""Fetch today's MLB schedule and probable starters from the MLB Stats API."""

from __future__ import annotations

import logging
from datetime import date

import requests

MLB_API = "https://statsapi.mlb.com/api/v1"
TIMEOUT = 20

log = logging.getLogger(__name__)


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

    log.info("Schedule: %d games with probable starters for %s", len(games), game_date)
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
        "away_sp_id":      away_sp["id"],
        "away_sp_name":    away_sp.get("fullName", ""),
        "home_lineup":     _extract_lineup(home),
        "away_lineup":     _extract_lineup(away),
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


def _extract_lineup(team_data: dict) -> list[int]:
    """Extract posted batting order MLBAM IDs, or return empty list."""
    batters = team_data.get("battingOrder", [])
    return [int(b["id"]) for b in batters if "id" in b]
