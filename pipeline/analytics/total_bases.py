"""Score batter total bases (TB) prop opportunities.

Signal logic: xSLG is a far better predictor of extra-base hit production than
actual SLG, which is noisy over short samples. Books price TB lines off observed
SLG/AVG, creating systematic edge for batters whose xSLG is meaningfully higher
than their actual SLG. Barrel rate anchors the power dimension.

Now uses platoon-split xSLG (vs. LHP or vs. RHP) when available, and applies a
PA-count multiplier based on batting order position.
"""

from __future__ import annotations

from pipeline.park_factors import get_run_factor
from pipeline.scorer import normalize, weighted_avg

# PA per game by lineup slot (index 0 = leadoff). Slots 4–6 ≈ baseline 1.0.
_PA_MULT = [1.10, 1.05, 1.02, 1.00, 0.98, 0.97, 0.96, 0.93, 0.88]


def score_total_bases_props(game: dict, cache: dict) -> list[dict]:
    picks = []
    venue = game.get("venue", "")
    park_s = normalize(get_run_factor(venue), lo=88, hi=118)

    for bat_side, sp_side in [("home", "away"), ("away", "home")]:
        opp_sp_id = game.get(f"{sp_side}_sp_id")
        opp_sp = cache.get(opp_sp_id, {}) if opp_sp_id else {}
        sp_throws = (
            opp_sp.get("throws")
            or game.get(f"{sp_side}_sp_throws")
        )

        # Higher xSLG-against means pitcher allows hard contact → batter-friendly
        sp_xslg_s = normalize(opp_sp.get("xslg_against"), lo=0.280, hi=0.480)

        context_comp = weighted_avg([
            (sp_xslg_s, 0.60),
            (park_s,    0.40),
        ])

        for order, batter_id in enumerate(game.get(f"{bat_side}_lineup", []), 1):
            b = cache.get(batter_id)
            if not b:
                continue

            # Use platoon-split xSLG when available, fall back to season average
            suffix   = "_vs_l" if sp_throws == "L" else "_vs_r" if sp_throws == "R" else ""
            xslg_val = (b.get(f"xslg{suffix}") if suffix else None) or b.get("xslg")
            if xslg_val is None:
                continue  # no Statcast data — skip rather than emit a 0.5-neutral fake signal

            xslg_s   = normalize(xslg_val,            lo=0.280, hi=0.580)
            barrel_s = normalize(b.get("barrel_pct"), lo=0.030, hi=0.200)

            batter_comp = weighted_avg([
                (xslg_s,   0.60),
                (barrel_s, 0.40),
            ])

            combined    = (batter_comp ** 0.55) * (context_comp ** 0.45)
            base_signal = max(0.0, min(10.0, round(combined * 10, 1)))

            pa_mult = _PA_MULT[order - 1] if order <= 9 else 1.0
            signal  = max(0.0, min(10.0, round(base_signal * pa_mult, 1)))

            if signal >= 99.0:  # disabled: no signal separation across tiers (28-33% flat)
                batter_name = b.get("name", f"Batter {batter_id}")
                picks.append({
                    "bet_type":   "TB_PROP",
                    "subject":    batter_name,
                    "subject_id": batter_id,
                    "direction":  "OVER",
                    "headline":   f"{batter_name} Total Bases — OVER",
                    "signal":     signal,
                    "reasons":    _build_reasons(b, opp_sp, venue, sp_throws, xslg_val),
                    "raw_scores": {
                        "xslg":              xslg_val,
                        "xslg_split":        f"vs_{'L' if sp_throws == 'L' else 'R' if sp_throws == 'R' else 'season'}",
                        "actual_slg":        b.get("slg"),
                        "barrel_pct":        _pct(b.get("barrel_pct")),
                        "sp_xslg_against":   opp_sp.get("xslg_against"),
                        "park_run_factor":   get_run_factor(venue),
                        "batter_component":  round(batter_comp, 3),
                        "context_component": round(context_comp, 3),
                        "batting_order":     order,
                        "pa_multiplier":     pa_mult,
                    },
                })

    return picks


def _build_reasons(b: dict, sp: dict, venue: str, sp_throws: str | None = None, xslg_val: float | None = None) -> list[str]:
    reasons = []
    xslg = xslg_val or b.get("xslg")
    split_label = f" vs. {'LHP' if sp_throws == 'L' else 'RHP' if sp_throws == 'R' else 'all pitchers'}"
    if xslg is not None:
        reasons.append(
            f"xSLG of {xslg:.3f}{split_label} — expected slugging from exit velocity and angle"
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
