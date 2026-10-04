"""
TENSION TRADING DESK — CRT plain (same-TF)
==========================================
Separate strategy from 3C Model (3c_model.py).

FIRST-FACTORIAL STREAM MAP (not validation-strict filter):
  PLAIN / MSS / CISD / FVG / TS  → all 5 HTF streams × core pairs
  MODEL1 → only cells that were 3/3 on most pairs in first factorial
  TBS → OFF

Streams (HTF→LTF):
  1H→15m | 1H→5m | 30MIN→5m | 4H→30m | 4H→15m

Core rules (all models share):
  C1 = prior closed HTF | C2 = sweep + close back inside (must close valid)
  Entry = LTF model after valid C2
  Entry price = C1 extreme | TP1 = mid | TP2 = opposite | SL beyond C2
  No auto BE | No Premium/Discount filter

Features:
  Same Telegram message edit: ACTIVE → TP1 HIT → WIN/LOSS
  Weekend: LIMIT / ENTRY PENDING (not market ACTIVE)
  Reports WAT-gated once: weekly Sat 12:00 PM | monthly 1st 6:00 AM | yearly 1 Jan 00:00
  Uses 3c_model only as infrastructure (fetch, Telegram, outcomes)
"""

import os
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import importlib

desk = importlib.import_module("3c_model")

STATE_FILE = "last_alert_state_crt_plain.json"
STATE_VERSION = 5
MIN_RR = 1.5
OUTCOMES_ENGINE = "CRT_PLAIN"
REPORT_WINDOW_MIN = 12

PAIRS = desk.PAIRS

# Core pairs from first factorial
CORE_PAIRS = ["EUR/USD", "AUD/USD", "USD/CHF", "EUR/JPY"]

# stream_label -> (htf, ltf, entry_model)
# Built from first factorial 3/3 coverage
STREAMS = {}

def _add_streams(model, stream_defs):
    for label, htf, ltf in stream_defs:
        STREAMS[f"{label} {model}" if model != "PLAIN" else label] = (htf, ltf, model)

_BASE = [
    ("CRT 1H→15m", "1h", "15min"),
    ("CRT 1H→5m", "1h", "5min"),
    ("CRT 30MIN→5m", "30min", "5min"),
    ("CRT 4H→30m", "4h", "30min"),
    ("CRT 4H→15m", "4h", "15min"),
]

# PLAIN / MSS / CISD / FVG / TS on all cells (first factorial: worked on most/all)
for _m in ("PLAIN", "MSS", "CISD", "FVG", "TS"):
    _add_streams(_m, _BASE)

# MODEL1 only on cells that were 3/3 for most pairs in first factorial
STREAMS["CRT 1H→15m MODEL1"] = ("1h", "15min", "MODEL1")
STREAMS["CRT 1H→5m MODEL1"] = ("1h", "5min", "MODEL1")
STREAMS["CRT 30MIN→5m MODEL1"] = ("30min", "5min", "MODEL1")
STREAMS["CRT 4H→15m MODEL1"] = ("4h", "15min", "MODEL1")
# 4H→30m MODEL1 was weak on EUR/USD + EUR/JPY in first factorial → omit

# Optional pair restrict (None = all CORE_PAIRS that exist in desk.PAIRS)
STREAM_PAIRS = {
    # MODEL1 30MIN weaker on USD/CHF in first factorial
    "CRT 30MIN→5m MODEL1": ["EUR/USD", "AUD/USD", "EUR/JPY"],
}


# ============================================================
# STATE
# ============================================================

def load_state():
    default = {
        "version": STATE_VERSION,
        "last_alert_keys": {},
        "pending": [],
        "weekly_report_id": None,
        "monthly_report_id": None,
        "yearly_report_id": None,
        "strategy_start": None,
    }
    if not os.path.exists(STATE_FILE):
        return default
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        if state.get("version") != STATE_VERSION:
            print("CRT plain state version bump — merge safe fields.")
            default["last_alert_keys"] = state.get("last_alert_keys", {})
            default["weekly_report_id"] = state.get("weekly_report_id")
            default["monthly_report_id"] = state.get("monthly_report_id")
            default["yearly_report_id"] = state.get("yearly_report_id")
            default["strategy_start"] = state.get("strategy_start")
            if isinstance(state.get("pending"), list):
                default["pending"] = state["pending"]
            return default
        for k, v in default.items():
            state.setdefault(k, v)
        return state
    except Exception as e:
        print("CRT plain state load error:", e)
        return default


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


