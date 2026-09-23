import unittest
from unittest.mock import patch

from home_information import InformationCache, MARKET_SERIES, fetch_markets, parse_weather, parse_yahoo_chart


class HomeInformationTests(unittest.TestCase):
    def test_market_series_are_distinct_homepage_benchmarks(self):
        labels = [series["label"] for series in MARKET_SERIES[:5]]
        self.assertEqual(labels, ["S&P 500", "Dow Jones", "Nasdaq Composite", "Nikkei 225", "ASX All Ords"])
        self.assertNotIn("Nasdaq 100", labels)

    def test_market_parser_keeps_last_seven_valid_sessions_and_calculates_change(self):
        payload = {
            "chart": {
                "result": [{
                    "timestamp": list(range(10)),
                    "indicators": {"quote": [{"close": [None, 100, 101, 102, 103, 104, 105, 106, 107, 108]}]},
                    "meta": {"exchangeTimezoneName": "UTC"},
                }],
            },
        }
        result = parse_yahoo_chart(payload, label="S&P 500", symbol="^GSPC", currency="USD")
        self.assertEqual(len(result["points"]), 7)
        self.assertEqual(result["points"][0]["value"], 102)
        self.assertEqual(result["last"], 108)
        self.assertEqual(result["change_percent"], 5.88)

    def test_market_fetch_preserves_provider_failures_without_fake_values(self):
        def http_json(url):
            if "%5EGSPC" in url:
                return {
                    "chart": {
                        "result": [{
                            "timestamp": list(range(7)),
                            "indicators": {"quote": [{"close": [100, 101, 102, 103, 104, 105, 106]}]},
                            "meta": {"exchangeTimezoneName": "UTC"},
                        }],
                    },
                }
            raise RuntimeError("provider unavailable")

        with patch("home_information.MARKET_SERIES", (home_information_series("^GSPC", "S&P 500"), home_information_series("bad", "Broken"))):
            result = fetch_markets(http_json=http_json)
        self.assertTrue(result["available"])
        self.assertEqual(result["cards"][0]["last"], 106)
        self.assertFalse(result["cards"][1]["available"])
        self.assertNotIn("last", result["cards"][1])

    def test_weather_parser_returns_current_and_five_day_forecast(self):
        result = parse_weather({
            "timezone": "Asia/Bangkok",
            "current": {
                "temperature_2m": 31,
                "apparent_temperature": 35,
                "relative_humidity_2m": 68,
                "weather_code": 2,
                "wind_speed_10m": 8,
            },
            "daily": {
                "time": ["2026-09-23", "2026-09-24", "2026-09-25", "2026-09-26", "2026-09-27", "2026-09-28"],
                "weather_code": [2, 3, 61, 95, 0, 0],
                "temperature_2m_max": [32, 33, 31, 30, 32, 33],
                "temperature_2m_min": [25, 25, 24, 24, 25, 25],
            },
        })
        self.assertTrue(result["available"])
        self.assertEqual(result["current"]["label"], "Partly cloudy")
        self.assertEqual(len(result["forecast"]), 5)
        self.assertEqual(result["forecast"][2]["label"], "Light rain")

    def test_information_payload_exposes_used_location_and_browser_accuracy(self):
        cache = InformationCache(clock=lambda: 100.0)
        with patch("home_information.fetch_weather", return_value={"available": True, "provider": "Open-Meteo"}), \
             patch("home_information.fetch_markets", return_value={"available": True, "cards": [], "oil": []}):
            result = cache.payload(13.7564, 100.5018, 42.7)
        self.assertEqual(result["weather"]["location"], {"latitude": 13.756, "longitude": 100.502})
        self.assertEqual(result["weather"]["accuracy_m"], 42.7)

    def test_cache_returns_cached_market_payload(self):
        clock = [100.0]
        cache = InformationCache(clock=lambda: clock[0])
        with patch("home_information.fetch_markets", return_value={"available": True, "cards": [], "oil": []}) as fetch:
            first = cache.markets()
            second = cache.markets()
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        fetch.assert_called_once()


def home_information_series(symbol, label):
    return {"id": symbol, "label": label, "symbol": symbol, "currency": "USD"}


if __name__ == "__main__":
    unittest.main()
