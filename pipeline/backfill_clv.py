"""Backfill closing-line snapshots into docs/history.json for CLV (2026 only).

CLV needs two lines per game: the bet-time line (already stored as vegas_total/
under_price, frozen at first capture) and the closing line (never persisted —
only ever transient in games.json). This reconstructs the closing line from The
Odds API historical endpoint: for each resolved 2026 record that fires an UNDER
edge but has no closing snapshot yet, fetch the historical odds snapshot nearest
the game's first pitch and write closing_total/closing_under_price/
closing_over_price. The edge scoreboard then computes CLV vs the bet-time line.

Precision: per game we request the snapshot at the game's first-pitch time (from
the public MLB schedule), so day games (~1 PM) and night games (~7-10 PM) each get
their true close. Calls are deduped into 15-min buckets to save credits.

Scope: 2026 only — pre-2026 had no live bet-time pick (the backtest grades at the
closing line), so CLV is undefined there. Idempotent: records that already have a
closing_total are skipped, so it is safe to re-run.

Usage:
    python -m pipeline.backfill_clv --dry-run                 # no API calls; plan + credit estimate
    python -m pipeline.backfill_clv --api-key $ODDS_API_KEY   # real backfill + scoreboard rebuild
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from pipeline.analytics.edge_detector import detect_edges
from pipeline.backfill_history_odds import _parse_odds_api_event, _norm_team

ROOT = Path(__file__).parent.parent
HISTORY_PATH = ROOT / "docs" / "history.json"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
MLB_API = "https://statsapi.mlb.com/api/v1"
TIMEOUT = 45
BUCKET_MIN = 15          # dedupe snapshot requests into 15-minute buckets
CREDITS_PER_CALL = 10    # historical totals × us region (1 market × 10× multiplier) — for the estimate

log = logging.getLogger(__name__)


def _first_pitch_map(date_str: str) -> dict[int, str]:
    """{gamePk: commence ISO} for a date, from the public MLB schedule (no API key)."""
    try:
        y, m, d = date_str.split("-")
        resp = requests.get(
            f"{MLB_API}/schedule",
            params={"sportId": 1, "date": f"{m}/{d}/{y}"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as exc:
        log.warning("Schedule fetch failed for %s: %s", date_str, exc)
        return {}
    out: dict[int, str] = {}
    for de in resp.json().get("dates", []):
        for g in de.get("games", []):
            pk, gd = g.get("gamePk"), g.get("gameDate")
            if pk and gd:
                out[pk] = gd
    return out


def _bucket(commence_iso: str) -> str:
    """Floor a commence timestamp to a BUCKET_MIN snapshot request time (ISO Z)."""
    dt = datetime.fromisoformat(commence_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    dt = dt.replace(minute=(dt.minute // BUCKET_MIN) * BUCKET_MIN, second=0, microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch_at(api_key: str, iso_ts: str) -> tuple[list, str]:
    """Fetch the Odds API historical snapshot nearest iso_ts. Returns (events, quota)."""
    resp = requests.get(
        f"{ODDS_API_BASE}/sports/baseball_mlb/odds-history/",
        params={"apiKey": api_key, "regions": "us", "markets": "totals",
                "oddsFormat": "american", "date": iso_ts},
        timeout=TIMEOUT,
    )
    quota = resp.headers.get("x-requests-remaining", "?")
    if resp.status_code == 401:
        raise RuntimeError("401 Unauthorized — check ODDS_API_KEY / plan tier")
    if resp.status_code == 429:
        raise RuntimeError("429 quota exhausted")
    if resp.status_code != 200:
        log.warning("HTTP %d for %s", resp.status_code, iso_ts)
        return [], quota
    body = resp.json()
    events = body.get("data", []) if isinstance(body, dict) else body
    return (events if isinstance(events, list) else []), quota


def _candidates(history: list[dict], edges_only: bool = False) -> list[dict]:
    """Resolved 2026 records still missing a closing snapshot.

    Default (edges_only=False) targets EVERY resolved 2026 game — the record grades on the
    CLOSING line, and a game can open far from 8.0/9.0 yet close on it (e.g. 9.5 -> 8.0), so we
    can't pre-filter by the frozen open line without dropping real plays. edges_only=True keeps
    the old narrow behaviour (only games that fired an UNDER edge on the open) for a cheap top-up.
    """
    out = []
    for r in history:
        if r.get("date", "") < "2026-01-01":
            continue
        if r.get("actual_winner") not in ("home", "away"):
            continue
        if r.get("closing_total") is not None:        # idempotent — already backfilled/captured
            continue
        if edges_only:
            if r.get("vegas_total") is None or r.get("under_price") is None:
                continue
            edges = detect_edges(r["vegas_total"], r.get("predicted_total"), r["under_price"])
            if not any(e["direction"] == "UNDER" for e in edges):
                continue
        out.append(r)
    return out


def backfill_clv(api_key: str, dry_run: bool = False, edges_only: bool = False) -> None:
    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    cands = _candidates(history, edges_only=edges_only)
    log.info("Closing-line backfill candidates (resolved 2026, no closing yet, %s): %d",
             "UNDER-edge only" if edges_only else "ALL games", len(cands))
    if not cands:
        log.info("Nothing to backfill.")
        return

    # First-pitch time per candidate, grouped into dedup'd snapshot buckets.
    fp: dict[int, str] = {}
    for ds in sorted({r["date"] for r in cands}):
        fp.update(_first_pitch_map(ds))

    buckets: dict[str, list[dict]] = {}
    missing_fp = 0
    for r in cands:
        commence = fp.get(r["gamePk"])
        if not commence:
            missing_fp += 1
            continue
        buckets.setdefault(_bucket(commence), []).append(r)

    log.info("Distinct snapshots to fetch: %d (≈%d credits) · candidates missing first-pitch: %d",
             len(buckets), len(buckets) * CREDITS_PER_CALL, missing_fp)

    if dry_run:
        log.info("Dry run — no Odds API calls, no write. Sample snapshot times: %s",
                 sorted(buckets)[:6])
        return
    if not api_key:
        log.error("No --api-key / ODDS_API_KEY — cannot fetch. Aborting.")
        return

    patched = 0
    for ts in sorted(buckets):
        try:
            events, quota = _fetch_at(api_key, ts)
        except RuntimeError as exc:
            log.error("Aborting: %s", exc)
            break
        emap: dict[tuple, dict] = {}
        for ev in events:
            rec = _parse_odds_api_event(ev, ts[:10])
            if rec:
                emap[(_norm_team(rec["away_team"]), _norm_team(rec["home_team"]))] = rec
        hit = 0
        for r in buckets[ts]:
            od = emap.get((_norm_team(r["away_team"]), _norm_team(r["home_team"])))
            if not od or od.get("closing_total") is None:
                continue
            r["closing_total"]       = od["closing_total"]
            r["closing_under_price"] = od.get("under_price")
            r["closing_over_price"]  = od.get("over_price")
            r["closing_captured_at"] = ts
            patched += 1
            hit += 1
        log.info("%s → %d events, matched %d/%d (quota %s)", ts, len(events), hit, len(buckets[ts]), quota)
        time.sleep(0.3)

    log.info("Patched closing lines on %d records", patched)
    HISTORY_PATH.write_text(json.dumps(history, separators=(",", ":")), encoding="utf-8")

    # Rebuild the scoreboard so CLV shows immediately.
    from pipeline.edge_scoreboard import build_scoreboard
    sb = build_scoreboard()
    t = sb["actionable_total"]
    log.info("Scoreboard rebuilt — actionable clv_n=%s, beat_pct=%s", t.get("clv_n"), t.get("clv_beat_pct"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(description="Backfill 2026 closing lines for CLV")
    p.add_argument("--api-key", default=os.environ.get("ODDS_API_KEY", ""), help="The Odds API key")
    p.add_argument("--dry-run", action="store_true", help="Plan + credit estimate; no API calls, no write")
    p.add_argument("--edges-only", action="store_true",
                   help="Only games that fired an UNDER edge on the open line (cheap top-up); "
                        "default fetches ALL 2026 games so the closing-line record is complete")
    args = p.parse_args()
    backfill_clv(args.api_key, dry_run=args.dry_run, edges_only=args.edges_only)
