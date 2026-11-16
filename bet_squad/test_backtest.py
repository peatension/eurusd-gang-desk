import unittest
from datetime import datetime, timezone

from backtest import evaluate_rows, form_rates, parse_kickoff


def fixture(fid, kickoff, home_id, away_id, hg, ag, status="FT"):
    return {
        "fixture": {"id": fid, "date": kickoff,
                    "status": {"short": status}},
        "teams": {"home": {"id": home_id, "name": f"Team {home_id}"},
                  "away": {"id": away_id, "name": f"Team {away_id}"}},
        "goals": {"home": hg, "away": ag},
        "league": {"name": "Test League"},
    }


class BacktestTests(unittest.TestCase):
    def test_kickoff_parses_utc(self):
        parsed = parse_kickoff(fixture(1, "2026-01-01T12:00:00Z", 1, 2, 1, 0))
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_form_requires_five_matches(self):
        from collections import deque
        cutoff = datetime(2026, 2, 1, tzinfo=timezone.utc)
        history = deque((cutoff.replace(day=i), 1, 0) for i in range(1, 5))
        self.assertIsNone(form_rates(history, cutoff))

    def test_same_kickoff_results_do_not_leak(self):
        rows = []
        # Five prior completed matches for each team, spread over prior days.
        for i in range(5):
            day = i + 1
            rows.append(fixture(i + 1, f"2026-01-{day:02d}T10:00:00Z", 1, 2, 2, 0))
            rows.append(fixture(i + 11, f"2026-01-{day:02d}T10:00:00Z", 3, 4, 0, 1))
        # Both target matches share kickoff. Neither target result can enter the other's form.
        rows.append(fixture(30, "2026-01-10T15:00:00Z", 1, 3, 3, 2))
        rows.append(fixture(31, "2026-01-10T15:00:00Z", 2, 4, 0, 0))
        start = datetime(2026, 1, 10, tzinfo=timezone.utc)
        end = datetime(2026, 1, 10, 23, 59, tzinfo=timezone.utc)
        predictions, summary = evaluate_rows(rows, start, end)
        self.assertEqual(len(predictions), 2)
        self.assertEqual(summary["fixtures_with_sufficient_prior_form"], 2)
        self.assertEqual(predictions[0]["home_form_matches"], 5)
        self.assertEqual(predictions[1]["home_form_matches"], 5)

    def test_no_prediction_without_sufficient_form(self):
        rows = [fixture(1, "2026-01-10T15:00:00Z", 1, 2, 1, 0)]
        start = datetime(2026, 1, 10, tzinfo=timezone.utc)
        end = datetime(2026, 1, 10, 23, 59, tzinfo=timezone.utc)
        predictions, summary = evaluate_rows(rows, start, end)
        self.assertEqual(predictions, [])
        self.assertEqual(summary["metrics"]["1x2"]["n"], 0)


if __name__ == "__main__":
    unittest.main()
