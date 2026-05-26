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
    from pipeline.odds import no_vig_prob
    existing_pks = {r["gamePk"] for r in history}
    added = 0
    for g in games:
        pk = g["gamePk"]
        if pk in existing_pks:
            continue
        pred    = g.get("prediction", {})
        signals = pred.get("model_signals", {})
        odds    = g.get("odds") or {}

        # Compute model edge vs Pinnacle no-vig ML probability
        home_ml = odds.get("home_ml")
        away_ml = odds.get("away_ml")
        model_edge_ml = None
        if home_ml is not None and away_ml is not None:
            try:
                pinnacle_home_prob, _ = no_vig_prob(int(home_ml), int(away_ml))
                model_edge_ml = round((pred.get("home_win_pct") or 0.5) - pinnacle_home_prob, 4)
            except Exception:
                pass

        history.append({
            "date":                    today_str,
            "gamePk":                  pk,
            "home_team":               g["home_team"],
            "away_team":               g["away_team"],
            "predicted_winner":        "home" if pred.get("home_win_pct", 0) >= 0.5 else "away",
            "home_win_pct":            pred.get("home_win_pct"),
            "predicted_total":         pred.get("predicted_total"),
            "predicted_home_sp_id":    g.get("home_sp_id"),
            "predicted_away_sp_id":    g.get("away_sp_id"),
            "pitcher_score_home":      signals.get("pitcher_score_home"),
            "pitcher_score_away":      signals.get("pitcher_score_away"),
            "lineup_score_home":       signals.get("lineup_score_home"),
            "lineup_score_away":       signals.get("lineup_score_away"),
            "comps_home_win_rate":     signals.get("comps_home_win_rate"),
            # Vegas lines — stored for forward-looking ROI tracking
            "vegas_total":             odds.get("total"),
            "over_price":              odds.get("over_price"),
            "under_price":             odds.get("under_price"),
            "home_ml":                 home_ml,
            "away_ml":                 away_ml,
            "model_edge_ml":           model_edge_ml,
            "actual_winner":           None,
            "home_score":              None,
            "away_score":              None,
            "sp_scratched":            False,
        })
        added += 1
    log.info("History: appended %d new prediction records", added)
    return history


def resolve_yesterday(history: list[dict]) -> list[dict]:
    """Fetch final scores for all pending (unresolved) past-game records.

    Groups pending records by date and makes one schedule API call per date,
    so missed games from multiple days ago are caught up automatically.
    """
    today_str = date.today().isoformat()
    pending   = [r for r in history if r["actual_winner"] is None and r["date"] < today_str]
    if not pending:
        log.info("No pending records to resolve")
        return history

    dates_pending: dict[str, list[dict]] = {}
    for r in pending:
        dates_pending.setdefault(r["date"], []).append(r)

    log.info("Resolving %d records across %d date(s): %s",
             len(pending), len(dates_pending), sorted(dates_pending))

    total_resolved = 0
    for date_str in sorted(dates_pending):
        try:
            d = date.fromisoformat(date_str)
            url    = f"{MLB_API}/schedule"
            params = {"sportId": 1, "date": d.strftime("%m/%d/%Y"), "hydrate": "linescore"}
            resp   = requests.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            scores = _parse_scores(resp.json())
        except Exception as exc:
            log.warning("Could not fetch scores for %s: %s", date_str, exc)
            continue

        by_pk = {r["gamePk"]: r for r in dates_pending[date_str]}
        resolved = 0
        for pk, record in by_pk.items():
            score = scores.get(pk)
            if not score:
                continue
            home_score = score["home_score"]
            away_score = score["away_score"]
            if home_score is None or away_score is None:
                continue
            if home_score == 0 and away_score == 0:
                continue  # suspended/postponed — do not resolve
            record["home_score"]    = home_score
            record["away_score"]    = away_score
            record["actual_winner"] = (
                "home" if home_score > away_score
                else "away" if away_score > home_score
                else "tie"
            )
            actual_total = home_score + away_score
            record["actual_total"] = actual_total
            if record.get("vegas_total") is not None:
                record["total_went_over"] = actual_total > record["vegas_total"]
            starters = _get_actual_starters(pk)
            if starters:
                pred_home_sp = record.get("predicted_home_sp_id")
                pred_away_sp = record.get("predicted_away_sp_id")
                scratched = False
                if pred_home_sp and starters.get("home_sp_id") and pred_home_sp != starters["home_sp_id"]:
                    scratched = True
                if pred_away_sp and starters.get("away_sp_id") and pred_away_sp != starters["away_sp_id"]:
                    scratched = True
                record["sp_scratched"] = scratched
            resolved += 1

        log.info("Resolved %d/%d records for %s", resolved, len(by_pk), date_str)
        total_resolved += resolved

    log.info("Total resolved: %d/%d records", total_resolved, len(pending))
    return history


def _get_actual_starters(gamePk: int) -> dict | None:
    """Fetch actual starting pitcher IDs from the boxscore after the game is final."""
    try:
        url  = f"{MLB_API}/game/{gamePk}/boxscore"
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        teams = data.get("teams", {})

        def _first_pitcher(side: str) -> int | None:
            pitchers = teams.get(side, {}).get("pitchers", [])
            return pitchers[0] if pitchers else None

        home_sp = _first_pitcher("home")
        away_sp = _first_pitcher("away")
        if home_sp or away_sp:
            return {"home_sp_id": home_sp, "away_sp_id": away_sp}
    except Exception as exc:
        log.debug("Could not fetch boxscore for gamePk %d: %s", gamePk, exc)
    return None


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
