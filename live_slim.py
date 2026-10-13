"""
LIVE SLIM DESK — API credit budget (does NOT change strategy rules)

Keeps crt_plain.py + 3c_model.py FULL (progress intact).
At runtime only these pairs are scanned:

  CRT side : EUR/USD  (best live CRT net) + XAU/USD (gold lock)
  3C side  : USD/JPY  (best live 3C)     + USD/CAD (2nd)

Paused (still in full engines for later):
  AUD/USD USD/CHF EUR/JPY GBP/USD GBP/JPY

desk_runner.py → this file only.
To resume full desk: point desk_runner back at crt_plain.main + 3c_model.main.
"""

from __future__ import annotations

import crt_plain
import importlib

desk = importlib.import_module("3c_model")

# --- CRT slim map (only streams that apply to EUR and/or XAU) ---
CRT_SLIM_PAIRS = ["EUR/USD", "XAU/USD"]
CRT_SLIM_STREAMS = {
    "CRT 1H→15m MSS":    ("1h", "15min", "MSS"),
    "CRT 1H→15m CISD":   ("1h", "15min", "CISD"),
    "CRT 1H→5m MODEL1":  ("1h", "5min", "MODEL1"),
    "CRT 4H→30m":        ("4h", "30min", "PLAIN"),
    "CRT 4H→30m MSS":    ("4h", "30min", "MSS"),
    "CRT 4H→15m CISD":   ("4h", "15min", "CISD"),
    "CRT 1H→15m FVG":    ("1h", "15min", "FVG"),
    "CRT 1H→5m CISD":    ("1h", "5min", "CISD"),
    "CRT 4H→15m MSS":    ("4h", "15min", "MSS"),
    "CRT 4H→15m FVG":    ("4h", "15min", "FVG"),
    "CRT 30MIN→5m CISD": ("30min", "5min", "CISD"),
}
CRT_SLIM_STREAM_PAIRS = {
    "CRT 1H→15m MSS":    ["EUR/USD", "XAU/USD"],
    "CRT 1H→15m CISD":   ["EUR/USD"],
    "CRT 1H→5m MODEL1":  ["EUR/USD"],
    "CRT 4H→30m":        ["EUR/USD"],
    "CRT 4H→30m MSS":    ["EUR/USD"],
    "CRT 4H→15m CISD":   ["EUR/USD"],
    "CRT 1H→15m FVG":    ["XAU/USD"],
    "CRT 1H→5m CISD":    ["XAU/USD"],
    "CRT 4H→15m MSS":    ["XAU/USD"],
    "CRT 4H→15m FVG":    ["XAU/USD"],
    "CRT 30MIN→5m CISD": ["XAU/USD"],
}

# --- 3C slim map ---
C3_SLIM_PAIRS = ["USD/JPY", "USD/CAD"]
C3_SLIM_STREAMS = {
    "3C 1H→15m MSS":   ("1h", "15min", "MSS"),
    "3C 1H→15m CISD":  ("1h", "15min", "CISD"),
    "3C 1H→5m MSS":    ("1h", "5min", "MSS"),
    "3C 4H→30m":       ("4h", "30min", "PLAIN"),
    "3C 4H→30m MSS":   ("4h", "30min", "MSS"),
    "3C 4H→30m CISD":  ("4h", "30min", "CISD"),
    "3C 4H→15m MSS":   ("4h", "15min", "MSS"),
    "3C 4H→15m CISD":  ("4h", "15min", "CISD"),
}
C3_SLIM_STREAM_PAIRS = {
    "3C 1H→15m MSS":  ["USD/JPY", "USD/CAD"],
    "3C 1H→15m CISD": ["USD/JPY", "USD/CAD"],
    "3C 1H→5m MSS":   ["USD/JPY", "USD/CAD"],
    "3C 4H→30m":      ["USD/CAD"],
    "3C 4H→30m MSS":  ["USD/JPY", "USD/CAD"],
    "3C 4H→30m CISD": ["USD/JPY", "USD/CAD"],
    "3C 4H→15m MSS":  ["USD/JPY", "USD/CAD"],
    "3C 4H→15m CISD": ["USD/JPY", "USD/CAD"],
}


def apply_slim():
    """Override pair maps in-memory only — source files stay full."""
    crt_plain.CORE_PAIRS = list(CRT_SLIM_PAIRS)
    crt_plain.STREAMS = dict(CRT_SLIM_STREAMS)
    crt_plain.STREAM_PAIRS = dict(CRT_SLIM_STREAM_PAIRS)

    desk.CORE_PAIRS = list(C3_SLIM_PAIRS)
    desk.STREAMS = dict(C3_SLIM_STREAMS)
    desk.STREAM_PAIRS = dict(C3_SLIM_STREAM_PAIRS)


def main():
    print("=" * 70)
    print("TENSION TRADING DESK — LIVE SLIM")
    print("CRT: EUR/USD + XAU/USD | 3C: USD/JPY + USD/CAD")
    print("Full crt_plain / 3c_model pair lists unchanged on disk")
    print("=" * 70)

    apply_slim()

    # Telegram /start + reports live on 3C module
    state = desk.load_state()
    desk.process_commands(state)
    desk.save_state(state)

    crt_plain.main()
    desk.main()


if __name__ == "__main__":
    main()
