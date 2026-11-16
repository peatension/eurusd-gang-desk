
import unittest

from bet_squad_bot import (
    BOOKMAKER_ID,
    extract_odds,
    market_probabilities,
    parse_selection,
    score_matrix,
)


class ModelTests(unittest.TestCase):

    def test_matrix_sums_to_one(self):
        matrix = score_matrix(1.4, 1.1)
        self.assertAlmostEqual(
            sum(map(sum, matrix)), 1.0, places=8
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
            parse_selection("Match Winner", "1"), "home"
        )
        self.assertEqual(
            parse_selection("Match Winner", "X"), "draw"
        )
        self.assertEqual(
            parse_selection("Match Winner", "Away"), "away"
        )
        self.assertEqual(
            parse_selection("Over/Under", "Over 2.5"),
            "over 2.5",
        )
        self.assertEqual(
            parse_selection("Both Teams Score", "Yes"), "yes"
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
            next(item[2] for item in parsed
                 if item[1] == "home"),
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
            {(market, selection)
             for market, selection, _ in parsed},
            {
                ("over/under", "over 2.5"),
                ("over/under", "under 2.5"),
                ("both teams score", "yes"),
                ("both teams score", "no"),
            },
        )


if __name__ == "__main__":
    unittest.main()
