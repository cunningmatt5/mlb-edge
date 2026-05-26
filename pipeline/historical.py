"""Pull historical MLB season data for backtesting signal quality.

Usage:
    python -m pipeline.historical --seasons 2023,2024

Saves to data/seasons/{year}/:
  games.parquet            — schedule + scores + probable SP IDs
  player_game_logs.parquet — per-game pitcher/batter stats (from boxscores)
  game_lineups.parquet     — batting-order slots per game
  player_cache.pkl         — full-season FanGraphs + Savant stat cache
"""
from __future__ import annotations

import argparse
import logging
import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

MLB_API = "https://statsapi.mlb.com/api/v1"
TIMEOUT = 20
MAX_WORKERS = 20

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def pull_season_data(
    season: int,
    output_dir: Path,
    with_odds: bool = False,
    odds_source: str = "sbro",
    odds_api_key: Optional[str] = None,
) -> None:
    """Pull and persist all data for one season. Idempotent — skips existing files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    games_path   = output_dir / "games.parquet"
    logs_path    = output_dir / "player_game_logs.parquet"
    lineups_path = output_dir / "game_lineups.parquet"
    cache_path   = output_dir / "player_cache.pkl"

    if games_path.exists():
        log.info("games.parquet exists — loading")
        games_df = pd.read_parquet(games_path)
    else:
        games_df = pull_season_schedule(season)
        games_df.to_parquet(games_path, index=False)
        log.info("Saved games.parquet: %d rows", len(games_df))

    game_pks = games_df["game_pk"].tolist()

    if logs_path.exists() and lineups_path.exists():
        log.info("player_game_logs.parquet + game_lineups.parquet exist — skipping")
        logs_df    = pd.read_parquet(logs_path)
        lineups_df = pd.read_parquet(lineups_path)
    else:
        log.info("Fetching boxscores for %d games (season %d)...", len(game_pks), season)
        logs_df, lineups_df = pull_season_boxscores(game_pks)
        logs_df.to_parquet(logs_path, index=False)
        lineups_df.to_parquet(lineups_path, index=False)
        log.info("Saved player_game_logs.parquet and game_lineups.parquet")

    if cache_path.exists():
        log.info("player_cache.pkl exists — skipping")
    else:
        all_ids = [int(p) for p in logs_df["player_id"].dropna().unique().tolist()]
        log.info("Building player cache for %d unique players (season %d)...", len(all_ids), season)
        cache = build_historical_player_cache(season, all_ids)
        with open(cache_path, "wb") as f:
            pickle.dump(cache, f)
        log.info("Saved player_cache.pkl: %d entries", len(cache))

    if with_odds:
        from pipeline.odds_historical import build_season_closing_lines
        build_season_closing_lines(
            season, output_dir.parent, source=odds_source, api_key=odds_api_key
        )


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

def pull_season_schedule(season: int) -> pd.DataFrame:
    """Pull all final regular-season games. Returns DataFrame."""
    rows = []
    months = [
        (f"{season}-03-01", f"{season}-03-31"),
        (f"{season}-04-01", f"{season}-04-30"),
        (f"{season}-05-01", f"{season}-05-31"),
        (f"{season}-06-01", f"{season}-06-30"),
        (f"{season}-07-01", f"{season}-07-31"),
        (f"{season}-08-01", f"{season}-08-31"),
        (f"{season}-09-01", f"{season}-09-30"),
        (f"{season}-10-01", f"{season}-10-31"),
    ]
    for start, end in months:
        url = (
            f"{MLB_API}/schedule"
            f"?sportId=1&startDate={start}&endDate={end}"
            f"&gameTypes=R&hydrate=probablePitcher,venue"
        )
        try:
            r = requests.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            for date_block in r.json().get("dates", []):
                for g in date_block.get("games", []):
                    if g.get("status", {}).get("codedGameState") != "F":
                        continue
                    teams = g.get("teams", {})
                    home  = teams.get("home", {})
                    away  = teams.get("away", {})
                    rows.append({
                        "game_pk":    g["gamePk"],
                        "date":       date_block["date"],
                        "home_team":  home.get("team", {}).get("name", ""),
                        "away_team":  away.get("team", {}).get("name", ""),
                        "venue":      g.get("venue", {}).get("name", ""),
                        "home_score": home.get("score"),
                        "away_score": away.get("score"),
                        # Probable SP IDs — may differ from actual starters
                        "sched_home_sp_id": home.get("probablePitcher", {}).get("id"),
                        "sched_away_sp_id": away.get("probablePitcher", {}).get("id"),
                    })
        except Exception as exc:
            log.warning("Schedule fetch failed for %s: %s", start, exc)

    df = pd.DataFrame(rows)
    log.info("Season %d schedule: %d final games", season, len(df))
    return df


# ---------------------------------------------------------------------------
# Boxscores
# ---------------------------------------------------------------------------

def pull_season_boxscores(game_pks: list[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch boxscores for all games. Returns (player_logs_df, lineups_df)."""
    log_rows    = []
    lineup_rows = []

    def fetch_one(game_pk: int) -> tuple[int, Optional[dict]]:
        try:
            r = requests.get(f"{MLB_API}/game/{game_pk}/boxscore", timeout=TIMEOUT)
            r.raise_for_status()
            return game_pk, r.json()
        except Exception as exc:
            log.debug("Boxscore fetch failed for %d: %s", game_pk, exc)
            return game_pk, None

    total = len(game_pks)
    done  = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_one, pk): pk for pk in game_pks}
        for fut in as_completed(futures):
            game_pk, data = fut.result()
            done += 1
            if done % 250 == 0:
                log.info("  Boxscores: %d/%d", done, total)
            if not data:
                continue

            teams = data.get("teams", {})
            for side in ("home", "away"):
                players = teams.get(side, {}).get("players", {})
                for _, pdata in players.items():
                    pid = pdata.get("person", {}).get("id")
                    if not pid:
                        continue
                    pos           = pdata.get("position", {}).get("abbreviation", "")
                    batting_order = pdata.get("battingOrder")
                    game_started  = pdata.get("gameStarted", False)
                    stats         = pdata.get("stats", {})
                    batting       = stats.get("batting", {})
                    pitching      = stats.get("pitching", {})

                    if not batting and not pitching:
                        continue

                    log_rows.append({
                        "game_pk":      game_pk,
                        "player_id":    pid,
                        "side":         side,
                        "is_pitcher":   pos == "P",
                        "game_started": game_started,
                        "H":    batting.get("hits",        0),
                        "HR":   batting.get("homeRuns",    0),
                        "TB":   batting.get("totalBases",  0),
                        "B_BB": batting.get("baseOnBalls", 0),
                        "AB":   batting.get("atBats",      0),
                        "K":    pitching.get("strikeOuts",  0),
                        "P_BB": pitching.get("baseOnBalls", 0),
                        "IP":   _parse_ip(pitching.get("inningsPitched", "")),
                        "ER":   pitching.get("earnedRuns",  0),
                    })

                    if batting_order:
                        lineup_rows.append({
                            "game_pk":      game_pk,
                            "player_id":    pid,
                            "side":         side,
                            "batting_order": int(str(batting_order)) // 100,
                        })

    logs_df    = pd.DataFrame(log_rows)
    lineups_df = pd.DataFrame(lineup_rows)
    log.info(
        "Boxscores done: %d player-game logs, %d lineup slots",
        len(logs_df), len(lineups_df),
    )
    return logs_df, lineups_df


