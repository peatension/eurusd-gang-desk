"""
LIVE SLIM DESK — API credit budget (does NOT change strategy rules)

  CRT : EUR/USD + AUD/JPY
  3C  : USD/JPY + USD/CHF

Locked 2026-10-15 from AUD/JPY + USD/CHF HTF focus Colab:
  - 30MIN HTF removed
  - USD/CHF bottom-6 dropped
  - WR >= 60% on W1 AND W2 AND W3
  - Avg Exp >= +1.0R
  - OCL / QML / OCL+MSS omitted (not in live ENTRY_FN yet)

EUR/USD + USD/JPY keep thin prior streams so they stay active.
Full crt_plain / 3c_model on disk intact.
"""

from __future__ import annotations

import crt_plain
import importlib

desk = importlib.import_module("3c_model")

CRT_SLIM_PAIRS = ["EUR/USD", "AUD/JPY"]

# Research lock (AUD/JPY) + thin prior (EUR/USD)
CRT_SLIM_STREAMS = {
    # --- AUD/JPY shortlist (WR>=60% all W, avg>=+1.0R) ---
    "CRT 1H→15m PLAIN":  ("1h", "15min", "PLAIN"),
    "CRT 4H→1H PLAIN":   ("4h", "1h", "PLAIN"),
    "CRT 4H→15m PLAIN":  ("4h", "15min", "PLAIN"),
    "CRT 4H→15m MODEL1": ("4h", "15min", "MODEL1"),
    "CRT 4H→30m PLAIN":  ("4h", "30min", "PLAIN"),
    "CRT W→4H PLAIN":    ("1week", "4h", "PLAIN"),
    # --- EUR/USD thin prior (not in this dual-pair grid) ---
    "CRT 1H→15m CISD":   ("1h", "15min", "CISD"),
    "CRT 1H→5m MODEL1":  ("1h", "5min", "MODEL1"),
}

CRT_SLIM_STREAM_PAIRS = {
    "CRT 1H→15m PLAIN":  ["AUD/JPY"],
    "CRT 4H→1H PLAIN":   ["AUD/JPY"],
    "CRT 4H→15m PLAIN":  ["AUD/JPY"],
    "CRT 4H→15m MODEL1": ["AUD/JPY"],
    "CRT 4H→30m PLAIN":  ["AUD/JPY"],
    "CRT W→4H PLAIN":    ["AUD/JPY"],
    "CRT 1H→15m CISD":   ["EUR/USD"],
    "CRT 1H→5m MODEL1":  ["EUR/USD"],
}

C3_SLIM_PAIRS = ["USD/JPY", "USD/CHF"]

# Research lock (USD/CHF) + thin prior (USD/JPY)
C3_SLIM_STREAMS = {
    # --- USD/CHF shortlist (WR>=60% all W, avg>=+1.0R) ---
    "3C MN→W PLAIN":     ("1month", "1week", "PLAIN"),
    "3C D→4H MODEL1":    ("1day", "4h", "MODEL1"),
    "3C D→1H PLAIN":     ("1day", "1h", "PLAIN"),
    "3C 4H→1H PLAIN":    ("4h", "1h", "PLAIN"),
    "3C 4H→15m PLAIN":   ("4h", "15min", "PLAIN"),
    "3C 4H→15m MODEL1":  ("4h", "15min", "MODEL1"),
    "3C 4H→15m CISD":    ("4h", "15min", "CISD"),
    "3C 4H→30m PLAIN":   ("4h", "30min", "PLAIN"),
    # --- USD/JPY thin prior ---
    "3C 1H→15m MSS":     ("1h", "15min", "MSS"),
    "3C 1H→15m CISD":    ("1h", "15min", "CISD"),
}

C3_SLIM_STREAM_PAIRS = {
    "3C MN→W PLAIN":     ["USD/CHF"],
    "3C D→4H MODEL1":    ["USD/CHF"],
    "3C D→1H PLAIN":     ["USD/CHF"],
    "3C 4H→1H PLAIN":    ["USD/CHF"],
    "3C 4H→15m PLAIN":   ["USD/CHF"],
    "3C 4H→15m MODEL1":  ["USD/CHF"],
    "3C 4H→15m CISD":    ["USD/CHF"],
    "3C 4H→30m PLAIN":   ["USD/CHF"],
    "3C 1H→15m MSS":     ["USD/JPY"],
    "3C 1H→15m CISD":    ["USD/JPY"],
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
    print("TENSION TRADING DESK — LIVE SLIM")
    print("CRT: EUR/USD + AUD/JPY | 3C: USD/JPY + USD/CHF")
    print("Lock: WR>=60% all W | avg>=+1.0R | 30MIN HTF out | no OCL/QML")
    print("=" * 70)
    apply_slim()
    state = desk.load_state()
    desk.process_commands(state)
    desk.save_state(state)
    crt_plain.main()
    desk.main()


if __name__ == "__main__":
    main()
