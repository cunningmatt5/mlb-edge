"""Score pitcher walk prop opportunities.

Signal logic:
  UNDER — Elite command pitchers (high zone%, high first-pitch strike%, low BB%)
    are systematically undervalued on walk unders. Books use season BB/9; Statcast
    zone% and F-Strike% are better predictors of per-start walk totals.

  OVER — Wild pitchers facing disciplined, patient lineups. Books underprice the
    over when both dimensions (pitcher wildness + lineup patience) are extreme.

Enhancements:
- BB% blends season (60%) and last-3-starts (40%) for recency weighting
- HP umpire zone tendency applies a small signal modifier
"""

from __future__ import annotations

from pipeline.scorer import normalize, weighted_avg, safe_mean
from pipeline.umpire import compute_umpire_modifier


def score_walk_props(game: dict, cache: dict) -> list[dict]:
    picks = []
    umpire = game.get("umpire", "")

    for sp_side, opp_side in [("home", "away"), ("away", "home")]:
        sp_id = game.get(f"{sp_side}_sp_id")
        if not sp_id or sp_id not in cache:
            continue
        sp = cache[sp_id]

        # Blend season BB% with last-3-starts BB% (60/40)
        season_bb = sp.get("bb_pct")
        recent_bb = sp.get("recent_bb_pct")
        if season_bb is not None and recent_bb is not None:
            blended_bb = 0.60 * season_bb + 0.40 * recent_bb
        else:
            blended_bb = season_bb if season_bb is not None else recent_bb

        # --- SP command quality ---
        bb_s      = 1.0 - normalize(blended_bb,                lo=0.04, hi=0.14)
        zone_s    = normalize(sp.get("zone_pct"),              lo=0.40, hi=0.52)
        fstrike_s = normalize(sp.get("f_strike_pct"),          lo=0.52, hi=0.70)

        command_comp = weighted_avg([
            (bb_s,      0.40),
            (zone_s,    0.35),
            (fstrike_s, 0.25),
        ])

        # --- Opposing lineup patience ---
        opp_lineup = [cache[b] for b in game.get(f"{opp_side}_lineup", []) if b in cache]
        opp_bb_pcts = [b.get("bb_pct") for b in opp_lineup]
        opp_bb_mean = safe_mean(opp_bb_pcts)
        lineup_patience_s = normalize(opp_bb_mean, lo=0.05, hi=0.14)

        under_raw  = (command_comp ** 0.65) * ((1.0 - lineup_patience_s) ** 0.35)
        under_signal_raw = under_raw * 10

        wild_s    = 1.0 - command_comp
        over_raw  = (wild_s ** 0.55) * (lineup_patience_s ** 0.45)
        over_signal_raw = over_raw * 10

        sp_name  = sp.get("name") or game.get(f"{sp_side}_sp_name", "SP")
        opp_team = game.get(f"{opp_side}Team", "Opponent")

        for direction, raw_sig in [("UNDER", under_signal_raw), ("OVER", over_signal_raw)]:
            ump_mod, ump_reason = compute_umpire_modifier(umpire, "WALK_PROP", direction)
            signal = max(0.0, min(10.0, round(raw_sig + ump_mod, 1)))

            if signal >= 7.0:
                reasons = _build_reasons(direction, sp, blended_bb, opp_bb_mean, opp_team, recent_bb)
                if ump_reason:
                    reasons = (reasons + [ump_reason])[:4]

                picks.append({
                    "bet_type":   "WALK_PROP",
                    "subject":    sp_name,
                    "subject_id": sp_id,
                    "direction":  direction,
                    "headline":   f"{sp_name} Walks — {direction}",
                    "signal":     signal,
                    "reasons":    reasons,
                    "raw_scores": {
                        "bb_pct":             _pct(blended_bb),
                        "zone_pct":           _pct(sp.get("zone_pct")),
                        "f_strike_pct":       _pct(sp.get("f_strike_pct")),
                        "opp_lineup_bb_pct":  _pct(opp_bb_mean),
                        "command_component":  round(command_comp, 3),
                        "lineup_patience":    round(lineup_patience_s, 3),
                        "umpire":             umpire or None,
                    },
                })

    return picks


def _build_reasons(direction, sp, blended_bb, opp_bb_mean, opp_team, recent_bb) -> list[str]:
    reasons = []
    sp_name = sp.get("name", "SP")
    if direction == "UNDER":
        if blended_bb:
            suffix = f" (blended w/ recent {sp.get('recent_starts_n', 3)} starts)" if recent_bb else ""
            reasons.append(f"{sp_name} BB% of {blended_bb:.1%}{suffix} — elite walk avoidance")
        if sp.get("zone_pct"):
            reasons.append(f"Zone rate of {sp['zone_pct']:.1%} — throws in the zone consistently")
        if sp.get("f_strike_pct"):
            reasons.append(f"First-pitch strike rate of {sp['f_strike_pct']:.1%} — gets ahead in counts")
        if opp_bb_mean:
            reasons.append(f"{opp_team} lineup BB% of {opp_bb_mean:.1%} — not a patient group")
    else:
        if blended_bb:
            reasons.append(f"{sp_name} BB% of {blended_bb:.1%} — elevated walk rate this season")
        if sp.get("zone_pct"):
            reasons.append(f"Zone rate of {sp['zone_pct']:.1%} — misses the zone frequently")
        if opp_bb_mean:
            reasons.append(f"{opp_team} lineup BB% of {opp_bb_mean:.1%} — disciplined, patient approach")
        if sp.get("f_strike_pct"):
            reasons.append(f"First-pitch strike rate of {sp['f_strike_pct']:.1%} — struggles to get ahead")
    return reasons[:4]


def _pct(v) -> str | None:
    return f"{v:.1%}" if v is not None else None
