"""Score moneyline and first-5-innings (F5) opportunities.

Signal logic: SP quality differential and lineup quality differential combine
to identify games where one side has a meaningful statistical advantage. The
F5 version weights SP more heavily since bullpen variance is removed.

Returns at most one pick per game (the stronger of ML vs. F5, one direction).
"""

from __future__ import annotations

from pipeline.scorer import normalize, weighted_avg, lineup_weighted_mean


def score_moneyline_f5(game: dict, cache: dict) -> list[dict]:
    picks = []

    home_sp = cache.get(game.get("home_sp_id"), {})
    away_sp = cache.get(game.get("away_sp_id"), {})

    home_sp_q = _sp_quality(home_sp)
    away_sp_q = _sp_quality(away_sp)
    sp_diff = home_sp_q - away_sp_q  # positive = home SP advantage

    home_lineup = [cache[b] for b in game.get("home_lineup", []) if b in cache]
    away_lineup = [cache[b] for b in game.get("away_lineup", []) if b in cache]

    home_wrc = lineup_weighted_mean(home_lineup, "wrc_plus") or 100.0
    away_wrc = lineup_weighted_mean(away_lineup, "wrc_plus") or 100.0
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

    # Line movement: +0.3 signal when sharp money confirms our ML direction
    line_movement = game.get("line_movement") or {}
    ml_move = line_movement.get("ml_move")  # positive = sharp money on HOME
    lm_ml_mod = 0.0
    lm_ml_reason = None
    if ml_move is not None and abs(ml_move) >= 0.02:
        agrees_ml = (ml_direction == "HOME" and ml_move > 0) or (ml_direction == "AWAY" and ml_move < 0)
        if agrees_ml:
            lm_ml_mod = 0.3
            steam_team = home_name if ml_move > 0 else away_name
            lm_ml_reason = f"Steam → {steam_team}: ML shortened {abs(ml_move):.1%} since open — sharp money confirms"
    ml_signal = min(10.0, round(ml_signal + lm_ml_mod, 1))

    lm_f5_mod = 0.0
    lm_f5_reason = None
    if ml_move is not None and abs(ml_move) >= 0.02:
        agrees_f5 = (f5_direction == "HOME" and ml_move > 0) or (f5_direction == "AWAY" and ml_move < 0)
        if agrees_f5:
            lm_f5_mod = 0.3
            steam_team = home_name if ml_move > 0 else away_name
            lm_f5_reason = f"Steam → {steam_team}: ML shortened {abs(ml_move):.1%} since open — sharp money confirms F5"
    f5_signal = min(10.0, round(f5_signal + lm_f5_mod, 1))

    if ml_signal >= 5.0:
        ml_reasons = _build_reasons(
            "ML", ml_direction, home_sp, away_sp, home_wrc, away_wrc, home_name, away_name
        )
        if lm_ml_reason:
            ml_reasons = (ml_reasons + [lm_ml_reason])[:4]
        picks.append({
            "bet_type":     "ML_F5",
            "subject":      f"{away_name} @ {home_name}",
            "subject_side": "home" if ml_direction == "HOME" else "away",
            "direction":    ml_direction,
            "headline": f"{fav_ml} Moneyline",
            "signal": ml_signal,
            "reasons": ml_reasons,
            "raw_scores": {
                "home_sp_quality": round(home_sp_q, 3),
                "away_sp_quality": round(away_sp_q, 3),
                "sp_diff": round(sp_diff, 3),
                "home_wrc_plus": round(home_wrc, 1),
                "away_wrc_plus": round(away_wrc, 1),
                "lineup_diff_normalized": round(lineup_diff, 3),
                "ml_edge": round(ml_edge, 3),
                "line_movement_mod": round(lm_ml_mod, 2) if lm_ml_mod else None,
                "ml_move": ml_move,
            },
        })

    if f5_signal >= 5.0:
        f5_reasons = _build_reasons(
            "F5", f5_direction, home_sp, away_sp, home_wrc, away_wrc, home_name, away_name
        )
        if lm_f5_reason:
            f5_reasons = (f5_reasons + [lm_f5_reason])[:4]
        picks.append({
            "bet_type":     "ML_F5",
            "subject":      f"{away_name} @ {home_name}",
            "subject_side": "home" if f5_direction == "HOME" else "away",
            "direction":    f5_direction,
            "headline": f"{fav_f5} First 5 Innings",
            "signal": f5_signal,
            "reasons": f5_reasons,
            "raw_scores": {
                "home_sp_quality": round(home_sp_q, 3),
                "away_sp_quality": round(away_sp_q, 3),
                "sp_diff": round(sp_diff, 3),
                "home_wrc_plus": round(home_wrc, 1),
                "away_wrc_plus": round(away_wrc, 1),
                "f5_edge": round(f5_edge, 3),
                "line_movement_mod": round(lm_f5_mod, 2) if lm_f5_mod else None,
                "ml_move": ml_move,
            },
        })

    return picks


def _sp_quality(sp: dict) -> float:
    """Return SP quality in [-1, 1] centered at league-average 0."""
    xfip_s = 1.0 - normalize(sp.get("xfip"), lo=2.50, hi=5.50)
    siera_s = 1.0 - normalize(sp.get("siera"), lo=2.50, hi=5.50)
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
