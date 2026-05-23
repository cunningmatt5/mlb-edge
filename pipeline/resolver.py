"""Grade PENDING picks against MLB box score results and maintain pick history."""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import requests

MLB_API = "https://statsapi.mlb.com/api/v1"
TIMEOUT = 15
HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "docs", "picks_history.json"
)
MAX_HISTORY_DAYS = 365

log = logging.getLogger(__name__)


def load_history() -> dict:
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return _empty_history()


def save_history(history: dict) -> None:
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, default=str)
    log.info("picks_history.json saved: %d picks total", len(history["picks"]))


def archive_picks(history: dict, game_blocks: list[dict], date_str: str) -> None:
    """Append today's qualifying picks to history as PENDING (deduplicates)."""
    existing = {
        (p["date"], p["game_pk"], p["bet_type"], p["subject"], p["direction"])
        for p in history["picks"]
    }
    added = 0
    for game in game_blocks:
        game_pk = game["gamePk"]
        for pick in game["picks"]:
            key = (date_str, game_pk, pick["bet_type"], pick["subject"], pick["direction"])
            if key in existing:
                continue
            history["picks"].append({
                "date": date_str,
                "game_pk": game_pk,
                "bet_type": pick["bet_type"],
                "subject": pick["subject"],
                "subject_id": pick.get("subject_id"),
                "subject_side": pick.get("subject_side"),
                "direction": pick["direction"],
                "headline": pick["headline"],
                "signal": pick["signal"],
                "tier": pick.get("tier"),
                "raw_scores": pick.get("raw_scores"),
                "outcome": "PENDING",
                "actual_value": None,
            })
            existing.add(key)
            added += 1
    log.info("Archived %d new picks to history", added)
    _prune_history(history)
    _rebuild_summary(history)


def resolve_pending(history: dict) -> None:
    """Grade all PENDING picks whose game has a final box score."""
    graded = 0
    for pick in history["picks"]:
        if pick["outcome"] != "PENDING":
            continue
        try:
            if _grade_pick(pick):
                graded += 1
        except Exception as exc:
            log.warning("Grade failed for %s %s: %s", pick["bet_type"], pick["subject"], exc)
    if graded:
        log.info("Graded %d previously-pending picks", graded)
        _rebuild_summary(history)


# ---------------------------------------------------------------------------
# Internal grading
# ---------------------------------------------------------------------------

def _grade_pick(pick: dict) -> bool:
    """Return True if the pick was successfully graded."""
    game_pk = pick["game_pk"]
    if not _is_game_final(game_pk):
        return False

    bet_type = pick["bet_type"]
    direction = pick["direction"]
    subject_id = pick.get("subject_id")
    subject_side = pick.get("subject_side")

    if bet_type in ("K_PROP", "HR_PROP", "HIT_PROP", "TB_PROP", "WALK_PROP"):
        if not subject_id:
            return False
        actual = _player_stat(game_pk, int(subject_id), bet_type)
        if actual is None:
            return False
        pick["actual_value"] = actual
        pick["outcome"] = _grade_player(bet_type, direction, actual)

    elif bet_type == "TOTAL":
        ls = _linescore(game_pk)
        if not ls:
            return False
        total = ls["home_runs"] + ls["away_runs"]
        pick["actual_value"] = total
        pick["outcome"] = "WIN" if (direction == "OVER" and total > 8) or (direction == "UNDER" and total < 9) else "LOSS"

    elif bet_type == "TEAM_TOTAL":
        ls = _linescore(game_pk)
        if not ls:
            return False
        side = subject_side or "home"
        runs = ls.get(f"{side}_runs", 0)
        pick["actual_value"] = runs
        if direction == "OVER":
            pick["outcome"] = "WIN" if runs >= 5 else "LOSS"
        else:
            pick["outcome"] = "WIN" if runs <= 3 else "LOSS"

    elif bet_type == "ML_F5":
        ls = _linescore(game_pk)
        if not ls:
            return False
        home_r, away_r = ls["home_runs"], ls["away_runs"]
        pick["actual_value"] = f"Home {home_r} - Away {away_r}"
        if home_r == away_r:
            pick["outcome"] = "LOSS"  # tie = loss (no push tracking)
        elif direction == "HOME":
            pick["outcome"] = "WIN" if home_r > away_r else "LOSS"
        else:
            pick["outcome"] = "WIN" if away_r > home_r else "LOSS"
    else:
        return False

    return True


def _grade_player(bet_type: str, direction: str, actual: float) -> str:
    if bet_type == "K_PROP":
        return "WIN" if actual >= 6 else "LOSS"
    if bet_type == "HR_PROP":
        return "WIN" if actual >= 1 else "LOSS"
    if bet_type == "HIT_PROP":
        return "WIN" if actual >= 1 else "LOSS"
    if bet_type == "TB_PROP":
        return "WIN" if actual >= 2 else "LOSS"
    if bet_type == "WALK_PROP":
        if direction == "UNDER":
            return "WIN" if actual <= 1 else "LOSS"
        return "WIN" if actual >= 2 else "LOSS"
    return "PENDING"


# ---------------------------------------------------------------------------
# MLB Stats API helpers
# ---------------------------------------------------------------------------

def _is_game_final(game_pk: int) -> bool:
    try:
        url = f"{MLB_API}/schedule?gamePk={game_pk}"
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        dates = r.json().get("dates", [])
        if not dates:
            return False
        game = dates[0].get("games", [{}])[0]
        return game.get("status", {}).get("codedGameState") == "F"
    except Exception:
        return False


