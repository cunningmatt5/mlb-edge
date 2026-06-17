"""Backfill historical First-5-innings (F5) TOTALS odds per gamePk via The Odds API
per-event historical endpoint (the bulk endpoint doesn't serve F5).

For each game date in the requested seasons, fetch two pre-game snapshots (afternoon + evening),
list the historical events, match each to our gamePk by date + team names, and pull that event's
`totals_1st_5_innings` line at the snapshot nearest its first pitch. Writes
{gamePk: {f5_total, f5_over_price, f5_under_price}} to data/f5_closing_odds_by_gamepk.json.

Cost ≈ 10 Odds-API units per event-odds call (+ ~1 per snapshot events-list). Resumable
(per (date,snapshot) windows) and quota-guarded (stops before --min-remaining).

Usage:
    ODDS_API_KEY=xxxx python -m pipeline.backfill_f5_odds --seasons 2025,2026
    python -m pipeline.backfill_f5_odds --seasons 2025,2026 --api-key xxxx --min-remaining 50000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.odds_historical import _norm_team  # noqa: E402

ROOT = Path(__file__).parent.parent
CACHE = ROOT / "data" / "f5_closing_odds_by_gamepk.json"
HBASE = "https://api.the-odds-api.com/v4/historical/sports/baseball_mlb"
# F5/props post ~30-60 min before first pitch, so we need snapshots that sit just before each
# start cluster. These ~bracket early-afternoon / late-afternoon / evening+late MLB starts (UTC).
SNAPSHOTS = ["T16:40:00Z", "T19:40:00Z", "T22:40:00Z"]
F5_MARKET = "totals_1st_5_innings"


def _target_games(seasons: set[int]) -> tuple[dict, set[str]]:
    """{(date, home_norm, away_norm): gamePk} and the set of dates to fetch."""
    tgt: dict[tuple, int] = {}
    dates: set[str] = set()
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))["games"]
    rows = list(bt)
    hp = ROOT / "docs" / "history.json"
    if hp.exists():
        raw = json.loads(hp.read_text(encoding="utf-8"))
        rows += raw if isinstance(raw, list) else raw.get("games", [])
    for g in rows:
        try:
            season = int(g.get("season") or (g.get("date", "")[:4]))
        except (TypeError, ValueError):
            continue
        d = (g.get("date") or "")[:10]
        if season not in seasons or not d or not g.get("gamePk"):
            continue
        if not g.get("home_team") or not g.get("away_team"):
            continue
        tgt[(d, _norm_team(g["home_team"]), _norm_team(g["away_team"]))] = g["gamePk"]
        dates.add(d)
    return tgt, dates


def _parse_f5(event: dict) -> dict | None:
    """Best-book F5 totals line from an event-odds payload."""
    for bm in event.get("bookmakers", []):
        for m in bm.get("markets", []):
            if m["key"] != F5_MARKET:
                continue
            total = over = under = None
            for o in m.get("outcomes", []):
                nm = (o.get("name") or "").lower()
                if nm == "over":
                    total, over = o.get("point"), o.get("price")
                elif nm == "under":
                    under = o.get("price")
            if total is not None and over is not None and under is not None:
                return {"f5_total": total, "f5_over_price": over, "f5_under_price": under}
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2025,2026")
    ap.add_argument("--api-key", default=os.environ.get("ODDS_API_KEY", ""))
    ap.add_argument("--min-remaining", type=int, default=50000,
                    help="stop before quota drops below this (budget guard)")
    args = ap.parse_args()
    if not args.api_key:
        print("ERROR: no key. Set ODDS_API_KEY or pass --api-key.")
        sys.exit(1)
    seasons = {int(s) for s in args.seasons.split(",")}

    tgt, dates = _target_games(seasons)
    print(f"Target: {len(tgt)} games across {len(dates)} dates, seasons {sorted(seasons)}")

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    by_pk: dict[str, dict] = cache.get("by_gamepk", {})
    done: set[str] = set(cache.get("windows", []))   # processed (date,snap) keys
    CACHE.parent.mkdir(parents=True, exist_ok=True)

    sess = requests.Session()
    remaining = None
    stop = False
    for d in sorted(dates):
        if stop:
            break
        for snap in SNAPSHOTS:
            wkey = f"{d}{snap}"
            if wkey in done:
                continue
            try:
                ev = sess.get(f"{HBASE}/events", params={"apiKey": args.api_key, "date": f"{d}{snap}"}, timeout=30)
                remaining = ev.headers.get("x-requests-remaining", remaining)
                if ev.status_code != 200:
                    print(f"  {wkey}: events HTTP {ev.status_code}")
                    if ev.status_code in (401, 429):
                        stop = True; break
                    done.add(wkey); continue
                data = ev.json()
                events = data.get("data", data) if isinstance(data, dict) else data
                events = events if isinstance(events, list) else []
            except Exception as exc:
                print(f"  {wkey}: events failed {exc}"); continue

            got = 0
            for e in events:
                # Match event → our gamePk by date + normalized team names. (The events list at a
                # snapshot only contains games not yet started, so a game is captured at the first
                # snapshot before its first pitch; later snapshots are skipped via by_pk dedup.)
                key = (d, _norm_team(e.get("home_team", "")), _norm_team(e.get("away_team", "")))
                pk = tgt.get(key)
                if not pk or str(pk) in by_pk:
                    continue
                if remaining is not None and int(remaining) <= args.min_remaining:
                    print(f"  quota floor reached (remaining={remaining}) — stopping."); stop = True; break
                try:
                    od = sess.get(f"{HBASE}/events/{e['id']}/odds",
                                  params={"apiKey": args.api_key, "date": f"{d}{snap}", "regions": "us",
                                          "markets": F5_MARKET, "oddsFormat": "american"}, timeout=30)
                    remaining = od.headers.get("x-requests-remaining", remaining)
                    if od.status_code != 200:
                        continue
                    od_data = od.json()
                    ed = od_data.get("data", od_data) if isinstance(od_data, dict) else od_data
                    parsed = _parse_f5(ed or {})
                    if parsed:
                        by_pk[str(pk)] = parsed; got += 1
                except Exception:
                    pass
                time.sleep(0.05)
            done.add(wkey)
            print(f"  {wkey}: +{got} F5 lines (total {len(by_pk)}, remaining={remaining})")
            CACHE.write_text(json.dumps({"by_gamepk": by_pk, "windows": sorted(done)}), encoding="utf-8")
            if stop:
                break

    covered = sum(1 for pk in tgt.values() if str(pk) in by_pk)
    print(f"\nDone. {len(by_pk)} F5 lines cached → {CACHE.relative_to(ROOT)}")
    print(f"Coverage: {covered}/{len(tgt)} target games ({100*covered/max(1,len(tgt)):.1f}%) | quota remaining={remaining}")


if __name__ == "__main__":
    main()
