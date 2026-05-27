"""Game prediction engine: pitcher/lineup scores → win probability + predicted runs."""

from __future__ import annotations

import logging
import math
from typing import Optional

from pipeline.comps import build_game_profile, find_similar_games
from pipeline.park_factors import get_run_factor
from pipeline.scorer import normalize, weighted_avg, lineup_weighted_mean, bullpen_score

log = logging.getLogger(__name__)

LEAGUE_AVG_RUNS  = 4.4   # recalibrated: backtest shows 8.91 actual vs 8.32 pred at 4.1 (bias -0.59)
HOME_ADVANTAGE   = 0.525  # baseline home win probability (calibrated: actual 52.7% from 805-game sample)
_PITCHER_WEIGHT  = 0.80  # run-suppression weight (reduced from 0.85 to address -0.275 run bias)
_LINEUP_WEIGHT   = 0.65  # run-production weight (increased from 0.55 to address -0.275 run bias)
_RAW_EDGE_MULT   = 2.0   # logit-space multiplier; 2.0 widens output to ~0.35-0.70 vs old 0.43-0.61
_COMPS_WIN_BLEND = 0.0   # comps are anti-correlated with outcomes in backtest (−10.5 Brier pts); disabled

# Pitcher strength weights: (weight, invert, (lo, hi))
# invert=True means lower value = better pitcher
_P_WEIGHTS: dict[str, tuple[float, bool, tuple[float, float]]] = {
    "xera":               (0.20, True,  (1.5,   6.0)),
    "barrel_pct_against": (0.20, True,  (0.03,  0.15)),  # quality of contact allowed; already fetched, previously unused
    "stuff_plus":         (0.15, False, (80,    120)),    # pitch quality composite; midpoint=100 = league avg
    "whiff_pct":          (0.12, False, (0.10,  0.40)),
    "o_swing_pct":        (0.12, False, (0.20,  0.40)),   # chase%
    "k_pct":              (0.10, False, (0.10,  0.40)),
    "bb_pct":             (0.06, True,  (0.04,  0.15)),
    "xba_against":        (0.05, True,  (0.150, 0.310)),  # barrel_pct_against captures this signal better
}

# Lineup strength weights
_L_WEIGHTS: dict[str, tuple[float, bool, tuple[float, float]]] = {
    "xwoba":        (0.28, False, (0.260, 0.380)),
    "xslg":         (0.20, False, (0.280, 0.580)),  # power/extra-base production; already fetched, previously unused
    "barrel_pct":   (0.15, False, (0.03,  0.20)),   # elite contact quality; already fetched, previously unused
    "hard_hit_pct": (0.15, False, (0.25,  0.55)),
    "avg_ev":       (0.10, False, (84.0,  94.0)),   # reduced — largely captured by barrel_pct and hard_hit_pct
    "k_pct":        (0.07, True,  (0.10,  0.35)),   # inverted: lower K% is better
    "bb_pct":       (0.05, False, (0.04,  0.15)),
}


# ---------------------------------------------------------------------------
# Strength scores
# ---------------------------------------------------------------------------

def _sp_quality_score(sp: dict) -> float:
    """Raw starter-only composite strength on [0, 1]."""
    pairs: list[tuple[float, float]] = []
    for key, (w, invert, (lo, hi)) in _P_WEIGHTS.items():
        season_val = sp.get(key)

        # 60/40 season/recent blend where recent data exists
        recent_key_map = {
            "whiff_pct":   "whiff_pct",
            "o_swing_pct": "o_swing_pct",
            "k_pct":       "recent_k_pct",
            "bb_pct":      "recent_bb_pct",
        }
        recent_val = sp.get(recent_key_map.get(key)) if key in recent_key_map else None

        if recent_val is not None and season_val is not None:
            val = season_val * 0.6 + recent_val * 0.4
        else:
            val = season_val

        normed = normalize(val, lo, hi) if val is not None else 0.5
        if invert:
            normed = 1.0 - normed
        pairs.append((normed, w))

    return weighted_avg(pairs)


def _pitcher_score(sp: dict, bullpen: dict | None = None) -> float:
    """Composite pitcher strength on [0, 1]; blends SP (60%) + bullpen (40%) when available."""
    sp_score = _sp_quality_score(sp)
    if bullpen:
        bp = bullpen_score(bullpen)
        return round(sp_score * 0.60 + bp * 0.40, 4)
    return sp_score


