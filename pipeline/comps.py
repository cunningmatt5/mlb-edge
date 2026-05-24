"""Historical game comps database and similarity search engine.

Build phase (run by backfill.yml):
    python -m pipeline.comps --build

Daily use:
    from pipeline.comps import load_comps_db, build_game_profile, find_similar_games, compute_insights

Architecture:
  - build_comps_database() reads data/seasons/*/  and writes data/game_comps.json
  - build_game_profile()   builds today's 7-dim feature vector from live player cache
  - find_similar_games()   returns the N closest historical games by Euclidean distance
  - compute_insights()     computes historical over/ML rates vs Pinnacle implied probs
"""
from __future__ import annotations

import argparse
import json
import logging
import pickle
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

DATA_DIR    = Path(__file__).parent.parent / "data"
SEASONS_DIR = DATA_DIR / "seasons"
COMPS_PATH  = DATA_DIR / "game_comps.json"

log = logging.getLogger(__name__)

# Feature vector bounds (same as scorer.normalize population p5/p95)
_BOUNDS = {
    "xfip":   (2.8, 5.5),
    "siera":  (2.8, 5.5),
    "xwoba":  (0.270, 0.370),
    "park":   (88.0, 118.0),
}


# ---------------------------------------------------------------------------
# Build phase (offline, run by backfill.yml)
# ---------------------------------------------------------------------------

def build_comps_database(
    seasons_dir: Path = SEASONS_DIR,
    output_path: Path = COMPS_PATH,
) -> None:
    """Build normalized game feature database from all available seasons."""
    from pipeline.park_factors import get_run_factor
    from pipeline.scorer import normalize, lineup_weighted_mean

    records: list[dict] = []

    for season_dir in sorted(seasons_dir.iterdir()):
        if not season_dir.is_dir() or not season_dir.name.isdigit():
            continue
        season = int(season_dir.name)

        required = [
            season_dir / "games.parquet",
            season_dir / "player_game_logs.parquet",
            season_dir / "game_lineups.parquet",
            season_dir / "player_cache.pkl",
        ]
        if not all(p.exists() for p in required):
            log.warning("Season %d: missing files — skipping", season)
            continue

        games_df   = pd.read_parquet(required[0])
        logs_df    = pd.read_parquet(required[1])
        lineups_df = pd.read_parquet(required[2])
        with open(required[3], "rb") as f:
            cache: dict = pickle.load(f)

        logs_by_game    = {pk: g for pk, g in logs_df.groupby("game_pk")}
        lineups_by_game = {pk: g for pk, g in lineups_df.groupby("game_pk")}
        added = 0

        for row in games_df.itertuples():
            hs = row.home_score
            as_ = row.away_score
            if hs is None or as_ is None or (isinstance(hs, float) and pd.isna(hs)):
                continue

            game_log = logs_by_game.get(row.game_pk)
            if game_log is None:
                continue

            home_sp_id = _find_sp_id(game_log, "home")
            away_sp_id = _find_sp_id(game_log, "away")
            home_sp    = cache.get(home_sp_id, {}) if home_sp_id else {}
            away_sp    = cache.get(away_sp_id, {}) if away_sp_id else {}

            lg = lineups_by_game.get(row.game_pk, pd.DataFrame())
            home_xwoba = _lineup_avg_xwoba(lg, "home", cache)
            away_xwoba = _lineup_avg_xwoba(lg, "away", cache)

            park = float(get_run_factor(row.venue))
            features = _build_features(home_sp, away_sp, home_xwoba, away_xwoba, park, normalize)

            hs_i = int(hs)
            as_i = int(as_)
            records.append({
                "game_pk":    int(row.game_pk),
                "date":       row.date,
                "season":     season,
                "home_team":  row.home_team,
                "away_team":  row.away_team,
                "venue":      row.venue,
                "home_score": hs_i,
                "away_score": as_i,
                "total_runs": hs_i + as_i,
                "home_won":   hs_i > as_i,
                "features":   features,
            })
            added += 1

        log.info("Season %d: %d games added to comps DB", season, added)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(records, f, separators=(",", ":"))
    log.info("Saved game_comps.json: %d total records", len(records))


# ---------------------------------------------------------------------------
# Daily use: build today's game profile
# ---------------------------------------------------------------------------

def build_game_profile(game: dict, cache: dict) -> Optional[list[float]]:
    """Return normalized 7-dim feature vector for today's game, or None if SP stats missing."""
    from pipeline.park_factors import get_run_factor
    from pipeline.scorer import normalize, lineup_weighted_mean

    home_sp_id = game.get("home_sp_id")
    away_sp_id = game.get("away_sp_id")
    if not home_sp_id or not away_sp_id:
        return None

    home_sp = cache.get(home_sp_id, {})
    away_sp = cache.get(away_sp_id, {})

    home_players = [cache[b] for b in game.get("home_lineup", []) if b in cache]
    away_players = [cache[b] for b in game.get("away_lineup", []) if b in cache]
    home_xwoba = lineup_weighted_mean(home_players, "xwoba")
    away_xwoba = lineup_weighted_mean(away_players, "xwoba")

    park = float(get_run_factor(game.get("venue", "")))
    return _build_features(home_sp, away_sp, home_xwoba, away_xwoba, park, normalize)


