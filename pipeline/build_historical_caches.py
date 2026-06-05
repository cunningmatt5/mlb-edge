"""Build historical player_cache.pkl files for prior seasons.

Usage:
    python pipeline/build_historical_caches.py 2021 2022 2023

For each year, fetches all finished games (SP IDs) and all-team 40-man rosters
(batter IDs) from the MLB Stats API, then builds a player cache using season-level
stats from Savant + FanGraphs + MLB API. Saves to data/seasons/{year}/player_cache.pkl.

Rolling/date-window Statcast fetches are skipped for historical seasons — only
season-level aggregate stats are populated. This is appropriate for using the cache
as a prior-year reference in the backtest (season N cache → score season N+1 games).

Skips years where a cache already exists.
"""

from __future__ import annotations

import logging
import pickle
import sys
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
log = logging.getLogger(__name__)

MLB_API = "https://statsapi.mlb.com/api/v1"
TIMEOUT = 30

_MLB_TEAM_IDS: dict[str, int] = {
    "Arizona Diamondbacks":  109,
    "Atlanta Braves":        144,
    "Baltimore Orioles":     110,
    "Boston Red Sox":        111,
    "Chicago Cubs":          112,
    "Chicago White Sox":     145,
    "Cincinnati Reds":       113,
    "Cleveland Guardians":   114,
    "Colorado Rockies":      115,
    "Detroit Tigers":        116,
    "Houston Astros":        117,
    "Kansas City Royals":    118,
    "Los Angeles Angels":    108,
    "Los Angeles Dodgers":   119,
    "Miami Marlins":         146,
    "Milwaukee Brewers":     158,
    "Minnesota Twins":       142,
    "New York Mets":         121,
    "New York Yankees":      147,
    "Oakland Athletics":     133,
    "Philadelphia Phillies": 143,
    "Pittsburgh Pirates":    134,
    "San Diego Padres":      135,
    "San Francisco Giants":  137,
    "Seattle Mariners":      136,
    "St. Louis Cardinals":   138,
    "Tampa Bay Rays":        139,
    "Texas Rangers":         140,
    "Toronto Blue Jays":     141,
    "Washington Nationals":  120,
}