def _lineup_score(players: list[dict]) -> float:
    """Composite lineup strength on [0, 1]; 1 = elite offense."""
    if not players:
        return 0.5

    pairs: list[tuple[float, float]] = []
    for key, (w, invert, (lo, hi)) in _L_WEIGHTS.items():
        vals = [p[key] for p in players if p.get(key) is not None]
        avg = sum(vals) / len(vals) if vals else None
        normed = normalize(avg, lo, hi) if avg is not None else 0.5
        if invert:
            normed = 1.0 - normed
        pairs.append((normed, w))

    return weighted_avg(pairs)


# ---------------------------------------------------------------------------
# Prediction math
# ---------------------------------------------------------------------------

def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, x))))


def _logit(p: float) -> float:
    p = max(0.001, min(0.999, p))
    return math.log(p / (1.0 - p))


def _win_probability(
    home_pitcher_score: float,
    away_pitcher_score: float,
    home_lineup_score: float,
    away_lineup_score: float,
    comps_home_win_rate: Optional[float],
    park_modifier: float,
    weather_modifier: float,
) -> tuple[float, float]:
    """Return (home_win_pct, away_win_pct) blended from stat model and historical comps."""
    raw_edge = (
        (home_lineup_score - away_pitcher_score)
        - (away_lineup_score - home_pitcher_score)
    )
    logit_stats = _logit(HOME_ADVANTAGE) + raw_edge * _RAW_EDGE_MULT

    if comps_home_win_rate is not None and _COMPS_WIN_BLEND > 0:
        logit_comps = _logit(comps_home_win_rate)
        logit_blend = logit_stats * (1 - _COMPS_WIN_BLEND) + logit_comps * _COMPS_WIN_BLEND
    else:
        logit_blend = logit_stats

    logit_blend += park_modifier * 0.5 + weather_modifier * 0.2

    home_pct = round(_sigmoid(logit_blend), 4)
    return home_pct, round(1.0 - home_pct, 4)


def _predicted_runs(
    home_lineup_score: float,
    away_lineup_score: float,
    home_pitcher_score: float,
    away_pitcher_score: float,
    park_run_factor: float,
    weather_modifier: float,
) -> tuple[float, float]:
    """Return (predicted_home_runs, predicted_away_runs)."""
    park_mult    = park_run_factor / 100.0
    weather_mult = 1.0 + weather_modifier * 0.05

    home_off_edge   = home_lineup_score  - 0.5
    away_pitch_edge = away_pitcher_score - 0.5
    away_off_edge   = away_lineup_score  - 0.5
    home_pitch_edge = home_pitcher_score - 0.5

    home_runs = LEAGUE_AVG_RUNS * (1.0 + home_off_edge * _LINEUP_WEIGHT - away_pitch_edge * _PITCHER_WEIGHT) * park_mult * weather_mult
    away_runs = LEAGUE_AVG_RUNS * (1.0 + away_off_edge * _LINEUP_WEIGHT - home_pitch_edge * _PITCHER_WEIGHT) * park_mult * weather_mult

    home_runs = round(max(1.0, min(12.0, home_runs)), 1)
    away_runs = round(max(1.0, min(12.0, away_runs)), 1)
    return home_runs, away_runs


# ---------------------------------------------------------------------------
# Rest & fatigue
# ---------------------------------------------------------------------------

def _rest_modifier(rest_days: int | None) -> float:
    """Return a logit adjustment for rest/fatigue.

    0 days (B2B): -0.04 penalty
    1 day (normal): no adjustment
    2+ days: +0.02 bonus
    """
    if rest_days is None:
        return 0.0
    if rest_days == 0:
        return -0.04
    if rest_days >= 2:
        return 0.02
    return 0.0


# ---------------------------------------------------------------------------
# Trend flags
# ---------------------------------------------------------------------------