# ============================================================
# TIME / SESSION
# ============================================================

def _as_utc(dt_like):
    ts = pd.Timestamp(dt_like)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def utc_now():
    return datetime.now(timezone.utc)


def wat_now():
    return utc_now() + timedelta(hours=1)


def format_signal_time(dt_like):
    try:
        utc = _as_utc(dt_like)
        wat = utc + pd.Timedelta(hours=1)
        wat_s = wat.strftime("%I:%M %p").lstrip("0")
        utc_s = utc.strftime("%H:%M")
        date_s = wat.strftime("%d %b %Y")
        return f"🇳🇬 {wat_s} WAT · 🌐 {utc_s} UTC · {date_s}"
    except Exception:
        return str(dt_like)


def forex_session(dt_like):
    try:
        h = int(_as_utc(dt_like).hour)
    except Exception:
        return "Unknown"
    if 0 <= h < 7:
        return "Asia"
    if 7 <= h < 12:
        return "London"
    if 12 <= h < 16:
        return "London/NY Overlap"
    if 16 <= h < 21:
        return "New York"
    return "Off-hours"


def is_weekend_utc(now=None):
    now = now or utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.weekday() >= 5


def in_wat_send_window(hour, minute, width_min=REPORT_WINDOW_MIN):
    w = wat_now()
    target = w.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta = (w - target).total_seconds()
    return 0 <= delta < width_min * 60


# ============================================================
# CRT STRATEGY
# ============================================================

def find_setup(htf, pip):
    """Latest closed C2: sweep C1 + close back inside. C2 must be closed."""
    if htf is None or len(htf) < 5:
        return None
    c2 = htf.iloc[-2]
    c1 = htf.iloc[-3]
    crh = float(c1["high"])
    crl = float(c1["low"])
    mid = (crh + crl) / 2.0
    if crh - crl < 5 * pip:
        return None
    if float(c2["low"]) < crl and crl < float(c2["close"]) < crh:
        return {
            "crh": crh, "crl": crl, "mid": mid,
            "extreme": float(c2["low"]), "direction": 1,
            "c2_time": str(c2["datetime"]),
        }
    if float(c2["high"]) > crh and crl < float(c2["close"]) < crh:
        return {
            "crh": crh, "crl": crl, "mid": mid,
            "extreme": float(c2["high"]), "direction": -1,
            "c2_time": str(c2["datetime"]),
        }
    return None


def _ltf_after(ltf, setup, n=24):
    if ltf is None or len(ltf) < 8:
        return None
    c2_time = pd.Timestamp(setup["c2_time"])
    after = ltf[ltf["datetime"] > c2_time]
    if after.empty:
        return None
    return after.tail(n).reset_index(drop=True)


def ltf_entry_plain(ltf, setup):
    """First LTF close back inside C1 after valid C2."""
    w = _ltf_after(ltf, setup, 16)
    if w is None:
        return None
    crh, crl = setup["crh"], setup["crl"]
    hit = None
    for _, row in w.iterrows():
        if crl < float(row["close"]) < crh:
            hit = row
    return hit


def ltf_entry_ts(ltf, setup, pip):
    """Turtle Soup: wick beyond extreme then reject close back."""
    w = _ltf_after(ltf, setup, 20)
    if w is None or len(w) < 2:
        return None
    d, ext = setup["direction"], setup["extreme"]
    for i in range(1, len(w)):
        prev, row = w.iloc[i - 1], w.iloc[i]
        if d == 1 and float(prev["low"]) <= ext + pip and float(row["close"]) > float(row["open"]) and float(row["close"]) > setup["crl"]:
            return row
        if d == -1 and float(prev["high"]) >= ext - pip and float(row["close"]) < float(row["open"]) and float(row["close"]) < setup["crh"]:
            return row
    return None


def ltf_entry_model1(ltf, setup, pip):
    """Model #1: opposing candle engulfed (close beyond it)."""
    w = _ltf_after(ltf, setup, 24)
    if w is None or len(w) < 3:
        return None
    d = setup["direction"]
    for i in range(1, len(w) - 1):
        prev, cur, nxt = w.iloc[i - 1], w.iloc[i], w.iloc[i + 1]
        if d == 1 and float(cur["close"]) < float(prev["low"]) and float(nxt["close"]) > float(cur["high"]):
            return nxt
        if d == -1 and float(cur["close"]) > float(prev["high"]) and float(nxt["close"]) < float(cur["low"]):
            return nxt
    return None


