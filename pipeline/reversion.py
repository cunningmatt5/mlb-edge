"""Reversion board — good hitters slumping on bad luck (INFORMATIONAL, not bet recs).

Surfaces ~100 good high-volume hitters whose recent results (5/10-game BA/SLG) are well
below their season expected (xBA/xSLG) while their underlying quality of contact (recent
barrel%/hard-hit%) is still strong — i.e. genuine bad-luck slumps that statistically revert.
Reversion is validated (see pipeline/reversion_research.py); the market mispricing is NOT,
so this is a "who's due" board — users source their own line and angle (hits / bases / HR).

Built once per day (date-gated) and written to docs/reversion.json for the Reversion tab.

Usage:
    python -m pipeline.reversion --season 2026            # build today's board
    python -m pipeline.reversion --season 2026 --limit 8  # quick test on 8 hitters
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from pipeline.statcast import (
    _fetch_savant_batter_expected_stats,
    _fetch_savant_batter_batted_ball_stats,
    _merge_savant_batter_expected,
    _merge_savant_batter_batted_ball,
)

OUT = Path(__file__).parent.parent / "docs" / "reversion.json"
MIN_PA = 200            # qualified regular (mid-season aware)
MIN_XWOBA = 0.330       # "good hitter"
RECENT_DAYS = 21        # recent-form window for statcast pull
SLUMP_MIN = 0.060       # show hitters whose xOPS deficit (xBA-gap + xSLG-gap) is >= this
QUALITY_FLOOR = 0.80    # recent xwOBA >= this × season ⇒ "still squaring it up" (bad luck)

log = logging.getLogger(__name__)

_HIT = {"single", "double", "triple", "home_run"}
_TB = {"single": 1, "double": 2, "triple": 3, "home_run": 4}
_NON_AB = {"walk", "intent_walk", "hit_by_pitch", "sac_fly", "sac_bunt",
           "catcher_interf", "sac_fly_double_play", "sac_bunt_double_play", "truncated_pa"}


def _recent_from_events(df) -> dict:
    """Recent 5/10-game BA/SLG/HR + barrel/hard-hit from a statcast_batter event df."""
    out: dict = {}
    if df is None or getattr(df, "empty", True) or "events" not in df.columns:
        return out
    ev = df.dropna(subset=["events"]).copy()
    if ev.empty or "game_date" not in ev.columns:
        return out
    ev["is_ab"] = ~ev["events"].isin(_NON_AB)
    ev["is_h"] = ev["events"].isin(_HIT)
    ev["tb"] = ev["events"].map(_TB).fillna(0)
    ev["is_hr"] = ev["events"] == "home_run"
    g = (ev.groupby("game_date")
           .agg(ab=("is_ab", "sum"), h=("is_h", "sum"), tb=("tb", "sum"), hr=("is_hr", "sum"))
           .reset_index().sort_values("game_date"))
    for w in (5, 10):
        last = g.tail(w)
        ab, h, tb, hr = int(last["ab"].sum()), int(last["h"].sum()), int(last["tb"].sum()), int(last["hr"].sum())
        if ab > 0:
            out[f"ba_{w}"] = round(h / ab, 3)
            out[f"slg_{w}"] = round(tb / ab, 3)
            out[f"hr_{w}"] = hr
            out[f"ab_{w}"] = ab
    out["games_recent"] = int(len(g))

    # Recent EXPECTED stats over the last-10-game events (the gold-standard luck check):
    # xBA = Σ estimated_ba_using_speedangle (batted balls) / AB; xwOBA = mean over terminal
    # PAs of (estimated_woba if a batted ball else woba_value). Comparable to season xba/xwoba.
    last10 = list(g["game_date"].tail(10))
    sub = ev[ev["game_date"].isin(last10)]
    ab10 = int(sub["is_ab"].sum()) if not sub.empty else 0
    if ab10 > 0 and "estimated_ba_using_speedangle" in sub.columns:
        out["xba_10"] = round(float(sub["estimated_ba_using_speedangle"].sum()) / ab10, 3)
        if out.get("ba_10") is not None:
            out["luck_gap"] = round(out["xba_10"] - out["ba_10"], 3)  # +ve = deserved more hits
    if not sub.empty and "estimated_woba_using_speedangle" in sub.columns and "woba_value" in sub.columns:
        xw = sub["estimated_woba_using_speedangle"].where(
            sub["estimated_woba_using_speedangle"].notna(), sub["woba_value"]).dropna()
        if len(xw) >= 10:
            out["xwoba_10"] = round(float(xw.mean()), 3)

    # Recent quality over TRUE batted balls (type=='X') so hard-hit% / avg EV match the
    # season Savant leaderboard (which is BBE-based). statcast_batter has no 'barrel' column,
    # and launch_speed.notna() would wrongly include fouls — so we gate on hard-hit%, not barrel.
    if "type" in df.columns and "launch_speed" in df.columns:
        ls = df.loc[df["type"] == "X", "launch_speed"].dropna()
        if len(ls) >= 15:
            out["hard_hit_recent"] = round(float((ls >= 95).mean()), 4)
            out["avg_ev_recent"] = round(float(ls.mean()), 1)
            out["bbe_recent"] = int(len(ls))
    return out


def _team_lookup(player_ids) -> dict:
    """{player_id: (abbr, team_id)} via MLB Stats API (team_id → logo, abbr for display)."""
    ids = list(player_ids)
    if not ids:
        return {}
    teams: dict[int, str] = {}
    try:
        r = requests.get("https://statsapi.mlb.com/api/v1/teams", params={"sportId": 1}, timeout=20)
        r.raise_for_status()
        for t in r.json().get("teams", []):
            if t.get("id"):
                teams[t["id"]] = t.get("abbreviation")
    except Exception as exc:
        log.debug("teams fetch failed: %s", exc)
    out: dict = {}
    for i in range(0, len(ids), 80):
        chunk = ids[i:i + 80]
        try:
            r = requests.get("https://statsapi.mlb.com/api/v1/people",
                             params={"personIds": ",".join(map(str, chunk)), "hydrate": "currentTeam"},
                             timeout=30)
            r.raise_for_status()
            for p in r.json().get("people", []):
                tid = (p.get("currentTeam") or {}).get("id")
                out[p["id"]] = (teams.get(tid), tid)
        except Exception as exc:
            log.debug("people/team fetch failed: %s", exc)
    return out


def _best_angle(xslg, season_barrel) -> str:
    """Hint at which angle the hitter's profile favors; users pick their own."""
    brl = season_barrel or 0
    if brl >= 0.12 and (xslg or 0) >= 0.500:
        return "HR"
    if brl >= 0.09 or (xslg or 0) >= 0.450:
        return "BASES"
    return "HIT"


