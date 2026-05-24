"""Compute pitcher and batter trend signals for the Trends tab."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

log = logging.getLogger(__name__)


def compute_trends(cache: dict, games: list[dict]) -> dict:
    """Return trends dict with six named category lists."""
    counts = {}
    result = {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "date":            date.today().isoformat(),
        "pitcher_lucky":   _pitcher_luck(cache, games, lucky=True),
        "pitcher_unlucky": _pitcher_luck(cache, games, lucky=False),
        "pitcher_hot_k":   _pitcher_k_trend(cache, games, surging=True),
        "pitcher_cold_k":  _pitcher_k_trend(cache, games, surging=False),
        "batter_cold":     _batter_trend(cache, games, cold=True),
        "batter_hot":      _batter_trend(cache, games, cold=False),
    }
    total = sum(len(v) for k, v in result.items() if isinstance(v, list))
    log.info(
        "Trends: lucky=%d unlucky=%d hot_k=%d cold_k=%d batter_cold=%d batter_hot=%d",
        len(result["pitcher_lucky"]),
        len(result["pitcher_unlucky"]),
        len(result["pitcher_hot_k"]),
        len(result["pitcher_cold_k"]),
        len(result["batter_cold"]),
        len(result["batter_hot"]),
    )
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _game_label(game: dict) -> str:
    away = (game.get("awayTeam") or "").split()
    home = (game.get("homeTeam") or "").split()
    return f"{away[-1] if away else '?'} @ {home[-1] if home else '?'}"


def _team_abbr(team: str) -> str:
    parts = (team or "").split()
    return parts[-1] if parts else "?"


def _sp_iter(games: list[dict]):
    """Yield (sp_id, sp_name, team_abbr, game_label) for today's starters."""
    for game in games:
        label = _game_label(game)
        for sp_id, sp_name, team in [
            (game.get("home_sp_id"), game.get("home_sp_name", "TBD"), game.get("homeTeam", "")),
            (game.get("away_sp_id"), game.get("away_sp_name", "TBD"), game.get("awayTeam", "")),
        ]:
            if sp_id:
                yield sp_id, sp_name, _team_abbr(team), label


# ── Pitcher: ERA luck ─────────────────────────────────────────────────────────

def _pitcher_luck(cache: dict, games: list[dict], *, lucky: bool) -> list[dict]:
    """Return pitchers with ERA significantly below (lucky) or above (unlucky) xFIP."""
    results: list[dict] = []
    for sp_id, sp_name, team, game_label in _sp_iter(games):
        s = cache.get(sp_id)
        if not s:
            continue
        era  = s.get("era")
        xfip = s.get("xfip")
        if era is None or xfip is None:
            continue

        if lucky:
            delta = xfip - era          # positive → ERA below xFIP → getting lucky
            if delta < 1.0:
                continue
            signal = "ERA_LUCK"
            impl = (
                f"ERA ({era:.2f}) is {delta:.2f} runs below xFIP ({xfip:.2f}) — "
                f"results outpacing process. Lean OVER against {sp_name}."
            )
        else:
            delta = era - xfip          # positive → ERA above xFIP → getting unlucky
            if delta < 1.0:
                continue
            signal = "ERA_STRUGGLE"
            impl = (
                f"ERA ({era:.2f}) is {delta:.2f} runs above xFIP ({xfip:.2f}) — "
                f"pitching better than results. Back {sp_name} or lean UNDER."
            )

        results.append({
            "name":        sp_name,
            "team":        team,
            "game":        game_label,
            "signal":      signal,
            "stat_a_label": "ERA",
            "stat_a":      round(era, 2),
            "stat_b_label": "xFIP",
            "stat_b":      round(xfip, 2),
            "delta":       round(delta, 2),
            "implication": impl,
        })

    results.sort(key=lambda r: -r["delta"])
    return results


# ── Pitcher: K-rate trend ──────────────────────────────────────────────────────

