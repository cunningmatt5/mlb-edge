"""Shared, dependency-free helpers used across the pipeline.

Kept free of any intra-package imports so it can be imported from anywhere
without risk of circular dependencies.
"""

from __future__ import annotations


def fmt_pct(v) -> str | None:
    """Format a 0–1 ratio as a 1-decimal percent string (0.123 → '12.3%'); None → None."""
    return f"{v:.1%}" if v is not None else None


def american_to_decimal(odds: float) -> float:
    """Convert American odds to decimal (e.g., -110 → 1.909, +120 → 2.2)."""
    if odds >= 0:
        return 1.0 + odds / 100.0
    return 1.0 - 100.0 / odds  # odds is negative → subtracting a negative
