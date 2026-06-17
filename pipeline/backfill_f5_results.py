"""Backfill first-5-innings (F5) actual run totals per gamePk.

For the F5 UNDER edge research we need the runs scored in innings 1-5 of each game
(home + away). This fetches the MLB schedule with the linescore hydrate over date
ranges and sums innings[0:5], writing a {gamePk: f5_total} cache. Games that didn't
complete 5 innings (rain-shortened, etc.) are stored as null → void for F5 grading.

Keyless (MLB Stats API). Idempotent/resumable: already-fetched windows are skipped.

Usage:
    python -m pipeline.backfill_f5_results
    python -m pipeline.backfill_f5_results --force
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
CACHE = ROOT / "data" / "f5_results_by_gamepk.json"
MLB_SCHEDULE = "https://statsapi.mlb.com/api/v1/schedule"
WINDOW_DAYS = 15


def _needed_dates() -> set[str]:
    dates: set[str] = set()
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))
    for g in bt.get("games", []):
        if g.get("date"):
            dates.add(g["date"][:10])
    hpath = ROOT / "docs" / "history.json"
    if hpath.exists():
        raw = json.loads(hpath.read_text(encoding="utf-8"))
        games = raw if isinstance(raw, list) else raw.get("games", [])
        for g in games:
            if g.get("date"):
                dates.add(g["date"][:10])
    return dates


def _f5_from_innings(innings: list[dict]) -> float | None:
    """Sum home+away runs over innings 1-5. None if fewer than 5 innings were played."""
    if len(innings) < 5:
        return None
    total = 0
    for inn in innings[:5]:
        total += (inn.get("home", {}).get("runs") or 0) + (inn.get("away", {}).get("runs") or 0)
    return float(total)


def _fetch_window(start: str, end: str) -> dict[str, float | None]:
    r = requests.get(
        MLB_SCHEDULE,
        params={"sportId": 1, "startDate": start, "endDate": end, "hydrate": "linescore"},
        timeout=30,
    )
    r.raise_for_status()
    out: dict[str, float | None] = {}
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            pk = g.get("gamePk")
            # Only grade games that reached a final state.
            state = (g.get("status", {}) or {}).get("abstractGameState", "")
            if not pk or state != "Final":
                continue
            innings = (g.get("linescore", {}) or {}).get("innings", [])
            out[str(pk)] = _f5_from_innings(innings)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill F5 (innings 1-5) run totals per gamePk")
    ap.add_argument("--force", action="store_true", help="refetch all windows")
    args = ap.parse_args()

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    by_pk: dict[str, float | None] = {} if args.force else cache.get("by_gamepk", {})
    done: set[str] = set() if args.force else set(cache.get("windows", []))

    dates = _needed_dates()
    if not dates:
        print("No game dates found in backtest.json / history.json — nothing to do.")
        return
    cur = datetime.strptime(min(dates), "%Y-%m-%d").date()
    end = datetime.strptime(max(dates), "%Y-%m-%d").date()
    print(f"Backfilling F5 results {cur} … {end}  ({len(dates)} distinct game dates)")
    CACHE.parent.mkdir(parents=True, exist_ok=True)

    while cur <= end:
        w_end = min(cur + timedelta(days=WINDOW_DAYS - 1), end)
        key = f"{cur}..{w_end}"
        if not (cur.month in range(3, 12) or w_end.month in range(3, 12)):
            cur = w_end + timedelta(days=1)
            continue
        if key in done:
            cur = w_end + timedelta(days=1)
            continue
        try:
            got = _fetch_window(cur.isoformat(), w_end.isoformat())
            by_pk.update(got)
            done.add(key)
            graded = sum(1 for v in got.values() if v is not None)
            print(f"  {key}: +{len(got):>3} ({graded} with 5+ innings)  total {len(by_pk)}")
        except Exception as exc:
            print(f"  {key}: FAILED — {exc}")
        CACHE.write_text(json.dumps({"by_gamepk": by_pk, "windows": sorted(done)}), encoding="utf-8")
        cur = w_end + timedelta(days=1)
        time.sleep(0.25)

    bt = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))
    pks = [str(g.get("gamePk")) for g in bt.get("games", []) if g.get("gamePk")]
    covered = sum(1 for pk in pks if pk in by_pk and by_pk[pk] is not None)
    print(f"\nDone. {len(by_pk)} gamePks cached → {CACHE.relative_to(ROOT)}")
    print(f"Backtest coverage (5+ innings): {covered}/{len(pks)} ({100*covered/max(1,len(pks)):.1f}%)")


if __name__ == "__main__":
    main()
