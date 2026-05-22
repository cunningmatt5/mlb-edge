"""Score batter total bases (TB) prop opportunities.

Signal logic: xSLG is a far better predictor of extra-base hit production than
actual SLG, which is noisy over short samples. Books price TB lines off observed
SLG/AVG, creating systematic edge for batters whose xSLG is meaningfully higher
than their actual SLG. Barrel rate anchors the power dimension.
"""

from __future__ import annotations

from pipeline.park_factors import get_run_factor
from pipeline.scorer import normalize, weighted_avg


def score_total_bases_props(game: dict, cache: dict) -> list[dict]:
    picks = []
    venue = game.get("venue", "")
    park_s = normalize(get_run_factor(venue), lo=88, hi=118)

    for bat_side, sp_side in [("home", "away"), ("away", "home")]:
        opp_sp_id = game.get(f"{sp_side}_sp_id")
        opp_sp = cache.get(opp_sp_id, {}) if opp_sp_id else {}

        # Higher xSLG-against means pitcher allows hard contact → batter-friendly
        sp_xslg_s = normalize(opp_sp.get("xslg_against"), lo=0.280, hi=0.480)

        context_comp = weighted_avg([
            (sp_xslg_s, 0.60),
            (park_s,    0.40),
        ])

        for batter_id in game.get(f"{bat_side}_lineup", []):
            b = cache.get(batter_id)
            if not b:
                continue

            xslg_s  = normalize(b.get("xslg"),       lo=0.280, hi=0.580)
            barrel_s = normalize(b.get("barrel_pct"), lo=0.030, hi=0.200)

            batter_comp = weighted_avg([
                (xslg_s,   0.60),
                (barrel_s, 0.40),
            ])

            combined = (batter_comp ** 0.55) * (context_comp ** 0.45)
            signal = round(combined * 10, 1)

            if signal >= 7.0:
                batter_name = b.get("name", f"Batter {batter_id}")
                picks.append({
                    "bet_type": "TB_PROP",
                    "subject": batter_name,
                    "direction": "OVER",
                    "headline": f"{batter_name} Total Bases — OVER",
                    "signal": signal,
                    "reasons": _build_reasons(b, opp_sp, venue),
                    "raw_scores": {
                        "xslg": b.get("xslg"),
                        "actual_slg": b.get("xslg"),
                        "barrel_pct": _pct(b.get("barrel_pct")),
                        "sp_xslg_against": opp_sp.get("xslg_against"),
                        "park_run_factor": get_run_factor(venue),
                        "batter_component": round(batter_comp, 3),
                        "context_component": round(context_comp, 3),
                    },
                })

    return picks


def _build_reasons(b: dict, sp: dict, venue: str) -> list[str]:
    reasons = []
    if b.get("xslg"):
        reasons.append(
            f"xSLG of {b['xslg']:.3f} — expected slugging based on exit velocity/angle"
        )
    if b.get("barrel_pct"):
        reasons.append(f"Barrel rate of {b['barrel_pct']:.1%} driving extra-base hit upside")
    sp_name = sp.get("name", "Opposing SP")
    if sp.get("xslg_against"):
        reasons.append(
            f"{sp_name} xSLG-against of {sp['xslg_against']:.3f} — allows hard contact"
        )
    if venue:
        reasons.append(f"Venue: {venue}")
    return reasons[:4]


def _pct(v) -> str | None:
    return f"{v:.1%}" if v is not None else None
