"""Score pitcher walk prop opportunities.

Signal logic:
  UNDER — Elite command pitchers (high zone%, high first-pitch strike%, low BB%)
    are systematically undervalued on walk unders. Books use season BB/9; Statcast
    zone% and F-Strike% are better predictors of per-start walk totals.

  OVER — Wild pitchers facing disciplined, patient lineups. Books underprice the
    over when both dimensions (pitcher wildness + lineup patience) are extreme.

Both dimensions must be strong for a signal to clear the threshold — weak command
alone isn't enough if the opposing lineup swings at everything.
"""

from __future__ import annotations

from pipeline.scorer import normalize, weighted_avg, safe_mean


def score_walk_props(game: dict, cache: dict) -> list[dict]:
    picks = []

    for sp_side, opp_side in [("home", "away"), ("away", "home")]:
        sp_id = game.get(f"{sp_side}_sp_id")
        if not sp_id or sp_id not in cache:
            continue
        sp = cache[sp_id]

        # --- SP command quality ---
        # Invert BB%: lower = better command
        bb_s      = 1.0 - normalize(sp.get("bb_pct"),      lo=0.04, hi=0.14)
        zone_s    = normalize(sp.get("zone_pct"),           lo=0.40, hi=0.52)
        fstrike_s = normalize(sp.get("f_strike_pct"),       lo=0.52, hi=0.70)

        command_comp = weighted_avg([
            (bb_s,      0.40),
            (zone_s,    0.35),
            (fstrike_s, 0.25),
        ])
        # command_comp near 1.0 = elite command → lean UNDER walks
        # command_comp near 0.0 = wild pitcher → lean OVER walks

        # --- Opposing lineup patience ---
        opp_lineup = [cache[b] for b in game.get(f"{opp_side}_lineup", []) if b in cache]
        opp_bb_pcts = [b.get("bb_pct") for b in opp_lineup]
        opp_bb_mean = safe_mean(opp_bb_pcts)
        lineup_patience_s = normalize(opp_bb_mean, lo=0.05, hi=0.14)

        # UNDER: elite command pitcher vs any lineup
        # Geometric mean slightly penalizes even elite command vs extremely patient lineups
        under_raw = (command_comp ** 0.65) * ((1.0 - lineup_patience_s) ** 0.35)
        under_signal = round(under_raw * 10, 1)

        # OVER: wild pitcher facing a patient lineup — both must be extreme
        wild_s = 1.0 - command_comp
        over_raw = (wild_s ** 0.55) * (lineup_patience_s ** 0.45)
        over_signal = round(over_raw * 10, 1)

        sp_name = sp.get("name") or game.get(f"{sp_side}_sp_name", "SP")
        opp_team = game.get(f"{opp_side}Team", "Opponent")

        for direction, signal in [("UNDER", under_signal), ("OVER", over_signal)]:
            if signal >= 7.0:
                picks.append({
                    "bet_type": "WALK_PROP",
                    "subject": sp_name,
                    "direction": direction,
                    "headline": f"{sp_name} Walks — {direction}",
                    "signal": signal,
                    "reasons": _build_reasons(
                        direction, sp, opp_bb_mean, opp_team
                    ),
                    "raw_scores": {
                        "bb_pct": _pct(sp.get("bb_pct")),
                        "zone_pct": _pct(sp.get("zone_pct")),
                        "f_strike_pct": _pct(sp.get("f_strike_pct")),
                        "opp_lineup_bb_pct": _pct(opp_bb_mean),
                        "command_component": round(command_comp, 3),
                        "lineup_patience": round(lineup_patience_s, 3),
                    },
                })

    return picks


def _build_reasons(
    direction: str,
    sp: dict,
    opp_bb_mean,
    opp_team: str,
) -> list[str]:
    reasons = []
    sp_name = sp.get("name", "SP")
    if direction == "UNDER":
        if sp.get("bb_pct"):
            reasons.append(f"{sp_name} BB% of {sp['bb_pct']:.1%} — elite walk avoidance")
        if sp.get("zone_pct"):
            reasons.append(f"Zone rate of {sp['zone_pct']:.1%} — throws in the zone consistently")
        if sp.get("f_strike_pct"):
            reasons.append(f"First-pitch strike rate of {sp['f_strike_pct']:.1%} — gets ahead in counts")
        if opp_bb_mean:
            reasons.append(f"{opp_team} lineup BB% of {opp_bb_mean:.1%} — not a patient group")
    else:
        if sp.get("bb_pct"):
            reasons.append(f"{sp_name} BB% of {sp['bb_pct']:.1%} — elevated walk rate this season")
        if sp.get("zone_pct"):
            reasons.append(f"Zone rate of {sp['zone_pct']:.1%} — misses the zone frequently")
        if opp_bb_mean:
            reasons.append(
                f"{opp_team} lineup BB% of {opp_bb_mean:.1%} — disciplined, patient approach"
            )
        if sp.get("f_strike_pct"):
            reasons.append(
                f"First-pitch strike rate of {sp['f_strike_pct']:.1%} — struggles to get ahead"
            )
    return reasons[:4]


def _pct(v) -> str | None:
    return f"{v:.1%}" if v is not None else None
