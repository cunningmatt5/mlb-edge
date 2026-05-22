"""Score one-team run total opportunities.

Unlike game_totals (which evaluates combined runs and needs both sides to align),
team totals let you take a directional view on just one offense. The signal here
is: strong lineup facing a weak SP, or weak lineup facing an elite SP — regardless
of what's happening on the other side of the game.

Enhancement: weather modifier and subject_side for track record grading.
"""

from __future__ import annotations

from pipeline.park_factors import get_run_factor
from pipeline.scorer import normalize, weighted_avg, safe_mean
from pipeline.weather import compute_weather_modifier


def score_team_totals(game: dict, cache: dict) -> list[dict]:
    picks = []
    venue   = game.get("venue", "")
    weather = game.get("weather")
    park_s  = normalize(get_run_factor(venue), lo=88, hi=118)

    weather_mod, weather_reason = compute_weather_modifier(weather, "TEAM_TOTAL")

    for offense_side, sp_side in [("home", "away"), ("away", "home")]:
        opp_sp_id = game.get(f"{sp_side}_sp_id")
        opp_sp    = cache.get(opp_sp_id, {}) if opp_sp_id else {}

        xfip_s  = 1.0 - normalize(opp_sp.get("xfip"),  lo=2.80, hi=5.50)
        siera_s = 1.0 - normalize(opp_sp.get("siera"), lo=2.80, hi=5.50)
        sp_suppress = weighted_avg([(xfip_s, 0.50), (siera_s, 0.50)])

        lineup = [cache[b] for b in game.get(f"{offense_side}_lineup", []) if b in cache]
        lineup_xwoba = safe_mean([b.get("xwoba") for b in lineup]) or 0.310
        offense_s    = normalize(lineup_xwoba, lo=0.270, hi=0.370)

        over_raw  = weighted_avg([(offense_s, 0.45), (1.0 - sp_suppress, 0.35), (park_s, 0.20)])
        under_raw = weighted_avg([(1.0 - offense_s, 0.45), (sp_suppress, 0.35), (1.0 - park_s, 0.20)])

        over_signal  = max(0.0, min(10.0, round(over_raw  * 10 + weather_mod, 1)))
        under_signal = max(0.0, min(10.0, round(under_raw * 10 - weather_mod, 1)))

        offense_team = game.get(f"{offense_side}Team", offense_side.title())
        sp_name      = opp_sp.get("name", "Opposing SP")

        for direction, signal in [("OVER", over_signal), ("UNDER", under_signal)]:
            if signal >= 7.0:
                reasons = _build_reasons(
                    direction, offense_team, sp_name, opp_sp,
                    lineup_xwoba, get_run_factor(venue), venue
                )
                if weather_reason:
                    reasons = (reasons + [weather_reason])[:4]

                picks.append({
                    "bet_type":      "TEAM_TOTAL",
                    "subject":       offense_team,
                    "subject_side":  offense_side,
                    "direction":     direction,
                    "headline":      f"{offense_team} Team Total — {direction}",
                    "signal":        signal,
                    "reasons":       reasons,
                    "raw_scores": {
                        "lineup_xwoba":   round(lineup_xwoba, 3),
                        "sp_xfip":        opp_sp.get("xfip"),
                        "sp_siera":       opp_sp.get("siera"),
                        "park_run_factor": get_run_factor(venue),
                        "sp_suppress":    round(sp_suppress, 3),
                        "offense_score":  round(offense_s, 3),
                    },
                })

    return picks


def _build_reasons(direction, team, sp_name, sp, lineup_xwoba, park_run, venue) -> list[str]:
    reasons = []
    if direction == "OVER":
        if lineup_xwoba:
            reasons.append(f"{team} lineup xwOBA of {lineup_xwoba:.3f} — above-average offensive unit")
        if sp.get("xfip") and sp["xfip"] > 4.20:
            reasons.append(f"{sp_name} xFIP of {sp['xfip']:.2f} — run-suppression below average")
        elif sp.get("siera") and sp["siera"] > 4.20:
            reasons.append(f"{sp_name} SIERA of {sp['siera']:.2f} — projects below average")
        if park_run > 102:
            reasons.append(f"{venue} run factor of {park_run} — boosts run scoring environment")
    else:
        if sp.get("xfip") and sp["xfip"] < 3.50:
            reasons.append(f"{sp_name} xFIP of {sp['xfip']:.2f} — elite run suppression")
        if sp.get("siera") and sp["siera"] < 3.50:
            reasons.append(f"{sp_name} SIERA of {sp['siera']:.2f} — elite expected ERA")
        if lineup_xwoba and lineup_xwoba < 0.300:
            reasons.append(f"{team} lineup xwOBA of {lineup_xwoba:.3f} — below-average offense")
        if park_run < 97:
            reasons.append(f"{venue} run factor of {park_run} — suppresses scoring")
    return reasons[:4]