def _trend_flags_pitcher(sp: dict) -> list[str]:
    flags: list[str] = []

    era  = sp.get("era")
    xera = sp.get("xera") or sp.get("xfip")
    if era is not None and xera is not None:
        diff = era - xera
        if diff >= 0.75:
            flags.append(f"Regression candidate: ERA ({era:.2f}) >> xERA ({xera:.2f})")
        elif diff <= -0.75:
            flags.append(f"Outperforming xERA: ERA ({era:.2f}) << xERA ({xera:.2f})")

    season_k = sp.get("k_pct")
    recent_k = sp.get("recent_k_pct")
    if season_k is not None and recent_k is not None:
        delta_pp = (recent_k - season_k) * 100
        if delta_pp >= 2.5:
            flags.append(f"K% trending up (+{delta_pp:.1f}pp last 3 starts)")
        elif delta_pp <= -2.5:
            flags.append(f"K% trending down ({delta_pp:.1f}pp last 3 starts)")

    return flags


def _trend_flags_batter(b: dict) -> list[str]:
    flags: list[str] = []

    recent_h = b.get("recent_h_games", [])
    if len(recent_h) >= 3:
        total_h = sum(recent_h)
        n = len(recent_h)
        if total_h >= n * 1.2:
            flags.append(f"Hot: {total_h}H in last {n} games")
        elif total_h == 0:
            flags.append(f"Cold: 0H in last {n} games")

    return flags


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_weather_display(weather: Optional[dict]) -> dict:
    if not weather:
        return {}
    if weather.get("dome"):
        return {"condition": "Dome"}

    wind_mph  = weather.get("wind_speed_mph")
    blowing   = weather.get("blowing_out")
    temp_f    = weather.get("temp_f")

    wind_dir = None
    if wind_mph is not None and blowing is not None:
        wind_dir = f"{wind_mph:.0f} mph blowing {'Out' if blowing else 'In'}"
    elif wind_mph is not None:
        wind_dir = f"{wind_mph:.0f} mph"

    return {
        "temp_f":     round(temp_f) if temp_f is not None else None,
        "wind_mph":   round(wind_mph) if wind_mph is not None else None,
        "wind_dir":   wind_dir,
        "blowing_out": blowing,
    }


def _weather_score_modifier(weather: Optional[dict]) -> float:
    """Small offensive modifier: positive = more scoring, negative = less."""
    if not weather or weather.get("dome"):
        return 0.0

    mod = 0.0
    wind_speed  = weather.get("wind_speed_mph")
    blowing_out = weather.get("blowing_out")
    temp_f      = weather.get("temp_f")

    if wind_speed and wind_speed > 10:
        factor = min(0.4, wind_speed * 0.015)
        mod += factor if blowing_out is True else -factor if blowing_out is False else 0.0

    if temp_f:
        if temp_f < 45:
            mod -= 0.3
        elif temp_f < 55:
            mod -= 0.15
        elif temp_f > 88:
            mod += 0.2
        elif temp_f > 80:
            mod += 0.1

    return max(-0.5, min(0.5, mod))


def _format_sp_stats(sp: dict, name_fallback: str) -> dict:
    def r(v, d=3):
        return round(v, d) if v is not None else None

    xera = sp.get("xera") or sp.get("xfip")

    season = {
        "xera":      r(xera),
        "xba":       r(sp.get("xba_against")),
        "whiff_pct": r(sp.get("whiff_pct")),
        "chase_pct": r(sp.get("o_swing_pct")),
        "k_pct":     r(sp.get("k_pct")),
        "bb_pct":    r(sp.get("bb_pct")),
        "rv100":     r(sp.get("rv100")),
        "era":       r(sp.get("era")),
        "ip":        r(sp.get("ip"), 1),
    }

    recent: dict = {}
    recent_k  = sp.get("recent_k_pct")
    recent_bb = sp.get("recent_bb_pct")
    starts_n  = sp.get("recent_starts_n")
    if any(v is not None for v in [recent_k, recent_bb, starts_n]):
        recent = {
            "k_pct":    r(recent_k),
            "bb_pct":   r(recent_bb),
            "whiff_pct": r(sp.get("whiff_pct")),
            "chase_pct": r(sp.get("o_swing_pct")),
            "starts_n":  starts_n,
        }

    return {
        "name":        sp.get("name", name_fallback),
        "mlbam_id":    sp.get("mlbam_id"),
        "season":      season,
        "recent":      recent,
        "trend_flags": _trend_flags_pitcher(sp),
    }