def ltf_entry_mss(ltf, setup, pip):
    """MSS: break recent LTF swing in trade direction after C2."""
    w = _ltf_after(ltf, setup, 30)
    if w is None or len(w) < 8:
        return None
    d = setup["direction"]
    for i in range(5, len(w)):
        look, row = w.iloc[i - 5 : i], w.iloc[i]
        c = float(row["close"])
        if d == 1 and c > float(look["high"].max()) and c > setup["crl"]:
            return row
        if d == -1 and c < float(look["low"].min()) and c < setup["crh"]:
            return row
    return None


def ltf_entry_cisd(ltf, setup, pip):
    """CISD: break open of last opposing leg candle."""
    w = _ltf_after(ltf, setup, 24)
    if w is None or len(w) < 6:
        return None
    d = setup["direction"]
    for i in range(2, len(w)):
        leg = w.iloc[max(0, i - 4) : i]
        row = w.iloc[i]
        if d == 1:
            bears = leg[leg["close"] < leg["open"]]
            if bears.empty:
                continue
            level = float(bears.iloc[-1]["open"])
            if float(row["close"]) > level and float(row["close"]) > setup["crl"]:
                return row
        else:
            bulls = leg[leg["close"] > leg["open"]]
            if bulls.empty:
                continue
            level = float(bulls.iloc[-1]["open"])
            if float(row["close"]) < level and float(row["close"]) < setup["crh"]:
                return row
    return None


def ltf_entry_fvg(ltf, setup, pip):
    """FVG: displacement gap then first revisit."""
    w = _ltf_after(ltf, setup, 30)
    if w is None or len(w) < 6:
        return None
    d = setup["direction"]
    for i in range(2, len(w) - 1):
        a, c = w.iloc[i - 2], w.iloc[i]
        if d == 1 and float(a["high"]) < float(c["low"]):
            gap_lo, gap_hi = float(a["high"]), float(c["low"])
            for j in range(i + 1, min(i + 12, len(w))):
                row = w.iloc[j]
                if float(row["low"]) <= gap_hi and float(row["high"]) >= gap_lo:
                    if float(row["close"]) > setup["crl"]:
                        return row
        if d == -1 and float(a["low"]) > float(c["high"]):
            gap_lo, gap_hi = float(c["high"]), float(a["low"])
            for j in range(i + 1, min(i + 12, len(w))):
                row = w.iloc[j]
                if float(row["low"]) <= gap_hi and float(row["high"]) >= gap_lo:
                    if float(row["close"]) < setup["crh"]:
                        return row
    return None


ENTRY_FN = {
    "PLAIN": lambda ltf, setup, pip: ltf_entry_plain(ltf, setup),
    "TS": ltf_entry_ts,
    "MODEL1": ltf_entry_model1,
    "MSS": ltf_entry_mss,
    "CISD": ltf_entry_cisd,
    "FVG": ltf_entry_fvg,
}


def analyze(stream_label, htf_name, ltf_name, entry_model, htf, ltf, pair, pip):
    setup = find_setup(htf, pip)
    if setup is None:
        return None
    fn = ENTRY_FN.get(entry_model, ENTRY_FN["PLAIN"])
    bar = fn(ltf, setup, pip)
    if bar is None:
        return None

    atr = float(bar["atr"]) if "atr" in bar and not pd.isna(bar.get("atr", np.nan)) else 0
    buf = max(1.5 * pip, 0.25 * atr if atr > 0 else 1.5 * pip)
    direction = setup["direction"]
    if direction == 1:
        entry = setup["crl"]
        stop = setup["extreme"] - buf
        tp1, tp2 = setup["mid"], setup["crh"]
    else:
        entry = setup["crh"]
        stop = setup["extreme"] + buf
        tp1, tp2 = setup["mid"], setup["crl"]

    risk = abs(entry - stop)
    if risk <= 0:
        return None
    rr1 = abs(tp1 - entry) / risk
    rr2 = abs(tp2 - entry) / risk
    if rr1 < MIN_RR:
        return None

    candle_time = str(bar["datetime"])
    return {
        "stream": stream_label,
        "pair": pair,
        "direction": direction,
        "side": "BUY" if direction == 1 else "SELL",
        "entry": float(entry),
        "stop": float(stop),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "rr1": float(rr1),
        "rr2": float(rr2),
        "risk_pips": float(risk / pip),
        "crh": setup["crh"],
        "crl": setup["crl"],
        "mid": setup["mid"],
        "candle_time": candle_time,
        "session": forex_session(candle_time),
        "candle_key": f"{stream_label}|{pair}|{setup['c2_time']}|{direction}|{entry_model}",
        "c1_tf": htf_name,
        "ltf": ltf_name,
        "entry_model": entry_model,
    }