def _linescore(game_pk: int) -> Optional[dict]:
    try:
        r = requests.get(f"{MLB_API}/game/{game_pk}/linescore", timeout=TIMEOUT)
        r.raise_for_status()
        teams = r.json().get("teams", {})
        return {
            "home_runs": teams.get("home", {}).get("runs", 0),
            "away_runs": teams.get("away", {}).get("runs", 0),
        }
    except Exception:
        return None


def _player_stat(game_pk: int, player_id: int, bet_type: str) -> Optional[float]:
    _STAT_MAP = {
        "K_PROP":    ("pitching", "strikeOuts"),
        "WALK_PROP": ("pitching", "baseOnBalls"),
        "HR_PROP":   ("batting",  "homeRuns"),
        "HIT_PROP":  ("batting",  "hits"),
        "TB_PROP":   ("batting",  "totalBases"),
    }
    stat_side, stat_key = _STAT_MAP[bet_type]
    try:
        r = requests.get(f"{MLB_API}/game/{game_pk}/boxscore", timeout=TIMEOUT)
        r.raise_for_status()
        teams = r.json().get("teams", {})
        key = f"ID{player_id}"
        for side in ("home", "away"):
            players = teams.get(side, {}).get("players", {})
            if key in players:
                val = players[key].get("stats", {}).get(stat_side, {}).get(stat_key)
                if val is not None:
                    return float(val)
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Summary and housekeeping
# ---------------------------------------------------------------------------

def _rebuild_summary(history: dict) -> None:
    picks = history["picks"]
    wins    = sum(1 for p in picks if p["outcome"] == "WIN")
    losses  = sum(1 for p in picks if p["outcome"] == "LOSS")
    pending = sum(1 for p in picks if p["outcome"] == "PENDING")
    graded  = wins + losses

    by_type: dict = {}
    by_band: dict = {
        "5.0-5.9": {"total": 0, "wins": 0, "losses": 0, "win_rate": None},
        "6.0-6.9": {"total": 0, "wins": 0, "losses": 0, "win_rate": None},
        "7.0-7.9": {"total": 0, "wins": 0, "losses": 0, "win_rate": None},
        "8.0-8.9": {"total": 0, "wins": 0, "losses": 0, "win_rate": None},
        "9.0+":    {"total": 0, "wins": 0, "losses": 0, "win_rate": None},
    }
    by_tier: dict = {
        "ELITE":     {"total": 0, "wins": 0, "losses": 0, "win_rate": None},
        "GREAT":     {"total": 0, "wins": 0, "losses": 0, "win_rate": None},
        "APPEALING": {"total": 0, "wins": 0, "losses": 0, "win_rate": None},
    }

    for p in picks:
        if p["outcome"] == "PENDING":
            continue
        outcome_key = "wins" if p["outcome"] == "WIN" else "losses"

        bt = p["bet_type"]
        if bt not in by_type:
            by_type[bt] = {"total": 0, "wins": 0, "losses": 0, "win_rate": None}
        by_type[bt]["total"] += 1
        by_type[bt][outcome_key] += 1

        sig = p.get("signal", 0)
        if sig >= 9.0:
            band = "9.0+"
        elif sig >= 8.0:
            band = "8.0-8.9"
        elif sig >= 7.0:
            band = "7.0-7.9"
        elif sig >= 6.0:
            band = "6.0-6.9"
        else:
            band = "5.0-5.9"
        by_band[band]["total"] += 1
        by_band[band][outcome_key] += 1

        tier = p.get("tier")
        if tier in by_tier:
            by_tier[tier]["total"] += 1
            by_tier[tier][outcome_key] += 1

    for d in list(by_type.values()) + list(by_band.values()) + list(by_tier.values()):
        t = d["total"]
        d["win_rate"] = round(d["wins"] / t, 3) if t > 0 else None

    history["summary"] = {
        "total": wins + losses + pending,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "win_rate": round(wins / graded, 3) if graded > 0 else None,
        "by_type": by_type,
        "by_signal_band": by_band,
        "by_tier": by_tier,
    }


def _prune_history(history: dict) -> None:
    """Remove picks older than MAX_HISTORY_DAYS."""
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=MAX_HISTORY_DAYS)).isoformat()
    history["picks"] = [p for p in history["picks"] if p.get("date", "9999") >= cutoff]


def _empty_history() -> dict:
    return {
        "picks": [],
        "summary": {
            "total": 0, "wins": 0, "losses": 0, "pending": 0, "win_rate": None,
            "by_type": {},
            "by_signal_band": {
                "5.0-5.9": {"total": 0, "wins": 0, "losses": 0, "win_rate": None},
                "6.0-6.9": {"total": 0, "wins": 0, "losses": 0, "win_rate": None},
                "7.0-7.9": {"total": 0, "wins": 0, "losses": 0, "win_rate": None},
                "8.0-8.9": {"total": 0, "wins": 0, "losses": 0, "win_rate": None},
                "9.0+":    {"total": 0, "wins": 0, "losses": 0, "win_rate": None},
            },
            "by_tier": {
                "ELITE":     {"total": 0, "wins": 0, "losses": 0, "win_rate": None},
                "GREAT":     {"total": 0, "wins": 0, "losses": 0, "win_rate": None},
                "APPEALING": {"total": 0, "wins": 0, "losses": 0, "win_rate": None},
            },
        },
    }