def build_reversion_board(season: int | None = None, today_team_ids=None,
                          limit: int | None = None, force: bool = False) -> dict:
    season = season or date.today().year
    # Flag "plays today" by whether the hitter's TEAM is in action today (robust — known
    # from the schedule all day), not by the individual lineup (TBD for hours; a slumping
    # regular who recently sat falls out of the last-10 proxy and gets wrongly un-flagged).
    today_team_ids = set(int(x) for x in (today_team_ids or []) if x)
    today_str = date.today().isoformat()

    if not force and not limit and OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            if prev.get("date") == today_str:
                log.info("Reversion board already built for %s — skipping", today_str)
                if today_team_ids:   # refresh the plays_today flags from today's schedule
                    for h in prev.get("hitters", []):
                        h["plays_today"] = h.get("team_id") in today_team_ids
                    OUT.write_text(json.dumps(prev, separators=(",", ":")), encoding="utf-8")
                return prev
        except Exception as exc:
            log.debug("Could not read existing reversion.json: %s", exc)

    exp = _fetch_savant_batter_expected_stats(season)
    bb = _fetch_savant_batter_batted_ball_stats(season)
    if exp is None or exp.empty:
        log.warning("No batter expected-stats leaderboard for %d — skipping reversion board", season)
        return {}

    universe = []
    for _, r in exp.iterrows():
        try:
            pid = int(r["player_id"]); pa = int(r.get("pa") or 0); xw = r.get("est_woba")
        except (TypeError, ValueError):
            continue
        if pa >= MIN_PA and xw is not None and float(xw) >= MIN_XWOBA:
            universe.append((pid, pa))
    universe.sort(key=lambda x: -x[1])
    if limit:
        universe = universe[:limit]
    log.info("Reversion universe: %d good high-volume hitters (season %d)", len(universe), season)

    from pybaseball import statcast_batter
    start = (date.today() - timedelta(days=RECENT_DAYS)).isoformat()
    end = today_str

    hitters = []
    for i, (pid, pa) in enumerate(universe, 1):
        entry: dict = {"mlbam_id": pid}
        _merge_savant_batter_expected(entry, exp, pid)
        _merge_savant_batter_batted_ball(entry, bb, pid)
        try:
            df = statcast_batter(start_dt=start, end_dt=end, player_id=pid)
            rec = _recent_from_events(df)
        except Exception as exc:
            log.debug("Recent fetch failed for %d: %s", pid, exc)
            rec = {}
        xba, xslg = entry.get("xba"), entry.get("xslg")
        ba10 = rec.get("ba_10")
        if xba is None or ba10 is None:
            continue
        gap_ba = round(xba - ba10, 3)            # >0 ⇒ recent BA below true talent
        slg10 = rec.get("slg_10")
        slg_gap = round(xslg - slg10, 3) if (xslg is not None and slg10 is not None) else 0.0
        # xOPS deficit = how far below true talent the hitter's OPS components (avg + power)
        # are right now; only count actual deficits (a strong dimension shouldn't offset).
        ops_deficit = round(max(0.0, gap_ba) + max(0.0, slg_gap), 3)
        if ops_deficit < SLUMP_MIN:
            continue                              # not currently slumping → not "due"
        # Luck gate: recent xwOBA still near season level ⇒ slump is bad luck, not decline.
        # (Best metric; falls back to recent hard-hit% when xwOBA sample is too thin.)
        s_xw, r_xw = entry.get("xwoba"), rec.get("xwoba_10")
        s_hh, r_hh = entry.get("hard_hit_pct"), rec.get("hard_hit_recent")
        if r_xw is not None and s_xw:
            quality_ok = r_xw >= QUALITY_FLOOR * s_xw
        else:
            quality_ok = (s_hh is None or r_hh is None or r_hh >= QUALITY_FLOOR * s_hh)
        score = round(ops_deficit * (1.0 if quality_ok else 0.45) * 100, 1)
        hitters.append({
            "name": entry.get("name") or str(pid), "id": pid, "pa": pa,
            "plays_today": False,   # set from team_id after the team lookup below
            "xba": xba, "xwoba": s_xw, "xslg": xslg,
            "season_barrel": entry.get("barrel_pct"), "season_hardhit": s_hh,
            "ba_5": rec.get("ba_5"), "ba_10": ba10,
            "slg_5": rec.get("slg_5"), "slg_10": rec.get("slg_10"),
            "hr_10": rec.get("hr_10"), "games_recent": rec.get("games_recent"),
            "hard_hit_recent": r_hh, "avg_ev_recent": rec.get("avg_ev_recent"),
            "xba_10": rec.get("xba_10"), "xwoba_10": r_xw, "luck_gap": rec.get("luck_gap"),
            "gap_ba": gap_ba, "slg_gap": slg_gap, "ops_deficit": ops_deficit,
            "quality_ok": quality_ok, "score": score,
            "best_angle": _best_angle(xslg, entry.get("barrel_pct")),
        })
        if i % 25 == 0:
            log.info("  reversion: processed %d/%d hitters", i, len(universe))

    hitters.sort(key=lambda h: -h["score"])

    # Team abbreviation + id (for logo) for the shown candidates.
    tl = _team_lookup([h["id"] for h in hitters])
    for h in hitters:
        abbr, tid = tl.get(h["id"], (None, None))
        h["team"] = abbr
        h["team_id"] = tid
        h["plays_today"] = tid in today_team_ids

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": today_str,
        "season": season,
        "n_universe": len(universe),
        "hitters": hitters,
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    log.info("Reversion board: %d slumping candidates of %d universe → %s",
             len(hitters), len(universe), OUT.name)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(description="Build the reversion board (docs/reversion.json)")
    p.add_argument("--season", type=int, default=None)
    p.add_argument("--limit", type=int, default=None, help="cap universe size (quick test)")
    p.add_argument("--force", action="store_true", help="rebuild even if today's board exists")
    args = p.parse_args()
    build_reversion_board(season=args.season, limit=args.limit, force=args.force)
