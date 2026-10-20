"""
LIVE SLIM DESK — 8 pairs from CRT KL+POI research lock

  CRT (4): XAU/USD · EUR/USD · AUD/JPY · GBP/JPY
  3C  (4): USD/JPY · EUR/JPY · USD/CAD · GBP/USD

Entry: PLAIN base on core streams.
Selective MSS/TS only where research showed edge.
Full crt_plain / 3c_model on disk intact — this file only overrides pairs/streams.
"""

from __future__ import annotations

import crt_plain
import importlib

desk = importlib.import_module("3c_model")

# ---------- CRT set (research top 4) ----------
CRT_SLIM_PAIRS = ["XAU/USD", "EUR/USD", "AUD/JPY", "GBP/JPY"]

CRT_SLIM_STREAMS = {
    # PLAIN base — all four pairs
    "CRT 1H→15m PLAIN": ("1h", "15min", "PLAIN"),
    "CRT 1H→5m PLAIN":  ("1h", "5min", "PLAIN"),
    "CRT 4H→30m PLAIN": ("4h", "30min", "PLAIN"),
    "CRT 4H→15m PLAIN": ("4h", "15min", "PLAIN"),
    # Selective MSS on strong 4H cells
    "CRT 4H→15m MSS":   ("4h", "15min", "MSS"),
    "CRT 4H→30m MSS":   ("4h", "30min", "MSS"),
    # Selective TS on 4H where it ranked
    "CRT 4H→15m TS":    ("4h", "15min", "TS"),
}

CRT_SLIM_STREAM_PAIRS = {
    "CRT 1H→15m PLAIN": ["XAU/USD", "EUR/USD", "AUD/JPY", "GBP/JPY"],
    "CRT 1H→5m PLAIN":  ["XAU/USD", "EUR/USD", "AUD/JPY", "GBP/JPY"],
    "CRT 4H→30m PLAIN": ["XAU/USD", "EUR/USD", "AUD/JPY", "GBP/JPY"],
    "CRT 4H→15m PLAIN": ["XAU/USD", "EUR/USD", "AUD/JPY", "GBP/JPY"],
    "CRT 4H→15m MSS":   ["XAU/USD", "EUR/USD"],
    "CRT 4H→30m MSS":   ["XAU/USD", "EUR/USD"],
    "CRT 4H→15m TS":    ["XAU/USD", "EUR/USD", "GBP/JPY"],
}

# ---------- 3C set (next 4 from remaining) ----------
C3_SLIM_PAIRS = ["USD/JPY", "EUR/JPY", "USD/CAD", "GBP/USD"]

C3_SLIM_STREAMS = {
    "3C 1H→15m PLAIN": ("1h", "15min", "PLAIN"),
    "3C 1H→5m PLAIN":  ("1h", "5min", "PLAIN"),
    "3C 4H→30m PLAIN": ("4h", "30min", "PLAIN"),
    "3C 4H→15m PLAIN": ("4h", "15min", "PLAIN"),
    "3C 4H→15m MSS":   ("4h", "15min", "MSS"),
    "3C 4H→30m MSS":   ("4h", "30min", "MSS"),
    "3C 1H→15m MSS":   ("1h", "15min", "MSS"),
}

C3_SLIM_STREAM_PAIRS = {
    "3C 1H→15m PLAIN": ["USD/JPY", "EUR/JPY", "USD/CAD", "GBP/USD"],
    "3C 1H→5m PLAIN":  ["USD/JPY", "EUR/JPY", "USD/CAD", "GBP/USD"],
    "3C 4H→30m PLAIN": ["USD/JPY", "EUR/JPY", "USD/CAD", "GBP/USD"],
    "3C 4H→15m PLAIN": ["USD/JPY", "EUR/JPY", "USD/CAD", "GBP/USD"],
    "3C 4H→15m MSS":   ["USD/JPY", "EUR/JPY"],
    "3C 4H→30m MSS":   ["USD/JPY", "EUR/JPY"],
    "3C 1H→15m MSS":   ["USD/JPY"],
}


def apply_slim():
    crt_plain.CORE_PAIRS = list(CRT_SLIM_PAIRS)
    crt_plain.STREAMS = dict(CRT_SLIM_STREAMS)
    crt_plain.STREAM_PAIRS = dict(CRT_SLIM_STREAM_PAIRS)
    desk.CORE_PAIRS = list(C3_SLIM_PAIRS)
    desk.STREAMS = dict(C3_SLIM_STREAMS)
    desk.STREAM_PAIRS = dict(C3_SLIM_STREAM_PAIRS)


def main():
    print("=" * 70)
    print("TENSION TRADING DESK — LIVE SLIM (8 pairs)")
    print("CRT: XAU/USD EUR/USD AUD/JPY GBP/JPY")
    print("3C : USD/JPY EUR/JPY USD/CAD GBP/USD")
    print("PLAIN base + selective MSS/TS | PDF signal format")
    print("=" * 70)
    apply_slim()
    # Commands at start (fresh updates) — shared bot token
    state = desk.load_state()
    try:
        desk.process_commands(state)
        desk.save_state(state)
    except Exception as e:
        print("process_commands (start) error:", e)
    crt_plain.main()
    desk.main()
    # Commands again at end so replies land same run if user typed mid-scan
    try:
        state2 = desk.load_state()
        desk.process_commands(state2)
        desk.save_state(state2)
    except Exception as e:
        print("process_commands (end) error:", e)


if __name__ == "__main__":
    main()
