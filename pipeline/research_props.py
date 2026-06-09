"""
MLB Edge — Assess the player props (HR/HIT/K/TB) the app surfaces as "edges".

Rigorous per-bet ROI can't be computed: prop odds were never ingested (free Odds
API tier = h2h,totals only), so docs/props_history.json stores hit/miss but no
odds_line/ev_pct. This script does the next-best honest thing — compares the
model-selected picks' HIT RATE (2026) to a representative BREAK-EVEN for each
prop type's typical price, and checks whether the signal ranks anything.

Caveats (printed in the verdict): no stored odds → break-even/ROI are approximate;
2026-only; only the picks the model selected (signal >= threshold), so no
counterfactual on the full population. A losing read under these limits is still
damning.

Usage:
    python pipeline/research_props.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent

# Representative (conservative, prop-favorable) prices per type → decimal + break-even.
# american_to_decimal: +o → 1+o/100 ; -o → 1-100/o
def _dec(american: float) -> float:
    return 1 + american / 100 if american >= 0 else 1 - 100 / american

REP_PRICE = {   # bet_type: representative American odds for an OVER 0.5 / std prop
    "HR_PROP":  450,    # "to hit a HR" — typically +350..+550
    "HIT_PROP": -150,   # "to record a hit" — typically -130..-250
    "K_PROP":   -120,   # "SP Ks over X.5" — typically -115..-130
    "TB_PROP":  120,    # "total bases over 1.5" — typically +100..+140
}


def breakeven(american: float) -> float:
    return 1.0 / _dec(american)


def approx_roi(hit_rate: float, american: float) -> float:
    """Flat-stake ROI% at the representative price."""
    return (hit_rate * _dec(american) - 1.0) * 100


def run() -> None:
    recs = json.loads((ROOT / "docs" / "props_history.json").read_text(encoding="utf-8"))
    res = [r for r in recs if r.get("hit") is not None]
    print("=" * 88)
    print("PLAYER PROPS ASSESSMENT (props_history.json, 2026, model-selected picks)")
    print("=" * 88)
    dates = sorted({r["date"] for r in res})
    print(f"  resolved picks: {len(res)}  |  span: {dates[0]} → {dates[-1]}")
    print(f"  NOTE: prop ODDS were never stored → ROI/break-even below are APPROXIMATE,")
    print(f"        using a representative (prop-favorable) price per type.\n")

    by_type: dict[str, list] = {}
    for r in res:
        by_type.setdefault(r["bet_type"], []).append(r)

    print(f"  {'type':<10}{'n':>5}{'hit%':>8}{'rep price':>11}{'break-even':>12}{'approx ROI':>12}  verdict")
    print("  " + "-" * 80)
    any_edge = False
    for t in sorted(by_type):
        rs = by_type[t]
        n = len(rs)
        hr = sum(1 for r in rs if r["hit"]) / n
        price = REP_PRICE.get(t)
        if price is None:
            print(f"  {t:<10}{n:>5}{hr*100:>7.1f}%   (no representative price)")
            continue
        be = breakeven(price)
        roi = approx_roi(hr, price)
        edge = roi > 0
        any_edge = any_edge or edge
        verdict = "EDGE?" if edge else "no edge (−EV)"
        ps = f"+{price}" if price > 0 else str(price)
        print(f"  {t:<10}{n:>5}{hr*100:>7.1f}%{ps:>11}{be*100:>11.1f}%{roi:>+11.1f}%  {verdict}")

    # Does the signal rank? hit-rate by signal band, per type.
    print("\n── Does the signal rank props into a better band? (hit% by signal band) ──")
    for t in sorted(by_type):
        rs = by_type[t]
        price = REP_PRICE.get(t)
        be = breakeven(price) * 100 if price else None
        parts = []
        for lo, hi, lbl in [(5, 6, "5-6"), (6, 7, "6-7"), (7, 11, "7+")]:
            b = [r for r in rs if lo <= (r.get("signal") or 0) < hi]
            if not b:
                continue
            h = sum(1 for r in b if r["hit"]) / len(b) * 100
            parts.append(f"{lbl}:{len(b)}g/{h:.0f}%")
        be_str = f"  (break-even ~{be:.0f}%)" if be else ""
        print(f"  {t:<10} {'  '.join(parts)}{be_str}")

    print("\n" + "=" * 88)
    print("VERDICT")
    print("=" * 88)
    if any_edge:
        print("  At least one type shows positive approx ROI — investigate with REAL odds.")
    else:
        print("  No prop type clears break-even at representative prices → the props the app")
        print("  frames as 'edge' are NOT a demonstrated profitable edge (−EV on 2026 hit-rates).")
    print("  Caveat: approximate (no stored odds), 2026-only, model-selected. Real per-bet ROI")
    print("  requires ingesting the PAID prop odds markets (batter_home_runs, pitcher_strikeouts,")
    print("  team_totals, h2h_h1); snapshot_picks already persists odds once they're fetched.")
    print("  Team totals / F5 / ML_F5 are not tracked at all → cannot even be hit-rate-checked.")
    print("\nDone.")


if __name__ == "__main__":
    run()
