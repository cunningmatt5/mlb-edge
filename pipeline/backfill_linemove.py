"""Extract opening + closing ML and TOTALS per gamePk from the free SBR dataset, for the
line-movement study. Keyless, $0 (data/external/mlb_odds_dataset.json, already downloaded).

Writes data/linemove_by_gamepk.json = {gamePk: {open_total, close_total, close_over, close_under,
open_home_ml, close_home_ml, open_away_ml, close_away_ml, book}}.

Usage:  python -m pipeline.backfill_linemove
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "data" / "external" / "mlb_odds_dataset.json"
OUT = ROOT / "data" / "linemove_by_gamepk.json"
BOOK_PRIORITY = ["pinnacle", "betmgm", "draftkings", "fanduel", "caesars", "bet365", "pointsbet"]


def _norm_team(name: str) -> str:
    n = re.sub(r"[^a-z0-9]", "", str(name).lower())
    return "athletics" if n.endswith("athletics") else n


def _target_map() -> dict[tuple, int]:
    tgt: dict[tuple, int] = {}
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))["games"]
    for g in bt:
        d = (g.get("date") or "")[:10]
        if d and g.get("gamePk") and g.get("home_team") and g.get("away_team"):
            tgt[(d, _norm_team(g["home_team"]), _norm_team(g["away_team"]))] = g["gamePk"]
    return tgt


def _pick(entries: list[dict]) -> dict | None:
    by = {e.get("sportsbook"): e for e in entries}
    for b in BOOK_PRIORITY:
        if b in by:
            return by[b]
    return entries[0] if entries else None


def main() -> None:
    if not SRC.exists():
        print(f"Dataset not found: {SRC} — download it first (free).")
        return
    data = json.loads(SRC.read_text(encoding="utf-8"))
    tgt = _target_map()
    by_pk: dict[str, dict] = {}
    for date, games in data.items():
        for g in games:
            gv = g.get("gameView", {})
            pk = tgt.get((date[:10], _norm_team((gv.get("homeTeam") or {}).get("fullName", "")),
                          _norm_team((gv.get("awayTeam") or {}).get("fullName", ""))))
            if not pk:
                continue
            ml = _pick(g.get("odds", {}).get("moneyline", []))
            tot = _pick(g.get("odds", {}).get("totals", []))
            rec: dict = {}
            if ml:
                o, c = ml.get("openingLine") or {}, ml.get("currentLine") or {}
                rec.update(open_home_ml=o.get("homeOdds"), open_away_ml=o.get("awayOdds"),
                           close_home_ml=c.get("homeOdds"), close_away_ml=c.get("awayOdds"), ml_book=ml.get("sportsbook"))
            if tot:
                o, c = tot.get("openingLine") or {}, tot.get("currentLine") or {}
                rec.update(open_total=o.get("total"), close_total=c.get("total"),
                           close_over=c.get("overOdds"), close_under=c.get("underOdds"), tot_book=tot.get("sportsbook"))
            if rec:
                by_pk[str(pk)] = rec
    OUT.write_text(json.dumps({"by_gamepk": by_pk}, separators=(",", ":")), encoding="utf-8")
    cov = sum(1 for pk in tgt.values() if str(pk) in by_pk)
    with_tot_move = sum(1 for v in by_pk.values() if v.get("open_total") is not None and v.get("close_total") is not None)
    with_ml_move = sum(1 for v in by_pk.values() if v.get("open_home_ml") is not None and v.get("close_home_ml") is not None)
    print(f"cached {len(by_pk)} games | coverage {cov}/{len(tgt)} ({100*cov/max(1,len(tgt)):.1f}%)")
    print(f"with totals open+close: {with_tot_move} | with ML open+close: {with_ml_move} -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
