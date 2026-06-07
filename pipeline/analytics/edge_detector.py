"""Detect quantitative edges for a game based on validated historical backtests.

Each edge has a tag, direction, ROI%, and season consistency metadata.
Call detect_edges() after build_game() and attach the result to game_obj.
"""

from __future__ import annotations


# Metadata for each edge — sourced from edge_research.py output (9,422 games, 2022-2026)
EDGE_METADATA: dict[str, dict] = {
    "UNDER_8_TO_8_5": {
        "tag":        "UNDER_8_TO_8_5",
        "label":      "Under Edge: Total 8.0–8.5",
        "direction":  "UNDER",
        "bet_type":   "TOTAL",
        "roi_pct":    9.82,
        "n_games":    1706,
        "confidence": "high",
        "seasons":    "4/5 seasons profitable (2022–2026)",
        "signal_boost": 0.8,
    },
    "UNDER_9_TO_9_5": {
        "tag":        "UNDER_9_TO_9_5",
        "label":      "Under Edge: Total 9.0–9.5",
        "direction":  "UNDER",
        "bet_type":   "TOTAL",
        "roi_pct":    10.32,
        "n_games":    1483,
        "confidence": "medium",
        "seasons":    "4/5 seasons profitable (strong 2022–2025, 2026 watch)",
        "signal_boost": 0.4,
    },
    "UNDER_MODEL_DEV": {
        "tag":        "UNDER_MODEL_DEV",
        "label":      "Under Edge: Model–Vegas Gap",
        "direction":  "UNDER",
        "bet_type":   "TOTAL",
        "roi_pct":    21.91,
        "n_games":    114,
        "confidence": "validated",
        "seasons":    "2022–2026 (n=114, +21.9% ROI) — validated across 5 seasons",
        "signal_boost": 0.6,
    },
}


def detect_edges(closing_total: float | None, predicted_total: float | None) -> list[dict]:
    """Return matched edge conditions for a game.

    Args:
        closing_total:  Vegas total line (from odds feed)
        predicted_total: Model's predicted run total

    Returns list of matched EDGE_METADATA dicts (copies).
    """
    if closing_total is None:
        return []

    matched: list[dict] = []

    if 8.0 <= closing_total < 8.5:
        matched.append(dict(EDGE_METADATA["UNDER_8_TO_8_5"]))

    if 9.0 <= closing_total < 9.5:
        matched.append(dict(EDGE_METADATA["UNDER_9_TO_9_5"]))

    if (
        predicted_total is not None
        and closing_total is not None
        and (predicted_total - closing_total) <= -0.75
    ):
        matched.append(dict(EDGE_METADATA["UNDER_MODEL_DEV"]))

    return matched
