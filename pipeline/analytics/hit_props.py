"""Score batter hit prop opportunities (anytime hits).

Signal logic: High-contact, high-xBA batters facing a pitcher with weak
xBA-against. Contact rate, hard hit%, and plate discipline anchor the batter
side; pitcher's expected outcomes anchor the matchup side.
"""

from __future__ import annotations

from pipeline.scorer import normalize, weighted_avg, batter_edge_score


def score_hit_props(game: dict, cache: dict) -> list[dict]:
    picks = []

    for bat_side, sp_side in [("home", "away"), ("away", "home")]:
        opp_sp_id = game.get(f"{sp_side}_sp_id")
        opp_sp    = cache.get(opp_sp_id, {}) if opp_sp_id else {}
        sp_throws = (
            opp_sp.get("throws")
            or game.get(f"{sp_side}_sp_throws")
        )

        # Pitcher xBA-against: higher value = more hitter-friendly
        sp_xba_s = normalize(opp_sp.get("xba_against"), lo=0.190, hi=0.330)

        for batter_id in game.get(f"{bat_side}_lineup", []):
            b = cache.get(batter_id)
            if not b:
                continue

            # Use split xBA when available (vs. LHP or vs. RHP)
            suffix = "_vs_l" if sp_throws == "L" else "_vs_r" if sp_throws == "R" else ""
            xba_val  = (b.get(f"xba{suffix}") if suffix else None) or b.get("xba")

            xba_s     = normalize(xba_val,               lo=0.190, hi=0.340)
            contact_s = normalize(b.get("contact_pct"),  lo=0.62,  hi=0.90)
            hh_s      = normalize(b.get("hard_hit_pct"), lo=0.25,  hi=0.55)
            bb_s      = normalize(b.get("bb_pct"),       lo=0.04,  hi=0.18)

            batter_comp = weighted_avg([
                (xba_s,     0.40),
                (contact_s, 0.25),
                (hh_s,      0.20),
                (bb_s,      0.15),
            ])

            combined = (batter_comp ** 0.60) * (sp_xba_s ** 0.40)
            signal = round(combined * 10, 1)

            if signal >= 5.0:
                batter_name = b.get("name", f"Batter {batter_id}")
                picks.append({
                    "bet_type":   "HIT_PROP",
                    "subject":    batter_name,
                    "subject_id": batter_id,
                    "direction":  "OVER",
                    "headline":   f"{batter_name} to Record a Hit",
                    "signal":     signal,
                    "reasons":    _build_reasons(b, opp_sp, sp_throws, xba_val),
                    "raw_scores": {
                        "xba":            xba_val,
                        "xba_split":      f"vs_{'L' if sp_throws == 'L' else 'R' if sp_throws == 'R' else 'season'}",
                        "xwoba":          b.get("xwoba"),
                        "contact_pct":    _pct(b.get("contact_pct")),
                        "hard_hit_pct":   _pct(b.get("hard_hit_pct")),
                        "bb_pct":         _pct(b.get("bb_pct")),
                        "k_pct":          _pct(b.get("k_pct")),
                        "sp_xba_against": opp_sp.get("xba_against"),
                        "sp_throws":      sp_throws,
                        "batter_component": round(batter_comp, 3),
                        "sp_matchup_score": round(sp_xba_s, 3),
                        "edge_score":      batter_edge_score(b, sp_throws),
                        "recent_h_games":  b.get("recent_h_games"),
                    },
                })

    return picks


def _build_reasons(b: dict, sp: dict, sp_throws: str | None = None, xba_val: float | None = None) -> list[str]:
    reasons = []

    xba = xba_val or b.get("xba")
    split_label = f" vs. {'LHP' if sp_throws == 'L' else 'RHP' if sp_throws == 'R' else 'all pitchers'}"
    if xba is not None:
        if xba >= 0.290:
            tier = "well above average"
        elif xba >= 0.255:
            tier = "above average"
        else:
            tier = "average"
        reasons.append(
            f"xBA of {xba:.3f}{split_label} ({tier}) — expected batting average from exit velocity and launch angle"
        )

    hh = b.get("hard_hit_pct")
    if hh is not None:
        reasons.append(
            f"Hard contact rate {hh:.1%} — line drives and hard grounders find holes more often"
        )

    contact = b.get("contact_pct")
    if contact is not None:
        reasons.append(
            f"Makes contact on {contact:.1%} of swings (MLB avg ~77%) — limits strikeout risk"
        )

    sp_name = sp.get("name", "Opposing SP")
    xba_against = sp.get("xba_against")
    if xba_against is not None:
        if xba_against >= 0.270:
            label = "hittable profile — elevated contact quality allowed"
        elif xba_against >= 0.240:
            label = "neutral contact profile"
        else:
            label = "tough matchup — suppresses expected contact quality"
        reasons.append(f"{sp_name} xBA-against {xba_against:.3f} — {label}")

    bb = b.get("bb_pct")
    if bb is not None and bb >= 0.09:
        reasons.append(
            f"Walks at {bb:.1%} — extends at-bats, works into favorable counts"
        )

    return reasons[:6]


def _pct(v) -> str | None:
    return f"{v:.1%}" if v is not None else None
