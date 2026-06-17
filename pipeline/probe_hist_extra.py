"""Probe: does The Odds API HISTORICAL per-event endpoint serve extra markets (F5 totals,
pitcher Ks, batter HR) for past seasons? Earlier probes used an arbitrary event/snapshot and
got false negatives; this matches the snapshot time to a game about to start.

For each (season test date, snapshot): list historical events at that snapshot, pick one whose
commence_time is shortly AFTER the snapshot (so markets are posted), then fetch its historical
odds for the extra markets and report which returned + cost.

Usage:  ODDS_API_KEY=xxxx python -m pipeline.probe_hist_extra
"""

from __future__ import annotations

import argparse
import os
import sys

import requests

HBASE = "https://api.the-odds-api.com/v4/historical/sports/baseball_mlb"
# Evening snapshot (~6:40 PM ET = 22:40Z) so 7 PM ET games are posted just after.
SNAPSHOTS = ["2023-06-15T22:40:00Z", "2024-06-15T22:40:00Z", "2025-06-15T22:40:00Z"]
EXTRA = "totals_1st_5_innings,h2h_1st_5_innings,pitcher_strikeouts,batter_home_runs"


def _probe(api_key: str, snap: str) -> None:
    he = requests.get(f"{HBASE}/events", params={"apiKey": api_key, "date": snap}, timeout=30)
    if he.status_code != 200:
        print(f"  {snap[:10]}: events HTTP {he.status_code} — {he.text[:120]}")
        return
    data = he.json()
    snap_ts = data.get("timestamp") if isinstance(data, dict) else None
    evs = data.get("data", data) if isinstance(data, dict) else data
    evs = evs if isinstance(evs, list) else []
    # pick an event commencing AFTER the snapshot (markets posted, game not started)
    upcoming = sorted([e for e in evs if e.get("commence_time", "") >= snap], key=lambda e: e["commence_time"])
    pick = upcoming[0] if upcoming else (evs[0] if evs else None)
    if not pick:
        print(f"  {snap[:10]}: no events (snapshot ts={snap_ts})")
        return
    ho = requests.get(f"{HBASE}/events/{pick['id']}/odds",
                      params={"apiKey": api_key, "date": snap, "regions": "us",
                              "markets": EXTRA, "oddsFormat": "american"}, timeout=30)
    cost = ho.headers.get("x-requests-last", "?")
    if ho.status_code != 200:
        print(f"  {snap[:10]}: odds HTTP {ho.status_code} cost={cost} — {ho.text[:150]}")
        return
    d = ho.json()
    ed = d.get("data", d) if isinstance(d, dict) else d
    present: set[str] = set()
    for bm in (ed or {}).get("bookmakers", []):
        for m in bm.get("markets", []):
            present.add(m["key"])
    want = EXTRA.split(",")
    served = [m for m in want if m in present]
    print(f"  {snap[:10]}: {pick.get('away_team')} @ {pick.get('home_team')} "
          f"({pick.get('commence_time')}) | snap_ts={snap_ts} cost={cost}")
    print(f"            SERVED extra: {served or 'NONE'}  | all markets: {sorted(present)[:8]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", default=os.environ.get("ODDS_API_KEY", ""))
    args = ap.parse_args()
    if not args.api_key:
        print("ERROR: no key.")
        sys.exit(1)
    print("Probing HISTORICAL per-event extra markets (F5 / pitcher Ks / batter HR) by season:\n")
    for snap in SNAPSHOTS:
        _probe(args.api_key, snap)
    print("\nIf 'SERVED extra' lists F5/props for a season, that market IS backtestable from that "
          "season via the per-event historical endpoint (cost shown is per event-snapshot).")


if __name__ == "__main__":
    main()
