"""
Realized edge scoreboard — closes the loop on the validated edges.

Grades the bets the app's edge system actually recommends against real outcomes,
so profitability is measured live and decay gets caught fast (e.g. the demoted 9.0
line shows its losing live ROI here). Reuses detect_edges() so the scoreboard tracks
the EXACT edge logic the app ships — no reimplementation / drift.

Reads docs/history.json (resolved live picks, 2026), runs detect_edges() on each
record, grades the UNDER bet at the stored under_price, and writes
docs/edge_scoreboard.json for the UI.

Run standalone:  python -m pipeline.edge_scoreboard
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pipeline.analytics.edge_detector import detect_edges, EDGE_METADATA

log = logging.getLogger(__name__)
ROOT = Path(__file__).parent.parent
HISTORY = ROOT / "docs" / "history.json"
OUT = ROOT / "docs" / "edge_scoreboard.json"
RECENT_DAYS = 30


def _dec(odds: float) -> float:
    odds = float(odds)
    return 1 + odds / 100 if odds >= 0 else 1 - 100 / odds


def _blank() -> dict:
    return {"n": 0, "wins": 0, "units": 0.0, "rn": 0, "runits": 0.0}


def _grade_under(rec: dict) -> tuple[bool, float]:
    """Return (won, units) for a $1 UNDER bet on this resolved record."""
    won = not bool(rec["total_went_over"])
    units = (_dec(rec["under_price"]) - 1) if won else -1.0
    return won, units


def _roi(a: dict) -> float | None:
    return round(a["units"] / a["n"] * 100, 1) if a["n"] else None


def build_scoreboard() -> dict:
    """Compute realized per-edge ROI from history.json and write edge_scoreboard.json."""
    raw = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    recs = raw if isinstance(raw, list) else raw.get("games", [])

    usable = [
        r for r in recs
        if r.get("total_went_over") is not None
        and r.get("vegas_total") is not None
        and r.get("predicted_total") is not None
        and r.get("under_price") is not None
    ]
    cutoff = None
    if usable:
        latest = max(r.get("date", "") for r in usable)
        try:
            from datetime import date, timedelta
            y, m, d = map(int, latest.split("-"))
            cutoff = (date(y, m, d) - timedelta(days=RECENT_DAYS)).isoformat()
        except Exception:
            cutoff = None

    per_edge: dict[str, dict] = {}
    total = _blank()          # deduped over actionable edges (one UNDER bet per game)

    for r in usable:
        edges = detect_edges(r["vegas_total"], r["predicted_total"], r["under_price"])
        under_edges = [e for e in edges if e["direction"] == "UNDER"]
        if not under_edges:
            continue
        won, units = _grade_under(r)
        recent = cutoff is not None and r.get("date", "") >= cutoff

        for e in under_edges:
            a = per_edge.setdefault(e["tag"], _blank())
            a["n"] += 1
            a["wins"] += 1 if won else 0
            a["units"] += units
            if recent:
                a["rn"] += 1
                a["runits"] += units

        # Headline total: grade the UNDER once if ANY actionable (boosted) edge fired.
        if any((e.get("signal_boost") or 0) > 0 for e in under_edges):
            total["n"] += 1
            total["wins"] += 1 if won else 0
            total["units"] += units
            if recent:
                total["rn"] += 1
                total["runits"] += units

    edges_out = []
    for tag, a in per_edge.items():
        meta = EDGE_METADATA.get(tag, {})
        edges_out.append({
            "tag": tag,
            "label": meta.get("label", tag),
            "confidence": meta.get("confidence", "?"),
            "actionable": (meta.get("signal_boost") or 0) > 0,
            "n": a["n"],
            "win_pct": round(a["wins"] / a["n"] * 100, 1) if a["n"] else None,
            "units": round(a["units"], 2),
            "roi_pct": _roi(a),
            "recent_n": a["rn"],
            "recent_roi_pct": round(a["runits"] / a["rn"] * 100, 1) if a["rn"] else None,
        })
    # Order: actionable first, then by ROI desc.
    edges_out.sort(key=lambda e: (not e["actionable"], -(e["roi_pct"] or -999)))

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": 2026,
        "recent_days": RECENT_DAYS,
        "actionable_total": {
            "n": total["n"],
            "win_pct": round(total["wins"] / total["n"] * 100, 1) if total["n"] else None,
            "units": round(total["units"], 2),
            "roi_pct": _roi(total),
            "recent_n": total["rn"],
            "recent_roi_pct": round(total["runits"] / total["rn"] * 100, 1) if total["rn"] else None,
        },
        "edges": edges_out,
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    log.info("Edge scoreboard: %d edges, actionable total n=%d ROI=%s%%",
             len(edges_out), total["n"], out["actionable_total"]["roi_pct"])
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
    sb = build_scoreboard()
    print(json.dumps(sb, indent=2))