def _format_batter(b: dict, order: int) -> dict:
    def r(v, d=3):
        return round(v, d) if v is not None else None

    return {
        "name":          b.get("name", "Unknown"),
        "mlbam_id":      b.get("mlbam_id"),
        "batting_order": order,
        "xwoba":         r(b.get("xwoba")),
        "woba":          r(b.get("woba")),
        "avg_ev":        r(b.get("avg_ev"), 1),
        "hard_hit_pct":  r(b.get("hard_hit_pct")),
        "k_pct":         r(b.get("k_pct")),
        "bb_pct":        r(b.get("bb_pct")),
        "trend_flags":   _trend_flags_batter(b),
    }


def _generate_narrative(
    home_team: str,
    away_team: str,
    home_sp_name: str,
    away_sp_name: str,
    home_sp: dict,
    away_sp: dict,
    home_pitcher_score: float,
    away_pitcher_score: float,
    home_xwoba: Optional[float],
    away_xwoba: Optional[float],
    park_run_factor: float,
    weather: Optional[dict],
) -> str:
    parts: list[str] = []

    pitcher_diff = home_pitcher_score - away_pitcher_score
    home_xera    = home_sp.get("xera") or home_sp.get("xfip")
    away_xera    = away_sp.get("xera") or away_sp.get("xfip")

    if abs(pitcher_diff) >= 0.08:
        better_name = home_sp_name if pitcher_diff > 0 else away_sp_name
        better_xera = home_xera    if pitcher_diff > 0 else away_xera
        better_side = "home"       if pitcher_diff > 0 else "away"
        if better_xera is not None:
            parts.append(
                f"{better_name}'s {better_xera:.2f} xERA gives the {better_side} side "
                f"a significant pitching edge."
            )
        else:
            parts.append(f"{better_name} has a meaningful pitching edge.")
    elif home_xera is not None and away_xera is not None:
        parts.append(
            f"Even pitching matchup: {home_sp_name} ({home_xera:.2f} xERA) vs "
            f"{away_sp_name} ({away_xera:.2f} xERA)."
        )
    else:
        parts.append(f"Pitching matchup: {home_sp_name} vs {away_sp_name}.")

    if home_xwoba is not None and away_xwoba is not None:
        diff = home_xwoba - away_xwoba
        if abs(diff) >= 0.010:
            better_team  = home_team if diff > 0 else away_team
            better_woba  = home_xwoba if diff > 0 else away_xwoba
            worse_woba   = away_xwoba if diff > 0 else home_xwoba
            parts.append(
                f"{better_team} lineup xwOBA (.{round(better_woba * 1000):03d}) "
                f"outpaces the opponent (.{round(worse_woba * 1000):03d})."
            )
        else:
            parts.append("Lineups are evenly matched by xwOBA.")

    context: list[str] = []
    if park_run_factor >= 106:
        context.append(f"hitter-friendly park (factor {round(park_run_factor)})")
    elif park_run_factor <= 94:
        context.append(f"pitcher-friendly park (factor {round(park_run_factor)})")

    if weather and not weather.get("dome"):
        wind = weather.get("wind_speed_mph")
        blowing = weather.get("blowing_out")
        temp = weather.get("temp_f")
        if wind and wind >= 15:
            context.append(f"wind {wind:.0f} mph blowing {'out' if blowing else 'in'}")
        if temp and temp < 50:
            context.append(f"cold ({temp:.0f}°F)")

    if context:
        parts.append(f"Context: {', '.join(context)}.")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_game(
    game:     dict,
    cache:    dict,
    comps_db: list[dict],
    weather:  Optional[dict],
    odds:     Optional[dict],
) -> dict:
    """Return the full game object for games.json."""
    gamePk       = game["gamePk"]
    home_team    = game["homeTeam"]
    away_team    = game["awayTeam"]
    venue        = game.get("venue", "Unknown")
    home_sp_id   = game.get("home_sp_id")
    away_sp_id   = game.get("away_sp_id")
    home_sp_name = game.get("home_sp_name", "TBD")
    away_sp_name = game.get("away_sp_name", "TBD")
    game_time_utc = game.get("gameTime", "")

    # ET time formatting (JS handles it from UTC if this fails)
    game_time_et = _format_time_et(game_time_utc)

    try:
        park_run_factor = float(get_run_factor(venue))
    except Exception:
        park_run_factor = 100.0

    home_sp = cache.get(home_sp_id, {}) if home_sp_id else {}
    away_sp = cache.get(away_sp_id, {}) if away_sp_id else {}

    home_sp_out = _format_sp_stats(home_sp, home_sp_name)
    away_sp_out = _format_sp_stats(away_sp, away_sp_name)

    home_lineup_ids     = game.get("home_lineup", [])
    away_lineup_ids     = game.get("away_lineup", [])
    lineup_status       = "official" if (home_lineup_ids or away_lineup_ids) else "tbd"
    home_lineup_players = [cache[b] for b in home_lineup_ids if b in cache]
    away_lineup_players = [cache[b] for b in away_lineup_ids if b in cache]

    home_lineup_out = [_format_batter(home_lineup_players[i], i + 1) for i in range(len(home_lineup_players))]
    away_lineup_out = [_format_batter(away_lineup_players[i], i + 1) for i in range(len(away_lineup_players))]

    home_bullpen = cache.get(f"bullpen:{home_team}")
    away_bullpen = cache.get(f"bullpen:{away_team}")
    home_pitcher_score = _pitcher_score(home_sp, home_bullpen)
    away_pitcher_score = _pitcher_score(away_sp, away_bullpen)
    home_lineup_score  = _lineup_score(home_lineup_players)
    away_lineup_score  = _lineup_score(away_lineup_players)

    home_xwoba = lineup_weighted_mean(home_lineup_players, "xwoba")
    away_xwoba = lineup_weighted_mean(away_lineup_players, "xwoba")

    comps_home_win_rate: Optional[float] = None
    comps_count = 0
    if comps_db:
        profile = build_game_profile(game, cache)
        if profile:
            similar = find_similar_games(profile, comps_db, n=30)
            comps_count = len(similar)
            if similar:
                comps_home_win_rate = round(sum(1 for g in similar if g["home_won"]) / len(similar), 4)

    weather_mod = _weather_score_modifier(weather)
    park_mod    = (park_run_factor - 100) / 1000

    # Rest/fatigue modifier: net logit advantage for home team
    home_rest = game.get("home_rest_days")
    away_rest = game.get("away_rest_days")
    rest_mod  = _rest_modifier(home_rest) - _rest_modifier(away_rest)

    # B2B bullpen fatigue: reduce bullpen score by 5% for back-to-back teams
    if home_rest == 0 and home_bullpen:
        home_bullpen = {**home_bullpen}
        for k in ("xera", "k_pct", "bb_pct", "whiff_pct"):
            if home_bullpen.get(k) is not None:
                if k in ("xera",):
                    home_bullpen[k] = home_bullpen[k] * 1.05
                else:
                    home_bullpen[k] = home_bullpen[k] * 0.95
        home_pitcher_score = _pitcher_score(home_sp, home_bullpen)
    if away_rest == 0 and away_bullpen:
        away_bullpen = {**away_bullpen}
        for k in ("xera", "k_pct", "bb_pct", "whiff_pct"):
            if away_bullpen.get(k) is not None:
                if k in ("xera",):
                    away_bullpen[k] = away_bullpen[k] * 1.05
                else:
                    away_bullpen[k] = away_bullpen[k] * 0.95
        away_pitcher_score = _pitcher_score(away_sp, away_bullpen)

    home_win_pct, away_win_pct = _win_probability(
        home_pitcher_score, away_pitcher_score,
        home_lineup_score, away_lineup_score,
        comps_home_win_rate, park_mod + rest_mod, weather_mod,
    )
    pred_home, pred_away = _predicted_runs(
        home_lineup_score, away_lineup_score,
        home_pitcher_score, away_pitcher_score,
        park_run_factor, weather_mod,
    )

    odds_out = _extract_odds(odds, home_team, away_team)

    # Vegas anchor: blend 70% model / 30% Pinnacle, capped at ±1.5 runs from Vegas line.
    # Applied after the model formula so it acts as a guard, not the primary signal.
    if odds_out and odds_out.get("total"):
        vegas_total = odds_out["total"]
        model_total_raw = pred_home + pred_away
        blended = 0.70 * model_total_raw + 0.30 * vegas_total
        blended = max(vegas_total - 1.5, min(vegas_total + 1.5, blended))
        ratio = blended / model_total_raw if model_total_raw > 0 else 1.0
        pred_home = round(pred_home * ratio, 1)
        pred_away = round(pred_away * ratio, 1)

    narrative = _generate_narrative(
        home_team, away_team,
        home_sp_name, away_sp_name,
        home_sp, away_sp,
        home_pitcher_score, away_pitcher_score,
        home_xwoba, away_xwoba,
        park_run_factor, weather,
    )

    return {
        "gamePk":          gamePk,
        "game_time_utc":   game_time_utc,
        "game_time_et":    game_time_et,
        "home_team":       home_team,
        "away_team":       away_team,
        "home_sp_id":      home_sp_id,
        "away_sp_id":      away_sp_id,
        "venue":           venue,
        "park_run_factor": park_run_factor,
        "weather":         _format_weather_display(weather),
        "odds":            odds_out,
        "game_status":     game.get("game_status", "preview"),
        "home_score":      game.get("home_score"),
        "away_score":      game.get("away_score"),
        "current_inning":  game.get("current_inning"),
        "inning_state":    game.get("inning_state"),
        "outs":            game.get("outs"),
        "home_sp":         home_sp_out,
        "away_sp":         away_sp_out,
        "home_lineup":     home_lineup_out,
        "away_lineup":     away_lineup_out,
        "lineup_status":   lineup_status,
        "prediction": {
            "home_win_pct":       home_win_pct,
            "away_win_pct":       away_win_pct,
            "predicted_home_runs": pred_home,
            "predicted_away_runs": pred_away,
            "predicted_total":    round(pred_home + pred_away, 1),
            "narrative":          narrative,
            "model_signals": {
                "pitcher_score_home": round(home_pitcher_score, 3),
                "pitcher_score_away": round(away_pitcher_score, 3),
                "lineup_score_home":  round(home_lineup_score,  3),
                "lineup_score_away":  round(away_lineup_score,  3),
                "comps_home_win_rate": comps_home_win_rate,
                "comps_count":        comps_count,
                "park_modifier":      round(park_mod, 4),
                "weather_modifier":   round(weather_mod, 3),
                "rest_modifier":      round(rest_mod, 4),
                "home_rest_days":     home_rest,
                "away_rest_days":     away_rest,
                "bullpen_xera_home":  home_bullpen.get("xera") if home_bullpen else None,
                "bullpen_xera_away":  away_bullpen.get("xera") if away_bullpen else None,
            },
        },
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _format_time_et(utc_str: str) -> str:
    if not utc_str:
        return ""
    try:
        from datetime import datetime, timezone
        import zoneinfo
        dt_utc = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        eastern = zoneinfo.ZoneInfo("America/New_York")
        dt_et   = dt_utc.astimezone(eastern)
        hour    = dt_et.strftime("%-I")
        minute  = dt_et.strftime("%M")
        ampm    = dt_et.strftime("%p")
        return f"{hour}:{minute} {ampm} ET"
    except Exception:
        return ""


def _extract_odds(odds: Optional[dict], home_team: str, away_team: str) -> Optional[dict]:
    if not odds:
        return None
    from pipeline.odds import _norm_team
    markets   = odds.get("markets", {})
    totals    = markets.get("totals", [])
    h2h       = markets.get("h2h", [])
    norm_home = _norm_team(home_team)
    norm_away = _norm_team(away_team)
    over_out  = next((o for o in totals if o.get("name") == "Over"),  None)
    under_out = next((o for o in totals if o.get("name") == "Under"), None)
    home_out  = next((o for o in h2h if _norm_team(o.get("name", "")) == norm_home), None)
    away_out  = next((o for o in h2h if _norm_team(o.get("name", "")) == norm_away), None)
    return {
        "home_ml":    home_out["price"]     if home_out  else None,
        "away_ml":    away_out["price"]     if away_out  else None,
        "total":      over_out.get("point") if over_out  else None,
        "over_price": over_out["price"]     if over_out  else None,
        "under_price": under_out["price"]   if under_out else None,
    }
