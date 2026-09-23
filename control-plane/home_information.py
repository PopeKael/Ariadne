from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
import io
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone


OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
STOOQ_HISTORY_URL = "https://stooq.com/q/d/l/"
REQUEST_TIMEOUT_SECONDS = 10.0
WEATHER_CACHE_SECONDS = 10 * 60
MARKET_CACHE_SECONDS = 15 * 60
USER_AGENT = "Ariadne-Home/1.0 (+local personal dashboard)"

MARKET_SERIES = (
    ("sp500", "S&P 500", "^spx"),
    ("dow", "Dow Jones", "^dji"),
    ("nasdaq", "Nasdaq Composite", "^ndq"),
    ("nasdaq100", "Nasdaq 100", "^ndx"),
    ("australia", "All Ordinaries", "^aor"),
)

OIL_SERIES = (
    ("wti", "WTI", "cl.f"),
    ("brent", "Brent", "cb.f"),
)

_WEATHER_CODES = {
    0: "Clear",
    1: "Mostly clear",
    2: "Partly cloudy",
    3: "Cloudy",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Heavy freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Heavy freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Rain showers",
    81: "Rain showers",
    82: "Heavy rain showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl_seconds: int, loader):
    now = time.monotonic()
    with _CACHE_LOCK:
        current = _CACHE.get(key)
        if current and now - current[0] < ttl_seconds:
            return current[1]
    value = loader()
    with _CACHE_LOCK:
        _CACHE[key] = (now, value)
    return value


def _get_json(url: str) -> dict[str, object]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Provider returned a non-object JSON payload.")
    return payload


def _get_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv,text/plain;q=0.9,*/*;q=0.5"})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _weather_label(code: object) -> str:
    try:
        return _WEATHER_CODES.get(int(code), "Weather")
    except (TypeError, ValueError):
        return "Weather"


def _weather_payload(latitude: float, longitude: float) -> dict[str, object]:
    query = urllib.parse.urlencode({
        "latitude": f"{latitude:.5f}",
        "longitude": f"{longitude:.5f}",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": "5",
    })
    payload = _get_json(f"{OPEN_METEO_URL}?{query}")
    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    daily = payload.get("daily") if isinstance(payload.get("daily"), dict) else {}

    dates = daily.get("time") if isinstance(daily.get("time"), list) else []
    codes = daily.get("weather_code") if isinstance(daily.get("weather_code"), list) else []
    highs = daily.get("temperature_2m_max") if isinstance(daily.get("temperature_2m_max"), list) else []
    lows = daily.get("temperature_2m_min") if isinstance(daily.get("temperature_2m_min"), list) else []
    rain = daily.get("precipitation_probability_max") if isinstance(daily.get("precipitation_probability_max"), list) else []

    forecast: list[dict[str, object]] = []
    for index, date in enumerate(dates[:5]):
        code = codes[index] if index < len(codes) else None
        forecast.append({
            "date": str(date),
            "weather_code": code,
            "condition": _weather_label(code),
            "high_c": _finite(highs[index]) if index < len(highs) else None,
            "low_c": _finite(lows[index]) if index < len(lows) else None,
            "rain_percent": _finite(rain[index]) if index < len(rain) else None,
        })

    weather_code = current.get("weather_code")
    return {
        "ok": True,
        "source": "Open-Meteo",
        "location": os.environ.get("ARIADNE_HOME_LOCATION_LABEL", "Local weather"),
        "timezone": payload.get("timezone"),
        "current": {
            "temperature_c": _finite(current.get("temperature_2m")),
            "feels_like_c": _finite(current.get("apparent_temperature")),
            "humidity_percent": _finite(current.get("relative_humidity_2m")),
            "wind_kmh": _finite(current.get("wind_speed_10m")),
            "weather_code": weather_code,
            "condition": _weather_label(weather_code),
            "observed_at": current.get("time"),
        },
        "forecast": forecast,
    }


def weather_payload(latitude: float | None, longitude: float | None) -> dict[str, object]:
    if latitude is None or longitude is None:
        return {
            "ok": False,
            "source": "Open-Meteo",
            "message": "Allow location access to load local weather.",
        }
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return {"ok": False, "source": "Open-Meteo", "message": "Location coordinates are invalid."}
    cache_key = f"weather:{round(latitude, 2)}:{round(longitude, 2)}"
    try:
        return _cached(cache_key, WEATHER_CACHE_SECONDS, lambda: _weather_payload(latitude, longitude))
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "source": "Open-Meteo", "message": f"Weather source unavailable: {exc}"}


def parse_stooq_history(text: str, *, points: int = 7) -> list[dict[str, object]]:
    rows = csv.DictReader(io.StringIO(text))
    values: list[dict[str, object]] = []
    for row in rows:
        date = str(row.get("Date") or "").strip()
        close = _finite(row.get("Close"))
        if not date or close is None:
            continue
        values.append({"date": date, "close": close})
    values.sort(key=lambda item: str(item["date"]))
    return values[-max(2, points):]


def _stooq_series(symbol: str, *, points: int = 7) -> list[dict[str, object]]:
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=24)
    query = urllib.parse.urlencode({
        "s": symbol,
        "d1": start.strftime("%Y%m%d"),
        "d2": today.strftime("%Y%m%d"),
        "i": "d",
    })
    text = _get_text(f"{STOOQ_HISTORY_URL}?{query}")
    if "<html" in text[:500].casefold() or "exceeded" in text[:500].casefold():
        raise ValueError("Stooq returned an access/rate-limit page instead of CSV.")
    values = parse_stooq_history(text, points=points)
    if len(values) < 2:
        raise ValueError(f"No recent daily data returned for {symbol}.")
    return values


def _series_payload(series_id: str, label: str, symbol: str) -> dict[str, object]:
    try:
        values = _stooq_series(symbol, points=7)
        first = float(values[0]["close"])
        last = float(values[-1]["close"])
        change_percent = ((last / first) - 1.0) * 100 if first else 0.0
        return {
            "ok": True,
            "id": series_id,
            "label": label,
            "symbol": symbol.upper(),
            "source": "Stooq",
            "last": last,
            "change_percent": change_percent,
            "series": values,
        }
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        return {
            "ok": False,
            "id": series_id,
            "label": label,
            "symbol": symbol.upper(),
            "source": "Stooq",
            "message": str(exc),
            "series": [],
        }


def _market_payload() -> dict[str, object]:
    definitions = list(MARKET_SERIES) + list(OIL_SERIES)
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="home-market") as executor:
        results = list(executor.map(lambda definition: _series_payload(*definition), definitions))
    markets = results[:len(MARKET_SERIES)]
    oil = results[len(MARKET_SERIES):]
    return {
        "ok": any(item.get("ok") for item in markets + oil),
        "source": "Stooq",
        "period_label": "Last 7 trading sessions",
        "markets": markets,
        "oil": oil,
    }


def market_payload() -> dict[str, object]:
    return _cached("markets", MARKET_CACHE_SECONDS, _market_payload)


def home_information_payload(latitude: float | None = None, longitude: float | None = None) -> dict[str, object]:
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weather": weather_payload(latitude, longitude),
        "market": market_payload(),
    }
