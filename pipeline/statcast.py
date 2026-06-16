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

SAVANT_BASE    = "https://baseballsavant.mlb.com"
MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"

# Full team name → Savant abbreviation
_TEAM_ABBREVS: dict[str, str] = {
    "Arizona Diamondbacks":  "ARI",
    "Atlanta Braves":        "ATL",
    "Baltimore Orioles":     "BAL",
    "Boston Red Sox":        "BOS",
    "Chicago Cubs":          "CHC",
    "Chicago White Sox":     "CWS",
    "Cincinnati Reds":       "CIN",
    "Cleveland Guardians":   "CLE",
    "Colorado Rockies":      "COL",
    "Detroit Tigers":        "DET",
    "Houston Astros":        "HOU",
    "Kansas City Royals":    "KC",
    "Los Angeles Angels":    "LAA",
    "Los Angeles Dodgers":   "LAD",
    "Miami Marlins":         "MIA",
    "Milwaukee Brewers":     "MIL",
    "Minnesota Twins":       "MIN",
    "New York Mets":         "NYM",
    "New York Yankees":      "NYY",
    "Oakland Athletics":     "OAK",
    "Athletics":             "OAK",   # Sacramento Athletics (2025+) — canonical for reverse map
    "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates":    "PIT",
    "San Diego Padres":      "SD",
    "San Francisco Giants":  "SF",
    "Seattle Mariners":      "SEA",
    "St. Louis Cardinals":   "STL",
    "Tampa Bay Rays":        "TB",
    "Texas Rangers":         "TEX",
    "Toronto Blue Jays":     "TOR",
    "Washington Nationals":  "WSH",
}
_ABBREV_TO_FULL: dict[str, str] = {v: k for k, v in _TEAM_ABBREVS.items()}


def team_abbrev(full_name: str) -> str:
    """Map a full MLB team name to its Savant abbreviation, e.g. 'New York Yankees' → 'NYY'."""
    return _TEAM_ABBREVS.get(full_name, "")
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
_FG_API_URL = "https://www.fangraphs.com/api/leaders/major-league/data"
_FG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://www.fangraphs.com/",
}
ROLLING_DAYS        = 21   # pitcher rolling window (days)
ROLLING_DAYS_BATTER = 30   # batter rolling window (days)
TIMEOUT = 45


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_player_cache(games: list[dict], season: int | None = None) -> dict[int, dict]:
    """Return a dict keyed by MLBAM player ID with all stats needed downstream.

    Pass season to build a historical cache (e.g., season=2022). When building
    historical caches, rolling/date-window Statcast fetches are skipped since they
    are relative to today and meaningless for prior seasons.
    """
    today = date.today()
    if season is None:
        season = today.year
    is_historical = season != today.year

    sp_ids = _collect_sp_ids(games)
    batter_ids = _collect_batter_ids(games)
    all_ids = list(set(sp_ids) | set(batter_ids))

    log.info(
        "Fetching stats for %d pitchers, %d batters (%d unique players) — season=%d%s",
        len(sp_ids), len(batter_ids), len(all_ids), season,
        " [historical — rolling fetches skipped]" if is_historical else "",
    )

    # --- Season-level data (single bulk calls) ---
    fg_pitch = _fetch_fg_pitching(season)
    fg_bat   = _fetch_fg_batting(season)
    sav_pitch         = _fetch_savant_pitcher_stats(season)          # expected stats (xwOBA etc.)
    sav_pitch_lead    = _fetch_savant_pitcher_leaderboard(season)    # leaderboard (barrel%, EV)
    sav_bat_expected  = _fetch_savant_batter_expected_stats(season)
    sav_bat_batted    = _fetch_savant_batter_batted_ball_stats(season)
    sav_bat_vs_lhp    = _fetch_savant_batter_splits(season, "vs_lhp")  # returns empty DF (Savant ignores splits param)
    sav_bat_vs_rhp    = _fetch_savant_batter_splits(season, "vs_rhp")  # returns empty DF
    mlb_platoon_splits = _fetch_mlb_platoon_splits(list(batter_ids), season)
    mlb_pitcher_stats  = _fetch_mlb_pitcher_stats(season)            # K%/BB% fallback when FG unavailable

    # --- Build per-player cache ---
    cache: dict[int, dict] = {}

    for mlbam_id in sp_ids:
        entry: dict = {"mlbam_id": mlbam_id, "role": "pitcher"}
        _merge_fg_pitching(entry, fg_pitch, mlbam_id)            # FG JSON API (direct MLBAM ID)
        _merge_savant_pitcher(entry, sav_pitch, mlbam_id)        # xwOBA against
        _merge_savant_pitcher_leaderboard(entry, sav_pitch_lead, mlbam_id)  # barrel%, EV
        # MLB Stats API K%/BB% — fills gap when FanGraphs returns 403
        ps = mlb_pitcher_stats.get(mlbam_id, {})
        if ps.get("k_pct") and not entry.get("k_pct"):
            entry["k_pct"] = ps["k_pct"]
        if ps.get("bb_pct") and not entry.get("bb_pct"):
            entry["bb_pct"] = ps["bb_pct"]
        # Throws fallback: use schedule data if Savant leaderboard didn't have p_throws
        if not entry.get("throws"):
            for g_dict in games:
                if g_dict.get("home_sp_id") == mlbam_id and g_dict.get("home_sp_throws"):
                    entry["throws"] = g_dict["home_sp_throws"]
                    break
                if g_dict.get("away_sp_id") == mlbam_id and g_dict.get("away_sp_throws"):
                    entry["throws"] = g_dict["away_sp_throws"]
                    break
        cache[mlbam_id] = entry
        log.info("Cache SP %d %s: era=%s xfip=%s k_pct=%s",
                 mlbam_id, entry.get("name", "?"), entry.get("era"), entry.get("xfip"), entry.get("k_pct"))

    for mlbam_id in batter_ids:
        entry = cache.get(mlbam_id, {"mlbam_id": mlbam_id, "role": "batter"})
        _merge_fg_batting(entry, fg_bat, mlbam_id)               # FG JSON API (direct MLBAM ID)
        _merge_savant_batter_expected(entry, sav_bat_expected, mlbam_id)  # xwOBA, wOBA, K%
        _merge_savant_batter_batted_ball(entry, sav_bat_batted, mlbam_id)
        _merge_batter_split(entry, sav_bat_vs_lhp, mlbam_id, "vs_lhp")   # no-op (empty DF)
        _merge_batter_split(entry, sav_bat_vs_rhp, mlbam_id, "vs_rhp")   # no-op (empty DF)
        # Overwrite with real MLB API platoon splits (OBP/SLG → wOBA proxy)
        splits = mlb_platoon_splits.get(mlbam_id, {})
        if splits.get("xwoba_vs_l") is not None:
            entry["xwoba_vs_l"] = splits["xwoba_vs_l"]
        if splits.get("xwoba_vs_r") is not None:
            entry["xwoba_vs_r"] = splits["xwoba_vs_r"]
        cache[mlbam_id] = entry

    n_xwoba  = sum(1 for v in cache.values() if v.get("role") == "batter" and v.get("xwoba"))
    n_barrel = sum(1 for v in cache.values() if v.get("role") == "batter" and v.get("barrel_pct"))
    n_bat    = sum(1 for v in cache.values() if v.get("role") == "batter")
    log.info("Batter data coverage: %d/%d have xwoba, %d/%d have barrel%%", n_xwoba, n_bat, n_barrel, n_bat)

    if not is_historical:
        # --- 21-day rolling (whiff%, chase rate for SPs) — skipped for historical seasons ---
        start = (today - timedelta(days=ROLLING_DAYS)).strftime("%Y-%m-%d")
        end = today.strftime("%Y-%m-%d")
        for mlbam_id in sp_ids:
            _merge_rolling_pitcher(cache[mlbam_id], mlbam_id, start, end)

        # --- Pitcher last-start deviation (season xERA vs. most recent start ERA est.) ---
        for mlbam_id in sp_ids:
            _merge_pitcher_last_start(cache[mlbam_id], mlbam_id, season)

        # --- Batter recent game logs (last 20 games: H, HR, K + rolling rates) ---
        for mlbam_id in batter_ids:
            _merge_batter_game_log(cache[mlbam_id], mlbam_id, season)

        # --- 30-day rolling Statcast for batters (contact%, hard-hit%, barrel%, K%/BB%) ---
        start_30d = (today - timedelta(days=ROLLING_DAYS_BATTER)).strftime("%Y-%m-%d")
        end_dt    = today.strftime("%Y-%m-%d")
        log.info("Fetching 30-day rolling Statcast for %d batters...", len(batter_ids))
        for mlbam_id in batter_ids:
            if mlbam_id in cache:
                _merge_rolling_batter(cache[mlbam_id], mlbam_id, start_30d, end_dt)
                _blend_batter_rolling(cache[mlbam_id])

    # --- Avg IP per start for opener/bulk detection (season-level fallback) ---
    for pid in sp_ids:
        if pid in cache:
            ip = cache[pid].get("ip") or 0
            gs = cache[pid].get("gs") or 0
            if gs > 0:
                cache[pid]["avg_ip_per_start"] = round(ip / gs, 2)

    # --- Team bullpen aggregates (stored under "bullpen:{full_team_name}" keys) ---
    bullpen_by_team = _build_team_bullpen_cache(
        season, set(sp_ids), fg_pitch_data=fg_pitch, pitch_lead_df=sav_pitch_lead)
    for full_name, bp_stats in bullpen_by_team.items():
        cache[f"bullpen:{full_name}"] = bp_stats

    log.info("Player cache built: %d player entries + %d bullpen team entries",
             len(cache) - len(bullpen_by_team), len(bullpen_by_team))
    return cache


