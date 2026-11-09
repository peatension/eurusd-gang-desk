"""
TENSION TRADING DESK — CRT GUIDE ENGINE
Built strictly from the COMPLETE CRT SYSTEM & STRUCTURAL GUIDE.
No research matrix. No Colab ranking.

PAIRS (guide):
  EUR/USD · GBP/USD · AUD/USD · USD/JPY

HTF: H1 | H4
LTF: M15 only

MODELS:
  2C   — Aggressive: same bar sweep + close inside + expansion bias
  3C   — Classic: C1 range, C2 purge+close inside, C3 delivery
  MULTI — Compression 2–4 bars / inside-bar, then raid + close inside

FILTERS (guide):
  - Structural validation on body (open/close), wicks = liquidity only
  - Session: London + London/NY Overlap + New York only (Asia blocked)
  - One-touch freshness: each CRH/CRL range used once (touch_count == 0)
  - Target = opposite extremity of the reference range
  - SL beyond purge extreme + buffer

Telegram + state lifecycle aligned with existing desk patterns.
State: last_alert_state_crt_guide.json
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import importlib

desk = importlib.import_module("3c_model")

# ---------------------------------------------------------------------------
# ENV / STATE
# ---------------------------------------------------------------------------
STATE_FILE = "last_alert_state_crt_guide.json"
STATE_VERSION = 1
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")

PAIRS = {
    "EUR/USD": {"pip": 0.0001, "tv": "OANDA:EURUSD"},
    "GBP/USD": {"pip": 0.0001, "tv": "OANDA:GBPUSD"},
    "AUD/USD": {"pip": 0.0001, "tv": "OANDA:AUDUSD"},
    "USD/JPY": {"pip": 0.01, "tv": "OANDA:USDJPY"},
}
CORE_PAIRS = list(PAIRS.keys())

# Stream label → (htf_interval, ltf_interval, model)
STREAMS = {
    "CRT Guide H1→15m 2C":    ("1h", "15min", "2C"),
    "CRT Guide H1→15m 3C":    ("1h", "15min", "3C"),
    "CRT Guide H1→15m MULTI": ("1h", "15min", "MULTI"),
    "CRT Guide H4→15m 2C":    ("4h", "15min", "2C"),
    "CRT Guide H4→15m 3C":    ("4h", "15min", "3C"),
    "CRT Guide H4→15m MULTI": ("4h", "15min", "MULTI"),
}

PIP_BUF = 2.0
MAX_HOLD_BARS = 96  # M15 bars ~ 24h
OUTPUT = {"4h": 400, "1h": 600, "15min": 1200}


# ---------------------------------------------------------------------------
# TIME / SESSION (guide: London + NY only)
# ---------------------------------------------------------------------------

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def wat_now():
    return utc_now() + timedelta(hours=1)


def _as_utc(dt_like):
    if dt_like is None:
        return None
    t = pd.Timestamp(dt_like)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t.to_pydatetime() if hasattr(t, "to_pydatetime") else t


def forex_session(dt_like):
    h = int(_as_utc(dt_like).hour)
    if 0 <= h < 6:
        return "Asia"
    if 6 <= h < 12:
        return "London"
    if 12 <= h < 16:
        return "London/NY Overlap"
    if 16 <= h < 21:
        return "New York"
    return "Off-hours"


def session_allowed(dt_like):
    s = forex_session(dt_like)
    return s in ("London", "London/NY Overlap", "New York")


def is_weekend_utc(now=None):
    now = now or utc_now()
    return now.weekday() >= 5


def format_dual_times(setup_time, signal_time=None):
    def _fmt(dt):
        if dt is None:
            return "—"
        u = _as_utc(dt)
        w = u + timedelta(hours=1)
        return (
            f"🕐 WAT {w.strftime('%I:%M %p').lstrip('0')} | "
            f"UTC {u.strftime('%I:%M %p').lstrip('0')}\n"
            f"📅 {w.strftime('%d %b %Y')}"
        )
    lines = [f"Setup candle:\n{_fmt(setup_time)}"]
    if signal_time is not None:
        lines.append(f"Signal time:\n{_fmt(signal_time)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# STATE
# ---------------------------------------------------------------------------

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
      
... 
