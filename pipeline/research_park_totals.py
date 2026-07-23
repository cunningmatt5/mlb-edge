"""MLB Edge research — does PARK FACTOR add a backtestable totals edge?

Park is the one run-environment driver we have but never tested on the totals market
(venue is in backtest.json; pipeline/park_factors.get_run_factor maps it to a run index,
100 = neutral, <100 pitcher-friendly, >100 hitter-friendly). The market prices park into
the line, so the question is whether park has RESIDUAL edge — e.g. the line under-adjusts at
extremes, making UNDER at pitcher parks (or OVER at hitter parks) profitable beyond baseline.

Backtestable now on full-game totals (the only market with multi-season closing odds). Grading
is PUSH-CORRECT from the start (totals landing exactly on the line are voided, not UNDER wins).

Usage:  python -m pipeline.research_park_totals
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.utils import american_to_decimal          # noqa: E402
from pipeline.park_factors import get_run_factor          # noqa: E402

ROOT = Path(__file__).parent.parent
MIN_SLICE_N = 100
MIN_SEASON_N = 20
LINES = [("7.5", 7.5), ("8.0", 8.0), ("8.5", 8.5), ("9.0", 9.0), ("9.5", 9.5), ("10.0+", 10.0)]


def _load() -> pd.DataFrame:
    frames = []
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))["games"]
    frames.append(pd.DataFrame(bt))
    hp = ROOT / "docs" / "history.json"
    if hp.exists():
        raw = json.loads(hp.read_text(encoding="utf-8"))
        h = raw if isinstance(raw, list) else raw.get("games", [])
        hdf = pd.DataFrame(h)
        if not hdf.empty:
            if "closing_total" not in hdf.columns and "vegas_total" in hdf.columns:
                hdf = hdf.rename(columns={"vegas_total": "closing_total"})
            elif "vegas_total" in hdf.columns:
                hdf["closing_total"] = hdf["closing_total"].fillna(hdf["vegas_total"])
            hdf = hdf.loc[:, ~hdf.columns.duplicated()].copy()
            hdf["season"] = 2026
            frames.append(hdf)
    df = pd.concat(frames, ignore_index=True)
    df = df.loc[:, ~df.columns.duplicated()].copy()
    df = df.dropna(subset=["closing_total", "under_price", "over_price", "actual_total", "gamePk", "venue"]).copy()
    df = df[(df["closing_total"] >= 5.0) & (df["closing_total"] <= 14.0)].copy()
    df = df.drop_duplicates(subset=["gamePk"], keep="last").copy()
    df["season"] = df["season"].astype(int)
    df["pf"] = df["venue"].map(get_run_factor)
    return df


def _roi(df: pd.DataFrame, pick_under: bool) -> dict:
    """Push-correct flat-$1 ROI. Pushes (actual == line) are voided (excluded)."""
    bets = wins = 0
    units = 0.0
    per_season: dict[int, list] = {}
    for r in df.itertuples():
        line, at = r.closing_total, r.actual_total
        if abs(at - line) < 1e-9:      # push → void
            continue
        won = (at < line) if pick_under else (at > line)
        price = r.under_price if pick_under else r.over_price
        u = (american_to_decimal(price) - 1) if won else -1.0
        bets += 1
        wins += 1 if won else 0
        units += u
        s = per_season.setdefault(int(r.season), [0, 0.0])
        s[0] += 1
        s[1] += u
    if not bets:
        return {"n": 0, "win": None, "roi": None, "season_roi": {}}
    sr = {y: round(v[1] / v[0] * 100, 1) for y, v in sorted(per_season.items()) if v[0] >= MIN_SEASON_N}
    return {"n": bets, "win": round(wins / bets * 100, 1), "roi": round(units / bets * 100, 2),
            "season_roi": sr, "_seasons_raw": per_season}


def _passes(res: dict) -> bool:
    counted = res.get("season_roi", {})
    losers = sum(1 for v in counted.values() if v <= 0)
    return res["n"] >= MIN_SLICE_N and len(counted) >= 3 and losers <= 1


def _line_mask(df, line):
    return df["closing_total"] >= 10.0 if line >= 10.0 else (df["closing_total"] >= line) & (df["closing_total"] < line + 0.25)


def _fmt(res, base_roi=None):
    if not res["n"]:
        return "n=0"
    tag = "  <- PASS" if _passes(res) else ""
    lift = f"  (lift {res['roi'] - base_roi:+.1f})" if base_roi is not None and res["roi"] is not None else ""
    return f"n={res['n']:>5} win={res['win']:>5.1f}% roi={res['roi']:>+7.2f}%{lift}{tag}"


def run():
    df = _load()
    pitcher = df["pf"] <= 96
    neutral = (df["pf"] > 96) & (df["pf"] < 104)
    hitter  = df["pf"] >= 104
    print("=" * 96)
    print("PARK-FACTOR TOTALS EDGE — does park have residual edge beyond the priced line? (push-corrected)")
    print("=" * 96)
    print(f"games: {len(df):,} | park buckets: pitcher(<=96)={pitcher.sum():,} neutral={neutral.sum():,} hitter(>=104)={hitter.sum():,}")
    print(f"seasons: {sorted(df['season'].unique())}\n")

    passes = []

    print("SANITY  blind UNDER 8.0 (all parks):", _fmt(_roi(df[_line_mask(df, 8.0)], True)))
    print()

    print("TEST 1 — UNDER by line: all parks vs PITCHER-friendly parks (<=96)")
    for lbl, ln in LINES:
        base = _roi(df[_line_mask(df, ln)], True)
        sub  = _roi(df[_line_mask(df, ln) & pitcher], True)
        print(f"  {lbl:>6} all: {_fmt(base)}")
        print(f"  {lbl:>6} pf:  {_fmt(sub, base['roi'])}")
        if _passes(sub): passes.append((f"UNDER {lbl} @ pitcher park", sub))
    print()

    print("TEST 2 — OVER by line at HITTER-friendly parks (>=104)")
    for lbl, ln in LINES:
        base = _roi(df[_line_mask(df, ln)], False)
        sub  = _roi(df[_line_mask(df, ln) & hitter], False)
        print(f"  {lbl:>6} over all: {_fmt(base)} | hitter-park: {_fmt(sub, base['roi'])}")
        if _passes(sub): passes.append((f"OVER {lbl} @ hitter park", sub))
    print()

    print("TEST 3 — UNDER (all lines pooled) by park tier")
    for name, m in [("pitcher <=96", pitcher), ("neutral 97-103", neutral), ("hitter >=104", hitter)]:
        print(f"  {name:>16}: {_fmt(_roi(df[m], True))}")
    print()

    print("TEST 4 — extreme pitcher parks (<=92) UNDER, std vig (-110..-106)")
    sub = df[(df["pf"] <= 92) & (df["under_price"] <= -106) & (df["under_price"] >= -110)]
    r = _roi(sub, True); print("  ", _fmt(r))
    if _passes(r): passes.append(("UNDER extreme-pitcher-park std-vig", r))
    print()

    print("=" * 96)
    if passes:
        print("SLICES CLEARING THE FLOOR (n>=100, >=3 seasons, <=1 losing):")
        for label, res in passes:
            print(f"  PASS  {label}: roi {res['roi']:+.2f}% (n={res['n']})  seasons {res['season_roi']}")
    else:
        print("No park slice cleared the robustness floor — no standalone park totals edge.")
    print("=" * 96)


if __name__ == "__main__":
    run()