# ---------------------------------------------------------------------------
# Player cache (adapts statcast.py for historical seasons)
# ---------------------------------------------------------------------------

def build_historical_player_cache(season: int, player_ids: list[int]) -> dict:
    """Full-season stat cache for backtesting.

    Reuses statcast.py merge helpers with historical season endpoints.
    Note: uses full-season stats, introducing within-season lookahead bias.
    Acceptable for signal calibration — not a live-trading system.
    """
    from pipeline.statcast import (
        _build_crosswalk,
        _fetch_fg_batting,
        _fetch_fg_pitching,
        _fetch_savant_batter_batted_ball_stats,
        _fetch_savant_batter_expected_stats,
        _fetch_savant_pitcher_stats,
        _merge_fg_batting,
        _merge_fg_pitching,
        _merge_savant_batter_batted_ball,
        _merge_savant_batter_expected,
        _merge_savant_pitcher,
    )

    fg_pitch    = _fetch_fg_pitching(season)
    fg_bat      = _fetch_fg_batting(season)
    sav_pitch   = _fetch_savant_pitcher_stats(season)
    sav_bat_exp = _fetch_savant_batter_expected_stats(season)
    sav_bat_bb  = _fetch_savant_batter_batted_ball_stats(season)
    crosswalk   = _build_crosswalk(player_ids)

    cache: dict[int, dict] = {}
    for pid in player_ids:
        fg_id = crosswalk.get(pid)
        entry: dict = {"mlbam_id": pid}
        _merge_fg_pitching(entry, fg_pitch, fg_id)
        _merge_fg_batting(entry, fg_bat, fg_id)
        _merge_savant_pitcher(entry, sav_pitch, pid)
        _merge_savant_batter_expected(entry, sav_bat_exp, pid)
        _merge_savant_batter_batted_ball(entry, sav_bat_bb, pid)
        cache[pid] = entry

    log.info("Historical player cache: %d entries for %d", len(cache), season)
    return cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ip(ip_str) -> float:
    """Convert MLB API innings-pitched string '5.1' (= 5⅓ IP) to decimal."""
    if not ip_str:
        return 0.0
    try:
        parts = str(ip_str).split(".")
        full  = int(parts[0]) if parts[0] else 0
        frac  = int(parts[1]) / 3.0 if len(parts) > 1 and parts[1] else 0.0
        return full + frac
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    import os

    parser = argparse.ArgumentParser(description="Pull historical MLB season data")
    parser.add_argument("--seasons", default="2023,2024", help="Comma-separated years, e.g. 2023,2024")
    parser.add_argument("--with-odds", action="store_true",
                        help="Also build closing_lines.parquet after game data pull")
    parser.add_argument("--odds-source", choices=["sbro", "odds_api"], default="sbro",
                        help="Odds source: sbro (free, 2019-2021) or odds_api (2022+, requires key)")
    parser.add_argument("--api-key", default=os.environ.get("ODDS_API_KEY", ""),
                        help="The Odds API key (or set ODDS_API_KEY env var)")
    args = parser.parse_args()

    base = Path(__file__).parent.parent / "data" / "seasons"
    for s in args.seasons.split(","):
        season = int(s.strip())
        log.info("=== Pulling season %d ===", season)
        pull_season_data(
            season, base / str(season),
            with_odds=args.with_odds,
            odds_source=args.odds_source,
            odds_api_key=args.api_key or None,
        )
