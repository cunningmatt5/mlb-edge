"""MLB Edge — New Stats Regression Analysis

Tests two tracks of candidate signals for betting edge:
  Track A: Stored-but-unused stats from player_cache.pkl (zero new fetches)
  Track B: New fetchable stats — pitcher velocity (avg_fb_mph) and GB% from FanGraphs

Pass criteria (all three required):
  |Pearson r| >= 0.05  AND  p-value < 0.01  AND  LOYO ROI > +2% in >= 3/4 seasons

Usage:
    python -m pipeline.regression_new_stats             # both tracks
    python -m pipeline.regression_new_stats --track a   # Track A only (fast, no network)
    python -m pipeline.regression_new_stats --track b   # Track B only (fetches data)
    python -m pipeline.regression_new_stats --out data/new_stats_report.json
"""
from __future__ import annotations

import argparse
import io
import json
import math
import pickle
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import requests
from scipy.stats import pearsonr

ROOT          = Path(__file__).parent.parent
BACKTEST_PATH = ROOT / "docs" / "backtest.json"
SEASONS_DIR   = ROOT / "data" / "seasons"
SAVANT_BASE   = "https://baseballsavant.mlb.com"

LINEUP_SEASONS = [2024, 2025]

# Track A — pitcher stats stored in player_cache but not used in _sp_quality_score()
PITCHER_UNUSED = [
    "swstr_pct",           # swinging-strike rate (FanGraphs SwStr%)
    "f_strike_pct",        # first-strike rate (FanGraphs F-Strike%)
    "zone_pct",            # zone rate (FanGraphs Zone%)
    "rv100",               # run value per 100 pitches (Savant)
    "siera",               # skills-interactive ERA (FanGraphs)
    "xba_against",         # expected BA against (Savant expected_statistics)
    "xslg_against",        # expected SLG against (Savant expected_statistics)
    "barrel_pct_against",  # barrel % against (Savant leaderboard)
]

# Track A — batter stats stored in player_cache but not used in _lineup_score()
BATTER_UNUSED = [
    "wrc_plus",        # park/era-adjusted offense (FanGraphs wRC+)
    "contact_pct",     # contact rate (FanGraphs Contact%)
    "o_swing_pct_fg",  # batter chase rate (FanGraphs O-Swing%)
    "xba",             # expected batting average (Savant)
    "xslg",            # expected slugging (Savant)
]

# Track B — candidate column names for avg fastball velocity in Savant pitcher leaderboard CSV
AVG_FB_MPH_COLS = [
    "avg_fastball",
    "fastball_avg_speed",
    "avg_fb_velocity",
    "avg_fb_mph",
    "ff_avg_speed",
    "release_speed",
]

_REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (MLBEdge regression script)"}
_TIMEOUT = 30


# ── Math helpers ──────────────────────────────────────────────────────────────

def _american_to_decimal(odds: float) -> float:
    if odds >= 0:
        return 1 + odds / 100
    return 1 - 100 / odds


# ── Data loading ──────────────────────────────────────────────────────────────

def load_backtest_priced() -> list[dict]:
    """Load 2022-2025 priced games from backtest.json."""
    bt = json.loads(BACKTEST_PATH.read_text(encoding="utf-8"))
    return [
        g for g in bt["games"]
        if g.get("season") in (2022, 2023, 2024, 2025)
        and g.get("home_implied_prob") is not None
        and g.get("pitcher_score_home") is not None
        and g.get("pitcher_score_away") is not None
        and g.get("actual_winner") in ("home", "away")
        and g.get("home_ml") is not None
        and g.get("away_ml") is not None
        and abs(g["home_ml"]) <= 600
    ]


def load_player_caches() -> dict[int, dict]:
    """Load per-season player caches. Returns {season: {player_id: stats_dict}}."""
    caches: dict[int, dict] = {}
    for year in range(2022, 2026):
        path = SEASONS_DIR / str(year) / "player_cache.pkl"
        if path.exists():
            with open(path, "rb") as f:
                caches[year] = pickle.load(f)
    return caches


def load_game_lineups() -> dict[int, tuple[list[int], list[int]]]:
    """Load 2024-2025 game lineups. Returns {game_pk: (home_ids, away_ids)}."""
    out: dict[int, tuple[list[int], list[int]]] = {}
    for year in LINEUP_SEASONS:
        path = SEASONS_DIR / str(year) / "game_lineups.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df = df[df["batting_order"].between(1, 9)]
        for pk, grp in df.groupby("game_pk"):
            home_ids = grp[grp["side"] == "home"]["player_id"].tolist()
            away_ids = grp[grp["side"] == "away"]["player_id"].tolist()
            out[int(pk)] = (home_ids, away_ids)
    return out


# ── Cache stat helpers ────────────────────────────────────────────────────────

def _get_stat(cache: dict, pid: int, key: str) -> float | None:
    player = cache.get(int(pid))
    if player is None:
        return None
    v = player.get(key)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _team_batter_avg(ids: list[int], cache: dict, key: str, min_found: int = 5) -> float | None:
    vals = [v for pid in ids if (v := _get_stat(cache, pid, key)) is not None]
    return float(np.mean(vals)) if len(vals) >= min_found else None


