"""Fetch and aggregate Statcast + FanGraphs data for today's players.

Strategy:
  1. FanGraphs season stats via pybaseball (xFIP, SIERA, K%, Stuff+, wRC+, etc.)
  2. Baseball Savant expected-stats CSV (xwOBA, xBA for pitchers and batters)
  3. Baseball Savant statcast leaderboard CSV (barrel%, hard-hit%, avg EV, launch angle)
  4. Per-player 21-day rolling Statcast (whiff%, chase rate) aggregated from pitch data
  5. MLBAM → FanGraphs ID crosswalk via pybaseball playerid_reverse_lookup
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
    fg_bat   = _fetch_fg_batting(season)
    sav_pitch         = _fetch_savant_pitcher_stats(season)          # expected stats (xwOBA etc.)
    sav_pitch_lead    = _fetch_savant_pitcher_leaderboard(season)    # leaderboard (ERA, xERA, K%)
    sav_bat_expected  = _fetch_savant_batter_expected_stats(season)
    sav_bat_batted    = _fetch_savant_batter_batted_ball_stats(season)

    # --- ID crosswalk: MLBAM → FanGraphs (only needed when FG data is available) ---
    crosswalk = _build_crosswalk(all_ids)

    # --- Build per-player cache ---
    cache: dict[int, dict] = {}

    for mlbam_id in sp_ids:
        fg_id = crosswalk.get(mlbam_id)
        entry: dict = {"mlbam_id": mlbam_id, "role": "pitcher"}
        _merge_fg_pitching(entry, fg_pitch, fg_id)               # FG (may be empty due to 403)
        _merge_savant_pitcher(entry, sav_pitch, mlbam_id)        # xwOBA against
        _merge_savant_pitcher_leaderboard(entry, sav_pitch_lead, mlbam_id)  # ERA, xERA→xfip, K%, BB%
        cache[mlbam_id] = entry
        log.info("Cache SP %d %s: era=%s xfip=%s k_pct=%s",
                 mlbam_id, entry.get("name", "?"), entry.get("era"), entry.get("xfip"), entry.get("k_pct"))

    for mlbam_id in batter_ids:
        fg_id = crosswalk.get(mlbam_id)
        entry = cache.get(mlbam_id, {"mlbam_id": mlbam_id, "role": "batter"})
        _merge_fg_batting(entry, fg_bat, fg_id)                  # FG (may be empty due to 403)
        _merge_savant_batter_expected(entry, sav_bat_expected, mlbam_id)  # xwOBA, wOBA, K%
        _merge_savant_batter_batted_ball(entry, sav_bat_batted, mlbam_id)
        cache[mlbam_id] = entry

    # --- 21-day rolling (whiff%, chase rate for SPs) ---
    start = (today - timedelta(days=ROLLING_DAYS)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    for mlbam_id in sp_ids:
        _merge_rolling_pitcher(cache[mlbam_id], mlbam_id, start, end)

    # --- Batter recent game logs (last 5 games: H, HR, K per game) ---
    for mlbam_id in batter_ids:
        _merge_batter_game_log(cache[mlbam_id], mlbam_id, season)

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
        # Plate discipline — used by walk_props
        "zone_pct": g("Zone%"),
        "f_strike_pct": g("F-Strike%"),
        "swstr_pct": g("SwStr%"),
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


def _fetch_savant_batter_expected_stats(season: int) -> pd.DataFrame:
    """xBA, xwOBA, xSLG per batter from the expected-statistics leaderboard."""
    url = (
        f"{SAVANT_BASE}/expected_statistics"
        f"?type=batter&year={season}&position=&team=&min=q&csv=true"
    )
    return _fetch_savant_csv(url, "batter-expected")


def _fetch_savant_batter_batted_ball_stats(season: int) -> pd.DataFrame:
    """Barrel%, hard-hit%, avg exit velocity, avg launch angle from statcast leaderboard.

    Columns confirmed from live endpoint:
      player_id, brl_percent (whole %, e.g. 8.5), ev95percent (whole %, e.g. 42.1),
      avg_hit_speed (mph), avg_hit_angle (degrees)
    Also contains woba, k_percent, bb_percent used as FanGraphs fallback.
    """
    url = (
        f"{SAVANT_BASE}/leaderboard/statcast"
        f"?type=batter&year={season}&position=&team=&min=q&csv=true"
    )
    return _fetch_savant_csv(url, "batter-batted-ball")


def _fetch_savant_pitcher_leaderboard(season: int) -> pd.DataFrame:
    """Savant statcast pitcher leaderboard: ERA, xERA, K%, BB%, whiff%, stuff_plus.

    Used as a FanGraphs fallback when FG returns 403. Key columns:
      player_id, p_era, xera, k_percent, bb_percent, whiff_percent, stuff_plus
    All percentage columns are whole numbers (25.0 = 25%) — divide by 100 on merge.
    """
    url = (
        f"{SAVANT_BASE}/leaderboard/statcast"
        f"?type=pitcher&year={season}&position=&team=&min=q&csv=true"
    )
    return _fetch_savant_csv(url, "pitcher-leaderboard")


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
        "xba_against":   g("est_ba"),
        "xslg_against":  g("est_slg"),
    })

    # FanGraphs fallback: pull ERA and xERA from Savant expected stats CSV
    # (only set if not already populated by FanGraphs)
    for savant_col, entry_key, divisor in [
        ("era",   "era",  1.0),
        ("xera",  "xfip", 1.0),   # xERA ≈ xFIP (contact-quality adjusted ERA)
    ]:
        val = g(savant_col)
        if val is not None and not entry.get(entry_key):
            entry[entry_key] = round(val / divisor, 4)


def _merge_savant_pitcher_leaderboard(entry: dict, df: pd.DataFrame, mlbam_id: int) -> None:
    """Merge exit-velocity contact-quality stats from Savant statcast pitcher leaderboard.

    The /leaderboard/statcast?type=pitcher endpoint has barrel%, hard-hit%, avg EV, avg LA.
    It does NOT have ERA/K%/BB% — those come from the expected_statistics endpoint.
    """
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

    brl = g("brl_percent")
    hh  = g("ev95percent")
    ev  = g("avg_hit_speed")
    la  = g("avg_hit_angle")
    if brl is not None and not entry.get("barrel_pct_against"):
        entry["barrel_pct_against"] = brl / 100.0
    if hh is not None and not entry.get("hard_hit_pct_against"):
        entry["hard_hit_pct_against"] = hh / 100.0
    if ev is not None and not entry.get("avg_ev_against"):
        entry["avg_ev_against"] = ev
    if la is not None and not entry.get("avg_la_against"):
        entry["avg_la_against"] = la


def _merge_savant_batter_expected(entry: dict, df: pd.DataFrame, mlbam_id: int) -> None:
    """Merge xBA, xwOBA, xSLG from the expected-statistics CSV."""
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
        "xba":   g("est_ba"),
        "xwoba": g("est_woba"),
        "xslg":  g("est_slg"),
    })

    # FanGraphs fallback: wOBA is also in the Savant expected-stats CSV
    woba = g("woba")
    if woba is not None and not entry.get("woba"):
        entry["woba"] = round(woba, 4)


def _merge_savant_batter_batted_ball(entry: dict, df: pd.DataFrame, mlbam_id: int) -> None:
    """Merge barrel%, hard-hit%, avg EV, avg launch angle from the statcast leaderboard CSV.

    Savant stores brl_percent and ev95percent as whole numbers (e.g., 8.5 means 8.5%),
    so we divide by 100 to match the decimal fractions used by normalize().
    """
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

    brl = g("brl_percent")
    hh  = g("ev95percent")
    ev  = g("avg_hit_speed")
    la  = g("avg_hit_angle")

    if brl is not None:
        entry["barrel_pct"] = brl / 100.0
    if hh is not None:
        entry["hard_hit_pct"] = hh / 100.0
    if ev is not None:
        entry["avg_ev"] = ev
    if la is not None:
        entry["avg_launch_angle"] = la

    # The batter statcast leaderboard has exit-velocity stats only (no wOBA/K%).


# ---------------------------------------------------------------------------
# Batter recent game log (MLB Stats API — last N games H/HR/K)
# ---------------------------------------------------------------------------

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"


def _merge_batter_game_log(entry: dict, mlbam_id: int, season: int, n: int = 5) -> None:
    """Fetch last n game log entries for a batter and store per-game H/HR/K arrays."""
    try:
        url = (
            f"{MLB_STATS_BASE}/people/{mlbam_id}/stats"
            f"?stats=gameLog&group=hitting&season={season}&limit=20"
        )
        resp = requests.get(url, headers=_HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        splits = []
        for stats_block in data.get("stats", []):
            splits = stats_block.get("splits", [])
            if splits:
                break
        if not splits:
            return
        # API returns most-recent first; take the last n and reverse to oldest-first
        recent = splits[:n][::-1]
        entry["recent_h_games"]  = [int(s["stat"].get("hits", 0))      for s in recent]
        entry["recent_hr_games"] = [int(s["stat"].get("homeRuns", 0))   for s in recent]
        entry["recent_k_games"]  = [int(s["stat"].get("strikeOuts", 0)) for s in recent]
    except Exception as exc:
        log.debug("Batter game log fetch failed for %d: %s", mlbam_id, exc)


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
        recent = _aggregate_pitcher_recent_starts(df, n=3)
        entry.update(recent)
        log.debug("Rolling Statcast merged for pitcher %d: %s", mlbam_id, agg)
    except Exception as exc:
        log.warning("Rolling pitcher Statcast failed for %d: %s", mlbam_id, exc)


def _aggregate_pitcher_recent_starts(df: pd.DataFrame, n: int = 3) -> dict:
    """Compute K% and BB% from the last n starts in the rolling pitch dataset."""
    if "game_pk" not in df.columns or "events" not in df.columns:
        return {}
    terminal = df[df["events"].notna() & (df["events"] != "")].copy()
    if terminal.empty:
        return {}

    starts = []
    for game_pk, gdf in terminal.groupby("game_pk"):
        total_pa = len(gdf)
        if total_pa < 8:
            continue
        ks  = int(gdf["events"].isin(["strikeout", "strikeout_double_play"]).sum())
        bbs = int(gdf["events"].isin(["walk", "intent_walk"]).sum())
        date_col = "game_date" if "game_date" in gdf.columns else None
        game_date = str(gdf[date_col].max()) if date_col else ""
        starts.append({"game_date": game_date, "pa": total_pa, "k": ks, "bb": bbs})

    if not starts:
        return {}

    starts.sort(key=lambda s: s["game_date"])
    recent = starts[-n:]
    total_pa = sum(s["pa"] for s in recent)
    if total_pa == 0:
        return {}

    return {
        "recent_k_pct":    sum(s["k"]  for s in recent) / total_pa,
        "recent_bb_pct":   sum(s["bb"] for s in recent) / total_pa,
        "recent_starts_n": len(recent),
        "recent_k_games":  [s["k"] for s in recent],
    }


def _aggregate_pitcher_pitch_data(df: pd.DataFrame) -> dict:
    """Aggregate per-pitch Statcast rows to whiff%, chase rate, and window K%."""
    swings = df["description"].isin([
        "swinging_strike", "swinging_strike_blocked",
        "foul", "foul_tip",
        "hit_into_play", "hit_into_play_no_out", "hit_into_play_score",
    ])
    whiffs = df["description"].isin(["swinging_strike", "swinging_strike_blocked"])
    out_zone = df["zone"].isin([11, 12, 13, 14])

    total_swings = swings.sum()
    total_out_zone = out_zone.sum()

    result = {
        "whiff_pct": float(whiffs.sum() / total_swings) if total_swings > 0 else None,
        "o_swing_pct": float((swings & out_zone).sum() / total_out_zone) if total_out_zone > 0 else None,
    }

    # Compute window K% from terminal PA events (21-day season proxy for FanGraphs fallback)
    if "events" in df.columns:
        terminal = df[df["events"].notna() & (df["events"] != "")]
        if len(terminal) >= 20:
            ks = int(terminal["events"].isin(["strikeout", "strikeout_double_play"]).sum())
            result["k_pct"] = round(ks / len(terminal), 4)

    return result


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
