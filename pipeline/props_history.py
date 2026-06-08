"""Track and resolve daily prop picks.

Snapshot today's picks.json into props_history.json each morning.
Resolve each pick against MLB boxscore data the following day.

Usage:
    python -m pipeline.props_history --snapshot   # save today's picks
    python -m pipeline.props_history --resolve     # fill in hit/miss for past picks
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path

import requests

DOCS_DIR   = Path(__file__).parent.parent / "docs"
PICKS_PATH = DOCS_DIR / "picks.json"
PROPS_PATH = DOCS_DIR / "props_history.json"
MLB_API    = "https://statsapi.mlb.com/api/v1"
TIMEOUT    = 15

# Minimum signal threshold to snapshot into history (avoid storing noise)
_MIN_SIGNAL = 5.0

# Resolution thresholds — what "OVER" means for each prop type
# HR_PROP:  anytime home run → binary (≥1 HR)
# HIT_PROP: anytime hit → binary (≥1 H)
# K_PROP:   SP strikeouts over 4.5 → ≥5 K  (common low anchor)
RESOLUTION_THRESHOLDS = {
    "HR_PROP":  ("batting",  "homeRuns",    1),
    "HIT_PROP": ("batting",  "hits",        1),
    "K_PROP":   ("pitching", "strikeOuts",  5),
}

log = logging.getLogger(__name__)


# ── Storage helpers ───────────────────────────────────────────────────────────

def load_props_history() -> list[dict]:
    if PROPS_PATH.exists():
        try:
            return json.loads(PROPS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_props_history(records: list[dict]) -> None:
    PROPS_PATH.write_text(
        json.dumps(records, separators=(",", ":")),
        encoding="utf-8",
    )
    log.info("Props history saved: %d records", len(records))


# ── Snapshot ──────────────────────────────────────────────────────────────────

def snapshot_picks(today_str: str | None = None) -> int:
    """Read picks.json and append unresolved records to props_history.json.

    Skips pick types that aren't resolvable (TEAM_TOTAL, TOTAL, ML_F5).
    Returns the number of new records added.
    """
    if not PICKS_PATH.exists():
        log.warning("picks.json not found — nothing to snapshot")
        return 0

    today_str = today_str or date.today().isoformat()
    picks_data = json.loads(PICKS_PATH.read_text(encoding="utf-8"))

    resolvable = set(RESOLUTION_THRESHOLDS.keys())
    history    = load_props_history()
    existing   = {(r["gamePk"], r["bet_type"], r.get("subject_id"), r["date"])
                  for r in history}

    added = 0
    for game in picks_data.get("games", []):
        pk = game.get("gamePk")
        if not pk:
            continue

        for pick in game.get("picks", []):
            bt = pick.get("bet_type", "")
            if bt not in resolvable:
                continue
            if pick.get("signal", 0) < _MIN_SIGNAL:
                continue

            subject_id = pick.get("subject_id")
            key = (pk, bt, subject_id, today_str)
            if key in existing:
                continue

            odds = pick.get("odds") or {}
            history.append({
                "date":         today_str,
                "gamePk":       pk,
                "home_team":    game.get("home_team", ""),
                "away_team":    game.get("away_team", ""),
                "bet_type":     bt,
                "subject":      pick.get("subject", ""),
                "subject_id":   subject_id,
                "direction":    pick.get("direction", "OVER"),
                "signal":       pick.get("signal"),
                "edge_score":   (pick.get("raw_scores") or {}).get("edge_score"),
                "odds_line":    odds.get("line"),
                "implied_prob": odds.get("implied_prob"),
                "ev_pct":       odds.get("edge_pct"),
                "hit":          None,
                "actual_value": None,
            })
            existing.add(key)
            added += 1

    save_props_history(history)
    log.info("Props snapshot: %d new records for %s", added, today_str)
    return added


# ── Resolution ────────────────────────────────────────────────────────────────

def _fetch_boxscore(game_pk: int) -> dict:
    """Return {playerId: {stat_group: {stat: value}}} from the MLB boxscore API."""
    try:
        resp = requests.get(f"{MLB_API}/game/{game_pk}/boxscore", timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("Boxscore fetch failed for gamePk %s: %s", game_pk, exc)
        return {}

    result: dict[int, dict] = {}
    for side in ("home", "away"):
        players = data.get("teams", {}).get(side, {}).get("players", {})
        for key, player in players.items():
            try:
                pid = int(key.replace("ID", ""))
            except ValueError:
                continue
            stats = player.get("stats", {})
            result[pid] = {
                "batting":  stats.get("batting", {}),
                "pitching": stats.get("pitching", {}),
            }
    return result


def resolve_props(history: list[dict]) -> list[dict]:
    """Fill in hit/actual_value for all unresolved past-date picks."""
    today_str = date.today().isoformat()
    pending   = [r for r in history
                 if r.get("hit") is None and r.get("date", "") < today_str]

    if not pending:
        log.info("No pending prop picks to resolve")
        return history

    # Group by gamePk — one boxscore fetch per game
    by_pk: dict[int, list[dict]] = {}
    for r in pending:
        by_pk.setdefault(r["gamePk"], []).append(r)

    log.info("Resolving %d prop picks across %d game(s)", len(pending), len(by_pk))
    resolved = 0

    for pk, records in by_pk.items():
        boxscore = _fetch_boxscore(pk)
        if not boxscore:
            continue

        for record in records:
            bt        = record["bet_type"]
            threshold = RESOLUTION_THRESHOLDS.get(bt)
            if not threshold:
                continue

            stat_group, stat_key, over_line = threshold
            subject_id = record.get("subject_id")
            if not subject_id:
                continue

            player_stats = boxscore.get(int(subject_id), {})
            actual = player_stats.get(stat_group, {}).get(stat_key)

            if actual is None:
                continue  # player didn't appear (DNP), leave unresolved

            record["actual_value"] = actual
            record["hit"]          = (actual >= over_line)
            resolved += 1

    log.info("Resolved %d/%d prop picks", resolved, len(pending))
    return history


# ── Performance summary (for app) ────────────────────────────────────────────

def compute_performance(history: list[dict]) -> dict:
    """Aggregate hit rates by prop type and signal band."""
    resolved = [r for r in history if r.get("hit") is not None]
    if not resolved:
        return {"by_type": [], "by_signal": [], "total": {"n": 0, "hits": 0, "hit_rate": None}}

    # By prop type
    by_type: dict[str, dict] = {}
    for r in resolved:
        bt = r["bet_type"]
        if bt not in by_type:
            by_type[bt] = {"n": 0, "hits": 0}
        by_type[bt]["n"]    += 1
        by_type[bt]["hits"] += int(bool(r["hit"]))

    type_rows = []
    for bt, d in sorted(by_type.items()):
        hr = round(d["hits"] / d["n"] * 100, 1) if d["n"] else None
        type_rows.append({"bet_type": bt, "n": d["n"], "hits": d["hits"], "hit_rate": hr})

    # By signal band
    bands = [("5.0–5.9", 5.0, 6.0), ("6.0–6.4", 6.0, 6.5), ("6.5+", 6.5, 99.0)]
    signal_rows = []
    for label, lo, hi in bands:
        subset = [r for r in resolved if lo <= (r.get("signal") or 0) < hi]
        if not subset:
            continue
        hits = sum(int(bool(r["hit"])) for r in subset)
        hr   = round(hits / len(subset) * 100, 1)
        signal_rows.append({"band": label, "n": len(subset), "hits": hits, "hit_rate": hr})

    total_hits = sum(int(bool(r["hit"])) for r in resolved)
    total_hr   = round(total_hits / len(resolved) * 100, 1) if resolved else None

    return {
        "by_type":   type_rows,
        "by_signal": signal_rows,
        "total":     {"n": len(resolved), "hits": total_hits, "hit_rate": total_hr},
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Manage props pick history")
    parser.add_argument("--snapshot", action="store_true", help="Save today's picks to props_history.json")
    parser.add_argument("--resolve",  action="store_true", help="Resolve past pick outcomes from boxscores")
    args = parser.parse_args()

    if args.snapshot:
        n = snapshot_picks()
        print(f"Snapshotted {n} new prop picks")

    if args.resolve:
        hist = load_props_history()
        hist = resolve_props(hist)
        save_props_history(hist)
        perf = compute_performance(hist)
        print(f"\nPerformance ({perf['total']['n']} resolved picks):")
        for r in perf["by_type"]:
            print(f"  {r['bet_type']:<15} n={r['n']:>4}  hit={r['hit_rate']}%")
        print("\nBy signal:")
        for r in perf["by_signal"]:
            print(f"  {r['band']:<10} n={r['n']:>4}  hit={r['hit_rate']}%")