# ============================================================
# MESSAGES
# ============================================================

def _core_levels(trade):
    return (
        f"C1 High <b>{float(trade['crh']):.5f}</b>\n"
        f"C1 Mid  <b>{float(trade['mid']):.5f}</b>\n"
        f"C1 Low  <b>{float(trade['crl']):.5f}</b>\n\n"
        f"Entry <b>{float(trade['entry']):.5f}</b>\n"
        f"SL    <b>{float(trade['stop']):.5f}</b>"
        f" ({float(trade.get('risk_pips', 0)):.1f} pips)\n"
        f"TP1   <b>{float(trade['tp1']):.5f}</b>"
        f" (mid · {float(trade.get('rr1', 0)):.2f}R)\n"
        f"TP2   <b>{float(trade['tp2']):.5f}</b>"
        f" (opposite · {float(trade.get('rr2', 0)):.2f}R)\n"
    )


def build_signal_message(sig, weekend_limit=False):
    emoji = "🟢" if sig["direction"] == 1 else "🔴"
    model = sig.get("entry_model", "PLAIN")
    if weekend_limit:
        limit_side = "BUY LIMIT" if sig["direction"] == 1 else "SELL LIMIT"
        status = (
            f"📌 Status: <b>ENTRY PENDING</b>\n"
            f"Order type: <b>{limit_side}</b> @ {sig['entry']:.5f}\n"
            f"Weekend setup — fills when market trades through entry (Mon open)\n"
        )
    else:
        status = (
            f"📌 Status: <b>ACTIVE</b>\n"
            f"💡 Suggestion only: BE after TP1 (not auto)\n"
        )
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"════════════════════\n"
        f"<b>{sig['stream']}</b>\n"
        f"────────────────────\n\n"
        f"{emoji} <b>{sig['side']} {sig['pair']}</b>\n\n"
        f"C1 TF: <b>{sig['c1_tf'].upper()}</b> · Entry TF: <b>{sig['ltf']}</b>\n"
        f"Entry model: <b>{model}</b>\n"
        f"Session: <b>{sig.get('session', '—')}</b>\n"
        f"Sweep: same TF as C1, close back inside\n\n"
        f"{_core_levels(sig)}\n"
        f"Signal: <b>{format_signal_time(sig['candle_time'])}</b>\n\n"
        f"{status}\n"
        f"<a href=\"{desk.tradingview_url(sig['pair'])}\">Open {sig['pair']} on TradingView</a>\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def tp1_update_message(trade):
    emoji = "🟢" if int(trade.get("direction", 1)) == 1 else "🔴"
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"════════════════════\n"
        f"<b>{trade['stream']}</b>\n"
        f"────────────────────\n\n"
        f"🎯 <b>TP1 HIT — HEADING TO TP2</b>\n\n"
        f"{emoji} <b>{trade['side']} {trade['pair']}</b>\n\n"
        f"Session: <b>{trade.get('session', '—')}</b>\n\n"
        f"{_core_levels(trade)}\n"
        f"Signal: <b>{format_signal_time(trade.get('candle_time'))}</b>\n\n"
        f"📌 Status: <b>TP1 HIT — ACTIVE</b>\n"
        f"Still heading TP2 <b>{float(trade['tp2']):.5f}</b>\n"
        f"💡 No automatic BE\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def tp2_final_message(trade, exit_time):
    emoji = "🟢" if int(trade.get("direction", 1)) == 1 else "🔴"
    rr2 = float(trade.get("rr2", 0))
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"════════════════════\n"
        f"<b>{trade['stream']}</b>\n"
        f"────────────────────\n\n"
        f"🏆 <b>TP2 HIT — WIN</b>\n\n"
        f"{emoji} <b>{trade['side']} {trade['pair']}</b>\n\n"
        f"Session: <b>{trade.get('session', '—')}</b>\n"
        f"Exit: <b>{format_signal_time(exit_time)}</b>\n"
        f"Result: <b>+{rr2:.2f}R</b>\n\n"
        f"{_core_levels(trade)}\n"
        f"📌 Status: <b>CLOSED — WIN</b>\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def sl_final_message(trade, exit_time):
    emoji = "🟢" if int(trade.get("direction", 1)) == 1 else "🔴"
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"════════════════════\n"
        f"<b>{trade['stream']}</b>\n"
        f"────────────────────\n\n"
        f"❌ <b>SL HIT — LOSS</b>\n\n"
        f"{emoji} <b>{trade['side']} {trade['pair']}</b>\n\n"
        f"Session: <b>{trade.get('session', '—')}</b>\n"
        f"Exit: <b>{format_signal_time(exit_time)}</b>\n"
        f"Result: <b>-1.00R</b>\n\n"
        f"{_core_levels(trade)}\n"
        f"📌 Status: <b>CLOSED — LOSS</b>\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def limit_cancelled_message(trade):
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"<b>{trade['stream']}</b>\n\n"
        f"⏹️ <b>LIMIT CANCELLED</b>\n\n"
        f"<b>{trade['side']} {trade['pair']}</b>\n"
        f"Entry was not filled at open.\n\n"
        f"📌 Status: <b>CANCELLED</b>\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def finalize_trade(trade, status, result_r, exit_time, edit_text):
    edit_chat = trade.get("edit_chat_id") or (str(desk.CHAT_ID) if desk.CHAT_ID else None)
    if trade.get("message_id") and edit_text:
        ok = desk.edit_telegram(trade.get("message_id"), edit_text, edit_chat)
        if not ok:
            print(f"  edit failed {trade.get('stream')} {trade.get('pair')}")
    if status in ("WIN", "LOSS"):
        desk.broadcast(desk.outcome_notice(trade, status, result_r, exit_time))
        desk.log_outcome(
            {
                "engine": OUTCOMES_ENGINE,
                "stream": trade.get("stream"),
                "pair": trade.get("pair"),
                "side": trade.get("side"),
                "direction": trade.get("direction"),
                "status": status,
                "result_r": float(result_r) if result_r is not None else None,
                "exit_time": exit_time,
                "entry": trade.get("entry"),
                "stop": trade.get("stop"),
                "tp1": trade.get("tp1"),
                "tp2": trade.get("tp2"),
                "rr1": trade.get("rr1"),
                "rr2": trade.get("rr2"),
                "candle_time": trade.get("candle_time"),
                "session": trade.get("session"),
                "tp1_hit": bool(trade.get("tp1_hit")),
                "c1_tf": trade.get("c1_tf"),
                "ltf": trade.get("ltf"),
                "entry_model": trade.get("entry_model"),
            }
        )


