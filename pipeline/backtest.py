"""Score all finished MLB games across multiple seasons and write docs/backtest.json.

Accepts small lookahead bias (current-season Savant stats used for earlier games within
that season) — acceptable for signal-quality measurement, not production betting.
Each season's games are scored with that season's Savant pitcher stats.

Usage:
    python -m pipeline.backtest
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from pipeline.comps import load_comps_db, build_game_profile
from pipeline.odds import american_to_decimal
from pipeline.predictor import _pitcher_score, _predicted_runs, _win_probability, LEAGUE_AVG_RUNS

log = logging.getLogger(__name__)

MLB_API = "https://statsapi.mlb.com/api/v1"
TIMEOUT = 30
SEASONS = [2026]

DOCS_DIR     = Path(__file__).parent.parent / "docs"
SEASONS_DIR  = Path(__file__).parent.parent / "data" / "seasons"
OUTPUT_PATH  = DOCS_DIR / "backtest.json"


# ---------------------------------------------------------------------------
# Schedule fetch
# ---------------------------------------------------------------------------

def fetch_season_finished_games(season: int) -> list[dict]:
    """Fetch all finished regular-season games for the given season."""
    start = f"03/01/{season}"
    # For past seasons use end of October; for current season use yesterday
    current_year = date.today().year
    if season < current_year:
        end = f"10/31/{season}"
    else:
        end = (date.today() - timedelta(days=1)).strftime("%m/%d/%Y")

    url = f"{MLB_API}/schedule"
    params = {
        "sportId": 1,
        "startDate": start,
        "endDate":   end,
        "hydrate": "probablePitcher,linescore",
        "gameType": "R",   # Regular season only
    }
    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("Season schedule fetch failed: %s", exc)
        return []

    games: list[dict] = []
    for day in data.get("dates", []):
        for raw in day.get("games", []):
            parsed = _parse_finished_game(raw)
            if parsed:
                games.append(parsed)

    log.info("Fetched %d finished games for %d season", len(games), season)
    return games


def _parse_finished_game(raw: dict) -> Optional[dict]:
    status = raw.get("status", {})
    if status.get("abstractGameState") != "Final":
        return None

    home = raw.get("teams", {}).get("home", {})
    away = raw.get("teams", {}).get("away", {})
    home_sp = home.get("probablePitcher")
    away_sp = away.get("probablePitcher")
    if not home_sp or not away_sp:
        return None

    ls = raw.get("linescore", {})
    ls_teams = ls.get("teams", {})
    home_score = ls_teams.get("home", {}).get("runs")
    away_score = ls_teams.get("away", {}).get("runs")
    if home_score is None or away_score is None:
        return None

    game_date = raw.get("gameDate", "")[:10]

    return {
        "gamePk":       raw["gamePk"],
        "date":         game_date,
        "home_team":    home.get("team", {}).get("name", "Unknown"),
        "away_team":    away.get("team", {}).get("name", "Unknown"),
        "venue":        raw.get("venue", {}).get("name", "Unknown"),
        "home_sp_id":   home_sp["id"],
        "home_sp_name": home_sp.get("fullName", ""),
        "away_sp_id":   away_sp["id"],
        "away_sp_name": away_sp.get("fullName", ""),
        "home_score":   int(home_score),
        "away_score":   int(away_score),
    }


# ---------------------------------------------------------------------------
# Pitcher cache
# ---------------------------------------------------------------------------

def load_historical_pitcher_cache(sp_ids: set[int], season: int) -> dict[int, dict]:
    """Load prior-season player cache for lookahead-safe historical backtesting.

    For a game in season N, uses stats from season N-1's player_cache.pkl.
    Rookies not in the prior cache get an empty dict (→ neutral 0.5 score).
    Falls back to current-season Savant fetch if prior cache is missing.
    """
    prior = season - 1
    cache_path = SEASONS_DIR / str(prior) / "player_cache.pkl"
    if cache_path.exists():
        import pickle
        log.info("Loading prior-season cache: %s (for %d games)", cache_path, season)
        with open(cache_path, "rb") as f:
            full_cache: dict = pickle.load(f)
        return {pid: full_cache.get(pid, {}) for pid in sp_ids}
    log.warning(
        "Prior-season cache for %d not found at %s — "
        "falling back to current-season Savant (introduces lookahead bias)",
        prior, cache_path,
    )
    return build_pitcher_cache(sp_ids, season)


def load_closing_lines(season: int) -> dict[int, dict]:
    """Load closing_lines.parquet for a season, keyed by game_pk. Returns {} if missing."""
    lines_path = SEASONS_DIR / str(season) / "closing_lines.parquet"
    if not lines_path.exists():
        return {}
    try:
        df = pd.read_parquet(lines_path)
        result = {}
        for _, row in df.iterrows():
            pk = int(row["game_pk"]) if pd.notna(row.get("game_pk")) else None
            if pk is not None:
                result[pk] = row.to_dict()
        log.info("Closing lines %d: %d games loaded, %d with ML",
                 season, len(result),
                 sum(1 for r in result.values() if pd.notna(r.get("home_ml"))))
        return result
    except Exception as exc:
        log.warning("Could not load closing_lines.parquet for %d: %s", season, exc)
        return {}


def build_pitcher_cache(sp_ids: set[int], season: int) -> dict[int, dict]:
    """Build a pitcher-only cache using current-season Savant data."""
    from pipeline.statcast import (
        _fetch_savant_pitcher_stats,
        _fetch_savant_pitcher_leaderboard,
        _merge_savant_pitcher,
        _merge_savant_pitcher_leaderboard,
    )

    log.info("Fetching Savant pitcher data for %d unique starters...", len(sp_ids))
    sav_pitch = _fetch_savant_pitcher_stats(season)
    sav_lead  = _fetch_savant_pitcher_leaderboard(season)

    cache: dict[int, dict] = {}
    for mlbam_id in sp_ids:
        entry: dict = {"mlbam_id": mlbam_id, "role": "pitcher"}
        _merge_savant_pitcher(entry, sav_pitch, mlbam_id)
        _merge_savant_pitcher_leaderboard(entry, sav_lead, mlbam_id)
        cache[mlbam_id] = entry

    found = sum(1 for e in cache.values() if e.get("xera") or e.get("xfip"))
    log.info("Pitcher cache: %d/%d pitchers have xERA/xFIP", found, len(sp_ids))
    return cache


# ---------------------------------------------------------------------------
# Game scoring
# ---------------------------------------------------------------------------

def score_game(
    game: dict,
    pitcher_cache: dict[int, dict],
    comps_db: list[dict],
    odds_row: Optional[dict] = None,
) -> dict:
    """Score a historical finished game and return a graded result dict."""
    from pipeline.park_factors import get_run_factor

    home_sp = pitcher_cache.get(game["home_sp_id"], {})
    away_sp = pitcher_cache.get(game["away_sp_id"], {})

    home_pitcher_score = _pitcher_score(home_sp)
    away_pitcher_score = _pitcher_score(away_sp)

    # Use neutral lineup score (0.5) — no historical lineup data in V1
    home_lineup_score = 0.5
    away_lineup_score = 0.5

    try:
        park_run_factor = float(get_run_factor(game.get("venue", "")))
    except Exception:
        park_run_factor = 100.0

    # Comps-based win rate
    comps_home_win_rate: Optional[float] = None
    if comps_db:
        fake_game = {
            "home_sp_id": game["home_sp_id"],
            "away_sp_id": game["away_sp_id"],
            "home_lineup": [],
            "away_lineup": [],
            "venue": game.get("venue", ""),
        }
        profile = build_game_profile(fake_game, pitcher_cache)
        if profile:
            from pipeline.comps import find_similar_games
            similar = find_similar_games(profile, comps_db, n=30)
            if similar:
                comps_home_win_rate = round(
                    sum(1 for g in similar if g["home_won"]) / len(similar), 4
                )

    park_mod = (park_run_factor - 100) / 1000
    home_win_pct, away_win_pct = _win_probability(
        home_pitcher_score, away_pitcher_score,
        home_lineup_score, away_lineup_score,
        comps_home_win_rate, park_mod, 0.0,
    )
    pred_home, pred_away = _predicted_runs(
        home_lineup_score, away_lineup_score,
        home_pitcher_score, away_pitcher_score,
        park_run_factor, 0.0,
    )

    actual_home = game["home_score"]
    actual_away = game["away_score"]
    actual_winner = "home" if actual_home > actual_away else "away" if actual_away > actual_home else "tie"
    predicted_winner = "home" if home_win_pct > away_win_pct else "away"
    correct = predicted_winner == actual_winner and actual_winner != "tie"

    result = {
        "date":              game["date"],
        "gamePk":            game["gamePk"],
        "home_team":         game["home_team"],
        "away_team":         game["away_team"],
        "home_win_pct":      home_win_pct,
        "away_win_pct":      away_win_pct,
        "predicted_winner":  predicted_winner,
        "actual_winner":     actual_winner,
        "home_score":        actual_home,
        "away_score":        actual_away,
        "predicted_total":   round(pred_home + pred_away, 1),
        "actual_total":      actual_home + actual_away,
        "pitcher_score_home": round(home_pitcher_score, 3),
        "pitcher_score_away": round(away_pitcher_score, 3),
        "comps_home_win_rate": comps_home_win_rate,
        "correct":           correct,
    }

    # Attach Vegas closing line data when available
    if odds_row:
        hml  = odds_row.get("home_ml")
        aml  = odds_row.get("away_ml")
        himp = odds_row.get("home_implied_prob")
        ct   = odds_row.get("closing_total")
        if pd.notna(hml) and pd.notna(himp):
            model_edge = round(home_win_pct - float(himp), 4)
            bet_home   = home_win_pct >= away_win_pct
            bet_won    = (bet_home and actual_winner == "home") or \
                         (not bet_home and actual_winner == "away")
            result.update({
                "home_ml":           int(hml) if pd.notna(hml) else None,
                "away_ml":           int(aml) if pd.notna(aml) else None,
                "closing_total":     float(ct) if pd.notna(ct) else None,
                "home_implied_prob": round(float(himp), 4),
                "model_edge_ml":     model_edge,
                "bet_side":          "home" if bet_home else "away",
                "bet_won":           bet_won,
            })

    return result


# ---------------------------------------------------------------------------
# Aggregated stats
# ---------------------------------------------------------------------------

def compute_stats(results: list[dict]) -> dict:
    decided = [r for r in results if r["actual_winner"] != "tie"]
    n = len(decided)
    if n == 0:
        return {}

    correct = sum(1 for r in decided if r["correct"])
    win_pct = round(correct / n, 4)

    # Win% by confidence tier (based on predicted winner's probability)
    tiers = {
        "50_55": (0.50, 0.55),
        "55_60": (0.55, 0.60),
        "60_65": (0.60, 0.65),
        "65_plus": (0.65, 1.00),
    }
    win_pct_by_confidence = {}
    for tier, (lo, hi) in tiers.items():
        bucket = [
            r for r in decided
            if lo <= max(r["home_win_pct"], r["away_win_pct"]) < hi
        ]
        if hi == 1.00:
            bucket = [r for r in decided if max(r["home_win_pct"], r["away_win_pct"]) >= lo]
        bn = len(bucket)
        bc = sum(1 for r in bucket if r["correct"])
        win_pct_by_confidence[tier] = {
            "total":   bn,
            "correct": bc,
            "pct":     round(bc / bn, 4) if bn > 0 else None,
        }

    # Run total accuracy
    totals_valid = [r for r in results if r.get("predicted_total") and r.get("actual_total") is not None]
    total_mae  = round(sum(abs(r["predicted_total"] - r["actual_total"]) for r in totals_valid) / len(totals_valid), 3) if totals_valid else None
    total_bias = round(sum(r["predicted_total"] - r["actual_total"] for r in totals_valid) / len(totals_valid), 3) if totals_valid else None

    # Run total directional accuracy (vs. league-average threshold)
    league_avg_total = LEAGUE_AVG_RUNS * 2
    totals_dir_valid = [
        r for r in results
        if r.get("predicted_total") is not None and r.get("actual_total") is not None
    ]
    totals_dir_correct = sum(
        1 for r in totals_dir_valid
        if (r["predicted_total"] >= league_avg_total) == (r["actual_total"] >= league_avg_total)
    )
    tdn = len(totals_dir_valid)
    totals_dir_acc = {
        "total":   tdn,
        "correct": totals_dir_correct,
        "pct":     round(totals_dir_correct / tdn, 4) if tdn > 0 else None,
    }

    # Signal accuracy: pitcher edge (when pitcher score diff >= 0.08)
    pitcher_signal = [
        r for r in decided
        if abs(r["pitcher_score_home"] - r["pitcher_score_away"]) >= 0.08
    ]
    pn = len(pitcher_signal)
    pc = sum(1 for r in pitcher_signal if r["correct"])
    pitcher_acc = {"total": pn, "correct": pc, "pct": round(pc / pn, 4) if pn > 0 else None}

    # Signal accuracy: comps (games where comps agreed with predicted winner direction)
    comps_agreed = [
        r for r in decided
        if r.get("comps_home_win_rate") is not None
        and ((r["comps_home_win_rate"] >= 0.5) == (r["predicted_winner"] == "home"))
    ]
    cn = len(comps_agreed)
    cc = sum(1 for r in comps_agreed if r["correct"])
    comps_acc = {"total": cn, "correct": cc, "pct": round(cc / cn, 4) if cn > 0 else None}

    return {
        "win_pct_overall":        win_pct,
        "total_correct":          correct,
        "total_decided":          n,
        "win_pct_by_confidence":  win_pct_by_confidence,
        "total_mae":              total_mae,
        "total_bias":             total_bias,
        "signal_accuracy": {
            "pitcher":       pitcher_acc,
            "comps":         comps_acc,
            "totals_dir":    totals_dir_acc,
        },
    }


# ---------------------------------------------------------------------------
# EV and calibration stats (requires closing lines)
# ---------------------------------------------------------------------------

def compute_ev_stats(results: list[dict]) -> dict:
    """Compute edge and calibration metrics for games that have Vegas closing lines."""
    lined = [r for r in results if r.get("home_implied_prob") is not None
             and r.get("actual_winner") not in (None, "tie")]
    if not lined:
        return {}

    n = len(lined)

    # Mean ML edge (model prob - closing implied prob)
    edges     = [r["model_edge_ml"] for r in lined]
    edge_mean = round(sum(edges) / n, 4)

    # Brier score: mean((model_prob - actual_outcome)^2)
    brier = round(
        sum((r["home_win_pct"] - (1.0 if r["actual_winner"] == "home" else 0.0)) ** 2
            for r in lined) / n,
        4,
    )

    # Win rate when model has positive edge >= 3%
    edge_bets = [r for r in lined if r.get("model_edge_ml", 0) >= 0.03]
    eb_won    = sum(1 for r in edge_bets if r.get("bet_won"))
    edge_win_rate = round(eb_won / max(len(edge_bets), 1), 4)

    # Calibration curve: model win prob buckets vs actual win rate
    bins = [
        ("0.45-0.50", 0.45, 0.50),
        ("0.50-0.55", 0.50, 0.55),
        ("0.55-0.60", 0.55, 0.60),
        ("0.60-0.65", 0.60, 0.65),
        ("0.65+",     0.65, 1.00),
    ]
    cal_curve = []
    for label, lo, hi in bins:
        bucket = [
            r for r in lined
            if lo <= max(r["home_win_pct"], 1 - r["home_win_pct"]) < hi
            or (hi == 1.00 and max(r["home_win_pct"], 1 - r["home_win_pct"]) >= lo)
        ]
        bn = len(bucket)
        if bn == 0:
            continue
        mp_mean = round(sum(max(r["home_win_pct"], 1 - r["home_win_pct"]) for r in bucket) / bn, 4)
        wr      = round(sum(1 for r in bucket if r["correct"]) / bn, 4)
        cal_curve.append({"bin": label, "n": bn, "model_prob_mean": mp_mean, "actual_win_rate": wr})

    # By edge bucket
    def _bucket(lo, hi, inclusive=False):
        subset = [r for r in lined if lo <= r.get("model_edge_ml", 0) < hi] \
                 if not inclusive else [r for r in lined if r.get("model_edge_ml", 0) >= lo]
        bn = len(subset)
        bw = sum(1 for r in subset if r.get("bet_won"))
        return {"n": bn, "win_rate": round(bw / bn, 4) if bn else None}

    by_edge = {
        "negative":  _bucket(-1.0, 0.0),
        "0_to_3pct": _bucket(0.00, 0.03),
        "3_to_6pct": _bucket(0.03, 0.06),
        "6pct_plus": _bucket(0.06, 1.00, inclusive=True),
    }

    return {
        "n_with_lines":       n,
        "ml_edge_mean":       edge_mean,
        "brier_score":        brier,
        "win_rate_when_edge": edge_win_rate,
        "n_edge_bets":        len(edge_bets),
        "calibration_curve":  cal_curve,
        "by_edge_bucket":     by_edge,
    }


# ---------------------------------------------------------------------------
# ROI tracking from live history records
# ---------------------------------------------------------------------------

def compute_roi_from_history() -> dict:
    """Compute ML and totals ROI from resolved history records that have Vegas lines stored.

    Only records with both a vegas_total/ML price AND actual results are included.
    1 unit risked per bet; payout = decimal_odds - 1 on win, -1 on loss.
    """
    from pathlib import Path as _Path
    import json as _json
    history_path = _Path(__file__).parent.parent / "docs" / "history.json"
    try:
        history = _json.loads(history_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    ml_bets = 0
    ml_units = 0.0
    total_bets = 0
    total_units = 0.0

    for r in history:
        if r.get("actual_winner") in (None, "tie"):
            continue
        if r.get("sp_scratched"):
            continue

        home_won = r["actual_winner"] == "home"
        predicted_home = r.get("predicted_winner") == "home"

        # Moneyline ROI
        home_ml = r.get("home_ml")
        away_ml = r.get("away_ml")
        if home_ml is not None and away_ml is not None:
            try:
                if predicted_home:
                    price = int(home_ml)
                    won   = home_won
                else:
                    price = int(away_ml)
                    won   = not home_won
                payout = american_to_decimal(price) - 1.0
                ml_units += payout if won else -1.0
                ml_bets  += 1
            except Exception:
                pass

        # Totals ROI
        vegas_total = r.get("vegas_total")
        pred_total  = r.get("predicted_total")
        over_price  = r.get("over_price")
        under_price = r.get("under_price")
        total_went_over = r.get("total_went_over")
        if (vegas_total is not None and pred_total is not None
                and over_price is not None and under_price is not None
                and total_went_over is not None):
            try:
                if pred_total > vegas_total:
                    price = int(over_price)
                    won   = total_went_over
                else:
                    price = int(under_price)
                    won   = not total_went_over
                payout = american_to_decimal(price) - 1.0
                total_units += payout if won else -1.0
                total_bets  += 1
            except Exception:
                pass

    def _roi(units, bets):
        return round(units / bets * 100, 2) if bets > 0 else None

    return {
        "ml_bets":     ml_bets,
        "ml_units_won": round(ml_units, 3),
        "ml_roi_pct":  _roi(ml_units, ml_bets),
        "total_bets":  total_bets,
        "total_units_won": round(total_units, 3),
        "total_roi_pct": _roi(total_units, total_bets),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_backtest(seasons: Optional[list[int]] = None) -> None:
    from datetime import datetime, timezone

    if seasons is None:
        seasons = SEASONS
    log.info("Starting multi-season backtest: %s", seasons)

    comps_db = load_comps_db()
    log.info("Comps DB: %d records loaded", len(comps_db))

    all_results: list[dict] = []
    current_year = date.today().year

    for season in seasons:
        log.info("--- Season %d ---", season)
        games = fetch_season_finished_games(season)
        if not games:
            log.warning("No finished games for %d — skipping", season)
            continue

        sp_ids = {g["home_sp_id"] for g in games} | {g["away_sp_id"] for g in games}

        # Use prior-season cache for historical seasons (lookahead-safe)
        if season < current_year:
            pitcher_cache = load_historical_pitcher_cache(sp_ids, season)
        else:
            pitcher_cache = build_pitcher_cache(sp_ids, season)

        # Load Vegas closing lines if available
        closing_lines = load_closing_lines(season)
        has_lines = bool(closing_lines) and any(
            pd.notna(v.get("home_ml")) for v in closing_lines.values()
        )
        log.info("Season %d: %d closing lines loaded (has_lines=%s)", season, len(closing_lines), has_lines)

        season_results: list[dict] = []
        for i, g in enumerate(games, 1):
            try:
                odds_row = closing_lines.get(g["gamePk"])
                result   = score_game(g, pitcher_cache, comps_db, odds_row=odds_row)
                result["season"] = season
                season_results.append(result)
            except Exception as exc:
                log.warning("Failed to score game %s (%s @ %s): %s",
                            g.get("gamePk"), g.get("away_team"), g.get("home_team"), exc)

            if i % 200 == 0:
                log.info("  Scored %d / %d games...", i, len(games))

        log.info("Season %d: scored %d games", season, len(season_results))
        all_results.extend(season_results)

    # Sort most recent first for the game log display
    all_results.sort(key=lambda r: r["date"], reverse=True)

    stats     = compute_stats(all_results)
    ev_stats  = compute_ev_stats(all_results)
    roi_stats = compute_roi_from_history()

    output = {
        "seasons":      seasons,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_games":  len(all_results),
        "stats":        stats,
        "ev_stats":     ev_stats,
        "roi_stats":    roi_stats,
        "games":        all_results,
    }

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, separators=(",", ":"), default=_json_default)

    correct = stats.get("total_correct", 0)
    n = stats.get("total_decided", 0)
    log.info(
        "Backtest complete: %d games across %s, %d/%d correct (%.1f%%), MAE=%.2f, bias=%+.2f",
        len(all_results), seasons, correct, n,
        stats.get("win_pct_overall", 0) * 100,
        stats.get("total_mae") or 0,
        stats.get("total_bias") or 0,
    )
    if ev_stats:
        log.info(
            "EV stats: %d games with lines, Brier=%.4f, edge_mean=%+.4f, edge_win_rate=%.1f%%",
            ev_stats.get("n_with_lines", 0),
            ev_stats.get("brier_score", 0),
            ev_stats.get("ml_edge_mean", 0),
            ev_stats.get("win_rate_when_edge", 0) * 100,
        )


def _json_default(obj):
    """Handle numpy/pandas types in JSON serialization."""
    try:
        import numpy as np
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
    except ImportError:
        pass
    if pd.isna(obj):
        return None
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


if __name__ == "__main__":
    import argparse as _argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    _parser = _argparse.ArgumentParser(description="Run MLB historical backtest")
    _parser.add_argument("--seasons", default=",".join(map(str, SEASONS)),
                         help="Comma-separated years, e.g. 2026 or 2019,2020,2021,2022,2023,2024")
    _args = _parser.parse_args()
    _seasons = [int(s.strip()) for s in _args.seasons.split(",")]
    run_backtest(seasons=_seasons)
