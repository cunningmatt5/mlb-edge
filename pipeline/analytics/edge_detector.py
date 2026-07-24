"""Detect quantitative edges for a game based on validated historical backtests.

Each edge has a tag, direction, ROI%, and season consistency metadata.
Call detect_edges() after build_game() and attach the result to game_obj.

MLB totals are set in 0.5-run increments (7.5, 8.0, 8.5, 9.0, ...), so the
half-open range checks below are equivalent to exact-line matches:
  8.0 <= x < 8.5  ≡  x == 8.0
  9.0 <= x < 9.5  ≡  x == 9.0

The 8.5 line is explicitly NOT an edge: -2.8% blind ROI across 2,171 games.

Vig matters (research_under_8.py, 2021-2026, PUSH-CORRECTED — totals landing exactly on the
line are voided, not counted as UNDER wins):
  At the 8.0 line the UNDER is +5.5% / 4-of-6 seasons at STANDARD vig (under_price -106..-110),
  but only ~0% outside that band — both cheaper-priced unders (the market already leans under)
  and vig-against unders (-111..-120) underperform, and it's negative when priced worse than -120.
  So detect_edges() gives the boost ONLY in the standard-vig band and suppresses it otherwise.
  (Earlier metadata claimed +12.6% / 6-of-6; that was inflated entirely by pushes graded as wins.)
"""

from __future__ import annotations

# Vig thresholds for the UNDER price (American odds; more negative = more expensive).
_VIG_STD_LO   = -110   # standard-vig favorable zone is [-110, -106]
_VIG_STD_HI   = -106

# Metadata for each edge — PUSH-CORRECTED ROI/season figures (research_under_8.py + edge_research.py
# re-run with pushes voided). All ROI is realized at a flat $1 UNDER stake, pushes refunded.
EDGE_METADATA: dict[str, dict] = {
    "UNDER_LINE_8_0": {
        "tag":        "UNDER_LINE_8_0",
        "label":      "Under Edge: Total = 8.0",
        "direction":  "UNDER",
        "bet_type":   "TOTAL",
        "roi_pct":    5.47,
        "n_games":    429,
        "confidence": "high",
        "seasons":    "4/6 seasons profitable (2021–2026) at standard vig, push-corrected",
        "season_roi": {"2021": -4.5, "2022": 10.1, "2023": -0.2, "2024": 9.4, "2025": 2.4, "2026": 10.1},
        "signal_boost": 0.8,   # std-vig band only; suppressed otherwise in detect_edges()
    },
    "UNDER_LINE_9_0": {
        "tag":        "UNDER_LINE_9_0",
        "label":      "Under Edge: Total = 9.0",
        "direction":  "UNDER",
        "bet_type":   "TOTAL",
        "roi_pct":    2.55,
        "n_games":    1405,
        "confidence": "watch",
        "seasons":    "barely +2.6% 2021–2025 (push-corrected), then -11.3% in 2026 — watch only",
        "season_roi": {"2021": 8.6, "2022": -1.3, "2023": 1.8, "2024": 2.9, "2025": 2.4, "2026": -11.3},
        "signal_boost": 0.0,   # do not move live signals: marginal historically, negative live
    },
    "UNDER_MODEL_DEV": {
        "tag":        "UNDER_MODEL_DEV",
        "label":      "Under Edge: Model–Vegas Gap",
        "direction":  "UNDER",
        "bet_type":   "TOTAL",
        "roi_pct":    32.58,
        "n_games":    46,
        "confidence": "emerging",
        "seasons":    "2026-only by construction (predicted_total was Vegas-anchored pre-2026); lines ≤9.0, small n",
        "season_roi": {"2026": 32.6},
        "signal_boost": 0.3,   # single season, small sample → modest boost
    },
}


def _vig_adjusted_boost(base_boost: float, under_price: float | None) -> float | None:
    """Boost the 8.0 UNDER edge ONLY in the standard-vig band; suppress otherwise.

    Push-corrected, the edge lives only at standard vig (-110..-106, +5.5%). Cheaper-priced
    unders (~0%, the market already leans under) and vig-against unders (-111..-120, ~0%/neg)
    don't clear the bar, so they get no boost. under_price None → base boost (can't assess vig).
    """
    if under_price is None:
        return base_boost
    if _VIG_STD_LO <= under_price <= _VIG_STD_HI:   # standard-vig band: the only validated zone
        return base_boost
    return None                              # outside the std band → suppress


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

    # Model–Vegas gap — emerging, 2026-only, concentrated at lines ≤9.0. Fills the gap at
    # the 8.5/9.0 lines where the 8.0 edge doesn't fire.
    if (
        predicted_total is not None
        and closing_total <= 9.0
        and (predicted_total - closing_total) <= -0.75
    ):
        e = dict(EDGE_METADATA["UNDER_MODEL_DEV"])
        e["under_price"] = under_price
        matched.append(e)

    return matched
