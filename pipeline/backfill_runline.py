"""Parse run-line (±1.5) odds from the FREE GitHub SBR dataset and join to gamePk.

Source: data/external/mlb_odds_dataset.json (public release of github.com/ArnavSaraogi/
mlb-odds-scraper, 2021-2025). Keyless, $0 — no Odds API. Structure: {date: [ {gameView, odds}, ]}
with odds.pointspread[] per sportsbook carrying openingLine/currentLine {homeOdds, awayOdds,
homeSpread, awaySpread}.

Writes data/runline_by_gamepk.json = {gamePk: {run_line, home_spread, home_rl_price,
away_rl_price, book, open_home_spread, open_home_price, open_away_price}}.

Usage:  python -m pipeline.backfill_runline
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "data" / "external" / "mlb_odds_dataset.json"
OUT = ROOT / "data" / "runline_by_gamepk.json"
# Prefer sharper/consensus books; fall back to whatever's present.
BOOK_PRIORITY = ["pinnacle", "betmgm", "draftkings", "fanduel", "caesars", "bet365", "pointsbet"]


def _norm_team(name: str) -> str:
    n = re.sub(r"[^a-z0-9]", "", str(name).lower())
    return "athletics" if n.endswith("athletics") else n


def _target_map() -> dict[tuple, int]:
    tgt: dict[tuple, int] = {}
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))["games"]
    rows = list(bt)
    hp = ROOT / "docs" / "history.json"
    if hp.exists():
        raw = json.loads(hp.read_text(encoding="utf-8"))
        rows += raw if isinstance(raw, list) else raw.get("games", [])
    for g in rows:
        d = (g.get("date") or "")[:10]
        if d and g.get("gamePk") and g.get("home_team") and g.get("away_team"):
            tgt[(d, _norm_team(g["home_team"]), _norm_team(g["away_team"]))] = g["gamePk"]
    return tgt


def _pick_book(entries: list[dict]) -> dict | None:
    by = {e.get("sportsbook"): e for e in entries}
    for b in BOOK_PRIORITY:
        if b in by:
            return by[b]
    return entries[0] if entries else None


def main() -> None:
    if not SRC.exists():
        print(f"Dataset not found: {SRC}\nDownload it first (free):\n"
              "  curl -sL -o data/external/mlb_odds_dataset.json "
              "https://github.com/ArnavSaraogi/mlb-odds-scraper/releases/download/dataset/mlb_odds_dataset.json")
        return
    data = json.loads(SRC.read_text(encoding="utf-8"))
    tgt = _target_map()
    print(f"Target games (backtest+history): {len(tgt):,}")

    by_pk: dict[str, dict] = {}
    seen = matched = had_ps = 0
    for date, games in data.items():
        for g in games:
            seen += 1
            gv = g.get("gameView", {})
            home = (gv.get("homeTeam") or {}).get("fullName", "")
            away = (gv.get("awayTeam") or {}).get("fullName", "")
            pk = tgt.get((date[:10], _norm_team(home), _norm_team(away)))
            if not pk:
                continue
            matched += 1
            ps = g.get("odds", {}).get("pointspread", [])
            book = _pick_book(ps)
            if not book:
                continue
            cur = book.get("currentLine") or {}
            opn = book.get("openingLine") or {}
            hs = cur.get("homeSpread")
            if hs is None or cur.get("homeOdds") is None or cur.get("awayOdds") is None:
                continue
            had_ps += 1
            by_pk[str(pk)] = {
                "run_line": abs(hs),
                "home_spread": hs,
                "home_rl_price": cur.get("homeOdds"),
                "away_rl_price": cur.get("awayOdds"),
                "book": book.get("sportsbook"),
                "open_home_spread": opn.get("homeSpread"),
                "open_home_price": opn.get("homeOdds"),
                "open_away_price": opn.get("awayOdds"),
            }

    OUT.write_text(json.dumps({"by_gamepk": by_pk}, separators=(",", ":")), encoding="utf-8")
    cov = sum(1 for pk in tgt.values() if str(pk) in by_pk)
    print(f"dataset games: {seen:,} | matched to gamePk: {matched:,} | with run line: {had_ps:,}")
    print(f"coverage vs targets: {cov}/{len(tgt)} ({100*cov/max(1,len(tgt)):.1f}%) -> {OUT.relative_to(ROOT)}")
    # quick spread sanity
    from collections import Counter
    print("run_line values:", dict(Counter(v["run_line"] for v in by_pk.values())))


if __name__ == "__main__":
    main()
