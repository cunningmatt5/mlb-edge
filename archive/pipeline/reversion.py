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
    _fetch_mlb_platoon_splits,
)

OUT = Path(__file__).parent.parent / "docs" / "reversion.json"
MIN_PA = 200            # qualified regular (mid-season aware)
MIN_XWOBA = 0.330       # "good hitter"
RECENT_DAYS = 21        # recent-form window for statcast pull
SLUMP_MIN = 0.060       # show hitters whose xOPS deficit (xBA-gap + xSLG-gap) is >= this
QUALITY_FLOOR = 0.80    # recent xwOBA >= this × season ⇒ "still squaring it up" (bad luck)

# Today's-matchup scoring: nudge the base reversion score by how favorable today's spot is.
# A weak opposing starter and/or a hitter's better platoon side = a riper bounce-back spot.
SP_W = 0.50             # opposing-SP quality weight (score 0.5 = avg; lower = weaker pitcher)
PLAT_W = 3.0            # platoon-split weight (hitter's vs-hand wOBA vs his own two-hand avg)
MATCH_LO, MATCH_HI = 0.80, 1.25   # clamp the matchup multiplier (matchup informs, never dominates)

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


def _filter_active(hitters: list[dict]) -> list[dict]:
    """Drop hitters who aren't on an active MLB roster (IL, optioned, etc.).

    The season leaderboard is full-season, so a qualified hitter now on the IL still
    appears — but he can't play today. The *current* roster entry (the open one with no
    endDate) carries the status code: 'A' = active; 'D7/D10/D60' = IL; others = not with
    the MLB club. One batched /people?hydrate=rosterEntries call covers everyone.

    Fail-safe: if the status lookup returns nothing (API hiccup) we keep the board intact
    rather than blanking it; we only drop a hitter whose status is *known* to be non-active.
    """
    if not hitters:
        return hitters
    ids = [h["id"] for h in hitters]
    status: dict[int, str] = {}
    for i in range(0, len(ids), 80):
        chunk = ids[i:i + 80]
        try:
            r = requests.get("https://statsapi.mlb.com/api/v1/people",
                             params={"personIds": ",".join(map(str, chunk)), "hydrate": "rosterEntries"},
                             timeout=30)
            r.raise_for_status()
            for p in r.json().get("people", []):
                cur = next((e for e in (p.get("rosterEntries") or []) if e.get("endDate") is None), None)
                code = (cur or {}).get("status", {}).get("code")
                if code:
                    status[p["id"]] = code
        except Exception as exc:
            log.debug("roster-status fetch failed: %s", exc)
    if not status:                                   # total failure → don't nuke the board
        return hitters
    kept, dropped = [], []
    for h in hitters:
        st = status.get(h["id"])
        if st is None or st == "A":                  # active, or unknown (keep — fail-safe)
            h["roster_status"] = st or "A"
            kept.append(h)
        else:
            dropped.append(f"{h.get('name')} ({st})")
    if dropped:
        log.info("Reversion: dropped %d non-active (IL/optioned): %s", len(dropped), ", ".join(dropped[:10]))
    return kept


def _best_angle(xslg, season_barrel) -> str:
    """Hint at which angle the hitter's profile favors; users pick their own."""
    brl = season_barrel or 0
    if brl >= 0.12 and (xslg or 0) >= 0.500:
        return "HR"
    if brl >= 0.09 or (xslg or 0) >= 0.450:
        return "BASES"
    return "HIT"


def _matchup_mult(h: dict, m: dict | None) -> tuple[float, str | None]:
    """Today's-spot multiplier on the base score, from opposing-SP quality + platoon edge.

    Returns (multiplier, short note). 1.0 when there's no matchup or no inputs — so a hitter
    who isn't in action (or whose opponent/splits are unknown) keeps his neutral base score.
    """
    if not m:
        return 1.0, None
    mult = 1.0
    notes: list[str] = []

    sp = m.get("opp_sp_score")                 # [0,1], 0.5 = league-avg, higher = tougher
    if sp is not None:
        mult *= 1.0 + (0.5 - sp) * SP_W        # weak SP boosts, ace dampens
        if sp <= 0.42:
            notes.append("weak SP")
        elif sp >= 0.58:
            notes.append("tough SP")

    hand = (m.get("opp_throws") or "").upper()
    vl, vr = h.get("xwoba_vs_l"), h.get("xwoba_vs_r")
    if hand in ("L", "R") and vl is not None and vr is not None:
        own_avg = (vl + vr) / 2.0
        vs_hand = vl if hand == "L" else vr
        delta = vs_hand - own_avg              # +ve = his stronger side vs this hand
        mult *= 1.0 + delta * PLAT_W
        if delta >= 0.012:
            notes.append(f"mashes {hand}HP")
        elif delta <= -0.012:
            notes.append(f"weak vs {hand}HP")

    mult = max(MATCH_LO, min(MATCH_HI, mult))
    return round(mult, 3), (", ".join(notes) or None)


