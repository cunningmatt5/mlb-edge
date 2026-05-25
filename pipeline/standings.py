"""Fetch current MLB team standings (W-L, streak, last-10) from the MLB Stats API."""
from __future__ import annotations

import logging
import requests

MLB_API = "https://statsapi.mlb.com/api/v1"
TIMEOUT = 15
log = logging.getLogger(__name__)


def fetch_team_records(season: int) -> dict[int, dict]:
    """Return {mlbam_team_id: {wins, losses, streak, l10_w, l10_l}} for all MLB teams."""
    url = f"{MLB_API}/standings"
    params = {
        "leagueId": "103,104",
        "season": season,
        "standingsTypes": "regularSeason",
    }
    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("Standings fetch failed: %s", exc)
        return {}

    out: dict[int, dict] = {}
    for division in data.get("records", []):
        for tr in division.get("teamRecords", []):
            team_id = tr.get("team", {}).get("id")
            if not team_id:
                continue

            streak_code = tr.get("streak", {}).get("streakCode", "") or None  # e.g. "W3"

            l10_w = l10_l = None
            for split in tr.get("records", {}).get("splitRecords", []):
                if split.get("type") == "lastTen":
                    l10_w = split.get("wins")
                    l10_l = split.get("losses")
                    break

            out[int(team_id)] = {
                "wins":   int(tr.get("wins", 0)),
                "losses": int(tr.get("losses", 0)),
                "streak": streak_code,
                "l10_w":  l10_w,
                "l10_l":  l10_l,
            }

    log.info("Standings: %d teams loaded", len(out))
    return out
