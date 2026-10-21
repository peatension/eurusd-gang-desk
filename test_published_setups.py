import ast
import os
import tempfile
import unittest
from pathlib import Path

import published_setups
import importlib
import pandas as pd
from unittest.mock import patch


class PublishedSetupsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_file = published_setups.LEDGER_FILE
        published_setups.LEDGER_FILE = str(Path(self.tmp.name) / "published_setups.json")

    def tearDown(self):
        published_setups.LEDGER_FILE = self.old_file
        self.tmp.cleanup()

    def test_same_setup_consecutive_runs_suppressed(self):
        first = published_setups.claim(
            "XAU/USD", 1, "2026-10-21T12:15:00Z", 4321.25, 0.01,
            engine="CRT_PLAIN", stream="CRT 1H→15m PLAIN",
        )
        second = published_setups.claim(
            "XAU/USD", 1, "2026-10-21T12:15:44Z", 4321.25, 0.01,
            engine="CRT_PLAIN", stream="CRT 4H→15m MSS",
        )
        self.assertTrue(first[0])
        self.assertFalse(second[0])
        self.assertEqual(first[1], second[1])

    def test_cross_stream_identity_is_shared(self):
        first = published_setups.claim(
            "EUR/JPY", -1, "2026-10-21 08:31:01", 176.123, 0.01,
            engine="CRT_PLAIN", stream="CRT stream",
        )
        second = published_setups.claim(
            "EUR/JPY", -1, "2026-10-21 08:31:59", 176.123, 0.01,
            engine="3C", stream="3C stream",
        )
        self.assertTrue(first[0])
        self.assertFalse(second[0])

    def test_new_c2_allowed(self):
        published_setups.claim("USD/JPY", 1, "2026-10-21 08:31:00", 151.123, 0.01)
        new_c2 = published_setups.claim("USD/JPY", 1, "2026-10-21 08:46:00", 151.123, 0.01)
        self.assertTrue(new_c2[0])

    def test_distinct_entry_allowed(self):
        published_setups.claim("AUD/JPY", 1, "2026-10-21 08:31:00", 101.123, 0.01)
        distinct_entry = published_setups.claim("AUD/JPY", 1, "2026-10-21 08:31:00", 101.130, 0.01)
        self.assertTrue(distinct_entry[0])

    def test_ledger_survives_module_reload(self):
        first = published_setups.claim("XAU/USD", -1, "2026-10-21 10:01", 4300.0, 0.01)
        self.assertTrue(first[0])
        # A second claim re-reads the on-disk JSON, like a fresh cron process.
        self.assertFalse(published_setups.claim("XAU/USD", -1, "2026-10-21 10:01", 4300.0, 0.01)[0])


class SourcePlumbingTests(unittest.TestCase):

    def test_pending_tp1_edits_original_message_without_telegram(self):
        desk = importlib.import_module("3c_model")
        trade = {
            "stream": "3C 1H→15m PLAIN", "pair": "USD/JPY", "ticket_id": "TTD-TEST",
            "message_id": 456, "message_ids": {"123": 456}, "edit_chat_id": "123",
            "direction": 1, "side": "BUY", "entry": 100.0, "stop": 98.0,
            "tp1": 102.0, "tp2": 104.0, "rr1": 1.0, "rr2": 2.0,
            "risk_pips": 20.0, "tp1_pips": 20.0, "tp2_pips": 40.0,
            "crh": 104.0, "crl": 98.0, "mid": 102.0,
            "candle_time": "2026-10-21 10:00:00", "c2_time": "2026-10-21 10:00:00",
            "session": "London", "ltf": "15min", "entry_model": "PLAIN",
            "status": "ACTIVE", "tp1_hit": False, "last_checked_time": "2026-10-21 10:00:00",
        }
        frame = pd.DataFrame([
            {"datetime": "2026-10-21 10:00:00", "high": 101.0, "low": 99.0},
            {"datetime": "2026-10-21 10:15:00", "high": 103.0, "low": 99.0},
        ])
        edits = []
        with patch.object(desk, "save_state"), patch.object(
            desk, "edit_telegram", side_effect=lambda mid, text, chat, pair=None: edits.append((mid, chat, text)) or True
        ):
            state = {"pending": [trade]}
            desk.check_pending(state, "USD/JPY", frame, "15min")
        self.assertTrue(edits, "TP1 should edit the stored original message")
        self.assertEqual(edits[0][0:2], (456, "123"))
        self.assertIn("TP1 Hit", edits[0][2])
        self.assertEqual(state["pending"][0]["status"], "TP1_HIT")
        self.assertTrue(state["pending"][0]["tp1_hit"])

    def test_stream_pair_overrides_contain_no_extra_pairs(self):
        source = Path(__file__).resolve().parents[1] / "live_slim.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        values = {}
        wanted = {"CRT_SLIM_PAIRS", "C3_SLIM_PAIRS", "CRT_SLIM_STREAM_PAIRS", "C3_SLIM_STREAM_PAIRS"}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name in wanted:
                    values[name] = ast.literal_eval(node.value)
        crt = set(values["CRT_SLIM_PAIRS"])
        c3 = set(values["C3_SLIM_PAIRS"])
        self.assertTrue(all(set(pairs) <= crt for pairs in values["CRT_SLIM_STREAM_PAIRS"].values()))
        self.assertTrue(all(set(pairs) <= c3 for pairs in values["C3_SLIM_STREAM_PAIRS"].values()))

    def test_no_eight_second_sleep_in_successful_fetch_branch(self):
        source = Path(__file__).resolve().parents[1] / "3c_model.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        fetch = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "fetch")
        values_branch = next(
            n for n in ast.walk(fetch)
            if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
            and isinstance(n.test.left, ast.Constant) and n.test.left.value == "values"
        )
        calls = [n for n in ast.walk(values_branch) if isinstance(n, ast.Call)]
        sleeps = []
        for call in calls:
            if isinstance(call.func, ast.Attribute) and call.func.attr == "sleep":
                if len(call.args) == 1 and isinstance(call.args[0], ast.Constant) and call.args[0].value == 8:
                    sleeps.append(call)
        self.assertEqual(sleeps, [])

    def test_live_slim_is_exactly_four_pairs(self):
        source = Path(__file__).resolve().parents[1] / "live_slim.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name in {"CRT_SLIM_PAIRS", "C3_SLIM_PAIRS"}:
                    values[name] = ast.literal_eval(node.value)
        self.assertEqual(values["CRT_SLIM_PAIRS"], ["XAU/USD", "AUD/JPY"])
        self.assertEqual(values["C3_SLIM_PAIRS"], ["USD/JPY", "EUR/JPY"])


if __name__ == "__main__":
    unittest.main()
