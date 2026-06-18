"""Long-run validation of the UNDER 8.0 edge over ~2015-2026 (free SBR data 2015-21 +
backtest/history 2022-26). The make-or-break confidence test before backing it with money.
Push-corrected. $0.

Usage:  python -m pipeline.research_under8_long
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.utils import american_to_decimal  # noqa: E402

ROOT = Path(__file__).parent.parent


def _rows():
    out = []
    sbr = json.loads((ROOT / "data" / "sbr_totals_by_season.json").read_text())["games"]
    for g in sbr:
        out.append((g["season"], g["closing_total"], g["under_price"], g["actual_total"], "SBR"))
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text())["games"]
    for g in bt:
        s = g.get("season")
        if s and 2022 <= s <= 2025 and g.get("closing_total") is not None and g.get("under_price") is not None and g.get("actual_total") is not None:
            out.append((s, g["closing_total"], g["under_price"], g["actual_total"], "PINN"))
    hp = ROOT / "docs" / "history.json"
    if hp.exists():
        raw = json.loads(hp.read_text())
        for r in (raw if isinstance(raw, list) else raw.get("games", [])):
            ct = r.get("vegas_total");
            if ct is not None and r.get("under_price") is not None and r.get("actual_total") is not None:
                out.append((2026, ct, r["under_price"], r["actual_total"], "HIST"))
    return out


def _under_roi(rows):
    """UNDER flat-$1, push-corrected. rows=[(season, line, under_price, actual)]."""
    n = w = 0; units = 0.0; per = {}
    for season, line, up, at in rows:
        if abs(at - line) < 1e-9:    # push void
            continue
        won = at < line
        u = (american_to_decimal(up) - 1) if won else -1.0
        n += 1; w += 1 if won else 0; units += u
        d = per.setdefault(season, [0, 0.0]); d[0] += 1; d[1] += u
    if not n:
        return None
    return {"n": n, "win": round(w / n * 100, 1), "roi": round(units / n * 100, 2),
            "by_season": {s: (v[0], round(v[1] / v[0] * 100, 1)) for s, v in sorted(per.items())}}


def run():
    rows = _rows()
    line8 = [(s, l, up, at) for (s, l, up, at, src) in rows if 8.0 <= l < 8.25]
    std8 = [r for r in line8 if -110 <= r[2] <= -106]
    print("=" * 92)
    print("UNDER 8.0 LONG-RUN VALIDATION (2015-2026) — push-corrected, free data")
    print("=" * 92)
    print(f"total games loaded: {len(rows):,} | at the 8.0 line: {len(line8):,} | 8.0 std-vig: {len(std8):,}\n")

    for label, data in [("8.0 ALL-VIG (UNDER)", line8), ("8.0 STD-VIG -106..-110 (UNDER)", std8)]:
        res = _under_roi(data)
        if not res:
            continue
        pos = sum(1 for n, r in res["by_season"].values() if r > 0)
        print(f"{label}: n={res['n']} win={res['win']}% ROI={res['roi']:+.2f}%  ({pos}/{len(res['by_season'])} seasons positive)")
        for s, (n, r) in res["by_season"].items():
            bar = "+" if r > 0 else ""
            print(f"    {s}: n={n:>4}  {bar}{r:>5.1f}%")
        print()

    # broader: UNDER ROI by line, all seasons (std vig), for context
    print("UNDER ROI by line (std-vig -106..-110, 2015-26):")
    for lbl, lo in [("7.5", 7.5), ("8.0", 8.0), ("8.5", 8.5), ("9.0", 9.0), ("9.5", 9.5)]:
        sub = [(s, l, up, at) for (s, l, up, at, src) in rows if lo <= l < lo + 0.25 and -110 <= up <= -106]
        res = _under_roi(sub)
        if res:
            print(f"  {lbl}: n={res['n']:>5} win={res['win']:>5.1f}% ROI={res['roi']:>+7.2f}%")
    print("=" * 92)


if __name__ == "__main__":
    run()
