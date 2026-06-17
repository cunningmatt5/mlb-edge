"""Backfill the home-plate umpire per gamePk for historical games.

The backtest/history records carry gamePk but not the umpire, so this fetches the
assigned HP umpire for every game date in docs/backtest.json + docs/history.json and
writes a {gamePk: "Full Name"} cache for the umpire-edge research (research_umpire.py).

Reuses the MLB schedule `officials` hydrate + pipeline.schedule._extract_hp_umpire
(the same source the live pipeline uses). Idempotent/resumable: already-fetched date
windows are skipped, so re-running only picks up new dates.

Usage:
    python -m pipeline.backfill_umpires
    python -m pipeline.backfill_umpires --force   # refetch every window
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.schedule import _extract_hp_umpire  # noqa: E402

ROOT = Path(__file__).parent.parent
CACHE = ROOT / "data" / "umpire_by_gamepk.json"
MLB_SCHEDULE = "https://statsapi.mlb.com/api/v1/schedule"
WINDOW_DAYS = 15


def _needed_dates() -> set[str]:
    """Union of game dates (YYYY-MM-DD) across backtest.json + history.json."""
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


def _fetch_window(start: str, end: str) -> dict[str, str]:
    """{gamePk: hp_umpire_name} for all games in [start, end] (inclusive)."""
    r = requests.get(
        MLB_SCHEDULE,
        params={"sportId": 1, "startDate": start, "endDate": end, "hydrate": "officials"},
        timeout=30,
    )
    r.raise_for_status()
    out: dict[str, str] = {}
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            pk = g.get("gamePk")
            ump = _extract_hp_umpire(g)
            if pk and ump:
                out[str(pk)] = ump
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill HP umpire per gamePk")
    ap.add_argument("--force", action="store_true", help="refetch all windows")
    args = ap.parse_args()

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    by_pk: dict[str, str] = {} if args.force else cache.get("by_gamepk", {})
    done: set[str] = set() if args.force else set(cache.get("windows", []))

    dates = _needed_dates()
    if not dates:
        print("No game dates found in backtest.json / history.json — nothing to do.")
        return
    cur = datetime.strptime(min(dates), "%Y-%m-%d").date()
    end = datetime.strptime(max(dates), "%Y-%m-%d").date()
    print(f"Backfilling HP umpires {cur} … {end}  ({len(dates)} distinct game dates)")
    CACHE.parent.mkdir(parents=True, exist_ok=True)

    while cur <= end:
        w_end = min(cur + timedelta(days=WINDOW_DAYS - 1), end)
        key = f"{cur}..{w_end}"
        # Skip windows fully inside the MLB offseason (mid-Nov … Feb) — no games.
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
            print(f"  {key}: +{len(got):>3}  (total {len(by_pk)})")
        except Exception as exc:
            print(f"  {key}: FAILED — {exc}")
        CACHE.write_text(json.dumps({"by_gamepk": by_pk, "windows": sorted(done)}), encoding="utf-8")
        cur = w_end + timedelta(days=1)
        time.sleep(0.25)

    # Coverage report vs the games we actually care about.
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))
    pks = [str(g.get("gamePk")) for g in bt.get("games", []) if g.get("gamePk")]
    covered = sum(1 for pk in pks if pk in by_pk)
    print(f"\nDone. {len(by_pk)} gamePks cached → {CACHE.relative_to(ROOT)}")
    print(f"Backtest coverage: {covered}/{len(pks)} ({100*covered/max(1,len(pks)):.1f}%)")


if __name__ == "__main__":
    main()
