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