def _apply_today(hitters: list[dict], matchups: dict) -> None:
    """Set plays_today (team in action) + today's matchup context, then matchup-adjust the score.

    plays_today is keyed on the TEAM being in action (robust all day), not the individual
    lineup (TBD for hours; a slumping regular who recently sat drops out of the last-10 proxy).
    The shown score = base_score × matchup multiplier, so a ripe spot (weak SP / good platoon)
    ranks above an equally-due hitter in a tough spot. Re-sorts in place. Idempotent: callable
    on every cycle (full build + intra-day refresh) as probable pitchers firm up.
    """
    for h in hitters:
        m = matchups.get(h.get("team_id"))
        h["plays_today"] = m is not None
        h["opp_sp"] = m.get("opp_sp") if m else None
        h["opp_throws"] = m.get("opp_throws") if m else None
        h["venue"] = m.get("venue") if m else None
        h["park_factor"] = m.get("park_factor") if m else None
        h["opp_sp_score"] = m.get("opp_sp_score") if m else None
        mult, note = _matchup_mult(h, m)
        h["matchup_mult"] = mult
        h["matchup_note"] = note
        base = h.get("base_score")
        if base is None:                       # legacy row (pre-matchup build) — derive once
            base = h.get("score", 0.0)
            h["base_score"] = base
        h["score"] = round(base * mult, 1)
    hitters.sort(key=lambda h: -h.get("score", 0.0))


def build_reversion_board(season: int | None = None, today_matchups=None,
                          limit: int | None = None, force: bool = False) -> dict:
    season = season or date.today().year
    today_matchups = {int(k): v for k, v in (today_matchups or {}).items() if k}
    today_str = date.today().isoformat()

    if not force and not limit and OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            if prev.get("date") == today_str:
                log.info("Reversion board already built for %s — skipping", today_str)
                # Refresh on every cycle: drop anyone newly IL'd + re-apply today's matchups.
                prev["hitters"] = _filter_active(prev.get("hitters", []))
                _apply_today(prev["hitters"], today_matchups)
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
        # Base (matchup-neutral) score; _apply_today scales it by today's spot favorability.
        base_score = round(ops_deficit * (1.0 if quality_ok else 0.45) * 100, 1)
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
            "quality_ok": quality_ok, "base_score": base_score, "score": base_score,
            "xwoba_vs_l": None, "xwoba_vs_r": None,   # filled from platoon fetch below
            "matchup_mult": 1.0, "matchup_note": None,
            "best_angle": _best_angle(xslg, entry.get("barrel_pct")),
        })
        if i % 25 == 0:
            log.info("  reversion: processed %d/%d hitters", i, len(universe))

    # Drop hitters who can't play (IL / not on the active roster) before the extra lookups.
    hitters = _filter_active(hitters)

    # Platoon splits (vs LHP / vs RHP wOBA proxy) for the shown candidates — drives the
    # platoon half of the matchup multiplier (his stronger/weaker side vs today's starter).
    try:
        splits = _fetch_mlb_platoon_splits([h["id"] for h in hitters], season)
    except Exception as exc:
        log.debug("platoon splits fetch failed: %s", exc)
        splits = {}
    for h in hitters:
        s = splits.get(h["id"]) or {}
        h["xwoba_vs_l"] = s.get("xwoba_vs_l")
        h["xwoba_vs_r"] = s.get("xwoba_vs_r")

    # Team abbreviation + id (for logo) for the shown candidates.
    tl = _team_lookup([h["id"] for h in hitters])
    for h in hitters:
        abbr, tid = tl.get(h["id"], (None, None))
        h["team"] = abbr
        h["team_id"] = tid
    # plays_today + today's matchup context (opp SP, park) → matchup-adjusted score + sort.
    _apply_today(hitters, today_matchups)

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
