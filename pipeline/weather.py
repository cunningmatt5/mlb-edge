"""Fetch game-day weather from Open-Meteo and compute signal modifiers."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import requests

log = logging.getLogger(__name__)
TIMEOUT = 10

# True enclosed domes — weather never applies
_DOMES = {"Tropicana Field", "Rogers Centre"}

# Stadium lat/lon and outfield orientation (degrees the outfield faces)
# Wind "blowing out" = wind comes from behind home plate (opposite of of_dir)
_STADIUMS: dict[str, dict] = {
    "Wrigley Field":                   {"lat": 41.9484, "lon": -87.6553, "of_dir": 90},
    "Great American Ball Park":        {"lat": 39.0979, "lon": -84.5082, "of_dir": 0},
    "Chase Field":                     {"lat": 33.4453, "lon": -112.0667, "of_dir": 315},
    "Truist Park":                     {"lat": 33.8908, "lon": -84.4678, "of_dir": 0},
    "Oriole Park at Camden Yards":     {"lat": 39.2839, "lon": -76.6217, "of_dir": 45},
    "Fenway Park":                     {"lat": 42.3467, "lon": -71.0972, "of_dir": 90},
    "Guaranteed Rate Field":           {"lat": 41.8300, "lon": -87.6339, "of_dir": 0},
    "Progressive Field":               {"lat": 41.4958, "lon": -81.6853, "of_dir": 315},
    "Coors Field":                     {"lat": 39.7559, "lon": -104.9942, "of_dir": 0},
    "Comerica Park":                   {"lat": 42.3390, "lon": -83.0486, "of_dir": 0},
    "Minute Maid Park":                {"lat": 29.7572, "lon": -95.3555, "of_dir": 45},
    "Kauffman Stadium":                {"lat": 39.0517, "lon": -94.4803, "of_dir": 315},
    "Angel Stadium":                   {"lat": 33.8003, "lon": -117.8827, "of_dir": 315},
    "Dodger Stadium":                  {"lat": 34.0739, "lon": -118.2400, "of_dir": 0},
    "loanDepot park":                  {"lat": 25.7781, "lon": -80.2197, "of_dir": 315},
    "LoanDepot park":                  {"lat": 25.7781, "lon": -80.2197, "of_dir": 315},
    "American Family Field":           {"lat": 43.0283, "lon": -87.9712, "of_dir": 315},
    "Target Field":                    {"lat": 44.9817, "lon": -93.2781, "of_dir": 45},
    "Yankee Stadium":                  {"lat": 40.8296, "lon": -73.9262, "of_dir": 315},
    "Oakland Coliseum":                {"lat": 37.7516, "lon": -122.2005, "of_dir": 0},
    "Citizens Bank Park":              {"lat": 39.9061, "lon": -75.1665, "of_dir": 0},
    "PNC Park":                        {"lat": 40.4469, "lon": -80.0057, "of_dir": 315},
    "Petco Park":                      {"lat": 32.7076, "lon": -117.1570, "of_dir": 315},
    "Oracle Park":                     {"lat": 37.7786, "lon": -122.3893, "of_dir": 315},
    "T-Mobile Park":                   {"lat": 47.5914, "lon": -122.3325, "of_dir": 315},
    "Busch Stadium":                   {"lat": 38.6226, "lon": -90.1928, "of_dir": 315},
    "Globe Life Field":                {"lat": 32.7513, "lon": -97.0836, "of_dir": 315},
    "Nationals Park":                  {"lat": 38.8730, "lon": -77.0074, "of_dir": 315},
    "Citi Field":                      {"lat": 40.7571, "lon": -73.8458, "of_dir": 315},
    "Sutter Health Park":              {"lat": 38.5775, "lon": -121.5030, "of_dir": 0},
}


def fetch_game_weather(venue: str, game_time_utc: str) -> Optional[dict]:
    """Return weather dict for venue at game time, or None if unavailable."""
    if venue in _DOMES:
        return {"dome": True}

    stadium = _STADIUMS.get(venue)
    if not stadium:
        log.debug("No stadium coordinates for: %s", venue)
        return None

    try:
        game_dt = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
        target_hour = game_dt.strftime("%Y-%m-%dT%H")

        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={stadium['lat']}&longitude={stadium['lon']}"
            f"&hourly=temperature_2m,windspeed_10m,winddirection_10m"
            f"&temperature_unit=fahrenheit&windspeed_unit=mph"
            f"&timezone=UTC&forecast_days=2"
        )
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        idx = next((i for i, t in enumerate(times) if t.startswith(target_hour)), None)
        if idx is None:
            return None

        wind_speed = _safe(hourly.get("windspeed_10m", []), idx)
        wind_dir   = _safe(hourly.get("winddirection_10m", []), idx)
        temp_f     = _safe(hourly.get("temperature_2m", []), idx)

        blowing_out = _is_blowing_out(wind_dir, stadium["of_dir"]) if wind_dir is not None else None

        return {
            "dome": False,
            "wind_speed_mph": wind_speed,
            "wind_direction_deg": wind_dir,
            "blowing_out": blowing_out,
            "temp_f": temp_f,
        }
    except Exception as exc:
        log.warning("Weather fetch failed for %s: %s", venue, exc)
        return None


def compute_weather_modifier(weather: Optional[dict], bet_type: str) -> tuple[float, Optional[str]]:
    """Return (offense_modifier, reason_str) where positive favors scoring/overs.

    Applies to: TOTAL, TEAM_TOTAL, HR_PROP, TB_PROP.
    Returns (0.0, None) for domes or non-scoring bet types.
    """
    if not weather or weather.get("dome"):
        return 0.0, None
    if bet_type not in ("TOTAL", "TEAM_TOTAL", "HR_PROP", "TB_PROP"):
        return 0.0, None

    modifier = 0.0
    reason: Optional[str] = None

    wind_speed = weather.get("wind_speed_mph")
    blowing_out = weather.get("blowing_out")
    temp_f = weather.get("temp_f")

    if wind_speed is not None and wind_speed > 10:
        if blowing_out is True:
            if wind_speed > 20:
                modifier += 1.0
                reason = f"Wind {wind_speed:.0f} mph blowing out — strong HR/scoring boost"
            elif wind_speed > 14:
                modifier += 0.6
                reason = f"Wind {wind_speed:.0f} mph blowing out — favors hitters"
            else:
                modifier += 0.3
                reason = f"Wind {wind_speed:.0f} mph blowing out"
        elif blowing_out is False:
            if wind_speed > 20:
                modifier -= 1.0
                reason = f"Wind {wind_speed:.0f} mph blowing in — suppresses HR/scoring"
            elif wind_speed > 14:
                modifier -= 0.6
                reason = f"Wind {wind_speed:.0f} mph blowing in — favors pitchers"
            else:
                modifier -= 0.3
                reason = f"Wind {wind_speed:.0f} mph blowing in"

    if temp_f is not None:
        if temp_f < 45:
            modifier -= 0.5
            if reason is None:
                reason = f"Cold ({temp_f:.0f}°F) — ball doesn't carry; suppresses scoring"
        elif temp_f < 55:
            modifier -= 0.25
            if reason is None:
                reason = f"Cool ({temp_f:.0f}°F) — slight suppression on scoring"
        elif temp_f > 88:
            modifier += 0.4
            if reason is None:
                reason = f"Hot ({temp_f:.0f}°F) — ball carries well in warm air"
        elif temp_f > 80:
            modifier += 0.2

    return max(-1.5, min(1.5, modifier)), reason


def _is_blowing_out(wind_direction: float, of_dir: float) -> bool:
    """True if wind blows toward outfield (from behind home plate)."""
    home_dir = (of_dir + 180) % 360
    diff = abs((wind_direction - home_dir + 180) % 360 - 180)
    return diff <= 60


def _safe(lst: list, idx: int):
    return lst[idx] if idx < len(lst) else None
