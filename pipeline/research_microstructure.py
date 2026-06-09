"""
MLB Edge — Market-microstructure totals sweep.

The validated edge (UNDER 8.0 at standard vig) is ONE cut of a larger space. The
model doesn't out-predict Vegas, but specific market spots are mispriced. This sweeps
the full market-observable space — line × vig × park × calendar × over/under-price
asymmetry — across all blind-usable games (2021-2026), BLIND (no model fields, so
lookahead-free in every season), to find every robust UNDER/OVER spot.

Anti-overfitting (a broad sweep manufactures false edges):
  - floor: n>=150 AND positive in >=(N-1) of N seasons (n>=20 to count).
  - hold-out: candidates found on TRAIN 2021-24 are PROMOTABLE only if also positive
    on TEST 2025 AND FORWARD 2026.
  - prefer spots with a structural story; report cut count + ~5% expected false +.

Usage:
    python pipeline/research_microstructure.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from edge_research import american_to_decimal, roi_totals  # noqa: F401

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
MIN_SEASON_N = 20
MIN_SLICE_N = 150
TRAIN = {2021, 2022, 2023, 2024}
TEST = {2025}
FWD = {2026}
LINES = [6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0, 10.5]
VIG_TIERS = ["std", "cheap", "mild", "heavy"]   # std=-110..-106, cheap>-106, mild=-120..-111, heavy<-120


def _vig_tier(price) -> str | None:
    if price is None or pd.isna(price):
        return None
    p = float(price)
    if p < -120:
        return "heavy"
    if -110 <= p <= -106:
        return "std"
    if -120 <= p < -110:
        return "mild"
    return "cheap"


def load() -> pd.DataFrame:
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))["games"]
    df = pd.DataFrame(bt)
    df = df[(df["season"] >= 2021) & (df["season"] <= 2025)].copy()
    h = json.loads((ROOT / "docs" / "history.json").read_text(encoding="utf-8"))
    h = h if isinstance(h, list) else h.get("games", [])
    hd = pd.DataFrame(h).rename(columns={"vegas_total": "closing_total"})
    hd["season"] = 2026
    df = pd.concat([df, hd], ignore_index=True)

    for c in ("closing_total", "over_price", "under_price", "home_ml", "away_ml"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df = df.dropna(subset=["closing_total", "over_price", "under_price", "total_went_over"]).copy()
    df = df[(df["closing_total"] >= 5.0) & (df["closing_total"] <= 14.0)].copy()
    hm = df["home_ml"]
    df = df[hm.isna() | hm.between(-600, 600)].copy()
    df = df.drop_duplicates(subset=["gamePk"], keep="first").copy()

    df["line"] = (df["closing_total"] * 2).round() / 2
    df["went_over"] = df["total_went_over"].astype(bool)
    df["under_tier"] = df["under_price"].map(_vig_tier)
    df["over_tier"] = df["over_price"].map(_vig_tier)
    df["price_skew"] = df["under_price"] - df["over_price"]   # +ve → under richer (book shades under)
    from park_factors import get_run_factor
    df["park"] = df["venue"].map(lambda v: _safe_park(get_run_factor, v))
    df["month"] = pd.to_datetime(df["date"], errors="coerce").dt.month
    return df


def _safe_park(fn, v):
    try:
        return float(fn(v or ""))
    except Exception:
        return 100.0


# ── evaluation ──────────────────────────────────────────────────────────────────

def evaluate(df: pd.DataFrame, pick_under: bool) -> dict:
    overall = roi_totals(df, pick_under=pick_under)
    seasons = {int(y): roi_totals(df[df["season"] == y], pick_under=pick_under)
               for y in sorted(df["season"].unique())}
    counted = {y: r for y, r in seasons.items() if r["n"] >= MIN_SEASON_N}
    pos = sum(1 for r in counted.values() if (r["roi_pct"] or 0) > 0)
    nc = len(counted)
    passed = overall["n"] >= MIN_SLICE_N and nc >= 4 and pos >= nc - 1
    return {"overall": overall, "seasons": seasons, "pos": pos, "nc": nc, "passed": passed}


def roi_window(df: pd.DataFrame, years: set, pick_under: bool) -> float:
    r = roi_totals(df[df["season"].isin(years)], pick_under=pick_under)
    return r["roi_pct"] if r["roi_pct"] is not None else 0.0


def seasons_str(res: dict) -> str:
    out = []
    for yr, r in res["seasons"].items():
        if r["n"] == 0:
            continue
        roi = r["roi_pct"] or 0.0
        out.append(f"{str(yr)[2:]}:{'+' if roi >= 0 else ''}{roi:.0f}")
    return " ".join(out)


# ── report ──────────────────────────────────────────────────────────────────────

def run() -> None:
    df = load()
    print("=" * 96)
    print("MARKET-MICROSTRUCTURE TOTALS SWEEP (blind, 2021-2026)")
    print("=" * 96)
    print(f"  games: {len(df)}  |  seasons: {dict(df.groupby('season').size())}")
    print(f"  floor: n>={MIN_SLICE_N} AND >=(N-1)/N seasons +   |   hold-out: TRAIN 2021-24 / TEST 2025 / FWD 2026\n")

    candidates: list[dict] = []   # cells clearing the floor → hold-out check
    cuts_tested = 0

    for pick_under in (True, False):
        d_name = "UNDER" if pick_under else "OVER"
        tier_col = "under_tier" if pick_under else "over_tier"
        print("=" * 96)
        print(f"{d_name}  —  line × vig grid   (vig tiers: std -110..-106, cheap >-106, mild -120..-111, heavy <-120)")
        print("=" * 96)
        print(f"  {'line':>5} {'tier':<6} {'n':>5} {'win%':>6} {'ROI%':>7}  {'sea+':>5}  floor   season ROIs")
        for ln in LINES:
            sub_line = df[abs(df["line"] - ln) < 1e-6]
            for tier in VIG_TIERS:
                sub = sub_line[sub_line[tier_col] == tier]
                if len(sub) < 50:
                    continue
                cuts_tested += 1
                res = evaluate(sub, pick_under)
                o = res["overall"]
                flag = "PASS✓" if res["passed"] else ("fail" if o["n"] >= MIN_SLICE_N else "n<150")
                print(f"  {ln:>5} {tier:<6} {o['n']:>5} {o['win_pct']:>5.1f}% {o['roi_pct']:>+6.1f}%"
                      f"  {res['pos']}/{res['nc']:<3} {flag:<6}  {seasons_str(res)}")
                if res["passed"]:
                    candidates.append({"dir": d_name, "line": ln, "tier": tier, "sub": sub,
                                       "pick_under": pick_under, "res": res})
        print()

    # ── price-asymmetry (does the UNDER edge live where the book is NOT shading the under?) ──
    print("=" * 96)
    print("UNDER × over/under price-skew  (skew = under_price - over_price; +ve = under priced richer)")
    print("=" * 96)
    for lo, hi, lbl in [(-999, -10, "skew <= -10 (under CHEAPER)"),
                        (-10, 0, "skew -10..0"),
                        (0, 10, "skew 0..+10"),
                        (10, 999, "skew >= +10 (under richer)")]:
        sub = df[(df["price_skew"] > lo) & (df["price_skew"] <= hi)]
        res = evaluate(sub, True)
        o = res["overall"]
        print(f"  {lbl:<30} n={o['n']:>5} win={o['win_pct']:>5.1f}% ROI={o['roi_pct']:>+6.1f}%  {res['pos']}/{res['nc']}")

    # ── hold-out validation of every floor-passing candidate ──
    print("\n" + "=" * 96)
    print("HOLD-OUT — floor-passers, TRAIN(2021-24) / TEST(2025) / FORWARD(2026)")
    print("=" * 96)
    promotable = []
    print(f"  {'spot':<30} {'overall':>9} {'TRAIN':>8} {'TEST25':>8} {'FWD26':>8}  verdict")
    for c in sorted(candidates, key=lambda x: -(x["res"]["overall"]["roi_pct"] or 0)):
        pu = c["pick_under"]
        tr = roi_window(c["sub"], TRAIN, pu)
        te = roi_window(c["sub"], TEST, pu)
        fw = roi_window(c["sub"], FWD, pu)
        ok = tr > 0 and te > 0 and fw > 0
        spot = f"{c['dir']} {c['line']} [{c['tier']}]"
        tag = "PROMOTABLE ✓" if ok else ("holds-not-OOS" if (te <= 0 or fw <= 0) else "—")
        print(f"  {spot:<30} {c['res']['overall']['roi_pct']:>+8.1f}% {tr:>+7.1f}% {te:>+7.1f}% {fw:>+7.1f}%  [{tag}]")
        if ok:
            promotable.append((spot, c))

    # ── park interaction on promotable (or top) UNDER spots ──
    pool = promotable if promotable else [(f"{c['dir']} {c['line']} [{c['tier']}]", c) for c in candidates[:3]]
    if pool:
        print("\n── park interaction on top spots (pitcher park <96 vs hitter park >104) ──")
        for spot, c in pool[:4]:
            pu = c["sub"], c["pick_under"]
            for lbl, mask in [("pitcher<96", c["sub"]["park"] < 96), ("neutral 96-104", c["sub"]["park"].between(96, 104)), ("hitter>104", c["sub"]["park"] > 104)]:
                r = roi_totals(c["sub"][mask], pick_under=c["pick_under"])
                if r["n"] >= 40:
                    print(f"    {spot:<26} {lbl:<14} n={r['n']:>4} ROI={r['roi_pct']:>+6.1f}%")

    # ── verdict ──
    print("\n" + "=" * 96)
    print("VERDICT")
    print("=" * 96)
    fp = round(cuts_tested * 0.05, 1)
    print(f"  cuts tested (line×vig cells): {cuts_tested}  →  ~{fp} expected false-positives at p=0.05.")
    print(f"  Promotable spots (clear floor AND positive on BOTH 2025 & 2026 hold-out): {len(promotable)}")
    for spot, _ in promotable:
        print(f"    • {spot}")
    if not promotable:
        print("    none — no spot beyond 8.0 survives the out-of-sample hold-out.")
    print("\n  Promote a spot only with a structural story (key number / vig / park), not just a")
    print("  passing cell — with this many cuts, a few will pass by chance.")
    print("\nDone.")


if __name__ == "__main__":
    run()
