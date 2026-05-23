"""Score batter hit prop opportunities (anytime hits).

Signal logic: High-contact, high-xBA batters facing a pitcher with weak
xBA-against. Contact rate and plate discipline anchor the batter side;
pitcher's expected outcomes anchor the matchup side.
"""

from __future__ import annotations

from pipeline.scorer import normalize, weighted_avg


def score_hit_props(game: dict, cache: dict) -> list[dict]:
    picks = []

    for bat_side, sp_side in [("home", "away"), ("away", "home")]:
        opp_sp_id = game.get(f"{sp_side}_sp_id")
        opp_sp = cache.get(opp_sp_id, {}) if opp_sp_id else {}

        # Pitcher xBA-against: higher value = more hitter-friendly
        sp_xba_s = normalize(opp_sp.get("xba_against"), lo=0.190, hi=0.330)

        for batter_id in game.get(f"{bat_side}_lineup", []):
            b = cache.get(batter_id)
            if not b:
                continue

            xba_s = normalize(b.get("xba"), lo=0.190, hi=0.340)
            contact_s = normalize(b.get("contact_pct"), lo=0.62, hi=0.90)
            bb_s = normalize(b.get("bb_pct"), lo=0.04, hi=0.18)

            batter_comp = weighted_avg([
                (xba_s,    0.50),
                (contact_s, 0.30),
                (bb_s,      0.20),
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
                    "headline": f"{batter_name} to Record a Hit",
                    "signal": signal,
                    "reasons": _build_reasons(b, opp_sp),
                    "raw_scores": {
                        "xba": b.get("xba"),
                        "contact_pct": _pct(b.get("contact_pct")),
                        "bb_pct": _pct(b.get("bb_pct")),
                        "sp_xba_against": opp_sp.get("xba_against"),
                        "batter_component": round(batter_comp, 3),
                        "sp_matchup_score": round(sp_xba_s, 3),
                    },
                })

    return picks


def _build_reasons(b: dict, sp: dict) -> list[str]:
    reasons = []
    if b.get("xba"):
        reasons.append(f"xBA of {b['xba']:.3f} — expected batting average based on exit velocity/angle")
    if b.get("contact_pct"):
        reasons.append(f"Contact rate of {b['contact_pct']:.1%} (MLB avg ~77%)")
    sp_name = sp.get("name", "Opposing SP")
    if sp.get("xba_against"):
        reasons.append(f"{sp_name} has an xBA-against of {sp['xba_against']:.3f} this season")
    if b.get("bb_pct"):
        reasons.append(f"BB% of {b['bb_pct']:.1%} — disciplined plate approach")
    return reasons[:4]


def _pct(v) -> str | None:
    return f"{v:.1%}" if v is not None else None
