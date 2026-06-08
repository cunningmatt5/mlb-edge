"""Detect quantitative edges for a game based on validated historical backtests.

Each edge has a tag, direction, ROI%, and season consistency metadata.
Call detect_edges() after build_game() and attach the result to game_obj.

MLB totals are set in 0.5-run increments (7.5, 8.0, 8.5, 9.0, ...), so the
half-open range checks below are equivalent to exact-line matches:
  8.0 <= x < 8.5  ≡  x == 8.0
  9.0 <= x < 9.5  ≡  x == 9.0

The 8.5 line is explicitly NOT an edge: -2.8% blind ROI across 2,171 games.

Vig matters (research_under_8.py, 2021-2026):
  At the 8.0 line the UNDER is a clean +12.6% / 6-of-6 seasons at STANDARD vig
  (under_price -106..-110), weaker (+6%) at -111..-120, and NEGATIVE (-20.7%)
  when priced worse than -120. The book charging heavy vig against the UNDER is
  itself a signal not to take it. detect_edges() therefore tiers the 8.0 boost by
  vig and suppresses the edge when the UNDER is priced worse than -120.
"""

from __future__ import annotations

# Vig thresholds for the UNDER price (American odds; more negative = more expensive).
_VIG_STD_LO   = -110   # standard-vig favorable zone is [-110, -106]
_VIG_STD_HI   = -106
_VIG_SUPPRESS = -120   # worse than this (e.g. -125) → UNDER unprofitable, suppress

# Metadata for each edge — ROI/season figures from research_under_8.py + edge_research.py.
EDGE_METADATA: dict[str, dict] = {
    "UNDER_LINE_8_0": {
        "tag":        "UNDER_LINE_8_0",
        "label":      "Under Edge: Total = 8.0",
        "direction":  "UNDER",
        "bet_type":   "TOTAL",
        "roi_pct":    12.59,
        "n_games":    458,
        "confidence": "high",
        "seasons":    "6/6 seasons profitable (2021–2026) at standard vig",
        "season_roi": {"2021": 8.2, "2022": 16.8, "2023": 6.1, "2024": 15.8, "2025": 9.3, "2026": 19.5},
        "signal_boost": 0.8,   # full boost; tiered down by vig in detect_edges()
    },
    "UNDER_LINE_9_0": {
        "tag":        "UNDER_LINE_9_0",
        "label":      "Under Edge: Total = 9.0",
        "direction":  "UNDER",
        "bet_type":   "TOTAL",
        "roi_pct":    11.87,
        "n_games":    1699,
        "confidence": "watch",
        "seasons":    "strong 2021–2025 (+11.9%) but COLLAPSED in 2026 (-13.9%) — watch only",
        "season_roi": {"2021": 21.9, "2022": 9.9, "2023": 12.0, "2024": 12.2, "2025": 13.8, "2026": -13.9},
        "signal_boost": 0.0,   # do not move live signals: the live season is negative
    },
    "UNDER_MODEL_DEV": {
        "tag":        "UNDER_MODEL_DEV",
        "label":      "Under Edge: Model–Vegas Gap",
        "direction":  "UNDER",
        "bet_type":   "TOTAL",
        "roi_pct":    35.86,
        "n_games":    42,
        "confidence": "emerging",
        "seasons":    "2026-only by construction (predicted_total was Vegas-anchored pre-2026); lines ≤9.0, small n",
        "season_roi": {"2026": 35.9},
        "signal_boost": 0.3,   # single season, small sample → modest boost
    },
}


def _vig_adjusted_boost(base_boost: float, under_price: float | None) -> float | None:
    """Scale an UNDER edge's boost by the vig on the UNDER price.

    Returns the adjusted boost, or None to suppress the edge entirely.
    under_price None (no odds) → return base boost unchanged (can't assess vig).
    """
    if under_price is None:
        return base_boost
    if under_price < _VIG_SUPPRESS:          # worse than -120: UNDER is unprofitable
        return None
    if _VIG_STD_LO <= under_price <= _VIG_STD_HI:   # standard vig: the strong zone
        return base_boost
    return round(base_boost * 0.5, 2)        # cheaper-than-std or mild vig-against: half boost


def detect_edges(
    closing_total: float | None,
    predicted_total: float | None,
    under_price: float | None = None,
) -> list[dict]:
    """Return matched edge conditions for a game.

    Line-based edges (8.0) require the Vegas total at a profitable line AND the
    model leaning UNDER — a structural line edge without model agreement is
    context, not a signal. The 8.0 boost is then tiered by vig (see module docs).

    The 9.0 line is attached as a WATCH tag only (zero boost): historically strong
    but the live 2026 season reversed, so it must not move live signals.

    Args:
        closing_total:   Vegas total line (from odds feed)
        predicted_total: Model's predicted run total
        under_price:     American odds on the UNDER (optional; gates 8.0 vig tiering)

    Returns list of matched EDGE_METADATA dicts (copies, with vig-adjusted boost).
    """
    if closing_total is None:
        return []

    model_leans_under = predicted_total is not None and predicted_total < closing_total

    matched: list[dict] = []

    # Total = 8.0 — strong edge, vig-tiered (+12.6% at standard vig, 6/6 seasons).
    # Half-open range == exact line since totals come in 0.5 steps.
    if 8.0 <= closing_total < 8.5 and model_leans_under:
        boost = _vig_adjusted_boost(EDGE_METADATA["UNDER_LINE_8_0"]["signal_boost"], under_price)
        if boost is not None:   # None → heavy vig against UNDER, suppress
            e = dict(EDGE_METADATA["UNDER_LINE_8_0"])
            e["signal_boost"] = boost
            e["under_price"] = under_price
            matched.append(e)

    # Total = 8.5 — explicitly excluded: -2.8% blind ROI across 2,171 games

    # Total = 9.0 — WATCH only. Fires on the line alone (the model filter HURTS here),
    # but with zero boost so it informs the UI without moving the live signal.
    if 9.0 <= closing_total < 9.5:
        e = dict(EDGE_METADATA["UNDER_LINE_9_0"])
        e["under_price"] = under_price
        matched.append(e)

    # Model–Vegas gap — emerging, 2026-only, concentrated at lines ≤9.0.
    if (
        predicted_total is not None
        and closing_total <= 9.0
        and (predicted_total - closing_total) <= -0.75
    ):
        matched.append(dict(EDGE_METADATA["UNDER_MODEL_DEV"]))

    return matched
