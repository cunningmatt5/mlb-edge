"""Shared scoring utilities used by all analytics modules."""

from __future__ import annotations


def normalize(value: float | None, lo: float, hi: float) -> float:
    """Clamp and linearly scale a raw stat to [0.0, 1.0].

    lo and hi represent roughly the p5 and p95 of the MLB population for
    that stat. Returns 0.5 (neutral) when value is None.
    """
    if value is None:
        return 0.5
    if hi == lo:
        return 0.5
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def weighted_avg(pairs: list[tuple[float, float]]) -> float:
    """Weighted average of (value, weight) pairs. Weights need not sum to 1."""
    total_w = sum(w for _, w in pairs)
    if total_w == 0:
        return 0.5
    return sum(v * w for v, w in pairs) / total_w


def safe_mean(values: list[float | None]) -> float | None:
    """Mean of a list, ignoring None values. Returns None if list is empty."""
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


# Batting-order position weights: slots 1-4 weighted 1.3x, 5-7 at 1.0x, 8-9 at 0.75x
_SLOT_WEIGHTS = [1.3, 1.3, 1.3, 1.3, 1.0, 1.0, 1.0, 0.75, 0.75]


def lineup_weighted_mean(players: list[dict], stat: str) -> float | None:
    """Batting-order-weighted mean of `stat` across lineup, skipping missing values."""
    pairs = [
        (players[i].get(stat), _SLOT_WEIGHTS[i] if i < len(_SLOT_WEIGHTS) else 1.0)
        for i in range(len(players))
        if players[i].get(stat) is not None
    ]
    if not pairs:
        return None
    total_w = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / total_w if total_w else None
