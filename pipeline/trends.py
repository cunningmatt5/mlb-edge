"""Compute pitcher and batter trend signals for the Trends tab."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

log = logging.getLogger(__name__)

_SIGNAL_PRIORITY = {"HOT_K": 0, "COLD_K": 1, "HIGH_WHIFF": 2, "WILD": 3, "SHARP": 4}


def compute_trends(cache: dict, games: list[dict]) -> dict:
    """Return trends dict with pitchers and batters lists."""
    pitchers = _pitcher_trends(cache, games)
    batters  = _batter_trends(cache, games)
    log.info("Trends: %d pitcher signals, %d batter signals", len(pitchers), len(batters))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date":         date.today().isoformat(),
        "pitchers":     pitchers,
        "batters":      batters,
    }


def _game_label(game: dict) -> str:
    away = (game.get("awayTeam") or "").split()
    home = (game.get("homeTeam") or "").split()
    return f"{away[-1] if away else '?'} @ {home[-1] if home else '?'}"


def _team_abbr(team: str) -> str:
    parts = (team or "").split()
    return parts[-1] if parts else "?"


def _pitcher_trends(cache: dict, games: list[dict]) -> list[dict]:
    results: list[dict] = []

    for game in games:
        label = _game_label(game)
        for sp_id, sp_name, team in [
            (game.get("home_sp_id"), game.get("home_sp_name", "TBD"), game.get("homeTeam", "")),
            (game.get("away_sp_id"), game.get("away_sp_name", "TBD"), game.get("awayTeam", "")),
        ]:
            if not sp_id or sp_id not in cache:
                continue
            s = cache[sp_id]
            k_pct         = s.get("k_pct")
            recent_k_pct  = s.get("recent_k_pct")
            bb_pct        = s.get("bb_pct")
            recent_bb_pct = s.get("recent_bb_pct")
            whiff_pct     = s.get("whiff_pct")
            xfip          = s.get("xfip")
            signals: list[dict] = []

            # K% trend (last 3 starts vs season)
            if k_pct is not None and recent_k_pct is not None:
                k_delta = recent_k_pct - k_pct
                if k_delta >= 0.04:
                    signals.append({
                        "signal":       "HOT_K",
                        "label":        "K Rate Surging",
                        "k_pct":        round(k_pct, 3),
                        "recent_k_pct": round(recent_k_pct, 3),
                        "k_pct_delta":  round(k_delta, 3),
                        "implication":  f"K rate up {k_delta*100:.1f}pp in last 3 starts — strikeout props have value",
                    })
                elif k_delta <= -0.04:
                    signals.append({
                        "signal":       "COLD_K",
                        "label":        "K Rate Fading",
                        "k_pct":        round(k_pct, 3),
                        "recent_k_pct": round(recent_k_pct, 3),
                        "k_pct_delta":  round(k_delta, 3),
                        "implication":  f"K rate down {abs(k_delta)*100:.1f}pp in last 3 starts — fade strikeout overs",
                    })

            # Whiff% signal (only if not already flagged as HOT_K)
            if (
                whiff_pct is not None
                and whiff_pct >= 0.30
                and not any(s2["signal"] == "HOT_K" for s2 in signals)
            ):
                signals.append({
                    "signal":    "HIGH_WHIFF",
                    "label":     "Elite Whiff Rate",
                    "whiff_pct": round(whiff_pct, 3),
                    "implication": f"Elite whiff rate ({whiff_pct*100:.1f}%) — swing-and-miss stuff, K props have value",
                })

            # BB% trend
            if bb_pct is not None and recent_bb_pct is not None:
                bb_delta = recent_bb_pct - bb_pct
                if bb_delta >= 0.03:
                    signals.append({
                        "signal":        "WILD",
                        "label":         "Walk Rate Spiking",
                        "bb_pct":        round(bb_pct, 3),
                        "recent_bb_pct": round(recent_bb_pct, 3),
                        "bb_pct_delta":  round(bb_delta, 3),
                        "implication":   f"Walk rate up +{bb_delta*100:.1f}pp — control concerns, lean OVER",
                    })
                elif bb_delta <= -0.02:
                    signals.append({
                        "signal":        "SHARP",
                        "label":         "Command Tightening",
                        "bb_pct":        round(bb_pct, 3),
                        "recent_bb_pct": round(recent_bb_pct, 3),
                        "bb_pct_delta":  round(bb_delta, 3),
                        "implication":   f"BB rate down {abs(bb_delta)*100:.1f}pp — efficient starts, lean UNDER",
                    })

            team_abbr = _team_abbr(team)
            for sig in signals:
                results.append({
                    "name":      sp_name,
                    "team":      team_abbr,
                    "game":      label,
                    "xfip":      round(xfip, 2) if xfip is not None else None,
                    "whiff_pct": round(whiff_pct, 3) if whiff_pct is not None else None,
                    **sig,
                })

    results.sort(key=lambda r: _SIGNAL_PRIORITY.get(r["signal"], 99))
    return results


def _batter_trends(cache: dict, games: list[dict]) -> list[dict]:
    results: list[dict] = []
    seen: set[int] = set()

    for game in games:
        label = _game_label(game)
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

                xwoba      = s.get("xwoba")
                woba       = s.get("woba")
                barrel_pct = s.get("barrel_pct")
                name       = s.get("name", f"Player {pid}")

                if xwoba is None or woba is None:
                    if barrel_pct is not None and barrel_pct > 0.12:
                        results.append({
                            "name":       name,
                            "team":       team_abbr,
                            "game":       label,
                            "signal":     "HIGH_BARREL",
                            "label":      "Power Upside",
                            "xwoba":      None,
                            "woba":       woba,
                            "xwoba_gap":  None,
                            "barrel_pct": round(barrel_pct, 3),
                            "implication": f"Elite barrel rate ({barrel_pct*100:.1f}%) — strong HR/TB prop candidate",
                        })
                    continue

                gap = xwoba - woba
                if abs(gap) >= 0.020 or (barrel_pct is not None and barrel_pct > 0.11):
                    if gap > 0:
                        signal = "UNDERPERFORMING"
                        label2 = "Expected to Rebound"
                        impl   = (
                            f"xwOBA (.{round(xwoba * 1000)}) far exceeds wOBA (.{round(woba * 1000)}) "
                            "— positive regression candidate, favor overs"
                        )
                    else:
                        signal = "OVERPERFORMING"
                        label2 = "Due for Regression"
                        impl   = (
                            f"wOBA (.{round(woba * 1000)}) exceeds xwOBA (.{round(xwoba * 1000)}) "
                            "— overperforming expected metrics, fade overs"
                        )
                    results.append({
                        "name":       name,
                        "team":       team_abbr,
                        "game":       label,
                        "signal":     signal,
                        "label":      label2,
                        "xwoba":      round(xwoba, 3),
                        "woba":       round(woba, 3),
                        "xwoba_gap":  round(gap, 3),
                        "barrel_pct": round(barrel_pct, 3) if barrel_pct is not None else None,
                        "implication": impl,
                    })

    results.sort(key=lambda r: -abs(r.get("xwoba_gap") or 0))
    return results
