"""
LIVE SLIM DESK — 4 pairs from CRT KL+POI research lock

  CRT (2): XAU/USD · AUD/JPY
  3C  (2): USD/JPY · EUR/JPY

Entry: PLAIN base on core streams.
Selective MSS/TS only where research showed edge.
Full crt_plain / 3c_model on disk intact — this file only overrides pairs/streams.
"""

from __future__ import annotations

import crt_plain
import importlib
import traceback
import os

desk = importlib.import_module("3c_model")

# ---------- CRT live slim set (2 pairs) ----------
CRT_SLIM_PAIRS = ["XAU/USD", "AUD/JPY"]

CRT_SLIM_STREAMS = {
    # PLAIN base — both live CRT pairs
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
    "CRT 1H→15m PLAIN": ["XAU/USD", "AUD/JPY"],
    "CRT 1H→5m PLAIN":  ["XAU/USD", "AUD/JPY"],
    "CRT 4H→30m PLAIN": ["XAU/USD", "AUD/JPY"],
    "CRT 4H→15m PLAIN": ["XAU/USD", "AUD/JPY"],
    "CRT 4H→15m MSS":   ["XAU/USD"],
    "CRT 4H→30m MSS":   ["XAU/USD"],
    "CRT 4H→15m TS":    ["XAU/USD"],
}

# ---------- 3C live slim set (2 pairs) ----------
C3_SLIM_PAIRS = ["USD/JPY", "EUR/JPY"]

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
    "3C 1H→15m PLAIN": ["USD/JPY", "EUR/JPY"],
    "3C 1H→5m PLAIN":  ["USD/JPY", "EUR/JPY"],
    "3C 4H→30m PLAIN": ["USD/JPY", "EUR/JPY"],
    "3C 4H→15m PLAIN": ["USD/JPY", "EUR/JPY"],
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
    print("TENSION TRADING DESK — LIVE SLIM (4 pairs)")
    print("CRT: XAU/USD AUD/JPY")
    print("3C : USD/JPY EUR/JPY")
    print("PLAIN base + selective MSS/TS | PDF signal format")
    print("=" * 70)
    apply_slim()
    # Commands at start (fresh updates) — shared bot token.
    _process_commands("start")

    # Isolate the engines: one uncaught engine error must not skip the other.
    for label, engine_main in (("CRT Plain", crt_plain.main), ("3C", desk.main)):
        try:
            print(f"[LIVE SLIM] Starting {label}")
            engine_main()
        except Exception:
            print(f"[LIVE SLIM] {label} crashed; continuing to the other engine:")
            traceback.print_exc()

    # Commands again at end so replies land in this run if user typed mid-scan.
    _process_commands("end")


def _process_commands(label):
    if os.getenv("DISABLE_COMMAND_POLLING", "0").strip() == "1":
        print(f"[LIVE SLIM] Telegram command polling disabled for market scan ({label})")
        return
    try:
        state = desk.load_state()
        desk.process_commands(state)
        desk.save_state(state)
    except Exception:
        print(f"[LIVE SLIM] process_commands ({label}) error:")
        traceback.print_exc()


if __name__ == "__main__":
    main()