# ============================================================
# PENDING
# ============================================================

def check_pending_limits(state, pair, ltf):
    if not state["pending"] or ltf is None or is_weekend_utc():
        return
    remaining = []
    primary = str(desk.CHAT_ID) if desk.CHAT_ID else None
    latest = ltf.iloc[-1]
    for trade in state["pending"]:
        if trade.get("pair") != pair:
            remaining.append(trade)
            continue
        if trade.get("status") != "ENTRY_PENDING":
            remaining.append(trade)
            continue
        entry = float(trade["entry"])
        direction = int(trade["direction"])
        high = float(latest["high"])
        low = float(latest["low"])
        filled = (direction == 1 and low <= entry) or (direction == -1 and high >= entry)
        edit_chat = trade.get("edit_chat_id") or primary
        if filled:
            trade["status"] = "ACTIVE"
            trade["tp1_hit"] = False
            trade["last_checked_index"] = len(ltf) - 1
            desk.edit_telegram(
                trade.get("message_id"),
                build_signal_message({**trade, "entry_model": trade.get("entry_model", "PLAIN")}, False),
                edit_chat,
            )
            print(f"  limit FILLED -> ACTIVE {trade['stream']} {pair}")
            remaining.append(trade)
        else:
            try:
                age = (utc_now() - _as_utc(trade["candle_time"]).to_pydatetime().replace(tzinfo=timezone.utc)).days
            except Exception:
                age = 0
            if age >= 3:
                desk.edit_telegram(trade.get("message_id"), limit_cancelled_message(trade), edit_chat)
                print(f"  limit CANCELLED {trade['stream']} {pair}")
            else:
                remaining.append(trade)
    state["pending"] = remaining


