"""Provider-backed information used by the Ariadne Home sidebar.

The module intentionally keeps the provider boundary small. Home receives
normalized weather and market records, while the provider and cache can be
replaced without changing the page contract.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import ssl
import threading
import time
from typing import Any, Callable
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


MARKET_CACHE_SECONDS = 15 * 60
WEATHER_CACHE_SECONDS = 10 * 60
MARKET_LOOKBACK_SECONDS = 35 * 24 * 60 * 60
MARKET_POINT_COUNT = 7

MARKET_SERIES: tuple[dict[str, str], ...] = (
    {"id": "sp500", "label": "S&P 500", "symbol": "^GSPC", "currency": "USD"},
    {"id": "dow", "label": "Dow Jones", "symbol": "^DJI", "currency": "USD"},
    {"id": "nasdaq", "label": "Nasdaq Composite", "symbol": "^IXIC", "currency": "USD"},
    {"id": "nikkei225", "label": "Nikkei 225", "symbol": "^N225", "currency": "JPY"},
    {"id": "all_ordinaries", "label": "ASX All Ords", "symbol": "^AORD", "currency": "AUD"},
    {"id": "wti", "label": "WTI crude", "symbol": "CL=F", "currency": "USD"},
    {"id": "brent", "label": "Brent crude", "symbol": "BZ=F", "currency": "USD"},
)

WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Rain showers",
    82: "Heavy showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _http_json(url: str, timeout: float = 12.0) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "Ariadne Home/0.1"})
    context = ssl._create_unverified_context()
    with urlopen(request, timeout=timeout, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Information provider returned an invalid object.")
    return payload


def _unavailable(message: str) -> dict[str, Any]:
    return {"available": False, "message": message}


def parse_yahoo_chart(payload: dict[str, Any], *, label: str, symbol: str, currency: str) -> dict[str, Any]:
    """Normalize a Yahoo chart response without inventing missing points."""
    chart = payload.get("chart") if isinstance(payload, dict) else None
    result = chart.get("result") if isinstance(chart, dict) else None
    if not isinstance(result, list) or not result or not isinstance(result[0], dict):
        raise ValueError("Market provider returned no chart result.")
    result_item = result[0]
    timestamps = result_item.get("timestamp")
    quote_data = result_item.get("indicators", {}).get("quote", [])
    closes = quote_data[0].get("close") if quote_data and isinstance(quote_data[0], dict) else None
    if not isinstance(timestamps, list) or not isinstance(closes, list):
        raise ValueError("Market provider returned no daily closes.")

    timezone_name = str(result_item.get("meta", {}).get("exchangeTimezoneName") or "UTC")
    try:
        zone = ZoneInfo(timezone_name)
    except (KeyError, ValueError):
        zone = timezone.utc
    points: list[dict[str, Any]] = []
    for timestamp, close in zip(timestamps, closes):
        value = _finite(close)
        if value is None:
            continue
        try:
            date = datetime.fromtimestamp(int(timestamp), timezone.utc).astimezone(zone).date().isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        points.append({"date": date, "value": round(value, 4)})
    points = points[-MARKET_POINT_COUNT:]
    if len(points) < 2:
        raise ValueError("Market provider returned fewer than two usable sessions.")
    first = points[0]["value"]
    last = points[-1]["value"]
    change = ((last - first) / first * 100) if first else 0.0
    return {
        "available": True,
        "label": label,
        "symbol": symbol,
        "currency": currency,
        "points": points,
        "last": last,
        "change_percent": round(change, 2),
    }


def _market_url(symbol: str, now: float | None = None) -> str:
    end = int(now or time.time()) + 86_400
    start = end - MARKET_LOOKBACK_SECONDS
    query = urlencode({"period1": start, "period2": end, "interval": "1d", "events": "history"})
    return "https://query1.finance.yahoo.com/v8/finance/chart/" + quote(symbol, safe="") + "?" + query


def fetch_market_series(series: dict[str, str], *, http_json: Callable[[str], dict[str, Any]] = _http_json) -> dict[str, Any]:
    try:
        return parse_yahoo_chart(
            http_json(_market_url(series["symbol"])),
            label=series["label"],
            symbol=series["symbol"],
            currency=series["currency"],
        )
    except Exception as exc:  # A single provider failure must not hide the other cards.
        return {"available": False, "label": series["label"], "symbol": series["symbol"], "message": str(exc)[:180]}


def fetch_markets(*, http_json: Callable[[str], dict[str, Any]] = _http_json) -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=len(MARKET_SERIES), thread_name_prefix="ariadne-market") as executor:
        cards = list(executor.map(lambda series: fetch_market_series(series, http_json=http_json), MARKET_SERIES))
    available = [card for card in cards if card.get("available")]
    return {
        "available": bool(available),
        "provider": "Yahoo Finance chart data",
        "cards": cards[:5],
        "oil": cards[5:],
        "message": "" if available else "Market data is unavailable right now.",
    }


def parse_weather(payload: dict[str, Any]) -> dict[str, Any]:
    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    daily = payload.get("daily") if isinstance(payload.get("daily"), dict) else {}
    temperature = _finite(current.get("temperature_2m"))
    if temperature is None:
        raise ValueError("Weather provider returned no current temperature.")
    dates = daily.get("time") if isinstance(daily.get("time"), list) else []
    codes = daily.get("weather_code") if isinstance(daily.get("weather_code"), list) else []
    highs = daily.get("temperature_2m_max") if isinstance(daily.get("temperature_2m_max"), list) else []
    lows = daily.get("temperature_2m_min") if isinstance(daily.get("temperature_2m_min"), list) else []
    forecast = []
    for date, code, high, low in zip(dates[:5], codes[:5], highs[:5], lows[:5]):
        forecast.append({
            "date": str(date),
            "label": WEATHER_CODES.get(int(code), "Conditions unavailable") if _finite(code) is not None else "Conditions unavailable",
            "high": _finite(high),
            "low": _finite(low),
        })
    return {
        "available": True,
        "provider": "Open-Meteo",
        "timezone": str(payload.get("timezone") or ""),
        "current": {
            "temperature": temperature,
            "apparent_temperature": _finite(current.get("apparent_temperature")),
            "humidity": _finite(current.get("relative_humidity_2m")),
            "wind_speed": _finite(current.get("wind_speed_10m")),
            "label": WEATHER_CODES.get(int(current.get("weather_code")), "Conditions unavailable") if _finite(current.get("weather_code")) is not None else "Conditions unavailable",
        },
        "forecast": forecast,
    }


def _weather_url(latitude: float, longitude: float) -> str:
    query = urlencode({
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "forecast_days": 5,
        "timezone": "auto",
    })
    return "https://api.open-meteo.com/v1/forecast?" + query


def fetch_weather(latitude: float, longitude: float, *, http_json: Callable[[str], dict[str, Any]] = _http_json) -> dict[str, Any]:
    try:
        return parse_weather(http_json(_weather_url(latitude, longitude)))
    except Exception as exc:
        return _unavailable("Weather is unavailable right now: " + str(exc)[:140])


class InformationCache:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._markets: tuple[float, dict[str, Any]] | None = None
        self._weather: dict[tuple[float, float], tuple[float, dict[str, Any]]] = {}

    def markets(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._markets and not force and self._clock() - self._markets[0] < MARKET_CACHE_SECONDS:
                return {**self._markets[1], "cached": True}
        result = fetch_markets()
        with self._lock:
            self._markets = (self._clock(), result)
        return {**result, "cached": False}

    def weather(self, latitude: float, longitude: float, *, force: bool = False) -> dict[str, Any]:
        key = (round(latitude, 3), round(longitude, 3))
        with self._lock:
            cached = self._weather.get(key)
            if cached and not force and self._clock() - cached[0] < WEATHER_CACHE_SECONDS:
                return {**cached[1], "cached": True}
        result = fetch_weather(*key)
        with self._lock:
            self._weather[key] = (self._clock(), result)
        return {**result, "cached": False}

    def payload(self, latitude: float | None = None, longitude: float | None = None, *, force: bool = False) -> dict[str, Any]:
        weather = _unavailable("Allow local browser location access to load weather.")
        if latitude is not None and longitude is not None:
            weather = self.weather(latitude, longitude, force=force)
        return {"ok": True, "weather": weather, "markets": self.markets(force=force), "updated_at": datetime.now(timezone.utc).isoformat()}


INFORMATION_CACHE = InformationCache()


def home_information_payload(latitude: str | None = None, longitude: str | None = None, *, force: bool = False) -> dict[str, Any]:
    def parse_coordinate(value: str | None, minimum: float, maximum: float) -> float | None:
        try:
            coordinate = float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
        return coordinate if coordinate is not None and minimum <= coordinate <= maximum else None

    lat = parse_coordinate(latitude, -90, 90)
    lon = parse_coordinate(longitude, -180, 180)
    return INFORMATION_CACHE.payload(lat, lon, force=force)
