import unittest
from unittest.mock import Mock, patch

import requests

from bet_squad_bot import (
    ApiFootball,
    BOOKMAKER_ID,
    PlanAccessError,
    RateLimitError,
    extract_odds,
    market_probabilities,
    parse_selection,
    score_matrix,
)


class ModelTests(unittest.TestCase):

    def test_matrix_sums_to_one(self):
        matrix = score_matrix(1.4, 1.1)

        self.assertAlmostEqual(
            sum(map(sum, matrix)),
            1.0,
            places=8,
        )

    def test_market_outcomes_sum_to_one(self):
        probabilities = market_probabilities(
            score_matrix(1.3, 0.9)
        )

        for market in ("1x2", "over/under", "btts"):
            self.assertAlmostEqual(
                sum(probabilities[market].values()),
                1.0,
                places=8,
            )

    def test_selection_aliases(self):
        self.assertEqual(
            parse_selection("Match Winner", "1"),
            "home",
        )

        self.assertEqual(
            parse_selection("Match Winner", "X"),
            "draw",
        )

        self.assertEqual(
            parse_selection("Match Winner", "Away"),
            "away",
        )

        self.assertEqual(
            parse_selection("Over/Under", "Over 2.5"),
            "over 2.5",
        )

        self.assertEqual(
            parse_selection("Both Teams Score", "Yes"),
            "yes",
        )

        self.assertIsNone(
            parse_selection("Correct Score", "1:0")
        )

    def test_only_configured_bookmaker_is_parsed(self):

        payload = [{
            "bookmakers": [
                {
                    "id": BOOKMAKER_ID,
                    "bets": [{
                        "name": "Match Winner",
                        "values": [
                            {"value": "Home", "odd": "2.10"},
                            {"value": "Draw", "odd": "3.20"},
                            {"value": "Away", "odd": "3.60"},
                        ],
                    }],
                },
                {
                    "id": BOOKMAKER_ID + 1000,
                    "bets": [{
                        "name": "Match Winner",
                        "values": [
                            {"value": "Home", "odd": "9.99"},
                            {"value": "Draw", "odd": "9.99"},
                            {"value": "Away", "odd": "9.99"},
                        ],
                    }],
                },
            ]
        }]

        parsed = extract_odds(payload)

        self.assertEqual(len(parsed), 3)

        self.assertEqual(
            {item[1] for item in parsed},
            {"home", "draw", "away"},
        )

        self.assertAlmostEqual(
            next(
                item[2]
                for item in parsed
                if item[1] == "home"
            ),
            2.10,
        )

    def test_incomplete_markets_are_rejected(self):

        payload = [{
            "bookmakers": [{
                "id": BOOKMAKER_ID,
                "bets": [{
                    "name": "Match Winner",
                    "values": [
                        {"value": "Home", "odd": "2.10"},
                        {"value": "Draw", "odd": "3.20"},
                    ],
                }],
            }]
        }]

        self.assertEqual(extract_odds(payload), [])

    def test_complete_totals_and_btts_markets(self):

        payload = [{
            "bookmakers": [{
                "id": BOOKMAKER_ID,
                "bets": [
                    {
                        "name": "Over/Under",
                        "values": [
                            {"value": "Over 2.5", "odd": "1.90"},
                            {"value": "Under 2.5", "odd": "1.95"},
                        ],
                    },
                    {
                        "name": "Both Teams Score",
                        "values": [
                            {"value": "Yes", "odd": "1.80"},
                            {"value": "No", "odd": "2.00"},
                        ],
                    },
                ],
            }]
        }]

        parsed = extract_odds(payload)

        self.assertEqual(len(parsed), 4)

        self.assertEqual(
            {
                (market, selection)
                for market, selection, _ in parsed
            },
            {
                ("over/under", "over 2.5"),
                ("over/under", "under 2.5"),
                ("both teams score", "yes"),
                ("both teams score", "no"),
            },
        )


class ApiFootballTests(unittest.TestCase):

    def setUp(self):
        self.api = ApiFootball("test-api-key")

    @patch("bet_squad_bot.time.sleep")
    @patch("bet_squad_bot.time.monotonic", return_value=100.0)
    def test_team_history_includes_season(
        self,
        mock_time,
        mock_sleep,
    ):

        self.api.get = Mock(return_value=[])

        self.api.recent_team_fixtures(
            team_id=123,
            before_date="2026-11-16",
            season=2026,
            limit=10,
        )

        self.api.get.assert_called_once()

        args, kwargs = self.api.get.call_args

        self.assertEqual(args[0], "fixtures")
        self.assertEqual(kwargs["team"], 123)
        self.assertEqual(kwargs["season"], 2026)
        self.assertIn("from", kwargs)
        self.assertIn("to", kwargs)

    @patch("bet_squad_bot.time.sleep")
    def test_free_plan_season_error_is_classified(
        self,
        mock_sleep,
    ):

        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None

        response.json.return_value = {
            "errors": {
                "plan": (
                    "Free plans do not have access to this season, "
                    "try from 2022 to 2024."
                )
            },
            "response": [],
        }

        self.api.session.get = Mock(return_value=response)

        with self.assertRaises(PlanAccessError):
            self.api.get(
                "fixtures",
                team=123,
                season=2026,
            )

    @patch("bet_squad_bot.time.sleep")
    def test_http_429_stops_future_requests(
        self,
        mock_sleep,
    ):

        response = Mock()
        response.status_code = 429
        response.headers = {"Retry-After": "60"}

        self.api.session.get = Mock(return_value=response)

        with self.assertRaises(RateLimitError):
            self.api.get("fixtures", date="2026-11-16")

        self.assertTrue(self.api._rate_limited)

        # A second request must stop locally.
        with self.assertRaises(RateLimitError):
            self.api.get("fixtures", date="2026-11-17")

        # Only the first request reached the provider.
        self.assertEqual(
            self.api.session.get.call_count,
            1,
        )

    @patch("bet_squad_bot.time.sleep")
    def test_successful_request_is_cached(
        self,
        mock_sleep,
    ):

        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None

        response.json.return_value = {
            "errors": [],
            "response": [{"fixture": {"id": 123}}],
        }

        self.api.session.get = Mock(return_value=response)

        first = self.api.get(
            "fixtures",
            date="2026-11-16",
        )

        second = self.api.get(
            "fixtures",
            date="2026-11-16",
        )

        self.assertEqual(first, second)

        self.assertEqual(
            self.api.session.get.call_count,
            1,
        )

    def test_missing_season_is_rejected(self):

        with self.assertRaises(Exception):
            self.api.recent_team_fixtures(
                team_id=123,
                before_date="2026-11-16",
                season=None,
            )


if __name__ == "__main__":
    unittest.main()
