"""Backfill FanGraphs stats into existing player_cache.pkl files.

Uses FanGraphs JSON API directly (no pybaseball needed, no crosswalk required —
xMLBAMID field maps directly to MLBAM player IDs already in the cache).

Populates stats that were previously empty due to FanGraphs 403 on legacy endpoint:
  Pitchers: swstr_pct, zone_pct, f_strike_pct, siera, gb_pct, xfip, k_minus_bb_pct, hr9
  Batters:  wrc_plus, woba, contact_pct, o_swing_pct_fg

Usage:
    python -m pipeline.backfill_fg_stats           # all seasons 2022-2025
    python -m pipeline.backfill_fg_stats 2024       # single season
    python -m pipeline.backfill_fg_stats 2022 2023  # specific seasons
"""
from __future__ import annotations

import logging
import pickle
import sys
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

ROOT    = Path(__file__).parent.parent
FG_URL  = "https://www.fangraphs.com/api/leaders/major-league/data"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://www.fangraphs.com/",
}
TIMEOUT = 30

# FanGraphs column → cache key mapping (values are already in 0-1 decimal form)
PITCHER_MAP: dict[str, str] = {
    "SwStr%":    "swstr_pct",
    "Zone%":     "zone_pct",
    "F-Strike%": "f_strike_pct",
    "SIERA":     "siera",
    "GB%":       "gb_pct",
    "xFIP":      "xfip",
    "K-BB%":     "k_minus_bb_pct",
    "HR/9":      "hr9",
}

BATTER_MAP: dict[str, str] = {
    "wRC+":      "wrc_plus",
    "wOBA":      "woba",
    "Contact%":  "contact_pct",
    "O-Swing%":  "o_swing_pct_fg",
}


def _fetch_fg(season: int, stats: str, pageitems: int = 500) -> dict[int, dict]:
    """Fetch FanGraphs API and return {mlbam_id: {cache_key: value}}."""
    qual = "1" if stats == "pit" else "50"
    params = {
        "age": "", "pos": "all", "stats": stats, "lg": "all",
        "qual": qual, "season": str(season), "season1": str(season),
        "startdate": "", "enddate": "", "month": "0",
        "hand": "", "team": "0", "pageitems": str(pageitems), "pagenum": "1",
        "ind": "0", "rost": "0", "players": "", "type": "8", "postseason": "",
    }
    try:
        resp = requests.get(FG_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        rows = resp.json().get("data", [])
    except Exception as exc:
        log.warning("FanGraphs %s fetch failed for %d: %s", stats, season, exc)
        return {}

    col_map = PITCHER_MAP if stats == "pit" else BATTER_MAP
    result: dict[int, dict] = {}
    for row in rows:
        mlbam = row.get("xMLBAMID")
        if not mlbam:
            continue
        try:
            mlbam = int(mlbam)
        except (TypeError, ValueError):
            continue
        stats_dict: dict = {}
        for fg_col, cache_key in col_map.items():
            v = row.get(fg_col)
            if v is not None:
                try:
                    stats_dict[cache_key] = float(v)
                except (TypeError, ValueError):
                    pass
        if stats_dict:
            result[mlbam] = stats_dict

    log.info("FanGraphs %s %d: %d rows fetched", stats, season, len(rows))
    return result


def backfill_season(season: int) -> None:
    cache_path = ROOT / "data" / "seasons" / str(season) / "player_cache.pkl"
    if not cache_path.exists():
        log.error("Cache not found: %s — run pipeline.historical first", cache_path)
        return

    with open(cache_path, "rb") as f:
        cache: dict[int, dict] = pickle.load(f)
    log.info("[%d] Loaded cache: %d players", season, len(cache))

    # Pre-backfill coverage check
    pre = {k: sum(1 for p in cache.values() if p.get(k) is not None)
           for k in list(PITCHER_MAP.values()) + list(BATTER_MAP.values())}
    log.info("[%d] Pre-backfill coverage: swstr_pct=%d, zone_pct=%d, siera=%d, wrc_plus=%d",
             season, pre["swstr_pct"], pre["zone_pct"], pre["siera"], pre["wrc_plus"])

    # Fetch FanGraphs data
    log.info("[%d] Fetching FanGraphs pitcher stats...", season)
    pitch_data = _fetch_fg(season, "pit")
    time.sleep(1)  # polite delay
    log.info("[%d] Fetching FanGraphs batter stats...", season)
    bat_data = _fetch_fg(season, "bat")

    if not pitch_data and not bat_data:
        log.warning("[%d] No FG data returned — skipping", season)
        return

    # Merge into existing cache (only fill in missing values)
    updated = 0
    for pid, entry in cache.items():
        merged_any = False
        for data_dict in (pitch_data.get(pid, {}), bat_data.get(pid, {})):
            for cache_key, value in data_dict.items():
                if entry.get(cache_key) is None:  # don't overwrite Savant values
                    entry[cache_key] = value
                    merged_any = True
        if merged_any:
            updated += 1

    # Save
    with open(cache_path, "wb") as f:
        pickle.dump(cache, f)

    post = {k: sum(1 for p in cache.values() if p.get(k) is not None)
            for k in list(PITCHER_MAP.values()) + list(BATTER_MAP.values())}
    log.info("[%d] Updated %d player entries", season, updated)
    log.info("[%d] Post-backfill: swstr_pct=%d, zone_pct=%d, siera=%d, gb_pct=%d, wrc_plus=%d",
             season, post["swstr_pct"], post["zone_pct"], post["siera"], post["gb_pct"], post["wrc_plus"])
    log.info("[%d] Saved: %s", season, cache_path)


if __name__ == "__main__":
    args = sys.argv[1:]
    seasons = [int(a) for a in args] if args else [2022, 2023, 2024, 2025]
    for season in seasons:
        log.info("=== Backfilling FG stats for season %d ===", season)
        try:
            backfill_season(season)
        except Exception as exc:
            log.exception("[%d] Backfill failed: %s", season, exc)
