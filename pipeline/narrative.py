"""Generate Claude-powered pre-game narrative for each game.

Falls back to a minimal template string if the Anthropic API is
unavailable or ANTHROPIC_API_KEY is not set — never raises, never
blocks the pipeline.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a sharp MLB analyst writing pre-game intelligence for a statistical betting model.
Given structured game data, write exactly 2-3 sentences that:
1. Identify the 1-2 most important signals driving the model's view of this game
2. Flag any meaningful caveats (recent SP form concern, lineup TBD, weather, bullpen fatigue)
3. If the model has a notable edge vs Vegas (pitcher score diff > 0.10 or < -0.10), briefly note the direction
Be analytical and direct. Use only the provided data — never invent statistics.
Avoid hedging words like "might", "could", "possibly". Write in present tense.
Do not start with "In" or repeat the matchup name. Output only the narrative text, no labels or headers.\
"""


def generate_narrative(game_data: dict) -> str:
    """Return a Claude-generated narrative, or fall back to the template."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _template_fallback(game_data)
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = _build_prompt(game_data)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        return text if text else _template_fallback(game_data)
    except Exception as exc:
        log.warning("Claude narrative failed (%s) — using template fallback", exc)
        return _template_fallback(game_data)


def _build_prompt(g: dict) -> str:
    pred    = g.get("prediction", {}) or {}
    sig     = pred.get("model_signals", {}) or {}
    home_sp = g.get("home_sp", {}) or {}
    away_sp = g.get("away_sp", {}) or {}
    odds    = g.get("odds", {}) or {}
    weather = g.get("weather", {}) or {}
    h_rec   = g.get("home_record", {}) or {}
    a_rec   = g.get("away_record", {}) or {}

    def _sp_line(sp: dict, label: str) -> str:
        s     = sp.get("season", {}) or {}
        r     = sp.get("recent", {}) or {}
        ls    = sp.get("last_start", {}) or {}
        flags = sp.get("trend_flags", []) or []
        parts = [f"{label}: {sp.get('name', '?')}"]
        if s.get("xera"):      parts.append(f"xERA {s['xera']:.2f}")
        if s.get("era"):       parts.append(f"ERA {s['era']:.2f}")
        if s.get("k_pct"):     parts.append(f"K% {s['k_pct']:.1%}")
        if r.get("bb_pct"):    parts.append(f"BB% {r['bb_pct']:.1%}")
        if s.get("whiff_pct"): parts.append(f"Whiff% {s['whiff_pct']:.1%}")
        dev = ls.get("deviation")
        if dev is not None:
            parts.append(f"last-start ERA deviation {dev:+.2f} vs season")
        if flags:
            parts.append(f"flag: {flags[0]}")
        return ", ".join(parts)

    lines = [
        f"Matchup: {g.get('away_team')} @ {g.get('home_team')}",
        f"Venue: {g.get('venue')} (park run factor {g.get('park_run_factor', 100):.0f})",
        _sp_line(home_sp, "Home SP"),
        _sp_line(away_sp, "Away SP"),
    ]

    # Lineup xwOBA
    lineup_status = g.get("lineup_status", "tbd")
    home_lineup = g.get("home_lineup") or []
    away_lineup = g.get("away_lineup") or []
    h_xwoba_vals = [p["xwoba"] for p in home_lineup if p.get("xwoba")]
    a_xwoba_vals = [p["xwoba"] for p in away_lineup if p.get("xwoba")]
    if h_xwoba_vals and a_xwoba_vals:
        lines.append(
            f"Lineup xwOBA: home {sum(h_xwoba_vals)/len(h_xwoba_vals):.3f}, "
            f"away {sum(a_xwoba_vals)/len(a_xwoba_vals):.3f}"
        )
    else:
        lines.append(f"Lineup status: {lineup_status} (lineups not yet posted)")

    # Bullpen
    bp_h = sig.get("bullpen_xera_home")
    bp_a = sig.get("bullpen_xera_away")
    if bp_h or bp_a:
        lines.append(f"Bullpen xERA: home {bp_h or '?'}, away {bp_a or '?'}")
    l3d_h = sig.get("bp_ip_last_3_home")
    l3d_a = sig.get("bp_ip_last_3_away")
    if l3d_h or l3d_a:
        lines.append(f"Bullpen L3D IP: home {l3d_h or 0:.1f}, away {l3d_a or 0:.1f}")

    # Weather (only meaningful conditions)
    if weather and not weather.get("dome"):
        w_parts = []
        wind = weather.get("wind_mph") or weather.get("wind_speed_mph")
        if wind and wind >= 8:
            direction = "blowing out" if weather.get("blowing_out") else "blowing in"
            w_parts.append(f"wind {wind:.0f} mph {direction}")
        temp = weather.get("temp_f")
        if temp and temp < 55:
            w_parts.append(f"cold {temp:.0f}°F")
        if w_parts:
            lines.append(f"Weather: {', '.join(w_parts)}")

    # Vegas odds
    if odds.get("home_ml"):
        lines.append(
            f"Vegas ML: home {odds['home_ml']:+d}, away {odds['away_ml']:+d}, "
            f"total {odds.get('total', '?')}"
        )

    # Model output
    if pred.get("home_win_pct"):
        lines.append(
            f"Model: home win {pred['home_win_pct']:.1%}, "
            f"predicted total {pred.get('predicted_total', '?')}"
        )
    if sig.get("consensus_suppressed"):
        lines.append(
            "Note: model and Vegas both lean home — consensus suppressed (signal dampened)"
        )

    # Team records
    if h_rec:
        lines.append(
            f"{g.get('home_team')} record: {h_rec.get('wins')}-{h_rec.get('losses')}, "
            f"streak {h_rec.get('streak')}, L10 {h_rec.get('l10_w')}-{h_rec.get('l10_l')}"
        )
    if a_rec:
        lines.append(
            f"{g.get('away_team')} record: {a_rec.get('wins')}-{a_rec.get('losses')}, "
            f"streak {a_rec.get('streak')}, L10 {a_rec.get('l10_w')}-{a_rec.get('l10_l')}"
        )

    # Model signal summary
    p_diff = (sig.get("pitcher_score_home") or 0) - (sig.get("pitcher_score_away") or 0)
    l_diff = (sig.get("lineup_score_home") or 0) - (sig.get("lineup_score_away") or 0)
    lines.append(f"Pitcher score diff (home minus away): {p_diff:+.3f}")
    lines.append(f"Lineup score diff (home minus away): {l_diff:+.3f}")

    return "\n".join(lines)


def _template_fallback(g: dict) -> str:
    """Minimal fallback matching the legacy template output."""
    pred    = g.get("prediction", {}) or {}
    sig     = pred.get("model_signals", {}) or {}
    home_sp = g.get("home_sp", {}) or {}
    away_sp = g.get("away_sp", {}) or {}
    p_diff  = (sig.get("pitcher_score_home") or 0) - (sig.get("pitcher_score_away") or 0)
    h_name  = home_sp.get("name", "Home SP")
    a_name  = away_sp.get("name", "Away SP")
    h_xera  = (home_sp.get("season") or {}).get("xera")
    a_xera  = (away_sp.get("season") or {}).get("xera")

    if abs(p_diff) >= 0.08:
        better = h_name if p_diff > 0 else a_name
        xera   = h_xera if p_diff > 0 else a_xera
        side   = "home" if p_diff > 0 else "away"
        if xera:
            return f"{better}'s {xera:.2f} xERA gives the {side} side a pitching edge."
        return f"{better} holds a meaningful pitching edge."
    if h_xera and a_xera:
        return f"Even pitching matchup: {h_name} ({h_xera:.2f} xERA) vs {a_name} ({a_xera:.2f} xERA)."
    return f"Pitching matchup: {h_name} vs {a_name}."
