"""Track game prediction accuracy by stat signal.

Daily pipeline appends today's predictions; a separate resolve step
(run the morning after games) fills in actual_winner/scores.

Usage:
    python -m pipeline.history --resolve    # resolve yesterday's results
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, timedelta
from pathlib import Path

import requests

DOCS_DIR     = Path(__file__).parent.parent / "docs"
HISTORY_PATH = DOCS_DIR / "history.json"
MLB_API      = "https://statsapi.mlb.com/api/v1"
TIMEOUT      = 15

log = logging.getLogger(__name__)


def load_history() -> list[dict]:
    if HISTORY_PATH.exists():
        try:
            return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_history(records: list[dict]) -> None:
    HISTORY_PATH.write_text(
        json.dumps(records, separators=(",", ":")),
        encoding="utf-8",
    )
    log.info("History saved: %d records", len(records))


def append_today(history: list[dict], games: list[dict], today_str: str) -> list[dict]:
    """Add today's prediction records (unresolved) to history."""
    existing_pks = {r["gamePk"] for r in history}
    added = 0
    for g in games:
        pk = g["gamePk"]
        if pk in existing_pks:
            continue
        pred    = g.get("prediction", {})
        signals = pred.get("model_signals", {})
        history.append({
            "date":               today_str,
            "gamePk":             pk,
            "home_team":          g["home_team"],
            "away_team":          g["away_team"],
            "predicted_winner":   "home" if pred.get("home_win_pct", 0) >= 0.5 else "away",
            "home_win_pct":       pred.get("home_win_pct"),
            "pitcher_score_home": signals.get("pitcher_score_home"),
            "pitcher_score_away": signals.get("pitcher_score_away"),
            "lineup_score_home":  signals.get("lineup_score_home"),
            "lineup_score_away":  signals.get("lineup_score_away"),
            "comps_home_win_rate": signals.get("comps_home_win_rate"),
            "actual_winner":      None,
            "home_score":         None,
            "away_score":         None,
        })
        added += 1
    log.info("History: appended %d new prediction records", added)
    return history


def resolve_yesterday(history: list[dict]) -> list[dict]:
    """Fetch yesterday's final scores and fill in actual_winner on pending records."""
    yesterday   = (date.today() - timedelta(days=1)).isoformat()
    pending     = [r for r in history if r["actual_winner"] is None and r["date"] == yesterday]
    if not pending:
        log.info("No pending records to resolve for %s", yesterday)
        return history

    log.info("Resolving %d records for %s...", len(pending), yesterday)

    try:
        url    = f"{MLB_API}/schedule"
        params = {
            "sportId": 1,
            "date":    yesterday.replace("-", "/"),
            "hydrate": "linescore",
        }
        resp   = requests.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data   = resp.json()
        scores = _parse_scores(data)
    except Exception as exc:
        log.warning("Could not fetch scores for %s: %s", yesterday, exc)
        return history

    by_pk = {r["gamePk"]: r for r in pending}
    resolved = 0
    for pk, record in by_pk.items():
        score = scores.get(pk)
        if not score:
            continue
        home_score = score["home_score"]
        away_score = score["away_score"]
        if home_score is None or away_score is None:
            continue
        record["home_score"]    = home_score
        record["away_score"]    = away_score
        record["actual_winner"] = "home" if home_score > away_score else "away"
        resolved += 1

    log.info("Resolved %d/%d records", resolved, len(pending))
    return history


def _parse_scores(schedule_data: dict) -> dict[int, dict]:
    """Return {gamePk: {home_score, away_score}} from a schedule API response."""
    results: dict[int, dict] = {}
    for date_entry in schedule_data.get("dates", []):
        for raw in date_entry.get("games", []):
            pk = raw.get("gamePk")
            if not pk:
                continue
            teams = raw.get("teams", {})
            home  = teams.get("home", {})
            away  = teams.get("away", {})
            ls    = raw.get("linescore", {})
            home_score = ls.get("teams", {}).get("home", {}).get("runs") if ls else None
            away_score = ls.get("teams", {}).get("away", {}).get("runs") if ls else None
            if home_score is None:
                home_score = home.get("score")
            if away_score is None:
                away_score = away.get("score")
            results[pk] = {"home_score": home_score, "away_score": away_score}
    return results


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Resolve yesterday's game predictions")
    parser.add_argument("--resolve", action="store_true", help="Fetch scores and resolve pending predictions")
    args = parser.parse_args()
    if args.resolve:
        hist = load_history()
        hist = resolve_yesterday(hist)
        save_history(hist)
    else:
        parser.print_help()