def check_pending(state, pair, ltf):
    """ACTIVE / TP1_HIT. Same-bar SL+TP2 → SL first."""
    if not state["pending"] or ltf is None:
        return
    remaining = []
    primary = str(desk.CHAT_ID) if desk.CHAT_ID else None
    latest = len(ltf) - 1

    for trade in state["pending"]:
        if trade.get("pair") != pair:
            remaining.append(trade)
            continue
        status = trade.get("status")
        if status == "ENTRY_PENDING":
            remaining.append(trade)
            continue
        if status not in (None, "ACTIVE", "TP1_HIT"):
            continue
        try:
            entry_time = pd.Timestamp(trade["candle_time"])
            matches = np.where(ltf["datetime"].values >= entry_time.to_datetime64())[0]
        except Exception:
            remaining.append(trade)
            continue
        if len(matches) == 0:
            remaining.append(trade)
            continue

        signal_index = int(matches[0])
        last_checked = int(trade.get("last_checked_index", signal_index))
        start = max(signal_index + 1, last_checked + 1)
        direction = int(trade["direction"])
        stop = float(trade["stop"])
        tp1 = float(trade["tp1"])
        tp2 = float(trade["tp2"])
        tp1_hit = bool(trade.get("tp1_hit", False))
        closed = False
        edit_chat = trade.get("edit_chat_id") or primary

        for j in range(start, latest + 1):
            high = float(ltf["high"].iloc[j])
            low = float(ltf["low"].iloc[j])
            candle_time = str(ltf["datetime"].iloc[j])
            trade["last_checked_index"] = j
            if direction == 1:
                hit_sl, hit_tp2, hit_tp1 = low <= stop, high >= tp2, high >= tp1
            else:
                hit_sl, hit_tp2, hit_tp1 = high >= stop, low <= tp2, low <= tp1

            if hit_sl:
                trade["status"] = "CLOSED_LOSS"
                finalize_trade(trade, "LOSS", -1.0, candle_time, sl_final_message(trade, candle_time))
                closed = True
                break
            if hit_tp2:
                trade["status"] = "CLOSED_WIN"
                finalize_trade(
                    trade, "WIN", float(trade.get("rr2", 0)), candle_time,
                    tp2_final_message(trade, candle_time),
                )
                closed = True
                break
            if hit_tp1 and not tp1_hit:
                trade["tp1_hit"] = True
                tp1_hit = True
                trade["status"] = "TP1_HIT"
                desk.edit_telegram(trade.get("message_id"), tp1_update_message(trade), edit_chat)

        if not closed:
            remaining.append(trade)

    state["pending"] = remaining


# ============================================================
# REPORTS
# ============================================================

def _crt_outcomes_between(t0, t1):
    rows = desk.load_outcomes()
    out = []
    for r in rows:
        stream = str(r.get("stream") or "")
        if r.get("engine") != OUTCOMES_ENGINE and not stream.startswith("CRT "):
            continue
        try:
            et = _as_utc(r.get("exit_time") or r.get("candle_time"))
            et_dt = et.to_pydatetime()
            if et_dt.tzinfo is None:
                et_dt = et_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if t0 <= et_dt <= t1:
            out.append(r)
    return out


def _metrics_block(trades):
    if not trades:
        return "No completed CRT Plain trades in this period."
    wins = [t for t in trades if str(t.get("status", "")).upper() == "WIN"]
    losses = [t for t in trades if str(t.get("status", "")).upper() == "LOSS"]
    n = len(trades)
    nw, nl = len(wins), len(losses)
    wr = 100.0 * nw / n if n else 0.0
    rs = [float(t["result_r"]) for t in trades if t.get("result_r") is not None]
    net = sum(rs) if rs else 0.0
    exp = net / n if n else 0.0
    by_session = {}
    for t in trades:
        s = t.get("session") or "Unknown"
        by_session.setdefault(s, []).append(float(t.get("result_r") or 0))
    sess = "\n".join(
        f"• {s}: n={len(xs)} net={sum(xs):+.2f}R" for s, xs in sorted(by_session.items())
    ) or "—"
    by_stream = {}
    for t in trades:
        s = t.get("stream") or "?"
        by_stream.setdefault(s, []).append(float(t.get("result_r") or 0))
    streams = "\n".join(
        f"• {s}: n={len(xs)} net={sum(xs):+.2f}R" for s, xs in sorted(by_stream.items())
    ) or "—"
    return (
        f"Trades: <b>{n}</b> | Wins: <b>{nw}</b> | Losses: <b>{nl}</b>\n"
        f"WR: <b>{wr:.1f}%</b> | Net: <b>{net:+.2f}R</b> | Exp: <b>{exp:+.2f}R</b>\n\n"
        f"<b>By session</b>\n{sess}\n\n"
        f"<b>By stream</b>\n{streams}"
    )