# ── ROI helpers ───────────────────────────────────────────────────────────────

def _ml_roi(games: list[dict], bet_side_fn) -> dict:
    """Flat-$100 ROI using bet_side_fn(g) -> 'home' | 'away'."""
    if not games:
        return {"n": 0, "win_pct": None, "roi_pct": None}
    staked = len(games) * 100
    total_return = 0.0
    wins = 0
    for g in games:
        side = bet_side_fn(g)
        won = g["actual_winner"] == side
        if won:
            wins += 1
            ml = g["home_ml"] if side == "home" else g["away_ml"]
            total_return += (_american_to_decimal(ml) - 1) * 100
        else:
            total_return -= 100
    n = len(games)
    return {"n": n, "win_pct": round(wins / n * 100, 1), "roi_pct": round(total_return / staked * 100, 2)}


def _under_roi(games: list[dict]) -> dict:
    """Flat-$100 ROI betting UNDER using under_price field (defaults to -110)."""
    usable = [g for g in games if g.get("total_went_over") is not None]
    if not usable:
        return {"n": 0, "win_pct": None, "roi_pct": None}
    staked = len(usable) * 100
    total_return = 0.0
    wins = 0
    for g in usable:
        won = not g["total_went_over"]
        if won:
            wins += 1
            price = g.get("under_price") or -110
            total_return += (_american_to_decimal(price) - 1) * 100
        else:
            total_return -= 100
    n = len(usable)
    return {"n": n, "win_pct": round(wins / n * 100, 1), "roi_pct": round(total_return / staked * 100, 2)}


# ── Regression engine ─────────────────────────────────────────────────────────

def _pass_criteria(r: float, p: float, loyo_pass_count: int) -> str:
    """Return PASS / FAIL / MARGINAL based on criteria."""
    r_ok   = abs(r) >= 0.05 and p < 0.01
    loyo_ok = loyo_pass_count >= 3
    if r_ok and loyo_ok:
        return "PASS"
    if r_ok and loyo_pass_count == 2:
        return "MARGINAL"
    return "FAIL"


def _loyo_roi(bt: list[dict], diff_fn, min_q: float = 0.75) -> dict:
    """
    Leave-one-year-out ROI analysis.
    diff_fn(g) -> float (the stat differential; positive = home advantage)
    Bets: Q4 (top quartile) -> home; Q1 (bottom quartile) -> away.
    Returns {year: {q1_roi, q4_roi, ml_roi, under_roi}, loyo_ml_pass, loyo_under_pass}.
    """
    years = [2022, 2023, 2024, 2025]
    by_yr: dict[int, list[dict]] = {yr: [] for yr in years}
    diff_cache: dict[int, float | None] = {}

    for g in bt:
        yr = g.get("season")
        if yr in years:
            d = diff_fn(g)
            diff_cache[id(g)] = d
            if d is not None:
                by_yr[yr].append(g)

    year_results: dict[int, dict] = {}
    ml_pass_count = 0
    under_pass_count = 0

    for test_yr in years:
        train = [g for yr2, gs in by_yr.items() for g in gs if yr2 != test_yr]
        train_diffs = [v for g in train if (v := diff_cache[id(g)]) is not None]
        if len(train_diffs) < 100:
            year_results[test_yr] = None
            continue

        lo_thresh = float(np.percentile(train_diffs, 25))
        hi_thresh = float(np.percentile(train_diffs, 75))

        test_games = [g for g in by_yr[test_yr] if diff_cache[id(g)] is not None]
        q1 = [g for g in test_games if diff_cache[id(g)] <= lo_thresh]
        q4 = [g for g in test_games if diff_cache[id(g)] >= hi_thresh]

        # Q4: home SP better → bet home; Q1: away SP better → bet away
        q4_ml = _ml_roi(q4, lambda g: "home")
        q1_ml = _ml_roi(q1, lambda g: "away")

        # Combined: bet on the "better" side by this stat
        combined = q4 + q1
        def _bet_side(g, _q4=set(id(g2) for g2 in q4), _q1=set(id(g2) for g2 in q1)):
            return "home" if id(g) in _q4 else "away"

        # Simpler: compute combined by averaging the two pools
        combined_n  = q4_ml["n"] + q1_ml["n"]
        combined_ret = 0.0
        combined_w   = 0
        for g in q4:
            won = g["actual_winner"] == "home"
            if won:
                combined_w += 1
                combined_ret += (_american_to_decimal(g["home_ml"]) - 1) * 100
            else:
                combined_ret -= 100
        for g in q1:
            won = g["actual_winner"] == "away"
            if won:
                combined_w += 1
                combined_ret += (_american_to_decimal(g["away_ml"]) - 1) * 100
            else:
                combined_ret -= 100

        comb_roi = round(combined_ret / (combined_n * 100) * 100, 2) if combined_n > 0 else None
        # UNDER: Q4 (home SP dominant = likely lower run environment) → bet UNDER
        q4_under = _under_roi(q4)
        q4_under_roi = q4_under["roi_pct"]

        year_results[test_yr] = {
            "q4_ml_roi":    q4_ml["roi_pct"],
            "q1_ml_roi":    q1_ml["roi_pct"],
            "combined_roi": comb_roi,
            "q4_under_roi": q4_under_roi,
            "q4_n":         q4_ml["n"],
            "q1_n":         q1_ml["n"],
        }
        if comb_roi is not None and comb_roi > 2.0:
            ml_pass_count += 1
        if q4_under_roi is not None and q4_under_roi > 2.0:
            under_pass_count += 1

    return {
        "by_year":          year_results,
        "ml_pass_count":    ml_pass_count,
        "under_pass_count": under_pass_count,
    }


