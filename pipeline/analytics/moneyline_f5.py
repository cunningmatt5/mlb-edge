"""Score moneyline and first-5-innings (F5) opportunities.

Signal logic: SP quality differential and lineup quality differential combine
to identify games where one side has a meaningful statistical advantage. The
F5 version weights SP more heavily since bullpen variance is removed.

Returns at most one pick per game (the stronger of ML vs. F5, one direction).
"""

from __future__ import annotations

from pipeline.scorer import normalize, weighted_avg, safe_mean


def score_moneyline_f5(game: dict, cache: dict) -> list[dict]:
    picks = []

    home_sp = cache.get(game.get("home_sp_id"), {})
    away_sp = cache.get(game.get("away_sp_id"), {})

    home_sp_q = _sp_quality(home_sp)
    away_sp_q = _sp_quality(away_sp)
    sp_diff = home_sp_q - away_sp_q  # positive = home SP advantage

    home_lineup = [cache[b] for b in game.get("home_lineup", []) if b in cache]
    away_lineup = [cache[b] for b in game.get("away_lineup", []) if b in cache]

    home_wrc = safe_mean([b.get("wrc_plus") for b in home_lineup]) or 100.0
    away_wrc = safe_mean([b.get("wrc_plus") for b in away_lineup]) or 100.0
    lineup_diff = (home_wrc - away_wrc) / 30.0  # normalize to ~[-1, 1]

    # Full-game moneyline
    ml_edge = (sp_diff * 0.55) + (lineup_diff * 0.45)
    ml_signal = round(min(abs(ml_edge) * 10, 10.0), 1)
    ml_direction = "HOME" if ml_edge >= 0 else "AWAY"

    # First 5 innings (SP-weighted more heavily)
    f5_edge = (sp_diff * 0.70) + (lineup_diff * 0.30)
    f5_signal = round(min(abs(f5_edge) * 10, 10.0), 1)
    f5_direction = "HOME" if f5_edge >= 0 else "AWAY"

    home_name = game.get("homeTeam", "Home")
    away_name = game.get("awayTeam", "Away")
    fav_ml = home_name if ml_direction == "HOME" else away_name
    fav_f5 = home_name if f5_direction == "HOME" else away_name

    if ml_signal >= 7.0:
        picks.append({
            "bet_type":     "ML_F5",
            "subject":      f"{away_name} @ {home_name}",
            "subject_side": "home" if ml_direction == "HOME" else "away",
            "direction":    ml_direction,
            "headline": f"{fav_ml} Moneyline",
            "signal": ml_signal,
            "reasons": _build_reasons(
                "ML", ml_direction, home_sp, away_sp, home_wrc, away_wrc, home_name, away_name
            ),
            "raw_scores": {
                "home_sp_quality": round(home_sp_q, 3),
                "away_sp_quality": round(away_sp_q, 3),
                "sp_diff": round(sp_diff, 3),
                "home_wrc_plus": round(home_wrc, 1),
                "away_wrc_plus": round(away_wrc, 1),
                "lineup_diff_normalized": round(lineup_diff, 3),
                "ml_edge": round(ml_edge, 3),
            },
        })

    if f5_signal >= 7.0:
        picks.append({
            "bet_type":     "ML_F5",
            "subject":      f"{away_name} @ {home_name}",
            "subject_side": "home" if f5_direction == "HOME" else "away",
            "direction":    f5_direction,
            "headline": f"{fav_f5} First 5 Innings",
            "signal": f5_signal,
            "reasons": _build_reasons(
                "F5", f5_direction, home_sp, away_sp, home_wrc, away_wrc, home_name, away_name
            ),
            "raw_scores": {
                "home_sp_quality": round(home_sp_q, 3),
                "away_sp_quality": round(away_sp_q, 3),
                "sp_diff": round(sp_diff, 3),
                "home_wrc_plus": round(home_wrc, 1),
                "away_wrc_plus": round(away_wrc, 1),
                "f5_edge": round(f5_edge, 3),
            },
        })

    return picks


def _sp_quality(sp: dict) -> float:
    """Return SP quality in [-1, 1] centered at league-average 0."""
    xfip_s = 1.0 - normalize(sp.get("xfip"), lo=2.80, hi=5.50)
    siera_s = 1.0 - normalize(sp.get("siera"), lo=2.80, hi=5.50)
    kbb_s = normalize(sp.get("k_minus_bb_pct"), lo=0.00, hi=0.25)
    stuff_s = normalize(sp.get("stuff_plus"), lo=80, hi=130)

    raw = weighted_avg([
        (xfip_s,  0.30),
        (siera_s, 0.30),
        (kbb_s,   0.20),
        (stuff_s, 0.20),
    ])
    return (raw - 0.5) * 2  # center at 0: [-1, 1]


def _build_reasons(
    bet_type: str,
    direction: str,
    home_sp: dict,
    away_sp: dict,
    home_wrc: float,
    away_wrc: float,
    home_name: str,
    away_name: str,
) -> list[str]:
    reasons = []
    fav_sp = home_sp if direction == "HOME" else away_sp
    dog_sp = away_sp if direction == "HOME" else home_sp
    fav_name = home_name if direction == "HOME" else away_name
    dog_name = away_name if direction == "HOME" else home_name

    fav_sp_name = fav_sp.get("name", f"{fav_name} SP")
    dog_sp_name = dog_sp.get("name", f"{dog_name} SP")

    if fav_sp.get("xfip") and dog_sp.get("xfip"):
        reasons.append(
            f"{fav_sp_name} xFIP {fav_sp['xfip']:.2f} vs. {dog_sp_name} xFIP {dog_sp['xfip']:.2f}"
        )
    elif fav_sp.get("siera"):
        reasons.append(f"{fav_sp_name} SIERA of {fav_sp['siera']:.2f}")

    if fav_sp.get("k_minus_bb_pct"):
        reasons.append(f"{fav_sp_name} K-BB% of {fav_sp['k_minus_bb_pct']:.1%} — elite command/stuff combo")

    if home_wrc and away_wrc:
        fav_wrc = home_wrc if direction == "HOME" else away_wrc
        dog_wrc = away_wrc if direction == "HOME" else home_wrc
        if abs(fav_wrc - dog_wrc) > 5:
            reasons.append(
                f"{fav_name} lineup wRC+ {fav_wrc:.0f} vs. {dog_name} wRC+ {dog_wrc:.0f}"
            )

    if bet_type == "F5":
        reasons.append("F5 bet isolates SP matchup — removes bullpen variance")

    return reasons[:4]
