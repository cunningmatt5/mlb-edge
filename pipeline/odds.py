"""Odds fetching, matching, and expected-value computation via The Odds API."""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from math import exp
from pathlib import Path
from typing import Optional

import requests

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
TIMEOUT = 15

log = logging.getLogger(__name__)

_PROP_MARKET_MAP = {
    "K_PROP":  "pitcher_strikeouts",
    "HR_PROP": "batter_home_runs",
    "HIT_PROP":"batter_hits",
    "TB_PROP": "batter_total_bases",
}


def fetch_mlb_game_lines(api_key: str, date_str: str) -> dict:
    """Return game-level odds keyed by normalized '{away}@{home}' matchup string.

    Returns {} if the API key is missing or the call fails — pipeline continues
    without odds and all picks show unfiltered.
    """
    if not api_key:
        log.debug("No ODDS_API_KEY set — skipping odds fetch")
        return {}
    try:
        # Free tier supports h2h + totals. team_totals requires a paid plan.
        # Set ODDS_MARKETS env var to override (e.g., "h2h,totals,team_totals").
        import os as _os
        markets = _os.environ.get("ODDS_MARKETS", "h2h,totals")
        params = {
            "apiKey": api_key,
            "regions": "us",
            "markets": markets,
            "bookmakers": "pinnacle",
            "oddsFormat": "american",
            "dateFormat": "iso",
            "commenceTimeFrom": f"{date_str}T00:00:00Z",
            "commenceTimeTo":   f"{date_str}T23:59:59Z",
        }
        r = requests.get(
            f"{ODDS_API_BASE}/sports/baseball_mlb/odds/",
            params=params,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        events = r.json()
        remaining = r.headers.get("x-requests-remaining", "?")
        log.info("Odds API: %d events fetched, %s requests remaining", len(events), remaining)

        result = {}
        for event in events:
            home = event.get("home_team", "")
            away = event.get("away_team", "")
            key = f"{_norm_team(away)}@{_norm_team(home)}"
            markets: dict[str, list] = {}
            for bm in event.get("bookmakers", []):
                if bm.get("key") != "pinnacle":
                    continue
                for mkt in bm.get("markets", []):
                    markets[mkt["key"]] = mkt.get("outcomes", [])
            if markets:
                result[key] = {
                    "home": home,
                    "away": away,
                    "event_id": event.get("id"),
                    "markets": markets,
                }
        return result
    except Exception as exc:
        log.warning("Odds API fetch failed: %s — proceeding without odds", exc)
        return {}


def fetch_mlb_props(api_key: str, event_id: str) -> dict:
    """Return player prop lines keyed by '{normalized_player}:{market}'.

    Requires the paid Odds API tier. Returns {} on any failure.
    """
    if not api_key or not event_id:
        return {}
    markets_str = ",".join(_PROP_MARKET_MAP.values())
    try:
        params = {
            "apiKey": api_key,
            "regions": "us",
            "markets": markets_str,
            "bookmakers": "pinnacle",
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
        r = requests.get(
            f"{ODDS_API_BASE}/sports/baseball_mlb/events/{event_id}/odds",
            params=params,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        result: dict[str, dict] = {}
        for bm in data.get("bookmakers", []):
            if bm.get("key") != "pinnacle":
                continue
            for mkt in bm.get("markets", []):
                mkt_key = mkt["key"]
                players: dict[str, dict] = {}
                for outcome in mkt.get("outcomes", []):
                    name = outcome.get("name", "")
                    player = outcome.get("description") or (name.rsplit(" ", 1)[0] if " " in name else name)
                    side = "Over" if "Over" in name else "Under"
                    players.setdefault(player, {})[side] = {
                        "price": outcome["price"],
                        "point": outcome.get("point"),
                    }
                for player, sides in players.items():
                    if "Over" in sides and "Under" in sides:
                        key = f"{_norm(player)}:{mkt_key}"
                        result[key] = {
                            "line":        sides["Over"]["point"],
                            "over_price":  sides["Over"]["price"],
                            "under_price": sides["Under"]["price"],
                        }
        return result
    except Exception as exc:
        log.debug("Props fetch failed for event %s: %s", event_id, exc)
        return {}


# ── Math helpers ──────────────────────────────────────────────────────────────

def american_to_decimal(odds: int) -> float:
    """Convert American odds to decimal (e.g., -110 → 1.909, +120 → 2.2)."""
    if odds >= 0:
        return 1.0 + odds / 100.0
    return 1.0 - 100.0 / odds  # odds is negative → subtracting a negative


def no_vig_prob(over_odds: int, under_odds: int) -> tuple[float, float]:
    """Return (fair_over_prob, fair_under_prob) with bookmaker vig removed.

    Both sides of a two-sided market are required. Symmetric -110/-110
    correctly returns (0.5, 0.5).
    """
    over_raw  = 1.0 / american_to_decimal(over_odds)
    under_raw = 1.0 / american_to_decimal(under_odds)
    total = over_raw + under_raw
    return round(over_raw / total, 4), round(under_raw / total, 4)


@lru_cache(maxsize=1)
def _load_calibration_params() -> tuple[float, float]:
    """Return (midpoint, slope) from data/calibration.json, or defaults (7.5, 0.45).

    Validates the fit before using it: if the midpoint hit the curve_fit boundary
    (≥10.5) or the slope is too flat (< 0.15), the calibration is degenerate
    (win rates never reached 50% in the backtest) and we fall back to defaults.
    """
    cal_path = Path(__file__).parent.parent / "data" / "calibration.json"
    try:
        d = json.loads(cal_path.read_text())
        p = d["logistic_params"]
        midpoint = float(p["midpoint"])
        slope    = float(p["slope"])
        if midpoint >= 9.0 or slope < 0.15:
            log.debug(
                "Calibration params degenerate (midpoint=%.2f slope=%.4f) — using defaults",
                midpoint, slope,
            )
            return 7.5, 0.45
        return midpoint, slope
    except Exception:
        return 7.5, 0.45


def signal_to_model_prob(signal: float) -> float:
    """Map 5–10 signal to win probability via logistic curve.

    Parameters are loaded from data/calibration.json when available;
    falls back to hardcoded defaults (midpoint=7.5, slope=0.45).
    signal 7.5 always → 0.5 regardless of midpoint (by logistic design).
    """
    midpoint, slope = _load_calibration_params()
    return round(1.0 / (1.0 + exp(-(signal - midpoint) * slope)), 4)


# ── Matching ──────────────────────────────────────────────────────────────────

def match_game_line(pick: dict, game: dict, game_lines: dict) -> Optional[dict]:
    """Match a game-level pick to a Pinnacle line.

    Returns a matched-line dict or None if no line is available.
    """
    home = game.get("homeTeam", "")
    away = game.get("awayTeam", "")
    key = f"{_norm(away)}@{_norm(home)}"
    event = game_lines.get(key)
    if not event:
        return None

    markets = event.get("markets", {})
    bet_type = pick["bet_type"]
    direction = pick["direction"]

    if bet_type == "TOTAL":
        outcomes = markets.get("totals", [])
        over_out  = next((o for o in outcomes if o.get("name") == "Over"),  None)
        under_out = next((o for o in outcomes if o.get("name") == "Under"), None)
        if not over_out or not under_out:
            return None
        fair_over, fair_under = no_vig_prob(over_out["price"], under_out["price"])
        return {
            "line":            over_out.get("point"),
            "over_price":      over_out["price"],
            "under_price":     under_out["price"],
            "fair_over_prob":  fair_over,
            "fair_under_prob": fair_under,
        }

    if bet_type == "TEAM_TOTAL":
        side = pick.get("subject_side", "home")
        team_name = home if side == "home" else away
        norm_team = _norm(team_name)
        outcomes = markets.get("team_totals", [])
        over_out  = next((o for o in outcomes if "over"  in o.get("name", "").lower() and norm_team in _norm(o["name"])), None)
        under_out = next((o for o in outcomes if "under" in o.get("name", "").lower() and norm_team in _norm(o["name"])), None)
        if not over_out or not under_out:
            return None
        fair_over, fair_under = no_vig_prob(over_out["price"], under_out["price"])
        return {
            "line":            over_out.get("point"),
            "over_price":      over_out["price"],
            "under_price":     under_out["price"],
            "fair_over_prob":  fair_over,
            "fair_under_prob": fair_under,
        }

    if bet_type == "ML_F5":
        # h2h_h1 (first-half ML) requires a paid plan; always fall back to h2h
        outcomes = markets.get("h2h_h1") or markets.get("h2h", [])
        if not outcomes:
            return None
        norm_home = _norm_team(home)
        norm_away = _norm_team(away)
        home_out = next((o for o in outcomes if _norm_team(o.get("name", "")) == norm_home), None)
        away_out = next((o for o in outcomes if _norm_team(o.get("name", "")) == norm_away), None)
        if not home_out or not away_out:
            return None
        # HOME direction uses over_price slot; AWAY uses under_price slot
        fair_home, fair_away = no_vig_prob(home_out["price"], away_out["price"])
        return {
            "line":            None,  # ML has no line
            "over_price":      home_out["price"],
            "under_price":     away_out["price"],
            "fair_over_prob":  fair_home,
            "fair_under_prob": fair_away,
        }

    return None


def match_prop_line(pick: dict, prop_lines: dict) -> Optional[dict]:
    """Match a player prop pick to a Pinnacle line."""
    mkt = _PROP_MARKET_MAP.get(pick["bet_type"])
    if not mkt:
        return None
    subject = pick.get("subject", "")
    matched = prop_lines.get(f"{_norm(subject)}:{mkt}")
    if not matched:
        return None
    fair_over, fair_under = no_vig_prob(matched["over_price"], matched["under_price"])
    return {
        "line":            matched["line"],
        "over_price":      matched["over_price"],
        "under_price":     matched["under_price"],
        "fair_over_prob":  fair_over,
        "fair_under_prob": fair_under,
    }


def get_event_id(game: dict, game_lines: dict) -> Optional[str]:
    """Return the Odds API event_id for a game dict, or None if not matched."""
    away = game.get("awayTeam", "")
    home = game.get("homeTeam", "")
    event = game_lines.get(f"{_norm_team(away)}@{_norm_team(home)}")
    return event.get("event_id") if event else None


def get_game_event(game: dict, game_lines: dict) -> Optional[dict]:
    """Return the full Odds API event dict (with markets) for a game, or None."""
    away = game.get("awayTeam", "")
    home = game.get("homeTeam", "")
    return game_lines.get(f"{_norm_team(away)}@{_norm_team(home)}")


def compute_ev(pick: dict, matched_line: dict) -> dict:
    """Compute expected value of a pick vs. Pinnacle no-vig probability."""
    model_prob  = signal_to_model_prob(pick["signal"])
    direction   = pick["direction"]
    if direction in ("OVER", "HOME"):
        implied_prob = matched_line["fair_over_prob"]
        price        = matched_line["over_price"]
    else:
        implied_prob = matched_line["fair_under_prob"]
        price        = matched_line["under_price"]
    return {
        "model_prob":   model_prob,
        "implied_prob": implied_prob,
        "edge_pct":     round(model_prob - implied_prob, 4),
        "line":         matched_line.get("line"),
        "price":        price,
        "over_price":   matched_line["over_price"],
        "under_price":  matched_line["under_price"],
        "book":         "pinnacle",
    }


# ── Internal ──────────────────────────────────────────────────────────────────

def _norm(name: str) -> str:
    """Normalize team/player name for fuzzy matching (lowercase alphanum only)."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


# Map Odds API team name variants → canonical normalized form that matches
# the MLB Stats API team names used by the schedule module.
_TEAM_CANON: dict[str, str] = {
    # Athletics (city changed Oakland→Sacramento 2025; Stats API omits city)
    "oaklandathletics":    "athletics",
    "sacramentoathletics": "athletics",
    # Angels (historical "of Anaheim" suffix)
    "losangelesangelsofanaheim": "losangelesangels",
    "anaheimangels":             "losangelesangels",
    # Marlins (pre-2012 Florida name)
    "floridamarlins": "miamimarlins",
    # Guardians (pre-2022 Indians name)
    "clevelandindians": "clevelandguardians",
}


def _norm_team(name: str) -> str:
    """Normalize a team name and resolve known alias variants."""
    n = _norm(name)
    return _TEAM_CANON.get(n, n)
