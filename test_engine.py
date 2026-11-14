import unittest
from bet_squad_bot import score_matrix, market_probabilities, extract_odds, parse_selection

class ModelTests(unittest.TestCase):
    def test_matrix_sums_to_one(self):
        m = score_matrix(1.4, 1.1)
        self.assertAlmostEqual(sum(map(sum, m)), 1.0, places=8)

    def test_outcomes_sum_to_one(self):
        p = market_probabilities(score_matrix(1.3, 0.9))
        self.assertAlmostEqual(sum(p["1x2"].values()), 1.0, places=8)
        self.assertAlmostEqual(sum(p["btts"].values()), 1.0, places=8)

    def test_only_configured_bookmaker_odds_are_parsed(self):
        payload = [{"bookmakers": [
            {"id": 7, "bets": [{"name":"Match Winner","values":[
                {"value":"Home","odd":"2.10"}, {"value":"Draw","odd":"3.2"}]}]},
            {"id": 23, "bets": [{"name":"Match Winner","values":[
                {"value":"Home","odd":"9.99"}]}]}
        ]}]
        got = extract_odds(payload)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0][2], 2.10)

    def test_known_market_alias(self):
        self.assertEqual(parse_selection("1"), ("match winner", "home"))
        self.assertIsNone(parse_selection("correct score 1:0"))

if __name__ == "__main__":
    unittest.main()
