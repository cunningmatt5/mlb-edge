"""Probe: which MLB betting markets does our Odds API key actually serve LIVE?

Picks the soonest upcoming MLB event (most likely to have full markets posted) and queries the
per-event odds endpoint with batches of candidate market keys, reporting which keys come back with
data (and from how many books), plus the quota cost. Tells us what's at least forward-trackable.

Usage (key is a CI secret):
    ODDS_API_KEY=xxxx python -m pipeline.probe_live_markets
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

import requests

BASE = "https://api.the-odds-api.com/v4/sports/baseball_mlb"

# Candidate MLB market keys, grouped so one unsupported key only 422s its own batch.
MARKET_GROUPS = {
    "featured":      ["h2h", "spreads", "totals"],
    "team/alt":      ["team_totals", "alternate_totals", "alternate_spreads", "alternate_team_totals"],
    "innings":       ["h2h_1st_1_innings", "totals_1st_1_innings", "h2h_1st_3_innings",
                      "totals_1st_3_innings", "h2h_1st_5_innings", "spreads_1st_5_innings",
                      "totals_1st_5_innings", "h2h_1st_7_innings", "totals_1st_7_innings"],
    "pitcher_props": ["pitcher_strikeouts", "pitcher_hits_allowed", "pitcher_walks",
                      "pitcher_earned_runs", "pitcher_outs", "pitcher_record_a_win"],
    "batter_props":  ["batter_home_runs", "batter_hits", "batter_total_bases", "batter_rbis",
                      "batter_runs_scored", "batter_walks", "batter_strikeouts", "batter_singles",
                      "batter_doubles", "batter_triples", "batter_stolen_bases", "batter_hits_runs_rbis"],
}


def _pick_event(api_key: str) -> dict | None:
    r = requests.get(f"{BASE}/events", params={"apiKey": api_key}, timeout=30)
    if r.status_code != 200:
        print(f"events: HTTP {r.status_code} — {r.text[:150]}")
        return None
    events = r.json()
    now = datetime.now(timezone.utc)
    future = [e for e in events if e.get("commence_time", "") > now.isoformat()]
    future.sort(key=lambda e: e["commence_time"])
    pick = (future or events)[0] if events else None
    print(f"{len(events)} live events | probing: "
          f"{pick.get('away_team')} @ {pick.get('home_team')} ({pick.get('commence_time')})\n" if pick else "no events")
    return pick


def _probe_group(api_key: str, eid: str, name: str, markets: list[str]) -> None:
    r = requests.get(f"{BASE}/events/{eid}/odds",
                     params={"apiKey": api_key, "regions": "us",
                             "markets": ",".join(markets), "oddsFormat": "american"}, timeout=30)
    cost = r.headers.get("x-requests-last", "?")
    if r.status_code == 422:
        print(f"  [{name}] 422 unsupported — {r.text[:160]}")
        return
    if r.status_code != 200:
        print(f"  [{name}] HTTP {r.status_code} — {r.text[:120]}")
        return
    data = r.json()
    present: dict[str, int] = {}
    for bm in data.get("bookmakers", []):
        for m in bm.get("markets", []):
            present[m["key"]] = present.get(m["key"], 0) + 1
    served = sorted(set(markets) & set(present))
    missing = sorted(set(markets) - set(present))
    print(f"  [{name}] cost={cost} | SERVED: {served or 'none'}")
    if missing:
        print(f"            not offered: {missing}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", default=os.environ.get("ODDS_API_KEY", ""))
    args = ap.parse_args()
    if not args.api_key:
        print("ERROR: no key. Run:  ODDS_API_KEY=xxxx python -m pipeline.probe_live_markets")
        sys.exit(1)
    print("Mapping LIVE MLB markets available on this Odds API key:\n")
    ev = _pick_event(args.api_key)
    if not ev:
        return
    for name, markets in MARKET_GROUPS.items():
        _probe_group(args.api_key, ev["id"], name, markets)
        time.sleep(0.3)
    print("\n'SERVED' = the key returned that market for this event. These are forward-trackable.")


if __name__ == "__main__":
    main()