def run_stat_regression(
    label: str,
    diffs: list[tuple[dict, float]],  # (game, diff_value) pairs
    target_win: np.ndarray,
    target_over: np.ndarray | None,
    stat_info: str = "",
) -> dict:
    """Run Pearson correlation + LOYO ROI for a single stat differential."""
    vals = np.array([d for _, d in diffs], dtype=float)
    if len(vals) < 200:
        print(f"    {label:<35}  SKIP (only {len(vals)} complete games)")
        return {"label": label, "verdict": "SKIP", "n": len(vals)}

    # Pearson vs home win
    r_win, p_win = pearsonr(vals, target_win)

    # Pearson vs over (if available)
    r_over = None
    if target_over is not None and len(target_over) == len(vals):
        r_over_val, p_over_val = pearsonr(vals, target_over)
        r_over = (round(float(r_over_val), 4), round(float(p_over_val), 5))

    return {
        "label":   label,
        "n":       len(vals),
        "r_win":   round(float(r_win), 4),
        "p_win":   round(float(p_win), 6),
        "r_over":  r_over,
        "info":    stat_info,
    }


# ── Track A: Stored-but-unused pitcher stats ──────────────────────────────────

def track_a_pitchers(bt: list[dict], caches: dict[int, dict]) -> dict:
    print("\n" + "-" * 70)
    print("  TRACK A — PITCHER: STORED-BUT-UNUSED STATS")
    print("-" * 70)
    print(f"  Stats: {', '.join(PITCHER_UNUSED)}\n")

    # Build per-game diffs for each stat
    stat_diffs: dict[str, list[tuple[dict, float]]] = {s: [] for s in PITCHER_UNUSED}
    coverage_total = 0

    for g in bt:
        season = g.get("season")
        cache = caches.get(season)
        if cache is None:
            continue
        hsp = g.get("home_sp_id")
        asp = g.get("away_sp_id")
        if not hsp or not asp:
            continue
        coverage_total += 1
        for stat in PITCHER_UNUSED:
            h = _get_stat(cache, hsp, stat)
            a = _get_stat(cache, asp, stat)
            if h is not None and a is not None:
                stat_diffs[stat].append((g, h - a))

    print(f"  Games with both SP IDs: {coverage_total:,} / {len(bt):,}")
    print()

    # Pearson + LOYO per stat
    print(f"  {'Stat':<25} {'n':>6}  {'r vs win':>9}  {'p-val':>10}  {'r vs over':>10}  {'ML LOYO':>8}  {'UND LOYO':>9}  Verdict")
    print("  " + "-" * 98)

    results: dict[str, dict] = {}
    for stat in PITCHER_UNUSED:
        pairs = stat_diffs[stat]
        if len(pairs) < 200:
            print(f"  {stat:<25} {'<200':>6}  {'SKIP — insufficient data':}")
            results[stat] = {"verdict": "SKIP", "n": len(pairs)}
            continue

        games_used = [g for g, _ in pairs]
        diffs_arr  = np.array([d for _, d in pairs], dtype=float)
        y_win  = np.array([1.0 if g["actual_winner"] == "home" else 0.0 for g in games_used])
        y_over = np.array([
            1.0 if g.get("total_went_over") is True else
            0.0 if g.get("total_went_over") is False else float("nan")
            for g in games_used
        ])
        valid_over = ~np.isnan(y_over)
        y_over_clean = y_over[valid_over]
        diffs_over   = diffs_arr[valid_over]

        r_win, p_win = pearsonr(diffs_arr, y_win)
        r_over_val   = float("nan")
        if len(y_over_clean) >= 200:
            r_over_val, _ = pearsonr(diffs_over, y_over_clean)

        # LOYO
        diff_map = {id(g): d for g, d in pairs}
        loyo = _loyo_roi(
            games_used,
            diff_fn=lambda g: diff_map.get(id(g)),
        )

        r_ok     = abs(r_win) >= 0.05 and p_win < 0.01
        ml_pass  = loyo["ml_pass_count"]
        und_pass = loyo["under_pass_count"]

        if r_ok and (ml_pass >= 3 or und_pass >= 3):
            verdict = "PASS"
        elif r_ok and (ml_pass >= 2 or und_pass >= 2):
            verdict = "MARGINAL"
        else:
            verdict = "FAIL"

        r_over_str = f"{r_over_val:>+8.4f}" if not math.isnan(r_over_val) else "       N/A"
        print(f"  {stat:<25} {len(pairs):>6}  {r_win:>+9.4f}  {p_win:>10.5f}  {r_over_str}  {ml_pass}/4    {und_pass}/4      {verdict}")
        results[stat] = {
            "n": len(pairs), "r_win": round(float(r_win), 4), "p_win": round(float(p_win), 6),
            "r_over": round(float(r_over_val), 4) if not math.isnan(r_over_val) else None,
            "ml_loyo": ml_pass, "under_loyo": und_pass, "verdict": verdict,
            "loyo_by_year": {yr: v for yr, v in loyo["by_year"].items() if v},
        }

    # LOYO detail for PASS/MARGINAL stats
    passing = [s for s in PITCHER_UNUSED if results.get(s, {}).get("verdict") in ("PASS", "MARGINAL")]
    if passing:
        print(f"\n  LOYO detail for promising pitcher stats ({len(passing)} stats):")
        for stat in passing:
            res = results[stat]
            print(f"\n    {stat}  (r={res['r_win']:+.4f}, verdict={res['verdict']})")
            print(f"    {'Year':<8}  {'Q4 home ML':>12}  {'Q1 away ML':>12}  {'Combined':>10}  {'Q4 UNDER':>10}")
            print(f"    " + "-" * 58)
            for yr in [2022, 2023, 2024, 2025]:
                yd = res["loyo_by_year"].get(yr)
                if yd:
                    q4s = f"{yd['q4_ml_roi']:>+10.2f}%" if yd.get("q4_ml_roi") is not None else "        N/A"
                    q1s = f"{yd['q1_ml_roi']:>+10.2f}%" if yd.get("q1_ml_roi") is not None else "        N/A"
                    cms = f"{yd['combined_roi']:>+8.2f}%" if yd.get("combined_roi") is not None else "      N/A"
                    uns = f"{yd['q4_under_roi']:>+8.2f}%" if yd.get("q4_under_roi") is not None else "      N/A"
                    print(f"    {yr:<8}  {q4s}  {q1s}  {cms}  {uns}")
                else:
                    print(f"    {yr:<8}  (no data)")

    return results