def _pitcher_k_trend(cache: dict, games: list[dict], *, surging: bool) -> list[dict]:
    """Return pitchers with K% significantly up (surging) or down (fading) vs season avg."""
    results: list[dict] = []
    for sp_id, sp_name, team, game_label in _sp_iter(games):
        s = cache.get(sp_id)
        if not s:
            continue
        k_pct        = s.get("k_pct")
        recent_k_pct = s.get("recent_k_pct")
        if k_pct is None or recent_k_pct is None:
            continue

        if surging:
            delta = recent_k_pct - k_pct      # positive → recent K% > season K%
            if delta < 0.03:
                continue
            signal = "HOT_K"
            impl = (
                f"K rate up {delta * 100:.1f}pp in last 3 starts "
                f"(21-day avg {k_pct * 100:.1f}% → recent {recent_k_pct * 100:.1f}%) — "
                "strikeout props have value."
            )
        else:
            delta = k_pct - recent_k_pct      # positive → season K% > recent K%
            if delta < 0.03:
                continue
            signal = "COLD_K"
            impl = (
                f"K rate down {delta * 100:.1f}pp in last 3 starts "
                f"(21-day avg {k_pct * 100:.1f}% → recent {recent_k_pct * 100:.1f}%) — "
                "fade strikeout overs."
            )

        results.append({
            "name":        sp_name,
            "team":        team,
            "game":        game_label,
            "signal":      signal,
            "stat_a_label": "21-Day K%",
            "stat_a":      round(k_pct, 3),
            "stat_b_label": "Last 3 K%",
            "stat_b":      round(recent_k_pct, 3),
            "delta":       round(delta, 3),
            "implication": impl,
        })

    results.sort(key=lambda r: -r["delta"])
    return results


# ── Batter: xwOBA vs wOBA ─────────────────────────────────────────────────────

def _batter_trend(cache: dict, games: list[dict], *, cold: bool) -> list[dict]:
    """Return batters with xwOBA significantly above (cold) or below (hot) their wOBA."""
    results: list[dict] = []
    seen: set[int] = set()

    for game in games:
        game_label = _game_label(game)
        for lineup, team in [
            (game.get("home_lineup", []), game.get("homeTeam", "")),
            (game.get("away_lineup", []), game.get("awayTeam", "")),
        ]:
            team_abbr = _team_abbr(team)
            for pid in lineup:
                if pid in seen or pid not in cache:
                    continue
                seen.add(pid)
                s = cache[pid]
                if s.get("role") == "pitcher":
                    continue

                xwoba = s.get("xwoba")
                woba  = s.get("woba")
                if xwoba is None or woba is None:
                    continue
                name = s.get("name", f"Player {pid}")

                if cold:
                    delta = xwoba - woba      # positive → xwOBA > wOBA → underperforming
                    if delta < 0.025:
                        continue
                    signal = "COLD_BAT"
                    impl = (
                        f"Hitting well below expectations — xwOBA (.{round(xwoba * 1000)}) "
                        f"is {round(delta * 1000)} points above wOBA (.{round(woba * 1000)}). "
                        "Due for positive regression; lean OVER on hit/TB props."
                    )
                else:
                    delta = woba - xwoba      # positive → wOBA > xwOBA → overperforming
                    if delta < 0.025:
                        continue
                    signal = "HOT_BAT"
                    impl = (
                        f"Results outpacing expected metrics — wOBA (.{round(woba * 1000)}) "
                        f"is {round(delta * 1000)} points above xwOBA (.{round(xwoba * 1000)}). "
                        "Negative regression likely; fade hit/TB props."
                    )

                results.append({
                    "name":        name,
                    "team":        team_abbr,
                    "game":        game_label,
                    "signal":      signal,
                    "stat_a_label": "xwOBA",
                    "stat_a":      round(xwoba, 3),
                    "stat_b_label": "wOBA",
                    "stat_b":      round(woba, 3),
                    "delta":       round(delta, 3),
                    "implication": impl,
                })

    results.sort(key=lambda r: -r["delta"])
    return results
