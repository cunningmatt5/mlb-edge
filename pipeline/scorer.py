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


def batter_edge_score(b: dict, sp_throws: str | None = None) -> float | None:
    """Composite 0–100 batter quality index: xwOBA 35% + Hard Hit% 30% + BB% 20% + (1-K%) 15%.

    When sp_throws is 'L' or 'R', uses split-specific xwOBA and K% if available,
    falling back to season stats. Lower K% scores higher (inverted bounds).
    Returns None if no qualifying stats are present.
    """
    suffix = "_vs_l" if sp_throws == "L" else "_vs_r" if sp_throws == "R" else ""
    xwoba = (b.get(f"xwoba{suffix}") if suffix else None) or b.get("xwoba")
    k_pct = (b.get(f"k_pct{suffix}") if suffix else None) or b.get("k_pct")

    raw_map = {
        "xwoba":        xwoba,
        "hard_hit_pct": b.get("hard_hit_pct"),
        "bb_pct":       b.get("bb_pct"),
        "k_pct":        k_pct,
    }
    candidates = [
        ("xwoba",        normalize(xwoba,                  lo=0.240, hi=0.420), 0.35),
        ("hard_hit_pct", normalize(b.get("hard_hit_pct"),  lo=0.25,  hi=0.55),  0.30),
        ("bb_pct",       normalize(b.get("bb_pct"),        lo=0.04,  hi=0.18),  0.20),
        ("k_pct",        normalize(k_pct,                  lo=0.35,  hi=0.12),  0.15),
    ]
    valid = [(v, w) for key, v, w in candidates if raw_map.get(key) is not None]
    if not valid:
        return None
    total_w = sum(w for _, w in valid)
    return round(sum(v * w for v, w in valid) / total_w * 100, 1)


def bullpen_score(bp: dict) -> float:
    """Composite bullpen run-suppression strength on [0, 1]; 1 = elite, 0 = poor.

    Weighted by: xERA (45%), K% (30%), BB% (15%), whiff% (10%).
    Returns 0.5 (neutral) when no data is available.
    """
    pairs: list[tuple[float, float]] = []
    xera = bp.get("xera")
    k_pct = bp.get("k_pct")
    bb_pct = bp.get("bb_pct")
    whiff = bp.get("whiff_pct")
    if xera is not None:
        pairs.append((1.0 - normalize(xera, lo=2.80, hi=5.50), 0.45))
    if k_pct is not None:
        pairs.append((normalize(k_pct, lo=0.18, hi=0.34), 0.30))
    if bb_pct is not None:
        pairs.append((1.0 - normalize(bb_pct, lo=0.06, hi=0.14), 0.15))
    if whiff is not None:
        pairs.append((normalize(whiff, lo=0.18, hi=0.34), 0.10))
    return weighted_avg(pairs) if pairs else 0.5


def lineup_weighted_mean(
    players: list[dict], stat: str, sp_throws: str | None = None
) -> float | None:
    """Batting-order-weighted mean of `stat` across lineup, skipping missing values.

    When sp_throws is provided and stat is 'xwoba' or 'k_pct', uses split-specific
    values ('_vs_l' / '_vs_r') where available, falling back to season stats.
    """
    suffix = ""
    if sp_throws and stat in ("xwoba", "k_pct", "xba", "bb_pct"):
        suffix = "_vs_l" if sp_throws == "L" else "_vs_r"

    def _get(player: dict, i: int):
        v = (player.get(f"{stat}{suffix}") if suffix else None) or player.get(stat)
        return v

    pairs = [
        (_get(players[i], i), _SLOT_WEIGHTS[i] if i < len(_SLOT_WEIGHTS) else 1.0)
        for i in range(len(players))
        if _get(players[i], i) is not None
    ]
    if not pairs:
        return None
    total_w = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / total_w if total_w else None
