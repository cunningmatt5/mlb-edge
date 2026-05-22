"""Fetch and aggregate Statcast + FanGraphs data for today's players.

Strategy:
  1. FanGraphs season stats via pybaseball (xFIP, SIERA, K%, Stuff+, wRC+, etc.)
  2. Baseball Savant expected-stats CSV (xwOBA, xBA, barrel%, hard-hit%)
  3. Per-player 21-day rolling Statcast (whiff%, chase rate) aggregated from pitch data
  4. MLBAM → FanGraphs ID crosswalk via pybaseball playerid_reverse_lookup
"""

from __future__ import annotations

import io
import logging
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import requests

log = logging.getLogger(__name__)

SAVANT_BASE = "https://baseballsavant.mlb.com"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
ROLLING_DAYS = 21
TIMEOUT = 45


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_player_cache(games: list[dict]) -> dict[int, dict]:
    """Return a dict keyed by MLBAM player ID with all stats needed downstream."""
    today = date.today()
    season = today.year

    sp_ids = _collect_sp_ids(games)
    batter_ids = _collect_batter_ids(games)
    all_ids = list(set(sp_ids) | set(batter_ids))

    log.info(
        "Fetching stats for %d pitchers, %d batters (%d unique players)",
        len(sp_ids), len(batter_ids), len(all_ids),
    )

    # --- Season-level data (single bulk calls) ---
    fg_pitch = _fetch_fg_pitching(season)
    fg_bat = _fetch_fg_batting(season)
    sav_pitch = _fetch_savant_pitcher_stats(season)
    sav_bat = _fetch_savant_batter_stats(season)

    # --- ID crosswalk: MLBAM → FanGraphs ---
    crosswalk = _build_crosswalk(all_ids)

    # --- Build per-player cache ---
    cache: dict[int, dict] = {}

    for mlbam_id in sp_ids:
        fg_id = crosswalk.get(mlbam_id)
        entry: dict = {"mlbam_id": mlbam_id, "role": "pitcher"}
        _merge_fg_pitching(entry, fg_pitch, fg_id)
        _merge_savant_pitcher(entry, sav_pitch, mlbam_id)
        cache[mlbam_id] = entry

    for mlbam_id in batter_ids:
        fg_id = crosswalk.get(mlbam_id)
        entry = cache.get(mlbam_id, {"mlbam_id": mlbam_id, "role": "batter"})
        _merge_fg_batting(entry, fg_bat, fg_id)
        _merge_savant_batter(entry, sav_bat, mlbam_id)
        cache[mlbam_id] = entry

    # --- 21-day rolling (whiff%, chase rate for SPs) ---
    start = (today - timedelta(days=ROLLING_DAYS)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    for mlbam_id in sp_ids:
        _merge_rolling_pitcher(cache[mlbam_id], mlbam_id, start, end)

    log.info("Player cache built: %d entries", len(cache))
    return cache


# ---------------------------------------------------------------------------
# FanGraphs via pybaseball
# ---------------------------------------------------------------------------

def _fetch_fg_pitching(season: int) -> pd.DataFrame:
    try:
        from pybaseball import pitching_stats
        df = pitching_stats(season, qual=1)
        log.info("FanGraphs pitching: %d rows", len(df))
        return df
    except Exception as exc:
        log.warning("FanGraphs pitching fetch failed: %s", exc)
        return pd.DataFrame()


def _fetch_fg_batting(season: int) -> pd.DataFrame:
    try:
        from pybaseball import batting_stats
        df = batting_stats(season, qual=1)
        log.info("FanGraphs batting: %d rows", len(df))
        return df
    except Exception as exc:
        log.warning("FanGraphs batting fetch failed: %s", exc)
        return pd.DataFrame()


def _merge_fg_pitching(entry: dict, df: pd.DataFrame, fg_id) -> None:
    if df.empty or fg_id is None:
        return
    row = df[df["IDfg"] == fg_id]
    if row.empty:
        return
    r = row.iloc[0]

    def g(col, default=None):
        try:
            v = r.get(col)
            return float(v) if v is not None and str(v) not in ("", "nan") else default
        except Exception:
            return default

    entry.update({
        "name": r.get("Name", entry.get("name", "")),
        "xfip": g("xFIP"),
        "siera": g("SIERA"),
        "k_pct": g("K%"),
        "bb_pct": g("BB%"),
        "k_minus_bb_pct": g("K-BB%"),
        "hr9": g("HR/9"),
        "stuff_plus": g("Stuff+"),
        "era": g("ERA"),
        "ip": g("IP"),
    })


def _merge_fg_batting(entry: dict, df: pd.DataFrame, fg_id) -> None:
    if df.empty or fg_id is None:
        return
    row = df[df["IDfg"] == fg_id]
    if row.empty:
        return
    r = row.iloc[0]

    def g(col, default=None):
        try:
            v = r.get(col)
            return float(v) if v is not None and str(v) not in ("", "nan") else default
        except Exception:
            return default

    entry.update({
        "name": r.get("Name", entry.get("name", "")),
        "wrc_plus": g("wRC+"),
        "woba": g("wOBA"),
        "k_pct": g("K%"),
        "bb_pct": g("BB%"),
        "contact_pct": g("Contact%"),
        "o_swing_pct_fg": g("O-Swing%"),
    })


# ---------------------------------------------------------------------------
# Baseball Savant CSV endpoints
# ---------------------------------------------------------------------------

def _fetch_savant_csv(url: str, label: str) -> pd.DataFrame:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        log.info("Savant %s: %d rows", label, len(df))
        return df
    except Exception as exc:
        log.warning("Savant %s fetch failed: %s", label, exc)
        return pd.DataFrame()


def _fetch_savant_pitcher_stats(season: int) -> pd.DataFrame:
    url = (
        f"{SAVANT_BASE}/expected_statistics"
        f"?type=pitcher&year={season}&position=&team=&min=q&csv=true"
    )
    return _fetch_savant_csv(url, "pitcher-expected")


def _fetch_savant_batter_stats(season: int) -> pd.DataFrame:
    url = (
        f"{SAVANT_BASE}/expected_statistics"
        f"?type=batter&year={season}&position=&team=&min=q&csv=true"
    )
    return _fetch_savant_csv(url, "batter-expected")


def _merge_savant_pitcher(entry: dict, df: pd.DataFrame, mlbam_id: int) -> None:
    if df.empty:
        return
    id_col = _find_id_col(df)
    if not id_col:
        return
    row = df[df[id_col] == mlbam_id]
    if row.empty:
        return
    r = row.iloc[0]

    def g(col, default=None):
        try:
            v = r.get(col)
            return float(v) if v is not None and str(v) not in ("", "nan") else default
        except Exception:
            return default

    if not entry.get("name") and r.get("last_name, first_name"):
        entry["name"] = str(r["last_name, first_name"])

    entry.update({
        "xwoba_against": g("est_woba"),
        "xba_against": g("est_ba"),
        "xslg_against": g("est_slg"),
    })


def _merge_savant_batter(entry: dict, df: pd.DataFrame, mlbam_id: int) -> None:
    if df.empty:
        return
    id_col = _find_id_col(df)
    if not id_col:
        return
    row = df[df[id_col] == mlbam_id]
    if row.empty:
        return
    r = row.iloc[0]

    def g(col, default=None):
        try:
            v = r.get(col)
            return float(v) if v is not None and str(v) not in ("", "nan") else default
        except Exception:
            return default

    if not entry.get("name") and r.get("last_name, first_name"):
        entry["name"] = str(r["last_name, first_name"])

    entry.update({
        "xba": g("est_ba"),
        "xwoba": g("est_woba"),
        "xslg": g("est_slg"),
    })

    # Barrel% and hard-hit% come from a separate Savant leaderboard; merge if present
    for col, key in [("barrel_batted_rate", "barrel_pct"), ("hard_hit_percent", "hard_hit_pct"),
                     ("avg_exit_velocity", "avg_ev"), ("avg_launch_angle", "avg_launch_angle")]:
        val = g(col)
        if val is not None:
            # Savant returns barrel% as a whole number (e.g., 8.5 = 8.5%)
            entry[key] = val / 100.0 if col in ("barrel_batted_rate", "hard_hit_percent") else val


# ---------------------------------------------------------------------------
# 21-day rolling Statcast (pitcher whiff% and chase rate)
# ---------------------------------------------------------------------------

def _merge_rolling_pitcher(entry: dict, mlbam_id: int, start: str, end: str) -> None:
    try:
        from pybaseball import statcast_pitcher
        df = statcast_pitcher(start_dt=start, end_dt=end, player_id=mlbam_id)
        if df is None or df.empty:
            return
        agg = _aggregate_pitcher_pitch_data(df)
        entry.update(agg)
        log.debug("Rolling Statcast merged for pitcher %d: %s", mlbam_id, agg)
    except Exception as exc:
        log.warning("Rolling pitcher Statcast failed for %d: %s", mlbam_id, exc)


def _aggregate_pitcher_pitch_data(df: pd.DataFrame) -> dict:
    """Aggregate per-pitch Statcast rows to whiff% and chase rate."""
    swings = df["description"].isin([
        "swinging_strike", "swinging_strike_blocked",
        "foul", "foul_tip",
        "hit_into_play", "hit_into_play_no_out", "hit_into_play_score",
    ])
    whiffs = df["description"].isin(["swinging_strike", "swinging_strike_blocked"])
    out_zone = df["zone"].isin([11, 12, 13, 14])

    total_swings = swings.sum()
    total_out_zone = out_zone.sum()

    return {
        "whiff_pct": float(whiffs.sum() / total_swings) if total_swings > 0 else None,
        "o_swing_pct": float((swings & out_zone).sum() / total_out_zone) if total_out_zone > 0 else None,
    }


# ---------------------------------------------------------------------------
# ID crosswalk
# ---------------------------------------------------------------------------

def _build_crosswalk(mlbam_ids: list[int]) -> dict[int, int]:
    """Return {mlbam_id: fangraphs_id} for the given player list."""
    if not mlbam_ids:
        return {}
    try:
        from pybaseball import playerid_reverse_lookup
        df = playerid_reverse_lookup(mlbam_ids, key_type="mlbam")
        if df is None or df.empty:
            return {}
        result = {}
        for _, row in df.iterrows():
            mlbam = row.get("key_mlbam")
            fg = row.get("key_fangraphs")
            if mlbam and fg and str(fg) not in ("", "nan"):
                result[int(mlbam)] = int(fg)
        log.info("ID crosswalk: %d/%d matched", len(result), len(mlbam_ids))
        return result
    except Exception as exc:
        log.warning("ID crosswalk failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_sp_ids(games: list[dict]) -> list[int]:
    ids = set()
    for g in games:
        if g.get("home_sp_id"):
            ids.add(g["home_sp_id"])
        if g.get("away_sp_id"):
            ids.add(g["away_sp_id"])
    return list(ids)


def _collect_batter_ids(games: list[dict]) -> list[int]:
    ids = set()
    for g in games:
        ids.update(g.get("home_lineup", []))
        ids.update(g.get("away_lineup", []))
    return list(ids)


def _find_id_col(df: pd.DataFrame) -> Optional[str]:
    for candidate in ("player_id", "mlbam_id", "batter", "pitcher"):
        if candidate in df.columns:
            return candidate
    return None