# ── Track A: Stored-but-unused batter stats ───────────────────────────────────

def track_a_batters(bt: list[dict], caches: dict[int, dict], lineups: dict) -> dict:
    print("\n" + "-" * 70)
    print("  TRACK A — BATTERS: STORED-BUT-UNUSED STATS (2024-2025)")
    print("-" * 70)
    print(f"  Stats: {', '.join(BATTER_UNUSED)}\n")

    if not lineups:
        print("  No lineup data found — skipping batter regression.")
        return {}

    # Build per-game diffs for each batter stat
    stat_diffs: dict[str, list[tuple[dict, float]]] = {s: [] for s in BATTER_UNUSED}
    skipped = 0

    for g in bt:
        if g.get("season") not in LINEUP_SEASONS:
            continue
        cache = caches.get(g["season"])
        if cache is None:
            continue
        lu = lineups.get(g.get("gamePk"))
        if lu is None:
            skipped += 1
            continue
        home_ids, away_ids = lu
        for stat in BATTER_UNUSED:
            h = _team_batter_avg(home_ids, cache, stat)
            a = _team_batter_avg(away_ids, cache, stat)
            if h is not None and a is not None:
                stat_diffs[stat].append((g, h - a))

    total_2024_25 = sum(1 for g in bt if g.get("season") in LINEUP_SEASONS)
    best_n = max((len(v) for v in stat_diffs.values()), default=0)
    print(f"  2024-2025 games: {total_2024_25:,}  |  best coverage: {best_n:,}")
    print()

    print(f"  {'Stat':<25} {'n':>6}  {'r vs win':>9}  {'p-val':>10}  {'r vs over':>10}  Verdict")
    print("  " + "-" * 72)

    results: dict[str, dict] = {}
    for stat in BATTER_UNUSED:
        pairs = stat_diffs[stat]
        if len(pairs) < 100:
            print(f"  {stat:<25} {len(pairs):>6}  SKIP — insufficient data")
            results[stat] = {"verdict": "SKIP", "n": len(pairs)}
            continue

        games_used = [g for g, _ in pairs]
        diffs_arr  = np.array([d for _, d in pairs], dtype=float)
        y_win  = np.array([1.0 if g["actual_winner"] == "home" else 0.0 for g in games_used])
        y_over_raw = np.array([
            1.0 if g.get("total_went_over") is True else
            0.0 if g.get("total_went_over") is False else float("nan")
            for g in games_used
        ])
        valid_over = ~np.isnan(y_over_raw)
        r_win, p_win = pearsonr(diffs_arr, y_win)
        r_over_val = float("nan")
        if valid_over.sum() >= 100:
            r_over_val, _ = pearsonr(diffs_arr[valid_over], y_over_raw[valid_over])

        # Only 2024-2025 — no LOYO possible (1-2 seasons)
        r_ok = abs(r_win) >= 0.05 and p_win < 0.01
        verdict = "CANDIDATE (no LOYO)" if r_ok else "FAIL"
        r_over_str = f"{r_over_val:>+8.4f}" if not math.isnan(r_over_val) else "       N/A"
        print(f"  {stat:<25} {len(pairs):>6}  {r_win:>+9.4f}  {p_win:>10.5f}  {r_over_str}  {verdict}")
        results[stat] = {
            "n": len(pairs), "r_win": round(float(r_win), 4), "p_win": round(float(p_win), 6),
            "r_over": round(float(r_over_val), 4) if not math.isnan(r_over_val) else None,
            "verdict": verdict,
        }

    print()
    print("  Note: Batter stats use 2024-2025 only (game_lineups.parquet). No LOYO possible.")
    print("  Stats labeled CANDIDATE require 2022-2023 backfill before full LOYO can run.")
    return results


