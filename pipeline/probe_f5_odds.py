"""Probe: does our Odds API key return first-5-innings (F5) markets historically, how far
back, and at what quota cost? Run this BEFORE any bulk F5 backfill.

Makes a handful of single-date historical calls (one per test season) requesting the F5
markets, and reports for each: HTTP status, quota remaining/used (cost), whether an F5
totals market was returned, and a sample F5 line. Cheap (~5 calls).

Usage (key is a CI secret — run locally with your key):
    ODDS_API_KEY=xxxx python -m pipeline.probe_f5_odds
    python -m pipeline.probe_f5_odds --api-key xxxx
"""

from __future__ import annotations

import argparse
import os
import sys

import requests

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
URL = f"{ODDS_API_BASE}/sports/baseball_mlb/odds-history/"
# One mid-season date per year to test availability/back-history.
TEST_DATES = ["2026-05-15T22:00:00Z", "2025-06-15T22:00:00Z", "2024-06-15T22:00:00Z",
              "2023-06-15T22:00:00Z", "2022-06-15T22:00:00Z"]
F5_TOTALS = "totals_1st_5_innings"
F5_H2H    = "h2h_1st_5_innings"


def _probe(api_key: str, date: str) -> None:
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": f"h2h,totals,{F5_TOTALS},{F5_H2H}",
        "oddsFormat": "american",
        "date": date,
    }
    try:
        r = requests.get(URL, params=params, timeout=30)
    except Exception as exc:
        print(f"  {date[:10]}: request failed — {exc}")
        return
    rem = r.headers.get("x-requests-remaining", "?")
    used = r.headers.get("x-requests-used", "?")
    last = r.headers.get("x-requests-last", "?")   # cost of THIS call
    if r.status_code != 200:
        print(f"  {date[:10]}: HTTP {r.status_code} (remaining={rem}) — {r.text[:120]}")
        return
    body = r.json()
    events = body.get("data", body) if isinstance(body, dict) else body
    events = events if isinstance(events, list) else []
    f5_events = 0
    sample = None
    for ev in events:
        for bm in ev.get("bookmakers", []):
            mkeys = {m["key"] for m in bm.get("markets", [])}
            if F5_TOTALS in mkeys:
                f5_events += 1
                if sample is None:
                    for m in bm["markets"]:
                        if m["key"] == F5_TOTALS:
                            pts = [o.get("point") for o in m.get("outcomes", []) if o.get("point") is not None]
                            sample = f"{ev.get('away_team','?')} @ {ev.get('home_team','?')} F5 line {pts[:1]}"
                break
    print(f"  {date[:10]}: {len(events)} events | F5-totals on {f5_events} | "
          f"cost={last} remaining={rem} used={used}")
    if sample:
        print(f"      sample: {sample}")


def _probe_event_endpoints(api_key: str) -> None:
    """F5 is a non-featured market — served only via the per-event endpoints. Check both:
    (1) LIVE event odds (can we forward-collect F5 going forward?), and
    (2) HISTORICAL per-event odds for a past date (can we backfill F5 at all, and at what cost?)."""
    base = f"{ODDS_API_BASE}/sports/baseball_mlb"

    # (1) LIVE: current events → one event's odds with F5 markets
    print("\n[LIVE event endpoint — forward-collection feasibility]")
    try:
        ev = requests.get(f"{base}/events", params={"apiKey": api_key}, timeout=30)
        events = ev.json() if ev.status_code == 200 else []
        print(f"  live events: {len(events)} (HTTP {ev.status_code}, remaining={ev.headers.get('x-requests-remaining','?')})")
        if events:
            eid = events[0]["id"]
            od = requests.get(f"{base}/events/{eid}/odds",
                              params={"apiKey": api_key, "regions": "us",
                                      "markets": f"{F5_TOTALS},{F5_H2H}", "oddsFormat": "american"}, timeout=30)
            if od.status_code == 200:
                mks = {m["key"] for bm in od.json().get("bookmakers", []) for m in bm.get("markets", [])}
                print(f"  live F5 markets present: {F5_TOTALS in mks} | cost={od.headers.get('x-requests-last','?')} | markets seen: {sorted(mks)[:6]}")
            else:
                print(f"  live event odds: HTTP {od.status_code} — {od.text[:140]}")
    except Exception as exc:
        print(f"  live probe failed: {exc}")

    # (2) HISTORICAL per-event for a 2025 date
    print("\n[HISTORICAL per-event endpoint — backfill feasibility]")
    hdate = "2025-06-15T22:00:00Z"
    try:
        he = requests.get(f"{ODDS_API_BASE}/historical/sports/baseball_mlb/events",
                          params={"apiKey": api_key, "date": hdate}, timeout=30)
        print(f"  historical events {hdate[:10]}: HTTP {he.status_code} cost={he.headers.get('x-requests-last','?')}")
        if he.status_code == 200:
            data = he.json()
            evs = data.get("data", data) if isinstance(data, dict) else data
            evs = evs if isinstance(evs, list) else []
            print(f"  events: {len(evs)}")
            if evs:
                eid = evs[0]["id"]
                ho = requests.get(f"{ODDS_API_BASE}/historical/sports/baseball_mlb/events/{eid}/odds",
                                  params={"apiKey": api_key, "date": hdate, "regions": "us",
                                          "markets": f"{F5_TOTALS},{F5_H2H}", "oddsFormat": "american"}, timeout=30)
                if ho.status_code == 200:
                    d = ho.json(); ed = d.get("data", d) if isinstance(d, dict) else d
                    mks = {m["key"] for bm in (ed or {}).get("bookmakers", []) for m in bm.get("markets", [])}
                    print(f"  historical F5 present: {F5_TOTALS in mks} | cost={ho.headers.get('x-requests-last','?')} | markets: {sorted(mks)[:6]}")
                else:
                    print(f"  historical event odds: HTTP {ho.status_code} — {ho.text[:140]}")
        else:
            print(f"  {he.text[:160]}")
    except Exception as exc:
        print(f"  historical event probe failed: {exc}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", default=os.environ.get("ODDS_API_KEY", ""))
    args = ap.parse_args()
    if not args.api_key:
        print("ERROR: no key. Run:  ODDS_API_KEY=xxxx python -m pipeline.probe_f5_odds")
        sys.exit(1)
    print("Probing F5 (first-5-innings) on the BULK historical endpoint:")
    print("(cost = x-requests-last per call; F5-totals count = events that returned the F5 line)\n")
    for d in TEST_DATES:
        _probe(args.api_key, d)
    _probe_event_endpoints(args.api_key)


if __name__ == "__main__":
    main()