# ---------------------------------------------------------------------------
# Similarity search
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_comps_cached(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_comps_db() -> list[dict]:
    """Load game_comps.json (cached after first call)."""
    if not COMPS_PATH.exists():
        log.debug("game_comps.json not found — run backfill.yml to build it")
        return []
    try:
        return _load_comps_cached(str(COMPS_PATH))
    except Exception as exc:
        log.warning("Failed to load game_comps.json: %s", exc)
        return []


def find_similar_games(
    today_features: list[float],
    comps_db: list[dict],
    n: int = 30,
) -> list[dict]:
    """Return the N historical games most similar to today's profile."""
    if not comps_db or not today_features:
        return []

    today_vec = np.array(today_features, dtype=float)
    hist_mat  = np.array([r["features"] for r in comps_db], dtype=float)
    dists     = np.linalg.norm(hist_mat - today_vec, axis=1)
    idx       = np.argsort(dists)[:n]
    return [comps_db[i] for i in idx]


# ---------------------------------------------------------------------------
# Insight computation
# ---------------------------------------------------------------------------

def compute_insights(
    similar: list[dict],
    total_line: Optional[float] = None,
    over_price: Optional[int] = None,
    under_price: Optional[int] = None,
    home_price: Optional[int] = None,
    away_price: Optional[int] = None,
) -> dict:
    """Compute historical win rates vs Pinnacle implied probs for similar games.

    Uses today's Pinnacle total line as the threshold when grading historical totals —
    the closest available proxy to "would this have hit the market line?"
    """
    from pipeline.odds import no_vig_prob

    n = len(similar)

    # Total
    total_insight = None
    if total_line is not None and over_price is not None and under_price is not None and n > 0:
        over_count  = sum(1 for g in similar if g["total_runs"] > total_line)
        hist_over   = over_count / n
        hist_under  = 1.0 - hist_over
        pin_over, pin_under = no_vig_prob(over_price, under_price)
        total_insight = {
            "line":                  total_line,
            "over_price":            over_price,
            "under_price":           under_price,
            "pinnacle_over_prob":    pin_over,
            "pinnacle_under_prob":   pin_under,
            "historical_over_rate":  round(hist_over, 4),
            "historical_under_rate": round(hist_under, 4),
            "over_edge":             round(hist_over  - pin_over,  4),
            "under_edge":            round(hist_under - pin_under, 4),
        }

    # Moneyline
    ml_insight = None
    if home_price is not None and away_price is not None and n > 0:
        home_wins  = sum(1 for g in similar if g["home_won"])
        hist_home  = home_wins / n
        hist_away  = 1.0 - hist_home
        pin_home, pin_away = no_vig_prob(home_price, away_price)
        ml_insight = {
            "home_price":           home_price,
            "away_price":           away_price,
            "pinnacle_home_prob":   pin_home,
            "pinnacle_away_prob":   pin_away,
            "historical_home_rate": round(hist_home, 4),
            "historical_away_rate": round(hist_away, 4),
            "home_edge":            round(hist_home - pin_home, 4),
            "away_edge":            round(hist_away - pin_away, 4),
        }

    return {"total": total_insight, "moneyline": ml_insight}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_sp_id(game_log: pd.DataFrame, side: str) -> Optional[int]:
    mask = game_log["is_pitcher"] & game_log["game_started"] & (game_log["side"] == side)
    rows = game_log[mask]
    return int(rows.iloc[0]["player_id"]) if not rows.empty else None


def _lineup_avg_xwoba(lineup_group: pd.DataFrame, side: str, cache: dict) -> Optional[float]:
    """Batting-order-weighted avg xwOBA for one side."""
    if lineup_group.empty:
        return None
    from pipeline.scorer import lineup_weighted_mean
    sub = lineup_group[lineup_group["side"] == side].sort_values("batting_order")
    players = [cache.get(int(pid), {}) for pid in sub["player_id"].tolist()]
    return lineup_weighted_mean(players, "xwoba")


def _sp_quality(sp: dict) -> Optional[float]:
    """Return best available pitcher quality stat: xfip > era > None."""
    return sp.get("xfip") or sp.get("era")


def _build_features(
    home_sp: dict,
    away_sp: dict,
    home_xwoba: Optional[float],
    away_xwoba: Optional[float],
    park: float,
    normalize_fn,
) -> list[float]:
    """Normalized 7-dim vector: [home_sp_quality, home_siera, away_sp_quality, away_siera,
    home_xwoba, away_xwoba, park_factor].

    SP quality uses xFIP when available, falls back to ERA so pitcher dimensions
    are never blank in the comps feature vector.
    """
    lo_p, hi_p = _BOUNDS["xfip"]
    lo_s, hi_s = _BOUNDS["siera"]
    lo_w, hi_w = _BOUNDS["xwoba"]
    lo_k, hi_k = _BOUNDS["park"]
    return [
        normalize_fn(_sp_quality(home_sp), lo=lo_p, hi=hi_p),
        normalize_fn(home_sp.get("siera"), lo=lo_s, hi=hi_s),
        normalize_fn(_sp_quality(away_sp), lo=lo_p, hi=hi_p),
        normalize_fn(away_sp.get("siera"), lo=lo_s, hi=hi_s),
        normalize_fn(home_xwoba,           lo=lo_w, hi=hi_w),
        normalize_fn(away_xwoba,           lo=lo_w, hi=hi_w),
        normalize_fn(park,                 lo=lo_k, hi=hi_k),
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Build or query the historical comps database")
    parser.add_argument("--build", action="store_true", help="Build game_comps.json from data/seasons/")
    args = parser.parse_args()

    if args.build:
        build_comps_database()
    else:
        parser.print_help()
