"""Score first-5-innings total (over/under) opportunities.

SP-only suppression model — no bullpen component — so starter dominance drives
the signal cleanly. A two-ace matchup surfaces as F5 UNDER even when the
full-game total looks neutral due to bullpen variance.

Weights: suppression 45% (SP only), offense 35%, park 20%.
"""

from __future__ import annotations

from pipeline.analytics.constants import MIN_SIGNAL_F5_TOTAL
from pipeline.park_factors import get_run_factor
from pipeline.scorer import normalize, weighted_avg, lineup_weighted_mean
from pipeline.umpire import compute_umpire_modifier
from pipeline.weather import compute_weather_modifier


def score_f5_totals(game: dict, cache: dict) -> list[dict]:
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

    # SP-only suppression — F5 has no bullpen involvement
    home_supp      = sp_suppress(home_sp)
    away_supp      = sp_suppress(away_sp)
    avg_suppression = (home_supp + away_supp) / 2.0

    home_sp_throws = home_sp.get("throws") or game.get("home_sp_throws")
    away_sp_throws = away_sp.get("throws") or game.get("away_sp_throws")

    home_lineup = [cache[b] for b in game.get("home_lineup", []) if b in cache]
    away_lineup = [cache[b] for b in game.get("away_lineup", []) if b in cache]

    home_xwoba = lineup_weighted_mean(home_lineup, "xwoba", sp_throws=away_sp_throws) or 0.320
    away_xwoba = lineup_weighted_mean(away_lineup, "xwoba", sp_throws=home_sp_throws) or 0.320
    avg_xwoba  = (home_xwoba + away_xwoba) / 2.0
    offense_s  = normalize(avg_xwoba, lo=0.260, hi=0.380)

    # SP weight lifted vs game_totals: 0.45 suppression, 0.35 offense, 0.20 park
    over_raw  = weighted_avg([(offense_s, 0.35), (park_s, 0.20), (1.0 - avg_suppression, 0.45)])
    under_raw = weighted_avg([(1.0 - offense_s, 0.35), (1.0 - park_s, 0.20), (avg_suppression, 0.45)])

    over_signal  = max(0.0, min(10.0, round(over_raw  * 10 + weather_mod, 1)))
    under_signal = max(0.0, min(10.0, round(under_raw * 10 - weather_mod, 1)))

    home_name = game.get("homeTeam", "Home")
    away_name = game.get("awayTeam", "Away")
    matchup   = f"{away_name} @ {home_name}"

    line_movement = game.get("line_movement") or {}
    total_move    = line_movement.get("total_move")

    for direction, base_signal in [("OVER", over_signal), ("UNDER", under_signal)]:
        ump_mod, ump_reason = compute_umpire_modifier(umpire, "TOTAL", direction)
        lm_mod = 0.0
        lm_reason = None
        if total_move is not None and abs(total_move) >= 0.5:
            agrees = (direction == "OVER" and total_move > 0) or (direction == "UNDER" and total_move < 0)
            if agrees:
                lm_mod = 0.3
                move_dir = "up" if total_move > 0 else "down"
                opening_t = line_movement.get("opening_total", "?")
                current_t = line_movement.get("current_total", "?")
                lm_reason = f"Total moved {move_dir} ({opening_t} => {current_t}) — sharp money confirms {direction}"
        signal = max(0.0, min(10.0, round(base_signal + ump_mod + lm_mod, 1)))
        if signal >= MIN_SIGNAL_F5_TOTAL:
            reasons = _build_reasons(direction, home_sp, away_sp, avg_xwoba, park_run, venue)
            if weather_reason:
                reasons = (reasons + [weather_reason])[:4]
            if ump_reason:
                reasons = (reasons + [ump_reason])[:4]
            if lm_reason:
                reasons = (reasons + [lm_reason])[:4]

            picks.append({
                "bet_type":  "F5_TOTAL",
                "subject":   matchup,
                "direction": direction,
                "headline":  f"{matchup} First 5 Innings — {direction}",
                "signal":    signal,
                "reasons":   reasons,
                "raw_scores": {
                    "home_sp_xfip":       home_sp.get("xfip"),
                    "away_sp_xfip":       away_sp.get("xfip"),
                    "home_sp_siera":      home_sp.get("siera"),
                    "away_sp_siera":      away_sp.get("siera"),
                    "avg_lineup_xwoba":   round(avg_xwoba, 3),
                    "park_run_factor":    park_run,
                    "avg_suppression":    round(avg_suppression, 3),
                    "offense_score":      round(offense_s, 3),
                    "lineup_data":        (home_xwoba != 0.320 and bool(home_lineup)) or (away_xwoba != 0.320 and bool(away_lineup)),
                    "weather_modifier":   round(weather_mod, 2) if weather_mod else None,
                    "umpire_modifier":    round(ump_mod, 2) if ump_mod else None,
                    "umpire":             umpire or None,
                    "line_movement_mod":  round(lm_mod, 2) if lm_mod else None,
                    "total_move":         total_move,
                },
            })

    return picks


def _build_reasons(direction, home_sp, away_sp, avg_xwoba, park_run, venue) -> list[str]:
    reasons = []
    home_name = home_sp.get("name", "Home SP")
    away_name = away_sp.get("name", "Away SP")

    if direction == "OVER":
        if avg_xwoba:
            reasons.append(f"Combined lineup xwOBA of {avg_xwoba:.3f} — above-average first-5 run environment")
        if park_run > 102:
            reasons.append(f"{venue} run factor of {park_run} — offense-friendly park")
        xfip_avg = _avg_xfip(home_sp, away_sp)
        if xfip_avg and xfip_avg > 4.20:
            reasons.append(f"Both SPs project to weak xFIP ({xfip_avg:.2f} combined avg) through 5 innings")
    else:
        xfip_avg = _avg_xfip(home_sp, away_sp)
        if xfip_avg and xfip_avg < 3.60:
            reasons.append(f"Elite SP matchup: combined xFIP avg of {xfip_avg:.2f} — first-5 suppression")
        if home_sp.get("siera"):
            reasons.append(f"{home_name} SIERA: {home_sp['siera']:.2f}")
        if away_sp.get("siera"):
            reasons.append(f"{away_name} SIERA: {away_sp['siera']:.2f}")
        if park_run < 97:
            reasons.append(f"{venue} run factor of {park_run} — suppresses early scoring")
    return reasons[:4]


def _avg_xfip(sp1, sp2):
    vals = [v for v in [sp1.get("xfip"), sp2.get("xfip")] if v is not None]
    return sum(vals) / len(vals) if vals else None
