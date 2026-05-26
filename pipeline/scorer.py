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


def batter_edge_score(b: dict) -> float | None:
    """Composite 0–100 batter quality index: xwOBA 35% + Hard Hit% 30% + BB% 20% + (1-K%) 15%.

    Inverted K% bounds (lo=0.30, hi=0.12) so lower K% scores higher.
    Returns None if no qualifying stats are present.
    """
    candidates = [
        ("xwoba",        normalize(b.get("xwoba"),        lo=0.240, hi=0.420), 0.35),
        ("hard_hit_pct", normalize(b.get("hard_hit_pct"), lo=0.25,  hi=0.55),  0.30),
        ("bb_pct",       normalize(b.get("bb_pct"),       lo=0.04,  hi=0.18),  0.20),
        ("k_pct",        normalize(b.get("k_pct"),        lo=0.35,  hi=0.12),  0.15),
    ]
    valid = [(v, w) for key, v, w in candidates if b.get(key) is not None]
    if not valid:
        return None
    total_w = sum(w for _, w in valid)
    return round(sum(v * w for v, w in valid) / total_w * 100, 1)


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
