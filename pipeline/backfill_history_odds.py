"""Backfill Pinnacle closing lines into docs/history.json.

Fetches odds for every date in history.json that has resolved games but
no moneyline data, using The Odds API historical endpoint (two snapshots
per date: 1 PM ET + 6 PM ET).

Usage:
    python -m pipeline.backfill_history_odds --api-key $ODDS_API_KEY
    python -m pipeline.backfill_history_odds --api-key $ODDS_API_KEY --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import re

import requests

from pipeline.odds import no_vig_prob

# ── Inline from odds_historical (avoids pulling in the pandas dependency) ──
ODDS_API_BASE   = "https://api.the-odds-api.com/v4"
_PREFERRED_BOOKS = ["pinnacle", "draftkings", "fanduel", "betmgm", "williamhill_us"]
_SNAPSHOTS       = ["T17:00:00Z", "T22:00:00Z"]


def _norm_team(name: str) -> str:
    n = re.sub(r"[^a-z0-9]", "", str(name).lower())
    if n.endswith("athletics"):
        return "athletics"
    return n


def _parse_odds_api_event(event: dict, date: str) -> Optional[dict]:
    home_team  = event.get("home_team", "")
    away_team  = event.get("away_team", "")
    bookmakers = event.get("bookmakers", [])
    bm_by_key  = {bm["key"]: bm for bm in bookmakers}

    bm = None
    for key in _PREFERRED_BOOKS:
        if key in bm_by_key:
            bm = bm_by_key[key]
            break
    if bm is None and bookmakers:
        bm = bookmakers[0]
    if bm is None:
        return None

    markets = {m["key"]: m for m in bm.get("markets", [])}

    home_ml = away_ml = None
    if "h2h" in markets:
        hn = _norm_team(home_team)
        an = _norm_team(away_team)
        for outcome in markets["h2h"].get("outcomes", []):
            on = _norm_team(outcome.get("name", ""))
            if on == hn:
                home_ml = outcome.get("price")
            elif on == an:
                away_ml = outcome.get("price")

    closing_total = over_price = under_price = None
    if "totals" in markets:
        for outcome in markets["totals"].get("outcomes", []):
            nm = outcome.get("name", "").lower()
            if nm == "over":
                closing_total = outcome.get("point")
                over_price    = outcome.get("price")
            elif nm == "under":
                under_price = outcome.get("price")

    return {
        "date":          date,
        "home_team":     home_team,
        "away_team":     away_team,
        "home_ml":       home_ml,
        "away_ml":       away_ml,
        "closing_total": closing_total,
        "over_price":    over_price  if over_price  else -110,
        "under_price":   under_price if under_price else -110,
    }

HISTORY_PATH = Path(__file__).parent.parent / "docs" / "history.json"
TIMEOUT      = 45

log = logging.getLogger(__name__)


def _fetch_snapshot(api_key: str, date: str, snap: str) -> tuple[list, str]:
    """Fetch one Odds API historical snapshot. Returns (events, quota_remaining)."""
    url = f"{ODDS_API_BASE}/sports/baseball_mlb/odds-history/"
    params = {
        "apiKey":     api_key,
        "regions":    "us",
        "markets":    "h2h,totals",
        "oddsFormat": "american",
        "date":       f"{date}{snap}",
    }
    resp = requests.get(url, params=params, timeout=TIMEOUT)
    quota = resp.headers.get("x-requests-remaining", "?")

    if resp.status_code == 401:
        raise RuntimeError("401 Unauthorized — check ODDS_API_KEY and plan tier")
    if resp.status_code == 429:
        raise RuntimeError("429 quota exhausted")
    if resp.status_code == 422:
        log.warning("422 for %s%s — date out of history window", date, snap)
        return [], quota
    if resp.status_code != 200:
        log.warning("HTTP %d for %s%s", resp.status_code, date, snap)
        return [], quota

    body   = resp.json()
    events = body.get("data", []) if isinstance(body, dict) else body
    return (events if isinstance(events, list) else []), quota


def _build_odds_map(api_key: str, dates: list[str]) -> dict[tuple[str, str], dict]:
    """Fetch odds for all dates. Returns {(norm_away, norm_home): odds_dict}."""
    # For each date, fetch both snapshots; later snapshot wins on conflict.
    result: dict[tuple[str, str], dict] = {}

    for i, date in enumerate(sorted(dates)):
        date_events: dict[str, dict] = {}  # event_id → event (later snapshot wins)
        for snap in _SNAPSHOTS:
            try:
                events, quota = _fetch_snapshot(api_key, date, snap)
                log.info(
                    "%s%s → %d events  (quota remaining: %s)",
                    date, snap, len(events), quota,
                )
                for ev in events:
                    eid = ev.get("id")
                    if eid:
                        date_events[eid] = ev
            except RuntimeError as exc:
                log.error("Aborting: %s", exc)
                return result
            except Exception as exc:
                log.warning("Request error for %s%s: %s", date, snap, exc)

            if i < len(dates) - 1 or snap != _SNAPSHOTS[-1]:
                time.sleep(0.3)

        matched = 0
        for event in date_events.values():
            rec = _parse_odds_api_event(event, date)
            if not rec:
                continue
            key = (_norm_team(rec["away_team"]), _norm_team(rec["home_team"]))
            result[key] = rec
            matched += 1
        log.info("%s: %d games parsed from API", date, matched)

    return result


def backfill(api_key: str, dry_run: bool = False) -> None:
    if not HISTORY_PATH.exists():
        log.error("history.json not found at %s", HISTORY_PATH)
        return

    history: list[dict] = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    log.info("Loaded %d history records", len(history))

    # Find dates that have resolved records missing moneyline data
    unpriced_dates: set[str] = set()
    for r in history:
        if r.get("actual_winner") in ("home", "away") and r.get("home_ml") is None:
            unpriced_dates.add(r["date"])

    if not unpriced_dates:
        log.info("All resolved records already have line data — nothing to backfill")
        return

    log.info(
        "Found %d records without odds across %d date(s)",
        sum(1 for r in history if r.get("actual_winner") in ("home", "away") and r.get("home_ml") is None),
        len(unpriced_dates),
    )

    odds_map = _build_odds_map(api_key, sorted(unpriced_dates))
    if not odds_map:
        log.warning("No odds fetched — nothing to patch")
        return

    matched = 0
    unmatched = 0
    for r in history:
        if r.get("home_ml") is not None:
            continue
        if r.get("actual_winner") not in ("home", "away"):
            continue
        key = (_norm_team(r.get("away_team", "")), _norm_team(r.get("home_team", "")))
        odds = odds_map.get(key)
        if not odds:
            unmatched += 1
            continue

        home_ml = odds.get("home_ml")
        away_ml = odds.get("away_ml")
        if home_ml is None or away_ml is None:
            unmatched += 1
            continue

        model_edge_ml: Optional[float] = None
        if r.get("home_win_pct") is not None:
            try:
                pinnacle_home_prob, _ = no_vig_prob(int(home_ml), int(away_ml))
                model_edge_ml = round(float(r["home_win_pct"]) - pinnacle_home_prob, 4)
            except Exception:
                pass

        r["home_ml"]       = home_ml
        r["away_ml"]       = away_ml
        r["vegas_total"]   = odds.get("closing_total")
        r["over_price"]    = odds.get("over_price")
        r["under_price"]   = odds.get("under_price")
        r["model_edge_ml"] = model_edge_ml

        # Compute total_went_over if we now have vegas_total and actual_total
        if r.get("vegas_total") is not None and r.get("home_score") is not None and r.get("away_score") is not None:
            actual_total = (r["home_score"] or 0) + (r["away_score"] or 0)
            r["actual_total"]     = actual_total
            r["total_went_over"]  = actual_total > r["vegas_total"]

        matched += 1

    log.info("Matched %d records, unmatched %d", matched, unmatched)

    if dry_run:
        log.info("Dry run — not writing history.json")
        sample = [r for r in history if r.get("home_ml") is not None][:5]
        for r in sample:
            log.info(
                "  %s @ %s (%s): home_ml=%s away_ml=%s edge=%.3f",
                r["away_team"], r["home_team"], r["date"],
                r.get("home_ml"), r.get("away_ml"), r.get("model_edge_ml") or 0,
            )
        return

    HISTORY_PATH.write_text(json.dumps(history, separators=(",", ":")), encoding="utf-8")
    log.info("Wrote history.json — %d total records, %d now priced", len(history), matched)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Backfill history.json with Pinnacle closing lines")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("ODDS_API_KEY", ""),
        help="The Odds API key (or set ODDS_API_KEY env var)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing file")
    args = parser.parse_args()

    if not args.api_key:
        parser.error("--api-key is required (or set ODDS_API_KEY env var)")

    backfill(args.api_key, dry_run=args.dry_run)
