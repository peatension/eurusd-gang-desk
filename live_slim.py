"""
LIVE SLIM DESK — API credit budget (does NOT change strategy rules)

  CRT : EUR/USD + AUD/JPY  (+ Daily/Weekly/Monthly HTF MSS/CISD)
  3C  : USD/JPY + USD/CAD

XAU removed from slim. Full engines unchanged on disk.
"""

from __future__ import annotations

import crt_plain
import importlib

desk = importlib.import_module("3c_model")

CRT_SLIM_PAIRS = ["EUR/USD", "AUD/JPY"]
CRT_SLIM_STREAMS = {
    # Intraday — MSS/CISD (+ MODEL1 EUR-style on both)
    "CRT 1H→15m MSS":    ("1h", "15min", "MSS"),
    "CRT 1H→15m CISD":   ("1h", "15min", "CISD"),
    "CRT 1H→5m MODEL1":  ("1h", "5min", "MODEL1"),
    "CRT 4H→30m MSS":    ("4h", "30min", "MSS"),
    "CRT 4H→15m CISD":   ("4h", "15min", "CISD"),
    "CRT 4H→15m MSS":    ("4h", "15min", "MSS"),
    # Daily / Weekly / Monthly — MSS + CISD only
    "CRT D→4H MSS":      ("1day", "4h", "MSS"),
    "CRT D→4H CISD":     ("1day", "4h", "CISD"),
    "CRT D→1H MSS":      ("1day", "1h", "MSS"),
    "CRT D→1H CISD":     ("1day", "1h", "CISD"),
    "CRT W→D MSS":       ("1week", "1day", "MSS"),
    "CRT W→D CISD":      ("1week", "1day", "CISD"),
    "CRT MN→W MSS":      ("1month", "1week", "MSS"),
    "CRT MN→D CISD":     ("1month", "1day", "CISD"),
}
CRT_SLIM_STREAM_PAIRS = {k: ["EUR/USD", "AUD/JPY"] for k in CRT_SLIM_STREAMS}

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
    "3C D→4H MSS":     ("1day", "4h", "MSS"),
    "3C D→4H CISD":    ("1day", "4h", "CISD"),
    "3C D→1H MSS":     ("1day", "1h", "MSS"),
    "3C D→1H CISD":    ("1day", "1h", "CISD"),
    "3C W→D MSS":      ("1week", "1day", "MSS"),
    "3C W→D CISD":     ("1week", "1day", "CISD"),
    "3C MN→W MSS":     ("1month", "1week", "MSS"),
    "3C MN→D CISD":    ("1month", "1day", "CISD"),
}
C3_SLIM_STREAM_PAIRS = {k: ["USD/JPY", "USD/CAD"] for k in C3_SLIM_STREAMS}
C3_SLIM_STREAM_PAIRS["3C 4H→30m"] = ["USD/CAD"]


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
    print("CRT: EUR/USD + AUD/JPY | 3C: USD/JPY + USD/CAD | both D/W/MN MSS+CISD")
    print("=" * 70)
    apply_slim()
    state = desk.load_state()
    desk.process_commands(state)
    desk.save_state(state)
    crt_plain.main()
    desk.main()


if __name__ == "__main__":
    main()
