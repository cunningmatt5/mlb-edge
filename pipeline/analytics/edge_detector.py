"""Detect quantitative edges for a game based on validated historical backtests.

Each edge has a tag, direction, ROI%, and season consistency metadata.
Call detect_edges() after build_game() and attach the result to game_obj.

MLB totals are set in 0.5-run increments (7.5, 8.0, 8.5, 9.0, ...), so the
half-open range checks below are equivalent to exact-line matches:
  8.0 <= x < 8.5  ≡  x == 8.0
  9.0 <= x < 9.5  ≡  x == 9.0

The 8.5 line is explicitly NOT an edge: -2.8% blind ROI across 2,171 games.
"""

from __future__ import annotations


# Metadata for each edge — sourced from edge_research.py output (9,436 games, 2022-2026)
EDGE_METADATA: dict[str, dict] = {
    "UNDER_LINE_8_0": {
        "tag":        "UNDER_LINE_8_0",
        "label":      "Under Edge: Total = 8.0",
        "direction":  "UNDER",
        "bet_type":   "TOTAL",
        "roi_pct":    9.06,
        "n_games":    1708,
        "confidence": "high",
        "seasons":    "5/5 seasons profitable blind (2022–2026); model-filtered: +10.9% ROI",
        "signal_boost": 0.8,
    },
    "UNDER_LINE_9_0": {
        "tag":        "UNDER_LINE_9_0",
        "label":      "Under Edge: Total = 9.0",
        "direction":  "UNDER",
        "bet_type":   "TOTAL",
        "roi_pct":    10.41,
        "n_games":    1487,
        "confidence": "medium",
        "seasons":    "4/5 seasons profitable blind (2022–2025); 2026 reversing (-13.9%)",
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
        closing_total:   Vegas total line (from odds feed)
        predicted_total: Model's predicted run total

    Returns list of matched EDGE_METADATA dicts (copies).
    """
    if closing_total is None:
        return []

    matched: list[dict] = []

    # Total = 8.0 — strong blind edge (+9.1% ROI, 5/5 seasons)
    # The half-open range is equivalent to exact-line since totals come in 0.5 steps
    if 8.0 <= closing_total < 8.5:
        matched.append(dict(EDGE_METADATA["UNDER_LINE_8_0"]))

    # Total = 8.5 — explicitly excluded: -2.8% blind ROI across 2,171 games

    # Total = 9.0 — watch edge (+10.4% blind ROI, 4/5 seasons; 2026 reversing)
    if 9.0 <= closing_total < 9.5:
        matched.append(dict(EDGE_METADATA["UNDER_LINE_9_0"]))

    if (
        predicted_total is not None
        and closing_total is not None
        and (predicted_total - closing_total) <= -0.75
    ):
        matched.append(dict(EDGE_METADATA["UNDER_MODEL_DEV"]))

    return matched
