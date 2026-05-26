"""Score game total (over/under runs) opportunities.

Signal logic: Evaluates run environment from all angles — both SPs' expected
ERA metrics, both lineups' offensive quality, and park run factor. Computes
independent OVER and UNDER signals so either direction can surface.

Enhancement: wind and temperature apply a weather modifier.
"""

from __future__ import annotations

from pipeline.park_factors import get_run_factor
from pipeline.scorer import normalize, weighted_avg, lineup_weighted_mean, bullpen_score
from pipeline.umpire import compute_umpire_modifier, get_run_tendency
from pipeline.weather import compute_weather_modifier


def score_game_total(game: dict, cache: dict) -> list[dict]:
    picks = []
    venue   = game.get("venue", "")
    weather = game.get("weather")
    umpire  = game.get("umpire", "")
    park_run = get_run_factor(venue)
    park_s   = normalize(park_run, lo=88, hi=118)

    weather_mod, weather_reason = compute_weather_modifier(weather, "TOTAL")

    home_sp = cache.get(game.get("home_sp_id"), {})
    away_sp = cache.get(game.get("away_sp_id"), {})

    def sp_suppress(sp: dict) -> float:
        xfip_s  = 1.0 - normalize(sp.get("xfip"),  lo=2.50, hi=5.50)
        siera_s = 1.0 - normalize(sp.get("siera"), lo=2.50, hi=5.50)
        brl_s   = 1.0 - normalize(sp.get("barrel_pct_against"), lo=0.03, hi=0.15)
        stuff_s = normalize(sp.get("stuff_plus"), lo=80, hi=130)
        return weighted_avg([(xfip_s, 0.35), (siera_s, 0.35), (brl_s, 0.20), (stuff_s, 0.10)])

    home_bp = cache.get(f"bullpen:{game.get('homeTeam', '')}", {})
    away_bp = cache.get(f"bullpen:{game.get('awayTeam', '')}", {})

    def effective_suppress(sp: dict, bp: dict) -> float:
        sp_s = sp_suppress(sp)
        if bp:
            return sp_s * 0.60 + bullpen_score(bp) * 0.40
        return sp_s

    home_supp      = effective_suppress(home_sp, home_bp)
    away_supp      = effective_suppress(away_sp, away_bp)
    avg_suppression = (home_supp + away_supp) / 2.0

    home_sp_throws = home_sp.get("throws") or game.get("home_sp_throws")
    away_sp_throws = away_sp.get("throws") or game.get("away_sp_throws")

    home_lineup = [cache[b] for b in game.get("home_lineup", []) if b in cache]
    away_lineup = [cache[b] for b in game.get("away_lineup", []) if b in cache]

    # Home batters face the away SP (and vice versa), so use the opposing SP's throws
    home_xwoba = lineup_weighted_mean(home_lineup, "xwoba", sp_throws=away_sp_throws) or 0.320
    away_xwoba = lineup_weighted_mean(away_lineup, "xwoba", sp_throws=home_sp_throws) or 0.320
    avg_xwoba  = (home_xwoba + away_xwoba) / 2.0
    offense_s  = normalize(avg_xwoba, lo=0.260, hi=0.380)

    over_raw  = weighted_avg([(offense_s, 0.40), (park_s, 0.25), (1.0 - avg_suppression, 0.35)])
    under_raw = weighted_avg([(1.0 - offense_s, 0.40), (1.0 - park_s, 0.25), (avg_suppression, 0.35)])

    over_signal  = max(0.0, min(10.0, round(over_raw  * 10 + weather_mod, 1)))
    under_signal = max(0.0, min(10.0, round(under_raw * 10 - weather_mod, 1)))

    home_name = game.get("homeTeam", "Home")
    away_name = game.get("awayTeam", "Away")
    matchup   = f"{away_name} @ {home_name}"

    run_tend = get_run_tendency(umpire)
    run_tend_reason = None
    if abs(run_tend) >= 0.3:
        tend_dir = "over" if run_tend > 0 else "under"
        run_tend_reason = (
            f"HP umpire {umpire} games average {abs(run_tend):.1f} runs "
            f"{'above' if run_tend > 0 else 'below'} expected — {tend_dir} lean"
        )

    for direction, base_signal in [("OVER", over_signal), ("UNDER", under_signal)]:
        ump_mod, ump_reason = compute_umpire_modifier(umpire, "TOTAL", direction)
        # run tendency: positive trend boosts OVER, suppresses UNDER
        tend_mod = run_tend if direction == "OVER" else -run_tend
        signal = max(0.0, min(10.0, round(base_signal + ump_mod + tend_mod, 1)))
        if signal >= 5.0:
            reasons = _build_reasons(direction, home_sp, away_sp, avg_xwoba, park_run, venue)
            if weather_reason:
                reasons = (reasons + [weather_reason])[:4]
            if ump_reason:
                reasons = (reasons + [ump_reason])[:4]
            if run_tend_reason and direction == ("OVER" if run_tend > 0 else "UNDER"):
                reasons = (reasons + [run_tend_reason])[:4]

            picks.append({
                "bet_type":  "TOTAL",
                "subject":   matchup,
                "direction": direction,
                "headline":  f"{matchup} Total Runs — {direction}",
                "signal":    signal,
                "reasons":   reasons,
                "raw_scores": {
                    "home_sp_xfip":       home_sp.get("xfip"),
                    "away_sp_xfip":       away_sp.get("xfip"),
                    "home_sp_siera":      home_sp.get("siera"),
                    "away_sp_siera":      away_sp.get("siera"),
                    "home_bullpen_xera":  home_bp.get("xera") if home_bp else None,
                    "away_bullpen_xera":  away_bp.get("xera") if away_bp else None,
                    "avg_lineup_xwoba":   round(avg_xwoba, 3),
                    "park_run_factor":    park_run,
                    "avg_suppression":    round(avg_suppression, 3),
                    "offense_score":      round(offense_s, 3),
                    "lineup_data":        (home_xwoba != 0.320 and bool(home_lineup)) or (away_xwoba != 0.320 and bool(away_lineup)),
                    "weather_modifier":   round(weather_mod, 2) if weather_mod else None,
                    "umpire_modifier":    round(ump_mod, 2) if ump_mod else None,
                    "run_tendency":       round(run_tend, 2) if run_tend else None,
                    "umpire":             umpire or None,
                },
            })

    return picks


