"""Backfill historical closing TOTALS (line + over/under juice) + final scores from the FREE
SportsBookReviewsOnline Excel archives (2010-2021). $0, no Odds API.

Per game: season, date, closing_total, over_price (visitor-row OU juice), under_price (home-row),
actual_total (V Final + H Final). Over/under prices are near-symmetric, so the V/H assignment has
negligible effect on the std-vig filter (verified). Handles year-to-year column-name variants
(CloseOU vs 'Close OU') + team-abbrev variants via pipeline.odds_historical._sbro_to_mlb.

Usage:  python -m pipeline.backfill_sbr_totals --seasons 2015,2016,2017,2018,2019,2020,2021
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import requests

from pipeline.odds_historical import _sbro_to_mlb

ROOT = Path(__file__).parent.parent
RAWDIR = ROOT / "data" / "external" / "sbr"
OUT = ROOT / "data" / "sbr_totals_by_season.json"
BASE = "https://www.sportsbookreviewsonline.com/wp-content/uploads/sportsbookreviewsonline_com_737/mlb-odds-{}.xlsx"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}


def _download(year: int) -> Path | None:
    RAWDIR.mkdir(parents=True, exist_ok=True)
    p = RAWDIR / f"mlb-odds-{year}.xlsx"
    if p.exists() and p.stat().st_size > 50000:
        return p
    try:
        r = requests.get(BASE.format(year), headers=UA, timeout=60)
        r.raise_for_status()
        if not r.content[:2] == b"PK":   # xlsx is a zip
            print(f"  {year}: not a valid xlsx"); return None
        p.write_bytes(r.content); return p
    except Exception as exc:
        print(f"  {year}: download failed — {exc}"); return None


def _col(cols, target):
    for c in cols:
        if str(c).replace(" ", "").lower() == target:
            return c
    return None


def _date(raw_date: str, year: int) -> str | None:
    s = str(raw_date).strip().split(".")[0]
    if not s.isdigit():
        return None
    s = s.zfill(3)
    mm, dd = (s[0], s[1:]) if len(s) == 3 else (s[:2], s[2:])
    try:
        return f"{year}-{int(mm):02d}-{int(dd):02d}"
    except ValueError:
        return None


def _num(x):
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return None


def parse_season(year: int) -> list[dict]:
    p = _download(year)
    if not p:
        return []
    raw = pd.read_excel(p, dtype=str)
    cols = list(raw.columns)
    oucol = _col(cols, "closeou")
    if oucol is None:
        print(f"  {year}: no CloseOU column"); return []
    pricecol = cols[cols.index(oucol) + 1]   # juice is the column immediately after the total
    out = []
    for i in range(0, len(raw) - 1, 2):
        v, h = raw.iloc[i], raw.iloc[i + 1]
        if str(v.get("VH")).strip().upper() != "V" or str(h.get("VH")).strip().upper() != "H":
            continue
        tot = _num(v[oucol]); vp = _num(v[pricecol]); hp = _num(h[pricecol])
        vf, hf = _num(v.get("Final")), _num(h.get("Final"))
        d = _date(v.get("Date"), year)
        if None in (tot, vp, hp, vf, hf) or d is None:
            continue
        out.append({
            "season": year, "date": d,
            "home": _sbro_to_mlb(str(h.get("Team")), year), "away": _sbro_to_mlb(str(v.get("Team")), year),
            "closing_total": tot, "over_price": int(vp), "under_price": int(hp),
            "actual_total": vf + hf,
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2015,2016,2017,2018,2019,2020,2021")
    args = ap.parse_args()
    all_rows = []
    for y in [int(s) for s in args.seasons.split(",")]:
        rows = parse_season(y)
        print(f"  {y}: {len(rows)} games parsed")
        all_rows += rows
    OUT.write_text(json.dumps({"games": all_rows}, separators=(",", ":")), encoding="utf-8")
    from collections import Counter
    print(f"\nTotal: {len(all_rows)} games -> {OUT.relative_to(ROOT)}")
    print("by season:", dict(sorted(Counter(r['season'] for r in all_rows).items())))


if __name__ == "__main__":
    main()