# ── Track B: New fetchable stats ──────────────────────────────────────────────

def _fetch_savant_pitcher_leaderboard(season: int) -> pd.DataFrame:
    url = (
        f"{SAVANT_BASE}/leaderboard/statcast"
        f"?type=pitcher&year={season}&position=&team=&min=10&csv=true"
    )
    try:
        resp = requests.get(url, headers=_REQUEST_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        return df
    except Exception as exc:
        print(f"  WARNING: Could not fetch Savant leaderboard for {season}: {exc}")
        return pd.DataFrame()


def _extract_avg_fb_mph(df: pd.DataFrame, mlbam_id: int) -> float | None:
    if df.empty:
        return None
    id_col = None
    for c in ("player_id", "pitcher", "pitcher_id", "mlbam_id"):
        if c in df.columns:
            id_col = c
            break
    if id_col is None:
        return None
    row = df[df[id_col] == mlbam_id]
    if row.empty:
        return None
    r = row.iloc[0]
    for col in AVG_FB_MPH_COLS:
        if col in r.index:
            try:
                v = float(r[col])
                if not math.isnan(v) and 70 <= v <= 105:  # sanity check
                    return v
            except (TypeError, ValueError):
                pass
    return None


def _fetch_fg_pitching_gb(season: int) -> dict[int, float]:
    """Fetch FanGraphs pitching stats and return {fangraphs_id: gb_pct}."""
    try:
        from pybaseball import pitching_stats
        df = pitching_stats(season, qual=1)
        if df is None or df.empty:
            return {}
        # GB% column may be 'GB%' or 'GB'
        gb_col = None
        for c in ("GB%", "GB", "gb_pct"):
            if c in df.columns:
                gb_col = c
                break
        if gb_col is None:
            return {}
        result = {}
        for _, row in df.iterrows():
            fg_id = row.get("IDfg")
            gb = row.get(gb_col)
            if fg_id is not None and gb is not None:
                try:
                    v = float(gb)
                    if not math.isnan(v):
                        result[int(fg_id)] = v / 100.0 if v > 1.0 else v  # normalize to 0-1
                except (TypeError, ValueError):
                    pass
        return result
    except Exception as exc:
        print(f"  WARNING: Could not fetch FanGraphs pitching for {season}: {exc}")
        return {}


def _build_fg_crosswalk(mlbam_ids: list[int]) -> dict[int, int]:
    """Return {mlbam_id: fangraphs_id} for given MLBAM IDs."""
    try:
        from pybaseball import playerid_reverse_lookup
        df = playerid_reverse_lookup(list(set(mlbam_ids)), key_type="mlbam")
        if df is None or df.empty:
            return {}
        result = {}
        for _, row in df.iterrows():
            mlbam = row.get("key_mlbam")
            fg    = row.get("key_fangraphs")
            if mlbam and fg and str(fg) not in ("", "nan"):
                try:
                    result[int(mlbam)] = int(fg)
                except (TypeError, ValueError):
                    pass
        return result
    except Exception as exc:
        print(f"  WARNING: Could not build FG crosswalk: {exc}")
        return {}


def track_b_new_stats(bt: list[dict]) -> dict:
    print("\n" + "-" * 70)
    print("  TRACK B — NEW FETCHABLE STATS")
    print("-" * 70)

    years = [2022, 2023, 2024, 2025]

    # ── Collect all SP IDs for crosswalk ─────────────────────────────────
    all_sp_ids: set[int] = set()
    for g in bt:
        if g.get("home_sp_id"):
            all_sp_ids.add(int(g["home_sp_id"]))
        if g.get("away_sp_id"):
            all_sp_ids.add(int(g["away_sp_id"]))
    print(f"  Unique SP IDs across 2022-2025: {len(all_sp_ids):,}")

    # ── Fetch Savant pitcher leaderboards (velocity) ──────────────────────
    print("\n  [1/3] Fetching Savant pitcher leaderboards for avg_fb_mph...")
    savant_by_season: dict[int, pd.DataFrame] = {}
    fb_mph_col_found: str | None = None
    for yr in years:
        print(f"    Fetching {yr}...", end="", flush=True)
        df = _fetch_savant_pitcher_leaderboard(yr)
        savant_by_season[yr] = df
        if not df.empty and fb_mph_col_found is None:
            for c in AVG_FB_MPH_COLS:
                if c in df.columns:
                    fb_mph_col_found = c
                    break
        print(f" {len(df):,} rows" if not df.empty else " FAILED")

    if fb_mph_col_found:
        print(f"    avg_fb_mph column found: '{fb_mph_col_found}'")
    else:
        print(f"    No velocity column found in Savant leaderboard.")
        print(f"    Available columns (from last season):")
        last_df = next((df for df in reversed(list(savant_by_season.values())) if not df.empty), pd.DataFrame())
        if not last_df.empty:
            print(f"      {list(last_df.columns)}")

    # ── Build per-SP per-season velocity dict ─────────────────────────────
    velo_by_season: dict[int, dict[int, float]] = {}
    for yr, df in savant_by_season.items():
        if df.empty:
            continue
        id_col = next((c for c in ("player_id", "pitcher", "pitcher_id") if c in df.columns), None)
        if id_col is None:
            continue
        if fb_mph_col_found and fb_mph_col_found in df.columns:
            velo_by_season[yr] = {}
            for _, row in df.iterrows():
                try:
                    pid = int(row[id_col])
                    v   = float(row[fb_mph_col_found])
                    if not math.isnan(v) and 70 <= v <= 105:
                        velo_by_season[yr][pid] = v
                except (TypeError, ValueError):
                    pass

    # ── Fetch FanGraphs GB% ──────────────────────────────────────────────
    print("\n  [2/3] Fetching FanGraphs GB% via pybaseball...")
    print("  Building ID crosswalk...", end="", flush=True)
    crosswalk = _build_fg_crosswalk(list(all_sp_ids))
    print(f" {len(crosswalk):,} / {len(all_sp_ids):,} matched")

    gb_by_season: dict[int, dict[int, float]] = {}  # {season: {mlbam_id: gb_pct}}
    for yr in years:
        print(f"    Fetching FanGraphs pitching {yr}...", end="", flush=True)
        fg_gb = _fetch_fg_pitching_gb(yr)  # {fangraphs_id: gb_pct}
        # Convert back to mlbam_id
        mlbam_gb: dict[int, float] = {}
        for mlbam, fg_id in crosswalk.items():
            if fg_id in fg_gb:
                mlbam_gb[mlbam] = fg_gb[fg_id]
        gb_by_season[yr] = mlbam_gb
        print(f" {len(mlbam_gb):,} SPs with GB%")

    # ── Build stat diffs per game ─────────────────────────────────────────
    print("\n  [3/3] Building game-level stat differentials...")
    velo_diffs:      list[tuple[dict, float]] = []
    velo_yoy_diffs:  list[tuple[dict, float]] = []  # YoY decline
    gb_diffs:        list[tuple[dict, float]] = []

    for g in bt:
        season = g.get("season")
        hsp = g.get("home_sp_id")
        asp = g.get("away_sp_id")
        if not hsp or not asp:
            continue
        hsp, asp = int(hsp), int(asp)

        # Raw velocity diff
        if season in velo_by_season:
            h_v = velo_by_season[season].get(hsp)
            a_v = velo_by_season[season].get(asp)
            if h_v is not None and a_v is not None:
                velo_diffs.append((g, h_v - a_v))

        # YoY velocity decline diff (positive = home SP declined more than away SP)
        # We want NEGATIVE sign: home pitcher lost more velocity = bad signal
        prior_yr = season - 1
        if season in velo_by_season and prior_yr in velo_by_season:
            h_cur  = velo_by_season[season].get(hsp)
            h_prev = velo_by_season[prior_yr].get(hsp)
            a_cur  = velo_by_season[season].get(asp)
            a_prev = velo_by_season[prior_yr].get(asp)
            if all(v is not None for v in (h_cur, h_prev, a_cur, a_prev)):
                h_decline = h_cur - h_prev   # negative = lost velocity
                a_decline = a_cur - a_prev
                # home_advantage_velo_delta: positive = home pitcher's velocity held up better
                velo_yoy_diffs.append((g, h_decline - a_decline))

        # GB% diff
        if season in gb_by_season:
            h_gb = gb_by_season[season].get(hsp)
            a_gb = gb_by_season[season].get(asp)
            if h_gb is not None and a_gb is not None:
                gb_diffs.append((g, h_gb - a_gb))

    print(f"  avg_fb_mph diffs:   {len(velo_diffs):,} games")
    print(f"  velo_decline diffs: {len(velo_yoy_diffs):,} games")
    print(f"  gb_pct diffs:       {len(gb_diffs):,} games")

    # ── Regression tests ─────────────────────────────────────────────────
    print(f"\n  {'Stat':<30} {'n':>6}  {'r vs win':>9}  {'p-val':>10}  {'r vs over':>10}  {'ML LOYO':>8}  {'UND LOYO':>9}  Verdict")
    print("  " + "-" * 104)

    results: dict[str, dict] = {}
    for label, pairs in [
        ("avg_fb_mph_diff",     velo_diffs),
        ("velo_decline_diff",   velo_yoy_diffs),
        ("gb_pct_diff",         gb_diffs),
    ]:
        if len(pairs) < 200:
            print(f"  {label:<30} {len(pairs):>6}  SKIP — insufficient data")
            results[label] = {"verdict": "SKIP", "n": len(pairs)}
            continue

        games_used = [g for g, _ in pairs]
        diffs_arr  = np.array([d for _, d in pairs], dtype=float)
        y_win  = np.array([1.0 if g["actual_winner"] == "home" else 0.0 for g in games_used])
        y_over_raw = np.array([
            1.0 if g.get("total_went_over") is True else
            0.0 if g.get("total_went_over") is False else float("nan")
            for g in games_used
        ])
        valid_over = ~np.isnan(y_over_raw)
        r_win, p_win = pearsonr(diffs_arr, y_win)
        r_over_val = float("nan")
        if valid_over.sum() >= 200:
            r_over_val, _ = pearsonr(diffs_arr[valid_over], y_over_raw[valid_over])

        diff_map = {id(g): d for g, d in pairs}
        loyo = _loyo_roi(games_used, diff_fn=lambda g: diff_map.get(id(g)))

        r_ok     = abs(r_win) >= 0.05 and p_win < 0.01
        ml_pass  = loyo["ml_pass_count"]
        und_pass = loyo["under_pass_count"]

        if r_ok and (ml_pass >= 3 or und_pass >= 3):
            verdict = "PASS"
        elif r_ok and (ml_pass >= 2 or und_pass >= 2):
            verdict = "MARGINAL"
        else:
            verdict = "FAIL"

        r_over_str = f"{r_over_val:>+8.4f}" if not math.isnan(r_over_val) else "       N/A"
        print(f"  {label:<30} {len(pairs):>6}  {r_win:>+9.4f}  {p_win:>10.5f}  {r_over_str}  {ml_pass}/4    {und_pass}/4      {verdict}")
        results[label] = {
            "n": len(pairs), "r_win": round(float(r_win), 4), "p_win": round(float(p_win), 6),
            "r_over": round(float(r_over_val), 4) if not math.isnan(r_over_val) else None,
            "ml_loyo": ml_pass, "under_loyo": und_pass, "verdict": verdict,
            "loyo_by_year": {yr: v for yr, v in loyo["by_year"].items() if v},
        }

    # Print LOYO detail for any passing Track B stats
    b_passing = [s for s, v in results.items() if v.get("verdict") in ("PASS", "MARGINAL")]
    if b_passing:
        print(f"\n  LOYO detail for promising Track B stats ({len(b_passing)} stats):")
        for stat in b_passing:
            res = results[stat]
            print(f"\n    {stat}  (r={res['r_win']:+.4f}, verdict={res['verdict']})")
            print(f"    {'Year':<8}  {'Q4 home ML':>12}  {'Q1 away ML':>12}  {'Combined':>10}  {'Q4 UNDER':>10}")
            print(f"    " + "-" * 58)
            for yr in [2022, 2023, 2024, 2025]:
                yd = res["loyo_by_year"].get(yr)
                if yd:
                    q4s = f"{yd['q4_ml_roi']:>+10.2f}%" if yd.get("q4_ml_roi") is not None else "        N/A"
                    q1s = f"{yd['q1_ml_roi']:>+10.2f}%" if yd.get("q1_ml_roi") is not None else "        N/A"
                    cms = f"{yd['combined_roi']:>+8.2f}%" if yd.get("combined_roi") is not None else "      N/A"
                    uns = f"{yd['q4_under_roi']:>+8.2f}%" if yd.get("q4_under_roi") is not None else "      N/A"
                    print(f"    {yr:<8}  {q4s}  {q1s}  {cms}  {uns}")
                else:
                    print(f"    {yr:<8}  (no data)")

    # Note on velocity column if not found
    if not fb_mph_col_found:
        print("\n  NOTE: avg_fb_mph not found in Savant pitcher leaderboard CSV.")
        print("  The velocity signal may require a different Savant endpoint.")
        print("  Try: https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats")

    return results


# ── Interaction test (top PASS stats × model_edge_ml) ────────────────────────

def interaction_test(bt: list[dict], caches: dict[int, dict], pass_stats: list[str]) -> dict:
    if not pass_stats:
        print("\n  No passing stats — skipping interaction test.")
        return {}

    print("\n" + "-" * 70)
    print("  INTERACTION: Top PASS stats × model_edge_ml bucket")
    print("-" * 70)

    edge_buckets = [
        ("away_edge",  lambda g: g["model_edge_ml"] <= -0.05),
        ("neutral",    lambda g: -0.05 < g["model_edge_ml"] < 0.05),
        ("home_edge",  lambda g: g["model_edge_ml"] >= 0.05),
    ]

    grid_results: dict[str, dict] = {}
    for stat in pass_stats[:3]:  # limit to top 3 passing stats
        print(f"\n  {stat} × model_edge_ml:")
        print(f"    {'edge_bucket':<14}  {'n':>5}  {'Win%':>6}  {'ROI':>8}  Strong?")
        print("    " + "-" * 48)
        for bname, bfn in edge_buckets:
            # Build diff for this stat
            pairs = []
            for g in bt:
                cache = caches.get(g.get("season"))
                if cache is None or not bfn(g):
                    continue
                hsp = g.get("home_sp_id")
                asp = g.get("away_sp_id")
                if not hsp or not asp:
                    continue
                h = _get_stat(cache, hsp, stat)
                a = _get_stat(cache, asp, stat)
                if h is not None and a is not None:
                    pairs.append((g, h - a))

            if not pairs:
                continue
            # Q4 games (home SP better) → bet home
            diffs = [d for _, d in pairs]
            q75 = np.percentile(diffs, 75)
            q4_games = [g for g, d in pairs if d >= q75]
            r = _ml_roi(q4_games, lambda g: "home")
            strong = " *" if r["n"] >= 80 and r.get("roi_pct") is not None and r["roi_pct"] >= 5.0 else ""
            key = f"{stat}x{bname}"
            grid_results[key] = {"n": r["n"], "roi": r["roi_pct"]}
            win_s = f"{r['win_pct']:.1f}%" if r["win_pct"] is not None else "  N/A"
            roi_s = f"{r['roi_pct']:>+7.2f}%" if r["roi_pct"] is not None else "      N/A"
            print(f"    {bname:<14}  {r['n']:>5}  {win_s}  {roi_s}{strong}")

    return grid_results


# ── Summary + recommendation ──────────────────────────────────────────────────

def print_summary(a_pitch: dict, a_bat: dict, b_stats: dict) -> list[str]:
    print("\n" + "=" * 70)
    print("  SUMMARY — PASS/FAIL/MARGINAL")
    print("=" * 70)

    all_pass: list[str] = []
    all_marginal: list[str] = []
    all_fail: list[str] = []

    print("\n  Track A — Pitcher stats:")
    for stat, res in a_pitch.items():
        v = res.get("verdict", "SKIP")
        print(f"    {stat:<30} {v}")
        if v == "PASS":
            all_pass.append(stat)
        elif v == "MARGINAL":
            all_marginal.append(stat)
        else:
            all_fail.append(stat)

    print("\n  Track A — Batter stats (2024-2025 only, no LOYO):")
    for stat, res in a_bat.items():
        v = res.get("verdict", "SKIP")
        print(f"    {stat:<30} {v}")
        if "CANDIDATE" in v:
            all_marginal.append(stat)
        elif v == "PASS":
            all_pass.append(stat)
        else:
            all_fail.append(stat)

    print("\n  Track B — New fetchable stats:")
    for stat, res in b_stats.items():
        v = res.get("verdict", "SKIP")
        print(f"    {stat:<30} {v}")
        if v == "PASS":
            all_pass.append(stat)
        elif v == "MARGINAL":
            all_marginal.append(stat)
        else:
            all_fail.append(stat)

    print(f"\n  {'='*50}")
    if all_pass:
        print(f"  CLEARED FOR MODEL INTEGRATION ({len(all_pass)}):")
        for s in all_pass:
            print(f"    + {s}")
    else:
        print(f"  No stats cleared the full pass criteria.")
    if all_marginal:
        print(f"\n  MARGINAL — worth monitoring ({len(all_marginal)}):")
        for s in all_marginal:
            print(f"    ~ {s}")

    print(f"\n  NEXT STEP:")
    if all_pass:
        print(f"  Review LOYO detail above, confirm findings look sensible,")
        print(f"  then approve for model integration in scorer.py + statcast.py.")
    else:
        print(f"  No new stats exceed the 95% confidence bar.")
        print(f"  Existing model signals (model_edge_ml, pitcher_score) remain primary.")

    return all_pass


# ── Main ──────────────────────────────────────────────────────────────────────

def run(track: str = "both", out: str | None = None) -> None:
    print("\n" + "=" * 70)
    print("  MLB EDGE — NEW STATS REGRESSION ANALYSIS")
    print("  Track A: stored-but-unused  |  Track B: new fetchable stats")
    print("=" * 70 + "\n")

    print("Loading data...")
    bt      = load_backtest_priced()
    caches  = load_player_caches()
    lineups = load_game_lineups() if track in ("a", "both") else {}
    print(f"  Backtest 2022-2025: {len(bt):,} games")
    print(f"  Player caches: seasons {sorted(caches.keys())}")
    print(f"  Lineup data: {len(lineups):,} games (2024-2025)")

    a_pitch_results: dict = {}
    a_bat_results:   dict = {}
    b_results:       dict = {}

    if track in ("a", "both"):
        a_pitch_results = track_a_pitchers(bt, caches)
        a_bat_results   = track_a_batters(bt, caches, lineups)

    if track in ("b", "both"):
        b_results = track_b_new_stats(bt)

    # Collect all passing pitcher stats for interaction test
    pass_pitcher = [
        s for s, r in {**a_pitch_results, **b_results}.items()
        if r.get("verdict") == "PASS"
    ]
    if pass_pitcher:
        grid = interaction_test(bt, caches, pass_pitcher)
    else:
        grid = {}
        print("\n  Interaction test: skipped (no PASS stats)")

    passing = print_summary(a_pitch_results, a_bat_results, b_results)

    if out:
        report = {
            "track_a": {
                "pitcher": a_pitch_results,
                "batter":  a_bat_results,
            },
            "track_b":      b_results,
            "interactions": grid,
            "recommendations": {
                "cleared_for_integration": passing,
            },
        }
        Path(out).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\n  Results written to {out}")

    print("\n" + "=" * 70 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Regression analysis for new MLB prediction stats")
    parser.add_argument("--track", choices=["a", "b", "both"], default="both",
                        help="Which track to run: a (stored stats), b (new fetches), both (default)")
    parser.add_argument("--out", default=None, help="Optional path to write JSON results")
    args = parser.parse_args()
    run(track=args.track, out=args.out)