def _build_reasons(direction, home_sp, away_sp, avg_xwoba, park_run, venue) -> list[str]:
    reasons = []
    home_name = home_sp.get("name", "Home SP")
    away_name = away_sp.get("name", "Away SP")

    if direction == "OVER":
        if avg_xwoba:
            reasons.append(f"Combined lineup xwOBA of {avg_xwoba:.3f} — above-average run environment")
        if park_run > 102:
            reasons.append(f"{venue} run factor of {park_run} — offense-friendly park")
        xfip_avg = _avg_xfip(home_sp, away_sp)
        if xfip_avg and xfip_avg > 4.20:
            reasons.append(f"Both SPs project to weak xFIP ({xfip_avg:.2f} combined avg)")
    else:
        xfip_avg = _avg_xfip(home_sp, away_sp)
        if xfip_avg and xfip_avg < 3.60:
            reasons.append(f"Elite pitching matchup: combined xFIP avg of {xfip_avg:.2f}")
        if home_sp.get("siera"):
            reasons.append(f"{home_name} SIERA: {home_sp['siera']:.2f}")
        if away_sp.get("siera"):
            reasons.append(f"{away_name} SIERA: {away_sp['siera']:.2f}")
        if park_run < 97:
            reasons.append(f"{venue} run factor of {park_run} — suppresses scoring")
    return reasons[:4]


def _avg_xfip(sp1, sp2):
    vals = [v for v in [sp1.get("xfip"), sp2.get("xfip")] if v is not None]
    return sum(vals) / len(vals) if vals else None
