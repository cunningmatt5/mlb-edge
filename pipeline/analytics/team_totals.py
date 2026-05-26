"""Score one-team run total opportunities.

Unlike game_totals (which evaluates combined runs and needs both sides to align),
team totals let you take a directional view on just one offense. The signal here
is: strong lineup facing a weak SP, or weak lineup facing an elite SP — regardless
of what's happening on the other side of the game.

Enhancement: weather modifier and subject_side for track record grading.
"""

from __future__ import annotations

from pipeline.park_factors import get_run_factor
from pipeline.scorer import normalize, weighted_avg, lineup_weighted_mean, bullpen_score
from pipeline.umpire import compute_umpire_modifier, get_run_tendency
from pipeline.weather import compute_weather_modifier


def score_team_totals(game: dict, cache: dict) -> list[dict]:
    picks = []
    venue   = game.get("venue", "")
    weather = game.get("weather")
    umpire  = game.get("umpire", "")
    park_s  = normalize(get_run_factor(venue), lo=88, hi=118)

    weather_mod, weather_reason = compute_weather_modifier(weather, "TEAM_TOTAL")

    run_tend = get_run_tendency(umpire)
    run_tend_reason = None
    if abs(run_tend) >= 0.3:
        tend_dir = "over" if run_tend > 0 else "under"
        run_tend_reason = (
            f"HP umpire {umpire} games average {abs(run_tend):.1f} runs "
            f"{'above' if run_tend > 0 else 'below'} expected — {tend_dir} lean"
        )

    for offense_side, sp_side in [("home", "away"), ("away", "home")]:
        opp_sp_id = game.get(f"{sp_side}_sp_id")
        opp_sp    = cache.get(opp_sp_id, {}) if opp_sp_id else {}
        sp_throws = opp_sp.get("throws") or game.get(f"{sp_side}_sp_throws")

        xfip_s  = 1.0 - normalize(opp_sp.get("xfip"),  lo=2.50, hi=5.50)
        siera_s = 1.0 - normalize(opp_sp.get("siera"), lo=2.50, hi=5.50)
        sp_suppress = weighted_avg([(xfip_s, 0.50), (siera_s, 0.50)])

        # Blend starter (60%) + bullpen (40%) for full-game suppression
        sp_team_name = game.get(f"{sp_side}Team", "")
        bp_data = cache.get(f"bullpen:{sp_team_name}", {})
        if bp_data:
            bp = bullpen_score(bp_data)
            sp_suppress = sp_suppress * 0.60 + bp * 0.40

        lineup = [cache[b] for b in game.get(f"{offense_side}_lineup", []) if b in cache]
        lineup_xwoba = lineup_weighted_mean(lineup, "xwoba", sp_throws=sp_throws) or 0.320
        offense_s    = normalize(lineup_xwoba, lo=0.260, hi=0.380)

        over_raw  = weighted_avg([(offense_s, 0.45), (1.0 - sp_suppress, 0.35), (park_s, 0.20)])
        under_raw = weighted_avg([(1.0 - offense_s, 0.45), (sp_suppress, 0.35), (1.0 - park_s, 0.20)])

        over_signal  = max(0.0, min(10.0, round(over_raw  * 10 + weather_mod, 1)))
        under_signal = max(0.0, min(10.0, round(under_raw * 10 - weather_mod, 1)))

        offense_team = game.get(f"{offense_side}Team", offense_side.title())
        sp_name      = opp_sp.get("name", "Opposing SP")

        for direction, base_signal in [("OVER", over_signal), ("UNDER", under_signal)]:
            ump_mod, ump_reason = compute_umpire_modifier(umpire, "TEAM_TOTAL", direction)
            tend_mod = run_tend if direction == "OVER" else -run_tend
            signal = max(0.0, min(10.0, round(base_signal + ump_mod + tend_mod, 1)))
            if signal >= 5.0:
                reasons = _build_reasons(
                    direction, offense_team, sp_name, opp_sp,
                    lineup_xwoba, get_run_factor(venue), venue
                )
                if weather_reason:
                    reasons = (reasons + [weather_reason])[:4]
                if ump_reason:
                    reasons = (reasons + [ump_reason])[:4]
                if run_tend_reason and direction == ("OVER" if run_tend > 0 else "UNDER"):
                    reasons = (reasons + [run_tend_reason])[:4]

                picks.append({
                    "bet_type":      "TEAM_TOTAL",
                    "subject":       offense_team,
                    "subject_side":  offense_side,
                    "direction":     direction,
                    "headline":      f"{offense_team} Team Total — {direction}",
                    "signal":        signal,
                    "reasons":       reasons,
                    "raw_scores": {
                        "lineup_xwoba":    round(lineup_xwoba, 3),
                        "sp_xfip":         opp_sp.get("xfip"),
                        "sp_siera":        opp_sp.get("siera"),
                        "bullpen_xera":    bp_data.get("xera") if bp_data else None,
                        "park_run_factor":  get_run_factor(venue),
                        "sp_suppress":     round(sp_suppress, 3),
                        "offense_score":   round(offense_s, 3),
                        "lineup_data":     lineup_xwoba != 0.320 and bool(lineup),
                        "umpire_modifier": round(ump_mod, 2) if ump_mod else None,
                        "run_tendency":    round(run_tend, 2) if run_tend else None,
                        "umpire":          umpire or None,
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
