"""
LIVE SLIM DESK — live-book lock (not Colab rank)

  CRT (2): XAU/USD · AUD/JPY
  3C  (2): USD/JPY · EUR/JPY

Entries (live outcomes Nov 2026):
  PRIMARY: MSS + CISD on all core HTF→LTF
  OUT:     PLAIN (noisy WR), TS, MODEL1 (3C 0/4), plain 4H base (0% WR)

Core CRT rule unchanged: C1 + C2 sweep/close-inside.
Full crt_plain / 3c_model on disk intact — this file only overrides pairs/streams.
"""

from __future__ import annotations

import crt_plain
import importlib
import traceback

desk = importlib.import_module("3c_model")

# ---------------------------------------------------------------------------
# CRT slim — pairs that still print volume; entries MSS + CISD only
# ---------------------------------------------------------------------------
CRT_SLIM_PAIRS = ["XAU/USD", "AUD/JPY"]

CRT_SLIM_STREAMS = {
    # 1H anchors
    "CRT 1H→15m MSS":  ("1h", "15min", "MSS"),
    "CRT 1H→15m CISD": ("1h", "15min", "CISD"),
    "CRT 1H→5m MSS":   ("1h", "5min", "MSS"),
    "CRT 1H→5m CISD":  ("1h", "5min", "CISD"),
    # 4H anchors
    "CRT 4H→15m MSS":  ("4h", "15min", "MSS"),
    "CRT 4H→15m CISD": ("4h", "15min", "CISD"),
    "CRT 4H→30m MSS":  ("4h", "30min", "MSS"),
    "CRT 4H→30m CISD": ("4h", "30min", "CISD"),
}

CRT_SLIM_STREAM_PAIRS = {
    "CRT 1H→15m MSS":  ["XAU/USD", "AUD/JPY"],
    "CRT 1H→15m CISD": ["XAU/USD", "AUD/JPY"],
    "CRT 1H→5m MSS":   ["XAU/USD", "AUD/JPY"],
    "CRT 1H→5m CISD":  ["XAU/USD", "AUD/JPY"],
    "CRT 4H→15m MSS":  ["XAU/USD", "AUD/JPY"],
    "CRT 4H→15m CISD": ["XAU/USD", "AUD/JPY"],
    "CRT 4H→30m MSS":  ["XAU/USD", "AUD/JPY"],
    "CRT 4H→30m CISD": ["XAU/USD", "AUD/JPY"],
}

# ---------------------------------------------------------------------------
# 3C slim — live carriers only (no GBP/USD, GBP/JPY)
# ---------------------------------------------------------------------------
C3_SLIM_PAIRS = ["USD/JPY", "EUR/JPY"]

C3_SLIM_STREAMS = {
    "3C 1H→15m MSS":  ("1h", "15min", "MSS"),
    "3C 1H→15m CISD": ("1h", "15min", "CISD"),
    "3C 1H→5m MSS":   ("1h", "5min", "MSS"),
    "3C 1H→5m CISD":  ("1h", "5min", "CISD"),
    "3C 4H→15m MSS":  ("4h", "15min", "MSS"),
    "3C 4H→15m CISD": ("4h", "15min", "CISD"),
    "3C 4H→30m MSS":  ("4h", "30min", "MSS"),
    "3C 4H→30m CISD": ("4h", "30min", "CISD"),
}

C3_SLIM_STREAM_PAIRS = {
    "3C 1H→15m MSS":  ["USD/JPY", "EUR/JPY"],
    "3C 1H→15m CISD": ["USD/JPY", "EUR/JPY"],
    "3C 1H→5m MSS":   ["USD/JPY", "EUR/JPY"],
    "3C 1H→5m CISD":  ["USD/JPY", "EUR/JPY"],
    "3C 4H→15m MSS":  ["USD/JPY", "EUR/JPY"],
    "3C 4H→15m CISD": ["USD/JPY", "EUR/JPY"],
    "3C 4H→30m MSS":  ["USD/JPY", "EUR/JPY"],
    "3C 4H→30m CISD": ["USD/JPY", "EUR/JPY"],
}


def apply_slim():
    crt_plain.CORE_PAIRS = list(CRT_SLIM_PAIRS)
    crt_plain.STREAMS = dict(CRT_SLIM_STREAMS)
    crt_plain.STREAM_PAIRS = dict(CRT_SLIM_STREAM_PAIRS)
    desk.CORE_PAIRS = list(C3_SLIM_PAIRS)
    desk.STREAMS = dict(C3_SLIM_STREAMS)
    desk.STREAM_PAIRS = dict(C3_SLIM_STREAM_PAIRS)


def _process_commands(when: str):
    for eng in (crt_plain, desk):
        fn = getattr(eng, "process_commands", None)
        if not callable(fn):
            continue
        try:
            st = eng.load_state() if hasattr(eng, "load_state") else {}
            fn(st)
            if hasattr(eng, "save_state"):
                eng.save_state(st)
        except Exception as e:
            print(f"[LIVE SLIM] process_commands ({when}) error: {e}")


def main():
    print("=" * 70)
    print("TENSION TRADING DESK — LIVE SLIM")
    print("CRT: XAU/USD · AUD/JPY")
    print("3C : USD/JPY · EUR/JPY")
    print("Entries: MSS + CISD only | PLAIN/TS/MODEL1 OFF")
    print("=" * 70)
    apply_slim()
    _process_commands("start")

    for label, engine_main in (("CRT Plain", crt_plain.main), ("3C", desk.main)):
        try:
            print(f"[LIVE SLIM] Starting {label}")
            engine_main()
        except Exception:
            print(f"[LIVE SLIM] {label} crashed; continuing to the other engine:")
            traceback.print_exc()

    _process_commands("end")
    print("[LIVE SLIM] desk run complete")


if __name__ == "__main__":
    main()