def build_report(title, period_label, t0, t1, trades):
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"════════════════════\n"
        f"<b>CRT PLAIN — {title}</b>\n"
        f"Period: <b>{period_label}</b>\n"
        f"{t0.strftime('%d %b %Y %H:%M')} → {t1.strftime('%d %b %Y %H:%M')} UTC\n"
        f"────────────────────\n\n"
        f"{_metrics_block(trades)}\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def weekly_bounds(now=None):
    now = now or utc_now()
    d = now.date()
    monday = d - timedelta(days=d.weekday())
    week_start = datetime(monday.year, monday.month, monday.day, 0, 0, tzinfo=timezone.utc)
    period_monday = week_start if now.weekday() >= 5 else week_start - timedelta(days=7)
    period_friday_end = period_monday + timedelta(days=4, hours=21)
    iso = period_monday.isocalendar()
    pid = f"{iso[0]}-W{iso[1]:02d}"
    return pid, period_monday, period_friday_end


def monthly_bounds(now=None):
    now = now or utc_now()
    first_this = datetime(now.year, now.month, 1, 0, 0, tzinfo=timezone.utc)
    last_prev = first_this - timedelta(seconds=1)
    first_prev = datetime(last_prev.year, last_prev.month, 1, 0, 0, tzinfo=timezone.utc)
    pid = f"{last_prev.year}-{last_prev.month:02d}"
    return pid, first_prev, last_prev


def yearly_bounds(state, now=None):
    now = now or utc_now()
    start_s = state.get("strategy_start")
    if not start_s:
        start_s = now.date().isoformat()
        state["strategy_start"] = start_s
    try:
        y, m, d = map(int, start_s.split("-")[:3])
        t0 = datetime(y, m, d, 0, 0, tzinfo=timezone.utc)
    except Exception:
        t0 = datetime(now.year, 1, 1, 0, 0, tzinfo=timezone.utc)
    year = now.year - 1 if (now.month == 1 and now.day == 1) else now.year
    if t0.year == now.year:
        year = now.year
        t1 = datetime(year, 12, 31, 23, 59, tzinfo=timezone.utc)
        return f"Y{year}", t0, t1
    t0 = max(t0, datetime(year, 1, 1, 0, 0, tzinfo=timezone.utc))
    t1 = datetime(year, 12, 31, 23, 59, tzinfo=timezone.utc)
    return f"Y{year}", t0, t1


def maybe_send_reports(state):
    w = wat_now()
    if w.weekday() == 5 and in_wat_send_window(12, 0):
        pid, t0, t1 = weekly_bounds()
        if state.get("weekly_report_id") != pid:
            trades = _crt_outcomes_between(t0, t1)
            desk.broadcast(build_report("WEEKLY PERFORMANCE", pid, t0, t1, trades))
            state["weekly_report_id"] = pid
            print(f"Weekly report SENT {pid} n={len(trades)}")
    if w.day == 1 and in_wat_send_window(6, 0):
        pid, t0, t1 = monthly_bounds()
        if state.get("monthly_report_id") != pid:
            trades = _crt_outcomes_between(t0, t1)
            desk.broadcast(build_report("MONTHLY PERFORMANCE", pid, t0, t1, trades))
            state["monthly_report_id"] = pid
            print(f"Monthly report SENT {pid} n={len(trades)}")
    if w.month == 1 and w.day == 1 and in_wat_send_window(0, 0):
        pid, t0, t1 = yearly_bounds(state)
        if state.get("yearly_report_id") != pid:
            trades = _crt_outcomes_between(t0, t1)
            desk.broadcast(build_report("YEARLY PERFORMANCE", pid, t0, t1, trades))
            state["yearly_report_id"] = pid
            print(f"Yearly report SENT {pid} n={len(trades)}")


