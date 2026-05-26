"""Score starting pitcher strikeout prop opportunities.

Signal logic: Both the pitcher quality AND the opposing lineup K-vulnerability
must be strong. Geometric mean enforces that — a dominant pitcher vs. a
contact-heavy lineup produces a weaker signal than when both dimensions align.

Enhancements:
- K% blends season (60%) and last-3-starts (40%) for recency weighting
- HP umpire zone tendency applies a small signal modifier
"""

from __future__ import annotations

from pipeline.scorer import normalize, weighted_avg, safe_mean
from pipeline.umpire import compute_umpire_modifier


def score_strikeout_props(game: dict, cache: dict) -> list[dict]:
    picks = []
    umpire = game.get("umpire", "")

    for sp_side, opp_side in [("home", "away"), ("away", "home")]:
        sp_id = game.get(f"{sp_side}_sp_id")
        if not sp_id or sp_id not in cache:
            continue
        sp = cache[sp_id]

        opp_lineup = [cache[b] for b in game.get(f"{opp_side}_lineup", []) if b in cache]
        if not opp_lineup:
            continue

        # --- Pitcher quality component ---
        # Blend season K% with last-3-starts K% (60/40)
        season_k = sp.get("k_pct")
        recent_k = sp.get("recent_k_pct")
        recent_starts_n = sp.get("recent_starts_n", 0)
        if season_k is not None and recent_k is not None and recent_starts_n >= 3:
            blended_k = 0.60 * season_k + 0.40 * recent_k
        else:
            blended_k = season_k if season_k is not None else recent_k

        stuff_s = normalize(sp.get("stuff_plus"), lo=80, hi=130)
        whiff_s = normalize(sp.get("whiff_pct"), lo=0.12, hi=0.38)
        chase_s = normalize(sp.get("o_swing_pct") or sp.get("o_swing_pct_fg"), lo=0.20, hi=0.40)
        k_s     = normalize(blended_k, lo=0.14, hi=0.36)

        pitcher_comp = weighted_avg([
            (stuff_s, 0.30),
            (whiff_s, 0.30),
            (chase_s, 0.20),
            (k_s,     0.20),
        ])

        # --- Opposing lineup K-vulnerability component ---
        opp_k_pcts    = [b.get("k_pct")      for b in opp_lineup]
        opp_contacts  = [b.get("contact_pct") for b in opp_lineup]
        opp_k_mean    = safe_mean(opp_k_pcts)
        opp_contact_mean = safe_mean(opp_contacts)

        opp_k_s       = normalize(opp_k_mean,      lo=0.16, hi=0.30)
        opp_contact_s = normalize(opp_contact_mean, lo=0.90, hi=0.68)

        lineup_comp = weighted_avg([
            (opp_k_s,       0.60),
            (opp_contact_s, 0.40),
        ])

        combined = (pitcher_comp ** 0.6) * (lineup_comp ** 0.4)
        ump_mod, ump_reason = compute_umpire_modifier(umpire, "K_PROP", "OVER")
        signal = max(0.0, min(10.0, round(combined * 10 + ump_mod, 1)))

        if signal >= 5.0:
            sp_name  = sp.get("name") or game.get(f"{sp_side}_sp_name", "SP")
            opp_team = game.get(f"{opp_side}Team", "Opponent")
            reasons  = _build_reasons(sp, opp_k_mean, opp_contact_mean, opp_team, blended_k, recent_k)
            if ump_reason:
                reasons = (reasons + [ump_reason])[:4]

            picks.append({
                "bet_type":   "K_PROP",
                "subject":    sp_name,
                "subject_id": sp_id,
                "direction":  "OVER",
                "headline":   f"{sp_name} Strikeouts — OVER",
                "signal":     signal,
                "reasons":    reasons,
                "raw_scores": {
                    "stuff_plus":        sp.get("stuff_plus"),
                    "whiff_pct":         _pct(sp.get("whiff_pct")),
                    "o_swing_pct":       _pct(sp.get("o_swing_pct") or sp.get("o_swing_pct_fg")),
                    "sp_k_pct":          _pct(blended_k),
                    "k_pct_season":      _pct(sp.get("k_pct")),
                    "k_pct_recent":      _pct(sp.get("recent_k_pct")),
                    "opp_k_pct":         _pct(opp_k_mean),
                    "opp_contact_pct":   _pct(opp_contact_mean),
                    "pitcher_component": round(pitcher_comp, 3),
                    "lineup_component":  round(lineup_comp, 3),
                    "umpire":            umpire or None,
                    "recent_k_games":    sp.get("recent_k_games"),
                    "recent_starts_n":   sp.get("recent_starts_n"),
                },
            })

    return picks


def _build_reasons(sp, opp_k_mean, opp_contact_mean, opp_team, blended_k, recent_k) -> list[str]:
    reasons = []

    stuff = sp.get("stuff_plus")
    if stuff is not None:
        if stuff >= 115:
            tier = "elite pitch quality"
        elif stuff >= 105:
            tier = "above-average pitch quality"
        else:
            tier = "average pitch quality"
        reasons.append(f"Stuff+ of {int(stuff)} — {tier} (100 = MLB average)")

    whiff = sp.get("whiff_pct")
    if whiff is not None:
        if whiff >= 0.28:
            tier = "elite swing-and-miss rate"
        elif whiff >= 0.22:
            tier = "above-average whiff rate"
        else:
            tier = "average whiff rate"
        reasons.append(f"Whiff rate {whiff:.1%} on swings — {tier}")

    season_k = sp.get("k_pct")
    if blended_k is not None and recent_k is not None:
        reasons.append(
            f"K% {blended_k:.1%} blended (season {season_k:.1%} / "
            f"last {sp.get('recent_starts_n', 3)} starts {recent_k:.1%})"
        )
    elif blended_k is not None:
        reasons.append(f"K% of {blended_k:.1%} — strikeout rate vs. MLB avg ~22%")

    recent_games = sp.get("recent_k_games")
    if recent_games:
        k_str = " · ".join(f"{k}K" for k in recent_games)
        reasons.append(f"Last {len(recent_games)} starts: {k_str}")

    if opp_k_mean is not None:
        if opp_k_mean >= 0.26:
            label = "high-strikeout lineup — prime matchup"
        elif opp_k_mean >= 0.22:
            label = "above-average K rate — favorable matchup"
        else:
            label = "average strikeout rate"
        reasons.append(f"{opp_team} averages {opp_k_mean:.1%} K rate — {label}")

    if opp_contact_mean is not None and opp_contact_mean <= 0.74:
        reasons.append(
            f"{opp_team} contact rate {opp_contact_mean:.1%} — struggles to put bat on ball"
        )

    return reasons[:6]


def _pct(v) -> str | None:
    return f"{v:.1%}" if v is not None else None
