"""Detect quantitative edges for a game based on validated historical backtests.

Each edge has a tag, direction, ROI%, and season consistency metadata.
Call detect_edges() after build_game() and attach the result to game_obj.

MLB totals are set in 0.5-run increments (7.5, 8.0, 8.5, 9.0, ...), so the
half-open range checks below are equivalent to exact-line matches:
  8.0 <= x < 8.5  ≡  x == 8.0
  9.0 <= x < 9.5  ≡  x == 9.0

The 8.5 line is explicitly NOT an edge: -2.8% blind ROI across 2,171 games.

Vig matters (research_under_8.py, PUSH-CORRECTED — totals landing exactly on the line are
voided, not counted as UNDER wins):
  At the 8.0 line the UNDER is solidly positive at STANDARD vig (under_price -106..-110), but
  only ~0% outside that band — both cheaper-priced unders (the market already leans under) and
  vig-against unders (-111..-120) underperform, and it's negative when priced worse than -120.
  So detect_edges() gives the boost ONLY in the standard-vig band and suppresses it otherwise.
  (The exact ROI is recomputed live from the reconciled backtest data and shown in the app; it
  is deliberately NOT hardcoded here, so it can never drift from the data.)
"""

from __future__ import annotations

# Vig thresholds for the UNDER price (American odds; more negative = more expensive).
_VIG_STD_LO   = -110   # standard-vig favorable zone is [-110, -106]
_VIG_STD_HI   = -106

# Metadata for each edge — PUSH-CORRECTED ROI/season figures (research_under_8.py + edge_research.py
# re-run with pushes voided). All ROI is realized at a flat $1 UNDER stake, pushes refunded.
EDGE_METADATA: dict[str, dict] = {
    # NOTE: no hardcoded roi_pct / n_games / season_roi here. The frontend computes every
    # displayed historical figure live from the reconciled backtest data (edgeHistorical →
    # computeUnderEdge in app.js), so there is no frozen constant to drift from the data. This
    # dict carries only what the pipeline needs to FIRE and LABEL an edge.
    "UNDER_LINE_8_0": {
        "tag":        "UNDER_LINE_8_0",
        "label":      "Under Edge: Total = 8.0",
        "direction":  "UNDER",
        "bet_type":   "TOTAL",
        "confidence": "high",
        "signal_boost": 0.8,   # std-vig band only; suppressed otherwise in detect_edges()
    },
    "UNDER_LINE_9_0": {
        "tag":        "UNDER_LINE_9_0",
        "label":      "Under Edge: Total = 9.0",
        "direction":  "UNDER",
        "bet_type":   "TOTAL",
        "confidence": "watch",
        "signal_boost": 0.0,   # do not move live signals: marginal historically, negative live
    },
    # UNDER_MODEL_DEV (Model–Vegas Gap) was DEMOTED and removed after un-anchoring the model
    # prediction (predicted_total_raw) and testing it historically: at its 0.75-run threshold it
    # was -4.7% over 2022-2025 (worse than a blind under), and the only positive threshold (>=1.5)
    # was 75% one season (2023) and flipped negative on any nearby cut. The 2026 "+35.8%" that
    # made it look emerging used the Vegas-anchored total, which structurally can't disagree by
    # much and never fired pre-2026 — a small-sample artifact. The market prices totals
    # efficiently (see score_prediction). Kept as disclosure in the app's "what we tested" table.
}


def detect_edges(
    closing_total: float | None,
    predicted_total: float | None,
    under_price: float | None = None,
) -> list[dict]:
    """Return matched edge conditions for a game.

    Both edges fire on the LINE alone — 8.0 (validated) and 9.0 (watch). The old 8.0
    model-lean requirement was dropped: it halved the play volume for no ROI gain (blind
    std-vig +6.2%/n=545 vs +model-lean +6.6%/n=243, identical win rate) and made the live
    picks a different bet than the displayed record. 8.0 is surfaced at ALL prices with a
    `price_status`, so the app informs rather than hides — a user can shop their books for the
    payable window instead of the game vanishing because one book's price is off.

    Args:
        closing_total:   Vegas total line (from odds feed)
        predicted_total: Model's predicted run total (retained for callers; no longer gates 8.0)
        under_price:     American odds on the UNDER (sets 8.0 price_status / boost)

    Returns list of matched EDGE_METADATA dicts (copies).
    """
    if closing_total is None:
        return []

    matched: list[dict] = []

    # Total = 8.0 — validated UNDER edge, fires on the line alone. price_status buckets the
    # current price so the UI can say "playable now" vs "shop for the number":
    #   playable = std-vig window [-110,-106], the only +EV zone (full boost 0.8)
    #   shop     = steeper than -110 — the edge is real, this book's price isn't (boost 0)
    #   weaker   = cheaper than -106 — market has already moved, edge is thinner (boost 0)
    if 8.0 <= closing_total < 8.5:
        e = dict(EDGE_METADATA["UNDER_LINE_8_0"])
        e["under_price"] = under_price
        if under_price is None or _VIG_STD_LO <= under_price <= _VIG_STD_HI:
            e["signal_boost"] = EDGE_METADATA["UNDER_LINE_8_0"]["signal_boost"]  # 0.8
            e["price_status"] = "playable"
        elif under_price < _VIG_STD_LO:                # steeper (more negative) than -110
            e["signal_boost"] = 0.0
            e["price_status"] = "shop"
        else:                                          # cheaper than -106
            e["signal_boost"] = 0.0
            e["price_status"] = "weaker"
        matched.append(e)

    # Total = 8.5 — explicitly excluded: -2.8% blind ROI across 2,171 games

    # Total = 9.0 — WATCH only. Fires on the line alone, zero boost so it informs without
    # moving the live signal.
    if 9.0 <= closing_total < 9.5:
        e = dict(EDGE_METADATA["UNDER_LINE_9_0"])
        e["under_price"] = under_price
        matched.append(e)

    # (No Model–Vegas Gap edge — demoted; see EDGE_METADATA note above.)
    return matched
