"""
LIVE SLIM DESK — API credit budget (does NOT change strategy rules)

  CRT : EUR/USD + AUD/JPY
  3C  : USD/JPY + USD/CHF

USD/JPY restored 2026-10-17 onto the 4H streams that logged
9/9 wins in outcomes.json (8–10 Oct cluster):
  4H→15m MSS / PLAIN / CISD
  4H→30m MSS / PLAIN / CISD
plus existing thin 1H→15m MSS / CISD.

USD/CHF research lock kept (WR>=70% cells).
Full crt_plain / 3c_model on disk intact.
"""

from __future__ import annotations

import crt_plain
import importlib

desk = importlib.import_module("3c_model")

CRT_SLIM_PAIRS = ["EUR/USD", "AUD/JPY"]

# Research lock (AUD/JPY) + thin prior (EUR/USD)
CRT_SLIM_STREAMS = {
    # --- AUD/JPY: WR>=70% all W | avg>=+0.9R ---
    "CRT 1H→15m PLAIN":  ("1h", "15min", "PLAIN"),   # +1.087R  WR 85/90/70
    "CRT 1H→15m MODEL1": ("1h", "15min", "MODEL1"),  # +0.998R  WR 87/87/73
    "CRT 4H→1H PLAIN":   ("4h", "1h", "PLAIN"),      # +1.016R  WR 88/71/84
    "CRT 4H→15m MODEL1": ("4h", "15min", "MODEL1"),  # +1.420R  WR 71/88/88
    "CRT 4H→30m PLAIN":  ("4h", "30min", "PLAIN"),   # +1.189R  WR 71/86/80
    # --- EUR/USD thin prior ---
    "CRT 1H→15m CISD":   ("1h", "15min", "CISD"),
    "CRT 1H→5m MODEL1":  ("1h", "5min", "MODEL1"),
}

CRT_SLIM_STREAM_PAIRS = {
    "CRT 1H→15m PLAIN":  ["AUD/JPY"],
    "CRT 1H→15m MODEL1": ["AUD/JPY"],
    "CRT 4H→1H PLAIN":   ["AUD/JPY"],
    "CRT 4H→15m MODEL1": ["AUD/JPY"],
    "CRT 4H→30m PLAIN":  ["AUD/JPY"],
    "CRT 1H→15m CISD":   ["EUR/USD"],
    "CRT 1H→5m MODEL1":  ["EUR/USD"],
}

C3_SLIM_PAIRS = ["USD/JPY", "USD/CHF"]

C3_SLIM_STREAMS = {
    # --- USD/CHF research lock ---
    "3C D→1H PLAIN":     ("1day", "1h", "PLAIN"),
    "3C 4H→1H PLAIN":    ("4h", "1h", "PLAIN"),
    "3C 4H→15m PLAIN":   ("4h", "15min", "PLAIN"),
    "3C 4H→15m MODEL1":  ("4h", "15min", "MODEL1"),
    "3C 4H→15m CISD":    ("4h", "15min", "CISD"),
    # --- USD/JPY restored (live outcomes 9/9) + thin 1H ---
    "3C 4H→15m MSS":     ("4h", "15min", "MSS"),
    "3C 4H→30m":         ("4h", "30min", "PLAIN"),
    "3C 4H→30m MSS":     ("4h", "30min", "MSS"),
    "3C 4H→30m CISD":    ("4h", "30min", "CISD"),
    "3C 1H→15m MSS":     ("1h", "15min", "MSS"),
    "3C 1H→15m CISD":    ("1h", "15min", "CISD"),
}

C3_SLIM_STREAM_PAIRS = {
    # USD/CHF lock
    "3C D→1H PLAIN":     ["USD/CHF"],
    "3C 4H→1H PLAIN":    ["USD/CHF"],
    "3C 4H→15m PLAIN":   ["USD/CHF", "USD/JPY"],  # PLAIN 4H→15m also won on JPY
    "3C 4H→15m MODEL1":  ["USD/CHF"],
    "3C 4H→15m CISD":    ["USD/CHF", "USD/JPY"],
    # USD/JPY restored
    "3C 4H→15m MSS":     ["USD/JPY"],
    "3C 4H→30m":         ["USD/JPY"],
    "3C 4H→30m MSS":     ["USD/JPY"],
    "3C 4H→30m CISD":    ["USD/JPY"],
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
    print("USD/JPY: 4H→15m/30m MSS+CISD+PLAIN restored | CHF research lock")
    print("=" * 70)
    apply_slim()
    state = desk.load_state()
    desk.process_commands(state)
    desk.save_state(state)
    crt_plain.main()
    desk.main()


if __name__ == "__main__":
    main()
