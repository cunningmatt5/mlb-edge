"""Score batter home run prop opportunities.

Signal logic: High-barrel%, hard-hit batters facing pitchers who allow
home runs, in HR-friendly parks. Both batter quality and context (SP + park)
must clear a threshold.

Enhancement: wind speed/direction and temperature apply a weather modifier.
"""

from __future__ import annotations

from pipeline.park_factors import get_hr_factor
from pipeline.scorer import normalize, weighted_avg, batter_edge_score
from pipeline.weather import compute_weather_modifier


def score_hr_props(game: dict, cache: dict) -> list[dict]:
    picks = []
    venue   = game.get("venue", "")
    weather = game.get("weather")
    hr_park = get_hr_factor(venue)
    park_s  = normalize(hr_park, lo=85, hi=120)

    weather_mod, weather_reason = compute_weather_modifier(weather, "HR_PROP")

    for bat_side, sp_side in [("home", "away"), ("away", "home")]:
        opp_sp_id = game.get(f"{sp_side}_sp_id")
        opp_sp    = cache.get(opp_sp_id, {}) if opp_sp_id else {}
        sp_hr9_s  = normalize(opp_sp.get("hr9"), lo=0.6, hi=2.0)

        context_comp = weighted_avg([
            (sp_hr9_s, 0.55),
            (park_s,   0.45),
        ])

        for batter_id in game.get(f"{bat_side}_lineup", []):
            b = cache.get(batter_id)
            if not b:
                continue

            barrel_s = normalize(b.get("barrel_pct"),       lo=0.03, hi=0.20)
            hh_s     = normalize(b.get("hard_hit_pct"),     lo=0.25, hi=0.55)
            xwoba_s  = normalize(b.get("xwoba"),            lo=0.280, hi=0.420)
            la_s     = normalize(b.get("avg_launch_angle"), lo=5,    hi=22)

            batter_comp = weighted_avg([
                (barrel_s, 0.40),
                (hh_s,     0.25),
                (xwoba_s,  0.20),
                (la_s,     0.15),
            ])

            combined = (batter_comp ** 0.55) * (context_comp ** 0.45)
            signal   = max(0.0, min(10.0, round(combined * 10 + weather_mod, 1)))

            if signal >= 5.0:
                batter_name = b.get("name", f"Batter {batter_id}")
                reasons = _build_reasons(b, opp_sp, venue, hr_park)
                if weather_reason:
                    reasons = (reasons + [weather_reason])[:6]

                picks.append({
                    "bet_type":   "HR_PROP",
                    "subject":    batter_name,
                    "subject_id": batter_id,
                    "direction":  "OVER",
                    "headline":   f"{batter_name} to Hit Home Run",
                    "signal":     signal,
                    "reasons":    reasons,
                    "raw_scores": {
                        "barrel_pct":        _pct(b.get("barrel_pct")),
                        "hard_hit_pct":      _pct(b.get("hard_hit_pct")),
                        "xwoba":             b.get("xwoba"),
                        "bb_pct":            _pct(b.get("bb_pct")),
                        "k_pct":             _pct(b.get("k_pct")),
                        "avg_launch_angle":  b.get("avg_launch_angle"),
                        "sp_hr9":            opp_sp.get("hr9"),
                        "park_hr_factor":    hr_park,
                        "batter_component":  round(batter_comp, 3),
                        "context_component": round(context_comp, 3),
                        "edge_score":        batter_edge_score(b),
                        "recent_hr_games":   b.get("recent_hr_games"),
                    },
                })

    return picks


def _build_reasons(b: dict, sp: dict, venue: str, hr_park: int) -> list[str]:
    reasons = []

    barrel = b.get("barrel_pct")
    if barrel is not None:
        if barrel >= 0.12:
            reasons.append(
                f"Elite barrel rate of {barrel:.1%} — ranks among MLB's top power profiles (avg ~8%)"
            )
        else:
            reasons.append(f"Barrel rate of {barrel:.1%} vs. MLB avg ~8%")

    hh = b.get("hard_hit_pct")
    if hh is not None:
        reasons.append(
            f"Hard-hit rate {hh:.1%} — exit velocity ≥95 mph on nearly half of batted balls (MLB avg ~38%)"
            if hh >= 0.44 else
            f"Hard-hit rate {hh:.1%} — exit velocity ≥95 mph (MLB avg ~38%)"
        )

    xwoba = b.get("xwoba")
    if xwoba is not None:
        if xwoba >= 0.380:
            tier = "elite contact quality"
        elif xwoba >= 0.340:
            tier = "above-average contact quality"
        else:
            tier = "average contact quality"
        reasons.append(f"xwOBA of {xwoba:.3f} — {tier} based on exit velocity and angle")

    sp_name = sp.get("name", "Opposing SP")
    hr9 = sp.get("hr9")
    if hr9 is not None:
        if hr9 >= 1.5:
            label = "top-quartile homer risk"
        elif hr9 >= 1.1:
            label = "elevated homer rate"
        else:
            label = "league-average homer rate"
        reasons.append(f"{sp_name} allows {hr9:.2f} HR/9 this season — {label}")

    if venue and hr_park != 100:
        direction = "HR-friendly" if hr_park > 100 else "pitcher-friendly"
        reasons.append(f"{venue} park HR factor: {hr_park} ({direction})")

    bb = b.get("bb_pct")
    if bb is not None and bb >= 0.10:
        reasons.append(
            f"Patient hitter ({bb:.1%} BB%) — works deep into counts, maximizes power opportunities"
        )

    return reasons[:6]


def _pct(v) -> str | None:
    return f"{v:.1%}" if v is not None else None
