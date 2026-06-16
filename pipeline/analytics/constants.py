"""Shared constants for the analytics scoring modules.

Centralizes values that were previously duplicated or inlined across the
per-bet-type scorers, so tuning and ablation happen in one place.
"""

from __future__ import annotations

# Plate appearances per game by lineup slot (index 0 = leadoff).
# Slots 4–6 ≈ baseline 1.0. Used to scale per-game batter prop volume.
PA_MULT: list[float] = [1.10, 1.05, 1.02, 1.00, 0.98, 0.97, 0.96, 0.93, 0.88]

# Minimum signal (0–10 scale) for a pick to be surfaced, by bet type.
# Higher = more selective. Derived empirically per bet type.
MIN_SIGNAL_HR          = 7.0
MIN_SIGNAL_HIT         = 6.0
MIN_SIGNAL_K           = 6.0
MIN_SIGNAL_TEAM_TOTAL  = 5.0
MIN_SIGNAL_F5_TOTAL    = 5.0
MIN_SIGNAL_MONEYLINE   = 5.0   # full-game ML leg in moneyline_f5
MIN_SIGNAL_F5_ML       = 5.0   # first-5 ML leg in moneyline_f5
MIN_SIGNAL_WALK        = 5.0

# Total-bases props are intentionally DISABLED: backtest showed no signal
# separation across tiers (flat ~28–33% regardless of score). The sentinel
# threshold (>10 max signal) keeps the scorer code intact but emits no picks.
MIN_SIGNAL_TB          = 99.0
