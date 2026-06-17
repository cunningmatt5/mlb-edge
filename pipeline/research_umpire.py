"""MLB Edge research — does the HP umpire add a validated totals edge?

Hypothesis: a big-zone / under-leaning home-plate umpire suppresses run scoring, so betting
UNDER at games called by such umpires should beat the blind same-line UNDER baseline — and a
tight-zone / over-leaning umpire should help OVERs. We test this on the only market with
multi-season historical closing odds: full-game totals (2021-2026).

Design notes:
  * We test the BLIND line bets (UNDER/OVER at a given line, no model filter) because the
    umpire is independent of the line, and the model's predicted_total already bakes in the
    umpire run-adjustment — testing the blind line avoids double-counting.
  * Umpire career tendencies (pipeline/umpire.py) are a static current table applied to past
    games — a mild lookahead, consistent with the project's accepted Statcast lookahead, and
    fine for signal-quality measurement (tendencies are stable career traits).
  * Robustness floor mirrors research_under_8.py: slice n >= 100, >= 3 counted seasons
    (n >= 20), at most 1 losing counted season.

Prereq: run `python -m pipeline.backfill_umpires` first (writes data/umpire_by_gamepk.json).

Usage:
    python -m pipeline.research_umpire
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.edge_research import roi_totals          # noqa: E402  (reuse ROI math)
from pipeline.umpire import get_zone_score, get_run_tendency  # noqa: E402

ROOT = Path(__file__).parent.parent
CACHE = ROOT / "data" / "umpire_by_gamepk.json"
MIN_SLICE_N = 100
MIN_SEASON_N = 20
LINES = [("7.5", 7.5), ("8.0", 8.0), ("8.5", 8.5), ("9.0", 9.0), ("9.5", 9.5), ("10.0+", 10.0)]


# ── Dataset ───────────────────────────────────────────────────────────────────

def _load_blind_totals() -> pd.DataFrame:
    """All games with closing total + both prices + outcome (blind-line fields only).

    Includes 2021 (blind-line fields are lookahead-safe; only model fields aren't).
    """
    frames = []
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))["games"]
    btdf = pd.DataFrame(bt)
    btdf = btdf.loc[:, ~btdf.columns.duplicated()].copy()
    frames.append(btdf)
    hpath = ROOT / "docs" / "history.json"
    if hpath.exists():
        raw = json.loads(hpath.read_text(encoding="utf-8"))
        h = raw if isinstance(raw, list) else raw.get("games", [])
        hdf = pd.DataFrame(h)
        if not hdf.empty:
            # Use vegas_total only when there's no closing_total already present.
            if "closing_total" not in hdf.columns and "vegas_total" in hdf.columns:
                hdf = hdf.rename(columns={"vegas_total": "closing_total"})
            elif "closing_total" in hdf.columns and "vegas_total" in hdf.columns:
                hdf["closing_total"] = hdf["closing_total"].fillna(hdf["vegas_total"])
            hdf = hdf.loc[:, ~hdf.columns.duplicated()].copy()
            hdf["season"] = 2026
            frames.append(hdf)
    df = pd.concat(frames, ignore_index=True)
    df = df.loc[:, ~df.columns.duplicated()].copy()
    df = df.dropna(subset=["closing_total", "under_price", "over_price", "total_went_over", "gamePk"]).copy()
    df = df[(df["closing_total"] >= 5.0) & (df["closing_total"] <= 14.0)].copy()
    # De-dup (2026 may appear in both backtest w/o odds and history w/ odds; odds rows win)
    df = df.drop_duplicates(subset=["gamePk"], keep="last").copy()
    df["season"] = df["season"].astype(int)
    return df


def _attach_umpire(df: pd.DataFrame) -> pd.DataFrame:
    by_pk = json.loads(CACHE.read_text(encoding="utf-8")).get("by_gamepk", {})
    df["umpire"] = df["gamePk"].astype("Int64").astype(str).map(by_pk)
    df["zone"] = df["umpire"].map(lambda u: get_zone_score(u) if isinstance(u, str) else 0.0)
    df["run_tend"] = df["umpire"].map(lambda u: get_run_tendency(u) if isinstance(u, str) else 0.0)
    return df


# ── Evaluation ──────────────────────────────────────────────────────────────────

def evaluate(df: pd.DataFrame, pick_under: bool) -> dict:
    overall = roi_totals(df, pick_under)
    seasons = {}
    for yr in sorted(df["season"].unique()):
        seasons[int(yr)] = roi_totals(df[df["season"] == yr], pick_under)
    counted = {y: r for y, r in seasons.items() if r["n"] >= MIN_SEASON_N}
    losers = sum(1 for r in counted.values() if (r["roi_pct"] or 0) <= 0)
    passed = (overall["n"] >= MIN_SLICE_N and len(counted) >= 3 and losers <= 1)
    return {"overall": overall, "seasons": seasons, "counted": counted, "losers": losers, "passed": passed}


def _season_str(seasons: dict) -> str:
    return "  ".join(
        f"{y}:{r['n']}g/{'+' if (r['roi_pct'] or 0) >= 0 else ''}{r['roi_pct']:.1f}%" if r["n"] else f"{y}:0"
        for y, r in seasons.items()
    )


def _line_mask(df: pd.DataFrame, line: float) -> pd.Series:
    if line >= 10.0:
        return df["closing_total"] >= 10.0
    return (df["closing_total"] >= line) & (df["closing_total"] < line + 0.25)


def _fmt(res: dict) -> str:
    o = res["overall"]
    if not o["n"]:
        return "n=0"
    tag = "  ← PASS ✓" if res["passed"] else ""
    return f"n={o['n']:>5}  win={o['win_pct']:>5.1f}%  ROI={o['roi_pct']:>+7.2f}%{tag}"


# ── Report ──────────────────────────────────────────────────────────────────────

def run() -> None:
    df = _attach_umpire(_load_blind_totals())
    matched = df["umpire"].notna().sum()
    print("=" * 96)
    print("HP-UMPIRE TOTALS EDGE — does umpire run-environment beat the blind line baseline?")
    print("=" * 96)
    print(f"Dataset: {len(df):,} games with closing odds | umpire matched: {matched:,} "
          f"({100*matched/len(df):.1f}%) | seasons {sorted(df['season'].unique())}\n")

    # Bucket by run tendency (direct runs residual) and by zone size.
    pf_run = df["run_tend"] <= -0.3   # under-leaning umps
    of_run = df["run_tend"] >= 0.3    # over-leaning umps
    bz = df["zone"] >= 0.5            # big zone (pitcher-friendly)
    sz = df["zone"] <= -0.3           # small zone (hitter-friendly)
    print(f"Umpire buckets: run-tend under-leaning(≤-0.3)={pf_run.sum():,}  "
          f"over-leaning(≥+0.3)={of_run.sum():,}  | big-zone(≥+0.5)={bz.sum():,}  "
          f"small-zone(≤-0.3)={sz.sum():,}\n")

    # ── SANITY: blind UNDER @ 8.0 should reproduce the known ~+12.6% ─────────────
    s = evaluate(df[_line_mask(df, 8.0)], pick_under=True)
    print(f"SANITY  blind UNDER @ 8.0 (all umps):  {_fmt(s)}")
    print(f"        {_season_str(s['seasons'])}\n")

    passes: list[str] = []

    def _report(title: str, sub: pd.DataFrame, pick_under: bool, baseline: dict | None) -> None:
        res = evaluate(sub, pick_under)
        line_tag = ""
        if baseline and baseline["overall"]["n"] and res["overall"]["roi_pct"] is not None:
            lift = res["overall"]["roi_pct"] - (baseline["overall"]["roi_pct"] or 0)
            line_tag = f"   (lift {lift:+.1f} vs blind)"
        print(f"  {title:<46} {_fmt(res)}{line_tag}")
        if res["passed"]:
            print(f"       {_season_str(res['seasons'])}")
            passes.append(f"{title}: ROI {res['overall']['roi_pct']:+.2f}% (n={res['overall']['n']})")

    # ── TEST 1: UNDER by line × umpire (run-tend) ───────────────────────────────
    print("TEST 1 — UNDER ROI by line, all umps vs under-leaning umps (run_tend ≤ -0.3):")
    for lbl, ln in LINES:
        base = evaluate(df[_line_mask(df, ln)], pick_under=True)
        print(f"  {lbl:>6}  all: {_fmt(base)}")
        _report(f"{lbl}  under-leaning ump", df[_line_mask(df, ln) & pf_run], True, base)
    print()

    # ── TEST 2: UNDER by line × big zone ────────────────────────────────────────
    print("TEST 2 — UNDER ROI by line, big-zone umps (zone ≥ +0.5):")
    for lbl, ln in LINES:
        base = evaluate(df[_line_mask(df, ln)], pick_under=True)
        _report(f"{lbl}  big-zone ump", df[_line_mask(df, ln) & bz], True, base)
    print()

    # ── TEST 3: OVER by line × over-leaning / small-zone ────────────────────────
    print("TEST 3 — OVER ROI by line, over-leaning (run_tend ≥ +0.3) / small-zone (zone ≤ -0.3):")
    for lbl, ln in LINES:
        base = evaluate(df[_line_mask(df, ln)], pick_under=False)
        print(f"  {lbl:>6}  all OVER: {_fmt(base)}")
        _report(f"{lbl}  over-leaning ump OVER", df[_line_mask(df, ln) & of_run], False, base)
        _report(f"{lbl}  small-zone ump OVER", df[_line_mask(df, ln) & sz], False, base)
    print()

    # ── TEST 4: vig-aware on the strongest UNDER slice (std vig -106..-110) ──────
    print("TEST 4 — std-vig (-106..-110) UNDER, under-leaning umps, lines 8.0-9.0:")
    stdvig = (df["under_price"] <= -106) & (df["under_price"] >= -110)
    band = df[(df["closing_total"] >= 8.0) & (df["closing_total"] < 9.25) & pf_run & stdvig]
    _report("8.0-9.0 under-leaning + std vig", band, True, None)
    print()

    # ── Verdict ─────────────────────────────────────────────────────────────────
    print("=" * 96)
    if passes:
        print("SLICES CLEARING THE ROBUSTNESS FLOOR (n≥100, ≥3 seasons, ≤1 losing season):")
        for p in passes:
            print(f"  ✓ {p}")
    else:
        print("No umpire slice cleared the robustness floor — no standalone umpire totals edge found.")
    print("=" * 96)


if __name__ == "__main__":
    run()