def _fetch_season_sp_ids(year: int) -> list[int]:
    """Fetch MLBAM IDs of all starting pitchers who started a regular-season game in year."""
    session = requests.Session()
    start = f"03/01/{year}"
    end   = f"10/31/{year}"
    params = {
        "sportId":   1,
        "startDate": start,
        "endDate":   end,
        "hydrate":   "probablePitcher",
        "gameType":  "R",
    }
    sp_ids: set[int] = set()
    try:
        resp = session.get(f"{MLB_API}/schedule", params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("Season schedule fetch failed for %d: %s", year, exc)
        return []

    for day in data.get("dates", []):
        for raw in day.get("games", []):
            if raw.get("status", {}).get("abstractGameState") != "Final":
                continue
            home = raw.get("teams", {}).get("home", {})
            away = raw.get("teams", {}).get("away", {})
            for side in (home, away):
                sp = side.get("probablePitcher")
                if sp and sp.get("id"):
                    sp_ids.add(sp["id"])

    log.info("Season %d: %d unique starting pitchers found", year, len(sp_ids))
    return list(sp_ids)


def _fetch_season_batter_ids(year: int) -> list[int]:
    """Fetch MLBAM IDs of all position players on 40-man rosters across all 30 teams for year."""
    session = requests.Session()
    batter_ids: set[int] = set()

    for full_name, team_id in _MLB_TEAM_IDS.items():
        try:
            resp = session.get(
                f"{MLB_API}/teams/{team_id}/roster",
                params={"rosterType": "40Man", "season": year},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.debug("Roster fetch failed for %s (%d): %s", full_name, team_id, exc)
            continue

        for entry in data.get("roster", []):
            pid = entry.get("person", {}).get("id")
            pos_code = entry.get("position", {}).get("code", "")
            # Position code "1" = pitcher; all others are position players (batters)
            if pid and pos_code != "1":
                batter_ids.add(pid)

    log.info("Season %d: %d unique position players from 40-man rosters", year, len(batter_ids))
    return list(batter_ids)


def build_historical_cache(year: int, force: bool = False) -> None:
    """Build and save player_cache.pkl for a historical season."""
    from pipeline.statcast import build_player_cache

    cache_path = Path(f"data/seasons/{year}/player_cache.pkl")
    if cache_path.exists() and not force:
        log.info("Cache already exists: %s — skipping (use --force to overwrite)", cache_path)
        return

    log.info("=== Building historical player cache for %d ===", year)

    sp_ids    = _fetch_season_sp_ids(year)
    bat_ids   = _fetch_season_batter_ids(year)

    if not sp_ids:
        log.error("No starters found for %d — aborting", year)
        return

    # Construct minimal game dicts so build_player_cache() can collect the right IDs.
    # We distribute batters across fake games (1 game per 9 batters) to satisfy
    # _collect_batter_ids() which reads home_lineup / away_lineup lists.
    fake_games: list[dict] = []
    bat_list = list(bat_ids)

    # Each SP gets a fake game entry; pack batters into lineups across those games
    chunk = 9  # typical lineup size
    bat_chunks = [bat_list[i:i + chunk] for i in range(0, len(bat_list), chunk)]

    for i, sp_id in enumerate(sp_ids):
        home_lineup = bat_chunks[i * 2 % len(bat_chunks)]       if bat_chunks else []
        away_lineup = bat_chunks[(i * 2 + 1) % len(bat_chunks)] if bat_chunks else []
        fake_games.append({
            "home_sp_id":  sp_id,
            "away_sp_id":  sp_id,
            "home_lineup": home_lineup,
            "away_lineup": away_lineup,
        })

    log.info("Building cache: %d pitchers, %d batters, %d synthetic games",
             len(sp_ids), len(bat_ids), len(fake_games))

    cache = build_player_cache(fake_games, season=year)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("wb") as f:
        pickle.dump(cache, f)

    n_pitchers = sum(1 for v in cache.values() if isinstance(v, dict) and v.get("role") == "pitcher")
    n_batters  = sum(1 for v in cache.values() if isinstance(v, dict) and v.get("role") == "batter")
    log.info("Saved: %s  (%d pitchers, %d batters, %d total entries)",
             cache_path, n_pitchers, n_batters, len(cache))

    _report_coverage(cache, year)


def _report_coverage(cache: dict, year: int) -> None:
    pitchers = [v for v in cache.values() if isinstance(v, dict) and v.get("role") == "pitcher"]
    if not pitchers:
        return
    for field in ("xera", "xfip", "barrel_pct_against", "k_pct", "bb_pct"):
        n = sum(1 for p in pitchers if p.get(field) is not None)
        log.info("  Pitcher coverage %s: %d/%d (%.0f%%)",
                 field, n, len(pitchers), 100 * n / len(pitchers))
    batters = [v for v in cache.values() if isinstance(v, dict) and v.get("role") == "batter"]
    if batters:
        for field in ("xwoba", "wrc_plus", "barrel_pct"):
            n = sum(1 for b in batters if b.get(field) is not None)
            log.info("  Batter coverage %s: %d/%d (%.0f%%)",
                     field, n, len(batters), 100 * n / len(batters))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv

    if not args:
        print("Usage: python pipeline/build_historical_caches.py <year> [<year> ...] [--force]")
        print("Example: python pipeline/build_historical_caches.py 2021 2022 2023")
        sys.exit(1)

    for arg in args:
        try:
            build_historical_cache(int(arg), force=force)
        except Exception as exc:
            log.error("Failed to build cache for %s: %s", arg, exc)
