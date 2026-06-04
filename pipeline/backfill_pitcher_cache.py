"""Enrich a saved player_cache.pkl with pitcher K%/BB%/barrel% from historical sources.

Usage:
    python pipeline/backfill_pitcher_cache.py 2024

Fetches:
  - Savant statcast leaderboard for `year` → barrel_pct_against
  - MLB Stats API season pitching stats for `year` → k_pct, bb_pct

Merges into data/seasons/{year}/player_cache.pkl without overwriting existing values.
"""

from __future__ import annotations

import logging
import pickle
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
log = logging.getLogger(__name__)


def _find_id_col(df) -> str | None:
    for col in ("player_id", "xMLBAMID", "MLBAMID", "mlbam_id", "batter", "pitcher"):
        if col in df.columns:
            return col
    return None


def _lookup_row(df, id_col: str, mlbam_id: int):
    try:
        return df[df[id_col] == mlbam_id]
    except Exception:
        return df.iloc[0:0]


def backfill(year: int) -> None:
    import requests

    cache_path = Path(f"data/seasons/{year}/player_cache.pkl")
    if not cache_path.exists():
        log.error("Cache not found: %s", cache_path)
        sys.exit(1)

    with cache_path.open("rb") as f:
        cache: dict = pickle.load(f)

    log.info("Loaded cache: %d entries from %s", len(cache), cache_path)

    import pandas as pd
    SAVANT_BASE = "https://baseballsavant.mlb.com"
    MLB_API = "https://statsapi.mlb.com/api/v1"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }

    # -------------------------------------------------------------------
    # 1. Savant pitcher statcast leaderboard → barrel_pct_against
    # -------------------------------------------------------------------
    barrel_map: dict[int, float] = {}
    try:
        url = f"{SAVANT_BASE}/leaderboard/statcast?type=pitcher&year={year}&position=&team=&min=10&csv=true"
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(pd.io.common.StringIO(resp.text))
        log.info("Savant pitcher leaderboard %d: %d rows, cols: %s", year, len(df), list(df.columns)[:8])
        id_col = _find_id_col(df)
        if id_col and "brl_percent" in df.columns:
            for _, row in df.iterrows():
                pid = row.get(id_col)
                brl = row.get("brl_percent")
                if pid and brl is not None:
                    try:
                        barrel_map[int(pid)] = float(brl) / 100.0
                    except (TypeError, ValueError):
                        pass
        log.info("barrel_pct_against: %d pitchers", len(barrel_map))
    except Exception as exc:
        log.warning("Savant leaderboard fetch failed: %s", exc)

    # -------------------------------------------------------------------
    # 2. MLB Stats API → k_pct, bb_pct
    # -------------------------------------------------------------------
    kbb_map: dict[int, dict] = {}
    MIN_BF = 20
    session = requests.Session()
    offset = 0
    limit = 1000
    while True:
        try:
            resp = session.get(
                f"{MLB_API}/stats",
                params={
                    "stats": "season",
                    "playerPool": "all",
                    "group": "pitching",
                    "season": year,
                    "sportId": 1,
                    "limit": limit,
                    "offset": offset,
                },
                headers=HEADERS,
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
            kbb_map[pid] = {
                "k_pct": round(so / bf, 4),
                "bb_pct": round(bb / bf, 4),
            }
        total = data.get("stats", [{}])[0].get("totalSplits", 0)
        offset += len(splits)
        if offset >= total:
            break
    log.info("MLB pitcher stats %d: %d pitchers with K%%/BB%%", year, len(kbb_map))

    # -------------------------------------------------------------------
    # 3. Merge into cache (pitcher entries only; don't overwrite existing)
    # -------------------------------------------------------------------
    updated_barrel = updated_k = updated_bb = 0
    for key, entry in cache.items():
        if not isinstance(entry, dict):
            continue
        pid = entry.get("mlbam_id")
        if pid is None:
            continue
        # Only enrich entries that look like pitchers (have xera or xfip)
        if not (entry.get("xera") or entry.get("xfip")):
            continue

        if pid in barrel_map and not entry.get("barrel_pct_against"):
            entry["barrel_pct_against"] = barrel_map[pid]
            updated_barrel += 1
        if pid in kbb_map:
            ps = kbb_map[pid]
            if ps.get("k_pct") and not entry.get("k_pct"):
                entry["k_pct"] = ps["k_pct"]
                updated_k += 1
            if ps.get("bb_pct") and not entry.get("bb_pct"):
                entry["bb_pct"] = ps["bb_pct"]
                updated_bb += 1

    log.info(
        "Enriched: barrel_pct_against=%d, k_pct=%d, bb_pct=%d entries updated",
        updated_barrel, updated_k, updated_bb,
    )

    # -------------------------------------------------------------------
    # 4. Save
    # -------------------------------------------------------------------
    with cache_path.open("wb") as f:
        pickle.dump(cache, f)
    log.info("Saved enriched cache to %s", cache_path)

    # Quick coverage report
    all_pitcher_entries = [v for v in cache.values() if isinstance(v, dict) and (v.get("xera") or v.get("xfip"))]
    for field in ("xera", "barrel_pct_against", "k_pct", "bb_pct"):
        n = sum(1 for p in all_pitcher_entries if p.get(field) is not None)
        log.info("  %s: %d/%d pitcher entries", field, n, len(all_pitcher_entries))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pipeline/backfill_pitcher_cache.py <year>")
        sys.exit(1)
    backfill(int(sys.argv[1]))
