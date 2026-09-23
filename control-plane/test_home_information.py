from __future__ import annotations

import unittest
from unittest.mock import patch

import home_information


class HomeInformationTests(unittest.TestCase):
    def test_parse_stooq_history_returns_recent_sorted_points(self):
        csv_text = """Date,Open,High,Low,Close,Volume
2026-09-18,10,11,9,10.5,1
2026-09-16,8,9,7,8.5,1
2026-09-17,9,10,8,9.5,1
2026-09-21,11,12,10,11.5,1
"""
        values = home_information.parse_stooq_history(csv_text, points=3)
        self.assertEqual(
            values,
            [
                {"date": "2026-09-17", "close": 9.5},
                {"date": "2026-09-18", "close": 10.5},
                {"date": "2026-09-21", "close": 11.5},
            ],
        )

    def test_parse_stooq_history_ignores_missing_close(self):
        csv_text = """Date,Open,High,Low,Close,Volume
2026-09-18,10,11,9,N/D,1
2026-09-21,11,12,10,11.5,1
2026-09-22,12,13,11,12.5,1
"""
        values = home_information.parse_stooq_history(csv_text)
        self.assertEqual(len(values), 2)
        self.assertEqual(values[-1]["close"], 12.5)

    def test_series_payload_calculates_display_period_change(self):
        points = [
            {"date": "2026-09-16", "close": 100.0},
            {"date": "2026-09-17", "close": 102.0},
            {"date": "2026-09-18", "close": 105.0},
        ]
        with patch("home_information._stooq_series", return_value=points):
            payload = home_information._series_payload("test", "Test", "^test")
        self.assertTrue(payload["ok"])
        self.assertAlmostEqual(payload["change_percent"], 5.0)
        self.assertEqual(payload["last"], 105.0)

    def test_weather_rejects_invalid_coordinates_without_network(self):
        payload = home_information.weather_payload(120.0, 20.0)
        self.assertFalse(payload["ok"])
        self.assertIn("invalid", payload["message"].casefold())

    def test_sidebar_series_are_five_indices_plus_two_oil_benchmarks(self):
        self.assertEqual(len(home_information.MARKET_SERIES), 5)
        self.assertEqual([item[1] for item in home_information.OIL_SERIES], ["WTI", "Brent"])


if __name__ == "__main__":
    unittest.main()
