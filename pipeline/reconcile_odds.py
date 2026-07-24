"""Reconcile closing_lines.parquet against the full schedule, 2021-2025.

Rebuilds each season's closing lines from every available source, using
games.parquet as the spine so coverage is measured against the FULL schedule
(12,148 games) rather than against whatever the odds universe happened to hold.

Source priority, applied per-field (totals and moneylines fill independently —
they are different markets and a game can legitimately have one but not the
other):

  2024-2025   odds API cache  ->  linemove  ->  legacy (if it passes sanity)
  2021-2023   legacy (SBR)    ->  linemove

The odds API cache is re-collapsed through ``records_from_odds_api_cache``,
which keeps only quotes strictly before commence_time. Before that fix the
builder preferred the latest snapshot outright, so T22:00Z (6 PM ET) stored
LIVE in-game odds for afternoon games: totals re-priced around runs already
scored, and moneylines pulled by the book. That poisoned ~475 rows across
2024-25 with a median total error of 2.5 runs.

Legacy rows are only trusted where the total is plausible, since their
provenance predates that fix. linemove is gamePk-keyed (no date/team mis-map
risk) and holds DraftKings closing numbers.

Run:  python -m pipeline.reconcile_odds [--seasons 2021 ... ] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from pipeline.odds_historical import (
    SEASONS_DIR,
    _norm_team,
    _sbro_to_mlb,
    records_from_odds_api_cache,
)
from pipeline.odds import no_vig_prob

log = logging.getLogger(__name__)

SEASONS = [2021, 2022, 2023, 2024, 2025]

# Sanity bands. A real MLB full-game total has never closed outside 5.5-13.0;
# anything beyond this band is a live in-game line or a mis-parse, not a market.
TOTAL_MIN, TOTAL_MAX = 5.0, 13.5
# Odds-API moneylines contain known garbage (-10000, +1440) for a subset.
ML_ABS_MAX = 600

LINEMOVE_PATH = Path("data/linemove_by_gamepk.json")
SBR_TOTALS_PATH = Path("data/sbr_totals_by_season.json")


def _valid_total(v) -> bool:
    return v is not None and pd.notna(v) and TOTAL_MIN <= float(v) <= TOTAL_MAX


def _valid_ml(v) -> bool:
    return v is not None and pd.notna(v) and 0 < abs(int(v)) <= ML_ABS_MAX


def _load_linemove() -> dict[int, dict]:
    if not LINEMOVE_PATH.exists():
        log.warning("linemove file not found at %s — skipping that source", LINEMOVE_PATH)
        return {}
    with open(LINEMOVE_PATH) as f:
        blob = json.load(f)
    by_pk = blob.get("by_gamepk", blob)
    return {int(k): v for k, v in by_pk.items()}


def _sbr_totals_by_gamepk(season: int, games: pd.DataFrame) -> dict[int, dict]:
    """Totals-only spine (2015-2021). Last-resort filler for games no other source has.

    Joined on date + both team names + actual_total. Requiring the score to agree
    is what makes this safe: the spine has no gamePk, and doubleheaders share a
    date and teams, so the score is the only thing that pins the right game.
    """
    if not SBR_TOTALS_PATH.exists():
        return {}
    with open(SBR_TOTALS_PATH) as f:
        rows = json.load(f).get("games", [])

    season_rows = [r for r in rows if r.get("season") == season]
    if not season_rows:
        return {}

    index: dict[tuple, list[dict]] = {}
    for r in season_rows:
        home = _norm_team(_sbro_to_mlb(str(r.get("home", "")), season) or r.get("home", ""))
        away = _norm_team(_sbro_to_mlb(str(r.get("away", "")), season) or r.get("away", ""))
        key = (str(r.get("date"))[:10], home, away, r.get("actual_total"))
        index.setdefault(key, []).append(r)

    out: dict[int, dict] = {}
    for _, g in games.iterrows():
        if pd.isna(g.get("home_score")) or pd.isna(g.get("away_score")):
            continue
        runs = float(g["home_score"]) + float(g["away_score"])
        key = (
            str(g["date"])[:10],
            _norm_team(g["home_team"]),
            _norm_team(g["away_team"]),
            runs,
        )
        hits = index.get(key)
        if hits and len(hits) == 1:  # ambiguous match -> skip rather than guess
            out[int(g["game_pk"])] = hits[0]
    return out


def _api_rows_by_gamepk(season: int, games: pd.DataFrame, seasons_dir: Path) -> dict[int, dict]:
    """Collapse the odds API cache and key it by game_pk via date+team join."""
    cache_path = seasons_dir / str(season) / "odds_api_cache.json"
    if not cache_path.exists():
        return {}
    with open(cache_path) as f:
        cache = json.load(f)

    recs = records_from_odds_api_cache(cache, season)
    if not recs:
        return {}
    odds = pd.DataFrame(recs)
    odds["_k"] = (
        odds["date"].astype(str) + "|"
        + odds["home_team"].apply(_norm_team) + "|"
        + odds["away_team"].apply(_norm_team)
    )
    # Doubleheaders share a date+team key; both games get the same quote, which is
    # the best available — the API does not distinguish games of a twin bill.
    lookup = {k: v for k, v in odds.drop_duplicates("_k").set_index("_k").to_dict("index").items()}

    out: dict[int, dict] = {}
    for _, g in games.iterrows():
        k = f"{str(g['date'])[:10]}|{_norm_team(g['home_team'])}|{_norm_team(g['away_team'])}"
        if k in lookup:
            out[int(g["game_pk"])] = lookup[k]
    return out


def _legacy_rows_by_gamepk(season: int, seasons_dir: Path) -> dict[int, dict]:
    path = seasons_dir / str(season) / "closing_lines.parquet"
    if not path.exists():
        return {}
    df = pd.read_parquet(path).drop_duplicates("game_pk")
    return {int(r["game_pk"]): r.to_dict() for _, r in df.iterrows()}


def reconcile_season(
    season: int,
    seasons_dir: Optional[Path] = None,
    linemove: Optional[dict[int, dict]] = None,
    dry_run: bool = False,
) -> dict:
    if seasons_dir is None:
        seasons_dir = SEASONS_DIR
    if linemove is None:
        linemove = _load_linemove()

    games_path = seasons_dir / str(season) / "games.parquet"
    games = pd.read_parquet(games_path).drop_duplicates("game_pk")

    api    = _api_rows_by_gamepk(season, games, seasons_dir)
    legacy = _legacy_rows_by_gamepk(season, seasons_dir)
    sbr    = _sbr_totals_by_gamepk(season, games)

    # 2024-25 have a trustworthy (now pre-commence-only) API cache, so it leads.
    # 2021-23 have no cache; their legacy rows are SBR closing lines and lead instead.
    # sbr is totals-only and last: it exists to fill games no other source priced.
    order = ["api", "linemove", "legacy"] if api else ["legacy", "linemove"]
    order = order + ["sbr"]

    prov = {"total": {}, "ml": {}}
    rows = []
    for _, g in games.iterrows():
        pk = int(g["game_pk"])
        cand = {
            "api":      api.get(pk),
            "legacy":   legacy.get(pk),
            "linemove": linemove.get(pk),
            "sbr":      sbr.get(pk),
        }

        total = over = under = None
        total_src = None
        for src in order:
            c = cand.get(src)
            if not c:
                continue
            v = c.get("close_total") if src == "linemove" else c.get("closing_total")
            if _valid_total(v):
                total = float(v)
                if src == "linemove":
                    over, under = c.get("close_over"), c.get("close_under")
                else:
                    over, under = c.get("over_price"), c.get("under_price")
                total_src = src
                break

        home_ml = away_ml = None
        ml_src = None
        for src in order:
            c = cand.get(src)
            if not c:
                continue
            h = c.get("close_home_ml") if src == "linemove" else c.get("home_ml")
            a = c.get("close_away_ml") if src == "linemove" else c.get("away_ml")
            if _valid_ml(h) and _valid_ml(a):
                home_ml, away_ml = int(h), int(a)
                ml_src = src
                break

        prov["total"][total_src] = prov["total"].get(total_src, 0) + 1
        prov["ml"][ml_src] = prov["ml"].get(ml_src, 0) + 1

        hp = ap = None
        if home_ml is not None and away_ml is not None:
            try:
                hp, ap = no_vig_prob(home_ml, away_ml)
                hp, ap = round(hp, 4), round(ap, 4)
            except Exception:
                hp = ap = None

        rows.append({
            "game_pk":     pk,
            "date":        str(g["date"])[:10],
            "home_team":   g["home_team"],
            "away_team":   g["away_team"],
            "home_score":  g.get("home_score"),
            "away_score":  g.get("away_score"),
            "home_ml":     home_ml,
            "away_ml":     away_ml,
            "closing_total": total,
            "over_price":  over  if over  is not None and pd.notna(over)  else -110,
            "under_price": under if under is not None and pd.notna(under) else -110,
            "home_implied_prob": hp,
            "away_implied_prob": ap,
        })

    out = pd.DataFrame(rows)
    n = len(out)
    n_tot = out["closing_total"].notna().sum()
    n_ml  = out["home_ml"].notna().sum()

    corr = None
    d = out.dropna(subset=["closing_total", "home_score", "away_score"])
    if len(d) > 100:
        corr = d["closing_total"].corr(d["home_score"] + d["away_score"])

    log.info(
        "%d: %d games | totals %d (%.1f%%) | ML %d (%.1f%%) | corr(total,runs)=%s",
        season, n, n_tot, 100 * n_tot / n, n_ml, 100 * n_ml / n,
        f"{corr:.3f}" if corr is not None else "n/a",
    )
    log.info("   total provenance: %s", prov["total"])
    log.info("   ML    provenance: %s", prov["ml"])

    if not dry_run:
        out.to_parquet(seasons_dir / str(season) / "closing_lines.parquet", index=False)

    return {
        "season": season, "games": n,
        "totals": int(n_tot), "ml": int(n_ml),
        "corr": corr, "provenance": prov,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seasons", nargs="*", type=int, default=SEASONS)
    ap.add_argument("--dry-run", action="store_true", help="report without writing parquet")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    linemove = _load_linemove()
    results = [reconcile_season(s, linemove=linemove, dry_run=args.dry_run) for s in args.seasons]

    tot_games = sum(r["games"] for r in results)
    tot_t = sum(r["totals"] for r in results)
    tot_m = sum(r["ml"] for r in results)
    log.info(
        "ALL: %d games | totals %d (%.2f%%) | ML %d (%.2f%%)%s",
        tot_games, tot_t, 100 * tot_t / tot_games, tot_m, 100 * tot_m / tot_games,
        "  [DRY RUN — nothing written]" if args.dry_run else "",
    )


if __name__ == "__main__":
    main()
