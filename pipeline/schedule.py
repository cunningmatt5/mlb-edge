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

    # Batch-fetch pitcher handedness (pitchHand.code not included in probablePitcher hydration)
    all_sp_ids = list({g[k] for g in games for k in ("home_sp_id", "away_sp_id") if g.get(k)})
    hand_map = _fetch_pitcher_handedness(all_sp_ids)
    for g in games:
        if not g.get("home_sp_throws"):
            g["home_sp_throws"] = hand_map.get(g["home_sp_id"])
        if not g.get("away_sp_throws"):
            g["away_sp_throws"] = hand_map.get(g["away_sp_id"])

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


def _fetch_pitcher_handedness(sp_ids: list[int]) -> dict[int, str]:
    """Batch-fetch pitchHand.code (L/R) for a list of pitcher MLBAM IDs.

    The /schedule probablePitcher hydration does not include pitchHand — this
    separate /people call is required to activate platoon split scoring.
    """
    if not sp_ids:
        return {}
    url = f"{MLB_API}/people"
    params = {"personIds": ",".join(str(i) for i in sp_ids),
              "fields": "people,id,pitchHand,code"}
    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return {p["id"]: p.get("pitchHand", {}).get("code")
                for p in resp.json().get("people", []) if "id" in p}
    except Exception as exc:
        log.debug("Pitcher handedness fetch failed: %s", exc)
        return {}


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


def fetch_recent_lineup_ids(tbd_games: list[dict], days: int = 14) -> dict[str, list[int]]:
    """Return {team_name: [player_id, ...]} using each team's last 10 completed game lineups.

    Called when some games have TBD lineups so the model can use a proxy instead of
    the 0.5 neutral default. Skips teams with fewer than 3 games found (too little data).
    """
    today = date.today()
    start = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    end   = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    # Collect unique (team_id, team_name) pairs for teams with missing lineups
    teams: dict[int, str] = {}
    for g in tbd_games:
        if not g.get("home_lineup"):
            tid = g.get("homeTeamId")
            if tid:
                teams[tid] = g.get("homeTeam", "")
        if not g.get("away_lineup"):
            tid = g.get("awayTeamId")
            if tid:
                teams[tid] = g.get("awayTeam", "")

    result: dict[str, list[int]] = {}
    session = requests.Session()

    for team_id, team_name in teams.items():
        try:
            resp = session.get(
                f"{MLB_API}/schedule",
                params={
                    "sportId":   1,
                    "teamId":    team_id,
                    "startDate": start,
                    "endDate":   end,
                    "hydrate":   "lineups",
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.debug("Proxy lineup fetch failed for %s: %s", team_name, exc)
            continue

        # Gather all Final games, sort most-recent first, take up to 10
        all_games: list[tuple[str, dict]] = []
        for day in data.get("dates", []):
            for raw in day.get("games", []):
                if raw.get("status", {}).get("abstractGameState") == "Final":
                    all_games.append((day.get("date", ""), raw))
        all_games.sort(key=lambda x: x[0], reverse=True)

        # Track each player's batting positions across recent games so the proxy
        # lineup preserves realistic order rather than arbitrary set iteration.
        player_positions: dict[int, list[int]] = {}
        games_counted = 0
        for _, raw in all_games[:10]:
            lineups  = raw.get("lineups", {})
            home_tid = raw.get("teams", {}).get("home", {}).get("team", {}).get("id")
            players  = lineups.get("homePlayers" if home_tid == team_id else "awayPlayers", [])
            ids      = [p["id"] for p in players if "id" in p]
            if ids:
                for slot, pid in enumerate(ids, 1):
                    player_positions.setdefault(pid, []).append(slot)
                games_counted += 1

        if games_counted < 3:
            log.debug("Proxy lineup: %s — only %d games found, skipping", team_name, games_counted)
            continue

        # Keep only players on the current 26-man active roster — this filters out
        # both IL players and optioned/DFA'd players in a single reliable call.
        active_roster_ids: set[int] = set()
        try:
            ar_resp = session.get(
                f"{MLB_API}/teams/{team_id}/roster",
                params={"rosterType": "active", "season": date.today().year},
                timeout=10,
            )
            ar_resp.raise_for_status()
            for entry in ar_resp.json().get("roster", []):
                pid = entry.get("person", {}).get("id")
                if pid:
                    active_roster_ids.add(pid)
        except Exception as exc:
            log.debug("Active roster fetch failed for %s — using full proxy pool: %s", team_name, exc)

        if active_roster_ids:
            before = set(player_positions)
            player_positions = {pid: slots for pid, slots in player_positions.items()
                                if pid in active_roster_ids}
            removed = len(before) - len(player_positions)
            if removed:
                log.info("Proxy lineup: %s — filtered %d non-active player(s), %d remain",
                         team_name, removed, len(player_positions))

        # Sort by average batting order so analytics get realistic slot assignments.
        ordered = sorted(player_positions, key=lambda pid: sum(player_positions[pid]) / len(player_positions[pid]))
        result[team_name] = ordered
        log.info("Proxy lineup: %s — %d players from last %d games",
                 team_name, len(ordered), games_counted)

    return result