def _pair_allowed(stream_label, pair):
    allowed = STREAM_PAIRS.get(stream_label)
    if allowed is None:
        return pair in CORE_PAIRS or pair in PAIRS
    return pair in allowed


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("TENSION TRADING DESK — CRT PLAIN")
    print("First-factorial streams: PLAIN TS MSS CISD FVG + MODEL1 cells")
    print("Reports: WAT-gated once | Weekend: LIMIT / ENTRY PENDING")
    print(f"Streams loaded: {len(STREAMS)}")
    print("=" * 70)

    if not desk.TWELVE_DATA_KEY:
        print("Missing TWELVE_DATA_KEY")
        return

    state = load_state()
    if not state.get("strategy_start"):
        state["strategy_start"] = utc_now().date().isoformat()
        print("strategy_start set:", state["strategy_start"])

    maybe_send_reports(state)

    weekend = is_weekend_utc()
    if weekend:
        print("Weekend UTC — market ACTIVE off | LIMIT / ENTRY PENDING only")

    cache = {}
    scan_pairs = [p for p in CORE_PAIRS if p in PAIRS]
    if not scan_pairs:
        scan_pairs = list(PAIRS.keys())

    for pair in scan_pairs:
        cfg = PAIRS[pair]
        pip = cfg["pip"]
        print(f"\n--- {pair} ---")

        needed = set()
        for htf_name, ltf_name, _ in STREAMS.values():
            needed.add(htf_name)
            needed.add(ltf_name)

        frames = {}
        ok = True
        for tf in needed:
            key = (pair, tf)
            if key not in cache:
                cache[key] = desk.fetch(pair, tf, 400 if tf != "5min" else 500)
            frames[tf] = cache[key]
            if frames[tf] is None:
                ok = False
        if not ok:
            print("  data failed")
            continue

        for tf in frames:
            frames[tf] = desk.add_atr(frames[tf])

        for stream_label, (htf_name, ltf_name, entry_model) in STREAMS.items():
            if not _pair_allowed(stream_label, pair):
                continue
            ltf = frames[ltf_name]
            check_pending_limits(state, pair, ltf)
            check_pending(state, pair, ltf)

            sig = analyze(
                stream_label, htf_name, ltf_name, entry_model,
                frames[htf_name], ltf, pair, pip,
            )
            if sig is None:
                print(f"  {stream_label}: no setup")
                continue

            key = sig["candle_key"]
            if state["last_alert_keys"].get(f"{stream_label}:{pair}") == key:
                print(f"  {stream_label}: already alerted")
                continue

            dup = any(
                t.get("pair") == pair
                and t.get("stream") == stream_label
                and t.get("status") in (None, "ACTIVE", "TP1_HIT", "ENTRY_PENDING")
                for t in state["pending"]
            )
            if dup:
                print(f"  {stream_label}: active/pending open")
                continue

            if weekend:
                message_id = desk.broadcast(build_signal_message(sig, weekend_limit=True))
                status = "ENTRY_PENDING"
                print(f"  {stream_label}: LIMIT PENDING {sig['side']} @ {sig['entry']:.5f}")
            else:
                message_id = desk.broadcast(build_signal_message(sig, weekend_limit=False))
                status = "ACTIVE"
                print(
                    f"  {stream_label}: ACTIVE {sig['side']} | "
                    f"{sig['session']} | model={entry_model} | {sig['risk_pips']:.1f} pips"
                )

            state["last_alert_keys"][f"{stream_label}:{pair}"] = key
            if message_id:
                state["pending"].append(
                    {
                        "stream": stream_label,
                        "pair": pair,
                        "message_id": message_id,
                        "edit_chat_id": str(desk.CHAT_ID) if desk.CHAT_ID else None,
                        "direction": sig["direction"],
                        "side": sig["side"],
                        "entry": sig["entry"],
                        "stop": sig["stop"],
                        "tp1": sig["tp1"],
                        "tp2": sig["tp2"],
                        "rr1": sig["rr1"],
                        "rr2": sig["rr2"],
                        "risk_pips": sig["risk_pips"],
                        "crh": sig["crh"],
                        "crl": sig["crl"],
                        "mid": sig["mid"],
                        "candle_time": sig["candle_time"],
                        "session": sig["session"],
                        "c1_tf": sig["c1_tf"],
                        "ltf": sig["ltf"],
                        "entry_model": sig["entry_model"],
                        "candle_key": key,
                        "status": status,
                        "tp1_hit": False,
                        "last_checked_index": len(ltf) - 1,
                    }
                )

    save_state(state)
    print("\nCRT PLAIN SCAN COMPLETE | pending:", len(state["pending"]))


if __name__ == "__main__":
    main()