# ---------------------------------------------------------------------------
# FanGraphs via direct JSON API (new endpoint — no pybaseball crosswalk needed)
# xMLBAMID field maps directly to MLBAM player IDs.
# ---------------------------------------------------------------------------

def _fetch_fg_json(season: int, stats: str, pageitems: int = 500) -> dict[int, dict]:
    """Fetch FanGraphs JSON API. Returns {mlbam_id: {stat_key: value}}."""
    qual = "1" if stats == "pit" else "50"
    params = {
        "age": "", "pos": "all", "stats": stats, "lg": "all",
        "qual": qual, "season": str(season), "season1": str(season),
        "startdate": "", "enddate": "", "month": "0",
        "hand": "", "team": "0", "pageitems": str(pageitems), "pagenum": "1",
        "ind": "0", "rost": "0", "players": "", "type": "8", "postseason": "",
    }
    pitch_cols = {
        "SwStr%": "swstr_pct", "Zone%": "zone_pct", "F-Strike%": "f_strike_pct",
        "SIERA": "siera", "GB%": "gb_pct", "xFIP": "xfip",
        "K-BB%": "k_minus_bb_pct", "HR/9": "hr9",
        "K%": "k_pct", "BB%": "bb_pct", "ERA": "era", "IP": "ip", "GS": "gs",
        "FBv": "fb_mph",
        "Name": None,
    }
    bat_cols = {
        "wRC+": "wrc_plus", "wOBA": "woba", "Contact%": "contact_pct",
        "O-Swing%": "o_swing_pct_fg", "K%": "k_pct", "BB%": "bb_pct",
        "Name": None,
    }
    col_map = pitch_cols if stats == "pit" else bat_cols
    try:
        resp = requests.get(_FG_API_URL, params=params, headers=_FG_HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        rows = resp.json().get("data", [])
    except Exception as exc:
        log.warning("FanGraphs %s fetch failed for %d: %s", stats, season, exc)
        return {}
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
        if row.get("PlayerName"):
            stats_dict["name"] = row["PlayerName"]
        elif row.get("Name"):
            stats_dict["name"] = row["Name"]
        for fg_col, cache_key in col_map.items():
            if cache_key is None:
                continue
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


def _fetch_fg_pitching(season: int) -> dict[int, dict]:
    return _fetch_fg_json(season, "pit")


def _fetch_fg_batting(season: int) -> dict[int, dict]:
    return _fetch_fg_json(season, "bat")


def _merge_fg_pitching(entry: dict, fg_data: dict[int, dict], mlbam_id: int) -> None:
    stats = fg_data.get(mlbam_id)
    if not stats:
        return
    for key, val in stats.items():
        if key == "name" and not entry.get("name"):
            entry["name"] = val
        elif key != "name" and entry.get(key) is None:
            entry[key] = val


def _merge_fg_batting(entry: dict, fg_data: dict[int, dict], mlbam_id: int) -> None:
    stats = fg_data.get(mlbam_id)
    if not stats:
        return
    for key, val in stats.items():
        if key == "name" and not entry.get("name"):
            entry["name"] = val
        elif key != "name" and entry.get(key) is None:
            entry[key] = val


# ---------------------------------------------------------------------------
# Baseball Savant CSV endpoints
# ---------------------------------------------------------------------------

def _fetch_savant_csv(url: str, label: str) -> pd.DataFrame:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        log.info("Savant %s: %d rows | cols: %s", label, len(df), list(df.columns))
        return df
    except Exception as exc:
        log.warning("Savant %s fetch failed: %s", label, exc)
        return pd.DataFrame()


def _fetch_savant_pitcher_stats(season: int) -> pd.DataFrame:
    url = (
        f"{SAVANT_BASE}/expected_statistics"
        f"?type=pitcher&year={season}&position=&team=&min=10&csv=true"
    )
    return _fetch_savant_csv(url, "pitcher-expected")


def _fetch_savant_batter_expected_stats(season: int) -> pd.DataFrame:
    """xBA, xwOBA, xSLG per batter from the expected-statistics leaderboard."""
    url = (
        f"{SAVANT_BASE}/expected_statistics"
        f"?type=batter&year={season}&position=&team=&min=50&csv=true"
    )
    return _fetch_savant_csv(url, "batter-expected")


def _fetch_savant_batter_batted_ball_stats(season: int) -> pd.DataFrame:
    """Barrel%, hard-hit%, avg exit velocity, avg launch angle from statcast leaderboard.

    Columns confirmed from live endpoint:
      player_id, brl_percent (whole %, e.g. 8.5), ev95percent (whole %, e.g. 42.1),
      avg_hit_speed (mph), avg_hit_angle (degrees)
    Also contains woba, k_percent, bb_percent used as FanGraphs fallback.
    min=50 instead of min=q so part-time players and recent call-ups are included.
    """
    url = (
        f"{SAVANT_BASE}/leaderboard/statcast"
        f"?type=batter&year={season}&position=&team=&min=50&csv=true"
    )
    return _fetch_savant_csv(url, "batter-batted-ball")


def _fetch_savant_batter_splits(season: int, split: str) -> pd.DataFrame:
    """Stub — Savant expected_statistics ignores the splits= parameter entirely.

    Returns an empty DataFrame so _merge_batter_split() is a no-op.
    Platoon split data is now sourced via _fetch_mlb_platoon_splits() instead.
    """
    return pd.DataFrame()


def _fetch_mlb_pitcher_stats(season: int) -> dict[int, dict]:
    """Fetch K%/BB% for all pitchers via MLB Stats API bulk endpoint.

    Returns {mlbam_id: {'k_pct': float, 'bb_pct': float}}.
    Requires >= 20 batters faced to exclude garbage-sample relievers.
    K% = strikeOuts / battersFaced, BB% = baseOnBalls / battersFaced.
    """
    result: dict[int, dict] = {}
    MIN_BF = 20
    session = requests.Session()
    offset = 0
    limit = 1000
    while True:
        try:
            resp = session.get(
                f"{MLB_STATS_BASE}/stats",
                params={
                    "stats": "season",
                    "playerPool": "all",
                    "group": "pitching",
                    "season": season,
                    "sportId": 1,
                    "limit": limit,
                    "offset": offset,
                },
                headers=_HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as exc:
            log.warning("MLB pitcher stats fetch failed (offset=%d): %s", offset, exc)
            break
        data = resp.json()
        splits = data.get("stats", [{}])[0].get("splits", [])
        if not splits:
            break
        for s in splits:
            pid = s.get("player", {}).get("id")
            if not pid:
                continue
            stat = s.get("stat", {})
            bf = stat.get("battersFaced") or 0
            so = stat.get("strikeOuts") or 0
            bb = stat.get("baseOnBalls") or 0
            if bf < MIN_BF:
                continue
            result[pid] = {
                "k_pct": round(so / bf, 4),
                "bb_pct": round(bb / bf, 4),
            }
        total = data.get("stats", [{}])[0].get("totalSplits", 0)
        offset += len(splits)
        if offset >= total:
            break
    log.info("MLB pitcher stats: %d pitchers with K%%/BB%% for season %d", len(result), season)
    return result


def _fetch_mlb_platoon_splits(batter_ids: list[int], season: int) -> dict[int, dict]:
    """Fetch vs-LHP and vs-RHP OBP/SLG from the MLB Stats API and convert to a
    wOBA proxy.  Two bulk /people requests (one per handedness) cover all batters.

    Returns {mlbam_id: {'xwoba_vs_l': float, 'xwoba_vs_r': float}}.
    Only entries with >= 20 PA vs that hand are included (tiny samples excluded).

    wOBA proxy formula: (1.2 * OBP + SLG) / 2.6
    This gives values on the same ~0.260-0.380 scale as xwOBA with a league
    average of ~0.310, close enough for platoon-split normalization.
    """
    if not batter_ids:
        return {}

    result: dict[int, dict] = {}
    MIN_PA = 20

    def _woba_proxy(obp: float, slg: float) -> float:
        return (1.2 * obp + slg) / 2.6

    # Batch in chunks of 200 (URL length limit)
    CHUNK = 200
    session = requests.Session()

    for sitcode, split_key in (("vl", "xwoba_vs_l"), ("vr", "xwoba_vs_r")):
        for i in range(0, len(batter_ids), CHUNK):
            chunk = batter_ids[i : i + CHUNK]
            try:
                resp = session.get(
                    f"{MLB_STATS_BASE}/people",
                    params={
                        "personIds": ",".join(str(p) for p in chunk),
                        "hydrate": (
                            f"stats(group=hitting,type=statSplits,"
                            f"sitCodes={sitcode},season={season},gameType=R)"
                        ),
                    },
                    headers=_HEADERS,
                    timeout=20,
                )
                resp.raise_for_status()
            except Exception as exc:
                log.debug("MLB platoon splits fetch failed (sitcode=%s): %s", sitcode, exc)
                continue

            for person in resp.json().get("people", []):
                pid = person.get("id")
                if not pid:
                    continue
                for sg in person.get("stats", []):
                    for s in sg.get("splits", []):
                        if s.get("split", {}).get("code") != sitcode:
                            continue
                        st = s.get("stat", {})
                        pa = st.get("plateAppearances") or 0
                        if pa < MIN_PA:
                            continue
                        try:
                            obp = float(st.get("obp") or 0)
                            slg = float(st.get("slg") or 0)
                        except (TypeError, ValueError):
                            continue
                        if obp <= 0 or slg <= 0:
                            continue
                        proxy = round(_woba_proxy(obp, slg), 4)
                        if pid not in result:
                            result[pid] = {}
                        result[pid][split_key] = proxy

    found = sum(1 for v in result.values() if "xwoba_vs_l" in v and "xwoba_vs_r" in v)
    log.info("MLB platoon splits: %d/%d batters have both L/R splits", found, len(batter_ids))
    return result


def _fetch_all_active_rosters(season: int) -> dict[str, list[int]]:
    """Return {full_team_name: [pitcher_mlbam_id, ...]} for all 30 MLB teams.

    Makes one request per team using the /teams/{id}/roster endpoint with
    rosterType=active (same endpoint confirmed working in schedule.py).
    Filters to position.code == '1' (pitchers only).
    """
    session = requests.Session()
    result: dict[str, list[int]] = {}

    for full_name, team_id in _MLB_TEAM_IDS.items():
        if full_name == "Athletics":
            continue  # duplicate entry — Sacramento Athletics already keyed as "Athletics"
        try:
            resp = session.get(
                f"{MLB_STATS_BASE}/teams/{team_id}/roster",
                params={"rosterType": "active", "season": season},
                headers=_HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.debug("Roster fetch failed for %s (%d): %s", full_name, team_id, exc)
            continue

        pitcher_ids = [
            entry["person"]["id"]
            for entry in data.get("roster", [])
            if entry.get("position", {}).get("code") == "1" and entry.get("person", {}).get("id")
        ]
        if pitcher_ids:
            result[full_name] = pitcher_ids

    log.info("Active rosters: %d teams, %d total pitchers fetched",
             len(result), sum(len(v) for v in result.values()))
    return result


def _fetch_savant_pitcher_leaderboard(season: int) -> pd.DataFrame:
    """Savant statcast pitcher leaderboard: ERA, xERA, K%, BB%, whiff%, stuff_plus.

    Used as a FanGraphs fallback when FG returns 403. Key columns:
      player_id, p_era, xera, k_percent, bb_percent, whiff_percent, stuff_plus
    All percentage columns are whole numbers (25.0 = 25%) — divide by 100 on merge.
    min=10 includes spot starters / call-ups with ~half a start of data.
    """
    url = (
        f"{SAVANT_BASE}/leaderboard/statcast"
        f"?type=pitcher&year={season}&position=&team=&min=10&csv=true"
    )
    return _fetch_savant_csv(url, "pitcher-leaderboard")


def _merge_savant_pitcher(entry: dict, df: pd.DataFrame, mlbam_id: int) -> None:
    if df.empty:
        return
    id_col = _find_id_col(df)
    if not id_col:
        return
    row = _lookup_row(df, id_col, mlbam_id)
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

    # FanGraphs fallback: pull ERA from Savant expected stats CSV
    # (only set if not already populated by FanGraphs)
    era_val = g("era")
    if era_val is not None and not entry.get("era"):
        entry["era"] = round(era_val, 4)

    # Explicitly store xera as its own key
    if not entry.get("xera"):
        xera_val = g("xera")
        if xera_val is not None:
            entry["xera"] = round(xera_val, 4)


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
    row = _lookup_row(df, id_col, mlbam_id)
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

    # Pitcher handedness — used by platoon split logic downstream
    if not entry.get("throws"):
        throws_val = r.get("p_throws")
        if throws_val and str(throws_val) not in ("", "nan"):
            entry["throws"] = str(throws_val).strip()

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

    # K%, BB%, whiff%, xera from leaderboard as fallbacks when FanGraphs unavailable
    for savant_col, entry_key in [
        ("k_percent",      "k_pct"),
        ("bb_percent",     "bb_pct"),
        ("whiff_percent",  "whiff_pct"),
    ]:
        val = g(savant_col)
        if val is not None and not entry.get(entry_key):
            entry[entry_key] = round(val / 100.0, 4)

    xera_lead = g("xera")
    if xera_lead is not None and not entry.get("xera"):
        entry["xera"] = round(xera_lead, 4)

    # Run value per 100 pitches — try multiple candidate column names
    for col in ("run_value_per_100", "rv100", "pitch_run_value"):
        rv = g(col)
        if rv is not None:
            entry["rv100"] = round(rv, 2)
            break


def _merge_savant_batter_expected(entry: dict, df: pd.DataFrame, mlbam_id: int) -> None:
    """Merge xBA, xwOBA, xSLG from the expected-statistics CSV."""
    if df.empty:
        return
    id_col = _find_id_col(df)
    if not id_col:
        return
    row = _lookup_row(df, id_col, mlbam_id)
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
        "xba":   g("est_ba")  or g("xba"),
        "xwoba": g("est_woba") or g("xwoba") or g("expected_woba"),
        "xslg":  g("est_slg") or g("xslg"),
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
    row = _lookup_row(df, id_col, mlbam_id)
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

    brl = g("brl_percent") or g("barrel_batted_rate") or g("barrel_rate")
    hh  = g("ev95percent") or g("hard_hit_percent") or g("hard_hit_rate")
    ev  = g("avg_hit_speed") or g("avg_exit_velocity") or g("launch_speed")
    la  = g("avg_hit_angle") or g("avg_launch_angle") or g("launch_angle")

    if brl is not None:
        entry["barrel_pct"] = brl / 100.0
    if hh is not None:
        entry["hard_hit_pct"] = hh / 100.0
    if ev is not None:
        entry["avg_ev"] = ev
    if la is not None:
        entry["avg_launch_angle"] = la

    # K% and BB% from the leaderboard as FanGraphs fallback
    for savant_col, entry_key in [
        ("k_percent",  "k_pct"),
        ("bb_percent", "bb_pct"),
    ]:
        val = g(savant_col)
        if val is not None and not entry.get(entry_key):
            entry[entry_key] = round(val / 100.0, 4)

    # The batter statcast leaderboard has exit-velocity stats only (no wOBA/K%).


def _merge_batter_split(entry: dict, df: pd.DataFrame, mlbam_id: int, split: str) -> None:
    """Merge split-specific xwOBA, xBA, K% into '_vs_l' or '_vs_r' suffixed fields.

    split: 'vs_lhp' or 'vs_rhp'
    Falls back gracefully — if no data for this batter vs. that hand, nothing is written.
    """
    if df.empty:
        return
    id_col = _find_id_col(df)
    if not id_col:
        return
    row = _lookup_row(df, id_col, mlbam_id)
    if row.empty:
        return
    r = row.iloc[0]

    suffix = "_vs_l" if split == "vs_lhp" else "_vs_r"

    def g(col, default=None):
        try:
            v = r.get(col)
            return float(v) if v is not None and str(v) not in ("", "nan") else default
        except Exception:
            return default

    xwoba = g("est_woba") or g("xwoba") or g("expected_woba")
    if xwoba is not None:
        entry[f"xwoba{suffix}"] = round(xwoba, 4)

    xba = g("est_ba") or g("xba")
    if xba is not None:
        entry[f"xba{suffix}"] = round(xba, 4)

    xslg = g("est_slg") or g("xslg") or g("expected_slg")
    if xslg is not None:
        entry[f"xslg{suffix}"] = round(xslg, 4)

    k_pct = g("k_percent")
    if k_pct is not None:
        entry[f"k_pct{suffix}"] = round(k_pct / 100.0, 4)

    bb_pct = g("bb_percent")
    if bb_pct is not None:
        entry[f"bb_pct{suffix}"] = round(bb_pct / 100.0, 4)


# ---------------------------------------------------------------------------
# Team bullpen aggregation
# ---------------------------------------------------------------------------

def _build_team_bullpen_cache(
    season: int,
    sp_ids: set[int] | None = None,
    fg_pitch_data: dict[int, dict] | None = None,
    pitch_lead_df: "pd.DataFrame | None" = None,
) -> dict[str, dict]:
    """Build IP-weighted bullpen xERA/K%/BB%/whiff% per team using roster-based pitcher mapping.

    Uses pre-fetched data sources instead of a separate URL fetch:
      1. pitch_lead_df (Savant pitcher leaderboard, min=10) — xera, k_percent (whole%),
         bb_percent, whiff_percent, IP
      2. fg_pitch_data (FanGraphs JSON, min=1 IP) — k_pct/bb_pct/swstr_pct as fractions,
         siera as xERA proxy
      3. MLB Stats API active roster — maps team → pitcher MLBAM IDs
      4. sp_ids exclusion so today's starters aren't counted as bullpen arms
    """
    if sp_ids is None:
        sp_ids = set()

    def _safe_float(v):
        try:
            f = float(v)
            return f if str(v) not in ("", "nan") else None
        except (TypeError, ValueError):
            return None

    # Build per-pitcher stat dict (Savant-scale: k_percent etc. as whole numbers 0-100).
    # Savant leaderboard is primary; FanGraphs is fallback for pitchers below min=10.
    pitcher_stats: dict[int, dict] = {}

    if pitch_lead_df is not None and not pitch_lead_df.empty:
        id_col = _find_id_col(pitch_lead_df)
        if id_col:
            for _, row in pitch_lead_df.iterrows():
                try:
                    pid = int(row[id_col])
                except (ValueError, TypeError):
                    continue
                stats: dict = {}
                for col in ("xera", "k_percent", "bb_percent", "whiff_percent",
                            "p_formatted_ip", "ip", "innings_pitched"):
                    v = row.get(col)
                    if v is not None and str(v) not in ("", "nan"):
                        try:
                            stats[col] = float(v)
                        except (ValueError, TypeError):
                            pass
                if stats:
                    pitcher_stats[pid] = stats

    # FanGraphs fallback — fractions × 100 → whole-number scale for uniform accumulation
    if fg_pitch_data:
        for mlbam_id, fg in fg_pitch_data.items():
            stats = pitcher_stats.setdefault(mlbam_id, {})
            for fg_key, sav_col, scale in [
                ("k_pct",     "k_percent",     100.0),
                ("bb_pct",    "bb_percent",    100.0),
                ("swstr_pct", "whiff_percent", 100.0),
                ("ip",        "ip",              1.0),
            ]:
                if stats.get(sav_col) is None and fg.get(fg_key) is not None:
                    try:
                        stats[sav_col] = float(fg[fg_key]) * scale
                    except (ValueError, TypeError):
                        pass
            # Use SIERA as xERA proxy when Savant xera is missing
            if stats.get("xera") is None and fg.get("siera") is not None:
                try:
                    stats["xera"] = float(fg["siera"])
                except (ValueError, TypeError):
                    pass

    if not pitcher_stats:
        log.warning("Bullpen: no pitcher stats available (pass pitch_lead_df or fg_pitch_data)")
        return {}

    rosters = _fetch_all_active_rosters(season)
    if not rosters:
        log.warning("Bullpen: roster fetch empty — skipping bullpen cache")
        return {}

    result: dict[str, dict] = {}
    for full_name, pitcher_ids in rosters.items():
        rp_ids = [pid for pid in pitcher_ids if pid not in sp_ids and pid in pitcher_stats]
        if not rp_ids:
            continue

        total_ip = 0.0
        w_sum = {"xera": 0.0, "k_percent": 0.0, "bb_percent": 0.0, "whiff_percent": 0.0}
        ip_sum = {"xera": 0.0, "k_percent": 0.0, "bb_percent": 0.0, "whiff_percent": 0.0}

        for pid in rp_ids:
            row = pitcher_stats[pid]
            ip_raw = (
                _safe_float(row.get("p_formatted_ip"))
                or _safe_float(row.get("ip"))
                or _safe_float(row.get("innings_pitched"))
                or 1.0
            )
            ip = max(ip_raw, 0.01)
            total_ip += ip
            for col in ("xera", "k_percent", "bb_percent", "whiff_percent"):
                v = _safe_float(row.get(col))
                if v is not None:
                    w_sum[col]  += v * ip
                    ip_sum[col] += ip

        if total_ip <= 0:
            continue

        def _wavg(col: str, divisor: float = 1.0):
            w = ip_sum[col]
            return round(w_sum[col] / w / divisor, 4) if w > 0 else None

        result[full_name] = {
            "xera":       _wavg("xera"),
            "k_pct":      _wavg("k_percent",    divisor=100.0),
            "bb_pct":     _wavg("bb_percent",   divisor=100.0),
            "whiff_pct":  _wavg("whiff_percent", divisor=100.0),
            "ip":         round(total_ip, 1),
            "n_pitchers": len(rp_ids),
        }

    log.info("Bullpen cache: %d teams (IP-weighted xERA/K%%/BB%%)", len(result))

    # Enrich with last-3-game-days reliever IP (fatigue signal)
    today = date.today()
    l3d_results = _build_bullpen_last3_ip_map(today)
    for full_name in list(result.keys()):
        result[full_name]["bp_ip_last_3"] = l3d_results.get(full_name, 0.0)

    return result


# ---------------------------------------------------------------------------
# Bullpen last-3-game-days IP (fatigue signal via MLB Stats API boxscores)
# ---------------------------------------------------------------------------

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
    "Athletics":             133,
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
# Reverse mapping: MLB Stats API team ID → full team name
_MLB_ID_TO_FULL: dict[int, str] = {v: k for k, v in _MLB_TEAM_IDS.items() if k != "Athletics"}

# Module-level boxscore cache to avoid duplicate API calls when multiple teams share a game
_boxscore_cache: dict[int, dict] = {}


def _get_boxscore(game_pk: int) -> dict:
    """Fetch and cache the boxscore for a game (keyed by game_pk)."""
    if game_pk not in _boxscore_cache:
        try:
            resp = requests.get(
                f"{MLB_STATS_BASE}/game/{game_pk}/boxscore",
                headers=_HEADERS, timeout=TIMEOUT,
            )
            resp.raise_for_status()
            _boxscore_cache[game_pk] = resp.json()
        except Exception as exc:
            log.debug("Boxscore fetch failed for game %d: %s", game_pk, exc)
            _boxscore_cache[game_pk] = {}
    return _boxscore_cache[game_pk]


def _reliever_ip_from_boxscore(game_pk: int, team_id: int) -> float:
    """Return total reliever IP for team_id in the given game (starter excluded)."""
    box = _get_boxscore(game_pk)
    if not box:
        return 0.0
    teams = box.get("teams", {})
    for side in ("home", "away"):
        t = teams.get(side, {})
        if t.get("team", {}).get("id") != team_id:
            continue
        pitchers = t.get("pitchers", [])
        players  = t.get("players", {})
        rp_ip = 0.0
        for pid in pitchers[1:]:   # skip index-0 (starter)
            stats = players.get(f"ID{pid}", {}).get("stats", {}).get("pitching", {})
            ip_str = stats.get("inningsPitched", "0")
            try:
                ip = float(ip_str)
                whole = int(ip)
                frac  = round(ip - whole, 1)
                ip_dec = whole + (frac / 0.3) if frac > 0 else float(whole)
                rp_ip += ip_dec
            except (ValueError, TypeError):
                pass
        return round(rp_ip, 1)
    return 0.0


def _build_bullpen_last3_ip_map(today: date) -> dict[str, float]:
    """Return {full_team_name: total_reliever_ip_last_3_game_days} for all teams.

    Uses a single schedule call to get all completed games in the past 5 calendar
    days, then fetches each unique boxscore once (shared between home/away teams).
    """
    start = (today - timedelta(days=5)).strftime("%Y-%m-%d")
    end   = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            f"{MLB_STATS_BASE}/schedule",
            params={
                "sportId": 1,
                "startDate": start,
                "endDate": end,
                "gameType": "R",
                "fields": "dates,date,games,gamePk,status,abstractGameState,teams,home,away,team,id",
            },
            headers=_HEADERS,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        sched = resp.json()
    except Exception as exc:
        log.debug("Schedule fetch for bullpen L3D failed: %s", exc)
        return {}

    # Collect (date, game_pk, home_team_id, away_team_id) for Final games
    game_records: list[tuple[str, int, int, int]] = []
    for date_block in sched.get("dates", []):
        date_str = date_block.get("date", "")
        for g in date_block.get("games", []):
            if g.get("status", {}).get("abstractGameState", "") != "Final":
                continue
            home_id = g.get("teams", {}).get("home", {}).get("team", {}).get("id", 0)
            away_id = g.get("teams", {}).get("away", {}).get("team", {}).get("id", 0)
            game_records.append((date_str, g["gamePk"], home_id, away_id))

    if not game_records:
        return {}

    # Determine last 3 unique game-dates
    unique_dates = sorted({d for d, _, _, _ in game_records})[-3:]
    date_set = set(unique_dates)
    recent = [(d, pk, hid, aid) for d, pk, hid, aid in game_records if d in date_set]

    # Aggregate reliever IP per team over those games
    team_ip: dict[int, float] = {}
    for _, game_pk, home_id, away_id in recent:
        for team_id in (home_id, away_id):
            ip = _reliever_ip_from_boxscore(game_pk, team_id)
            team_ip[team_id] = round(team_ip.get(team_id, 0.0) + ip, 1)

    # Map MLB team IDs back to full names
    return {
        _MLB_ID_TO_FULL[tid]: ip
        for tid, ip in team_ip.items()
        if tid in _MLB_ID_TO_FULL
    }


# ---------------------------------------------------------------------------
# Pitcher last-start deviation (MLB Stats API — most recent start ERA estimate)
# ---------------------------------------------------------------------------

def _ip_to_decimal(ip_str: str) -> float | None:
    """Convert MLB innings-pitched string (e.g. '5.1', '6.2') to decimal."""
    try:
        ip = float(ip_str)
        whole = int(ip)
        frac  = round(ip - whole, 1)
        return whole + (frac / 0.3) if frac > 0 else float(whole)
    except (ValueError, TypeError):
        return None


def _merge_pitcher_last_start(entry: dict, mlbam_id: int, season: int) -> None:
    """Fetch pitching game log and compute single and 3-start trend deviations.

    Single-start deviation (last_start_deviation): for backward-compat display.
    3-start weighted trend (last_3_start_deviation): weights 0.5/0.3/0.2 newest→oldest.
    Pitch count (last_start_pitches): flags high-pitch outings for SP load signal.
    """
    try:
        url = (
            f"{MLB_STATS_BASE}/people/{mlbam_id}/stats"
            f"?stats=gameLog&group=pitching&season={season}"
        )
        resp = requests.get(url, headers=_HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        splits = []
        for stats_block in data.get("stats", []):
            splits = stats_block.get("splits", [])
            if splits:
                break
        # Filter to starts only (games where the pitcher was the starter)
        starts = [s for s in splits if s.get("stat", {}).get("gamesStarted", 0) >= 1]
        if not starts:
            return

        season_xera = entry.get("xera") or entry.get("xfip")

        def _start_era(stat: dict) -> float | None:
            ip_dec = _ip_to_decimal(stat.get("inningsPitched", "0"))
            if ip_dec is None or ip_dec < 1.0:
                return None
            er = int(stat.get("earnedRuns", 0))
            return (er / ip_dec) * 9.0

        # --- Single most-recent start ---
        last = starts[-1]["stat"]
        last_era = _start_era(last)
        if last_era is not None:
            entry["last_start_era_est"] = round(last_era, 2)
            ip_dec = _ip_to_decimal(last.get("inningsPitched", "0"))
            if ip_dec:
                entry["last_start_ip"] = round(ip_dec, 1)
            pitches = last.get("pitchesThrown")
            if pitches is not None:
                entry["last_start_pitches"] = int(pitches)
            if season_xera is not None:
                dev = last_era - season_xera
                entry["last_start_deviation"] = round(max(-3.0, min(3.0, dev)), 2)

        # --- Rolling 3-start weighted trend ---
        recent = starts[-3:]  # up to 3 most recent starts (newest last)
        weighted_deviations: list[tuple[float, float]] = []  # (deviation, weight)
        weights = [0.2, 0.3, 0.5]  # oldest → newest
        offset = 3 - len(recent)
        for i, s in enumerate(recent):
            era = _start_era(s["stat"])
            if era is not None and season_xera is not None:
                w = weights[offset + i]
                dev = max(-3.0, min(3.0, era - season_xera))
                weighted_deviations.append((dev, w))
        if weighted_deviations:
            total_w = sum(w for _, w in weighted_deviations)
            trend_dev = sum(d * w for d, w in weighted_deviations) / total_w
            entry["last_3_start_deviation"] = round(trend_dev, 2)
    except Exception as exc:
        log.debug("Pitcher last-start fetch failed for %d: %s", mlbam_id, exc)


# ---------------------------------------------------------------------------
# Batter recent game log (MLB Stats API — last N games H/HR/K)
# ---------------------------------------------------------------------------


def _merge_batter_game_log(entry: dict, mlbam_id: int, season: int, n: int = 20) -> None:
    """Fetch last n game log entries for a batter.

    Stores per-game H/HR/K arrays (last 10 for display) and rolling rate stats
    (last n games) for K%, BB%, and H-rate used in 60/40 blending.
    """
    try:
        url = (
            f"{MLB_STATS_BASE}/people/{mlbam_id}/stats"
            f"?stats=gameLog&group=hitting&season={season}"
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
        # API returns chronological order (oldest first); take last n for most recent games
        recent = splits[-n:]

        # Per-game display arrays (last 10 games)
        display = recent[-10:]
        entry["recent_h_games"]  = [int(s["stat"].get("hits", 0))      for s in display]
        entry["recent_hr_games"] = [int(s["stat"].get("homeRuns", 0))   for s in display]
        entry["recent_k_games"]  = [int(s["stat"].get("strikeOuts", 0)) for s in display]

        # Rolling rate stats over last n games (used for 60/40 blend)
        total_pa = sum(int(s["stat"].get("plateAppearances", 0)) for s in recent)
        total_ab = sum(int(s["stat"].get("atBats",           0)) for s in recent)
        total_k  = sum(int(s["stat"].get("strikeOuts",       0)) for s in recent)
        total_bb = sum(int(s["stat"].get("baseOnBalls",      0)) for s in recent)
        total_h  = sum(int(s["stat"].get("hits",             0)) for s in recent)

        entry["recent_pa_n"] = total_pa
        if total_pa >= 30:
            entry["k_pct_recent"]   = round(total_k  / total_pa, 4)
            entry["bb_pct_recent"]  = round(total_bb / total_pa, 4)
        if total_ab >= 30:
            entry["h_rate_recent"]  = round(total_h  / total_ab, 4)
    except Exception as exc:
        log.debug("Batter game log fetch failed for %d: %s", mlbam_id, exc)


# ---------------------------------------------------------------------------
# 30-day rolling Statcast for batters
# ---------------------------------------------------------------------------


def _merge_rolling_batter(entry: dict, mlbam_id: int, start: str, end: str) -> None:
    """Fetch 30-day pitch-level Statcast for a batter and aggregate contact/power metrics."""
    try:
        from pybaseball import statcast_batter
        df = statcast_batter(start_dt=start, end_dt=end, player_id=mlbam_id)
        if df is None or df.empty:
            return
        agg = _aggregate_batter_pitch_data(df)
        if agg:
            entry.update(agg)
            log.debug("Rolling batter Statcast merged for %d: %s", mlbam_id, list(agg))
    except Exception as exc:
        log.debug("Rolling batter stats failed for %d: %s", mlbam_id, exc)


def _aggregate_batter_pitch_data(df: "pd.DataFrame") -> dict:
    """Aggregate 30-day pitch-level Statcast into per-batter metrics.

    Returns a dict with _30d suffixed keys for barrel_pct, hard_hit_pct,
    contact_pct, k_pct, bb_pct, and pa_30d (sample size).
    """
    result: dict = {}

    # Contact rate: 1 − (swinging strikes / total swings)
    swing_descs = {"swinging_strike", "swinging_strike_blocked", "foul",
                   "foul_tip", "hit_into_play", "hit_into_play_score"}
    miss_descs  = {"swinging_strike", "swinging_strike_blocked"}
    if "description" in df.columns:
        swings    = df["description"].isin(swing_descs)
        misses    = df["description"].isin(miss_descs)
        total_sw  = int(swings.sum())
        if total_sw >= 20:
            result["contact_pct_30d"] = round(float(1 - misses.sum() / total_sw), 4)

    # Hard-hit % and barrel % from batted balls with recorded launch_speed
    if "launch_speed" in df.columns:
        bip   = df["launch_speed"].notna()
        n_bip = int(bip.sum())
        if n_bip >= 15:
            hh = int((df.loc[bip, "launch_speed"] >= 95).sum())
            result["hard_hit_pct_30d"] = round(float(hh / n_bip), 4)
            if "barrel" in df.columns:
                barrels = float(df.loc[bip, "barrel"].fillna(0).sum())
                result["barrel_pct_30d"] = round(barrels / n_bip, 4)

    # K% and BB% from terminal plate-appearance events
    if "events" in df.columns:
        terminal = df["events"].dropna()
        n_pa     = len(terminal)
        if n_pa >= 30:
            ks  = int(terminal.isin(["strikeout", "strikeout_double_play"]).sum())
            bbs = int(terminal.isin(["walk", "intent_walk"]).sum())
            result["k_pct_30d"]  = round(ks  / n_pa, 4)
            result["bb_pct_30d"] = round(bbs / n_pa, 4)
            result["pa_30d"]     = n_pa

    return result


def _blend_batter_rolling(entry: dict) -> None:
    """Apply 60/40 season/rolling blend for batters with ≥50 PA in the rolling window.

    Modifies entry in-place so all downstream scorers automatically use blended values.
    Sets entry['rolling_blended'] = True as a transparency flag for raw_scores.
    """
    pa = entry.get("pa_30d", 0)
    if pa < 50:
        return

    pairs = [
        ("barrel_pct",   "barrel_pct_30d"),
        ("hard_hit_pct", "hard_hit_pct_30d"),
        ("contact_pct",  "contact_pct_30d"),
        ("k_pct",        "k_pct_30d"),
        ("bb_pct",       "bb_pct_30d"),
    ]
    blended_any = False
    for season_key, rolling_key in pairs:
        s = entry.get(season_key)
        r = entry.get(rolling_key)
        if s is not None and r is not None:
            entry[season_key] = round(0.60 * s + 0.40 * r, 4)
            blended_any = True

    if blended_any:
        entry["rolling_blended"] = True


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
        ids.update(g.get("home_lineup_proxy", []))
        ids.update(g.get("away_lineup_proxy", []))
    return list(ids)


def _find_id_col(df: pd.DataFrame) -> Optional[str]:
    for candidate in ("player_id", "mlbam_id", "batter", "pitcher", "IDfg", "id"):
        if candidate in df.columns:
            return candidate
    return None


def _lookup_row(df: pd.DataFrame, id_col: str, mlbam_id: int):
    """Return the first matching row, handling float ID columns robustly."""
    row = df[df[id_col] == mlbam_id]
    if row.empty:
        # Some CSVs store IDs as floats; try float comparison
        try:
            row = df[df[id_col] == float(mlbam_id)]
        except Exception as exc:
            log.debug("Float-ID lookup failed for %s in %s: %s", mlbam_id, id_col, exc)
    return row
