"""
TENSION TRADING DESK — 3c_model.py
CRT on GBP/USD | USD/JPY | USD/CAD | GBP/JPY
Telegram labels: 3C …
Also provides fetch/Telegram for crt_plain.py
"""

import os
import json
import re
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

# ============================================================
# ENV
# ============================================================

TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
SHEET_URL = os.getenv("SHEET_URL", "")

STATE_FILE = "last_alert_state_crt.json"
SUBSCRIBERS_FILE = "subscribers.json"
OUTCOMES_FILE = "outcomes.json"
STATE_VERSION = 2

MIN_RR = 1.5
MAX_HOLD_BARS = 200
ATR_PERIOD = 14

# All 8 pairs shared by CRT set A + CRT set B (4+4). 3C strategy no longer runs.
PAIRS = {
    "EUR/USD": {"pip": 0.0001, "tv": "OANDA:EURUSD"},
    "AUD/USD": {"pip": 0.0001, "tv": "OANDA:AUDUSD"},
    "USD/CHF": {"pip": 0.0001, "tv": "OANDA:USDCHF"},
    "EUR/JPY": {"pip": 0.01, "tv": "OANDA:EURJPY"},
    "GBP/USD": {"pip": 0.0001, "tv": "OANDA:GBPUSD"},
    "USD/JPY": {"pip": 0.01, "tv": "OANDA:USDJPY"},
    "USD/CAD": {"pip": 0.0001, "tv": "OANDA:USDCAD"},
    "GBP/JPY": {"pip": 0.01, "tv": "OANDA:GBPJPY"},
}

STREAMS = {}  # filled by CRT strategy below


# ============================================================
# SUBSCRIBERS
# ============================================================

def load_subscribers():
    """Return clean Telegram chat_id strings only (never user dict dumps)."""
    ids = set()

    def _ok(x):
        s = str(x).strip()
        # reject dict-looking garbage saved by old /start bugs
        if not s or s.startswith("{") or s.startswith("["):
            return None
        # allow numeric chat ids and negative group ids
        if re.fullmatch(r"-?\d+", s):
            return s
        return None

    if CHAT_ID:
        c = _ok(CHAT_ID)
        if c:
            ids.add(c)
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                for x in data:
                    if isinstance(x, dict):
                        x = x.get("id") or x.get("chat_id")
                    c = _ok(x)
                    if c:
                        ids.add(c)
        except Exception as e:
            print("Subscriber load error:", e)
    return sorted(ids)


def save_subscribers(subscribers):
    try:
        with open(SUBSCRIBERS_FILE, "w") as f:
            json.dump(sorted(set(str(x) for x in subscribers)), f, indent=2)
    except Exception as e:
        print("Subscriber save error:", e)


# ============================================================
# OUTCOMES LOG (shared by 3C Model + CRT plain)
# ============================================================

def load_outcomes():
    if not os.path.exists(OUTCOMES_FILE):
        return []
    try:
        with open(OUTCOMES_FILE, "r") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        print("Outcomes load error:", e)
        return []


def log_outcome(record):
    """Append a closed-trade record. Used for weekly metrics later."""
    rows = load_outcomes()
    rows.append(record)
    try:
        tmp = OUTCOMES_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rows, f, indent=2)
        os.replace(tmp, OUTCOMES_FILE)
    except Exception as e:
        print("Outcomes save error:", e)


def outcome_notice(trade, status, result_r, exit_time):
    """Short WIN/LOSS message sent to ALL subscribers (edit alone is not enough)."""
    if status == "WIN":
        head = "🏆 FINAL VERDICT: WIN"
        line = f"Result <b>+{float(result_r):.2f}R</b>"
    elif status == "LOSS":
        head = "🔴 FINAL VERDICT: LOSS"
        line = "Result <b>-1.00R</b>"
    else:
        head = f"⏱️ {status}"
        line = f"Result <b>{float(result_r):+.2f}R</b>" if result_r is not None else "Result n/a"
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"<b>{trade.get('stream')}</b>\n\n"
        f"{head}\n\n"
        f"<b>{trade.get('side')} {trade.get('pair')}</b>\n"
        f"Exit <b>{format_times(exit_time)}</b>\n"
        f"{line}\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def resolve_trade(trade, status, result_r, exit_time, edit_text):
    """
    1) Edit original signal on every chat we have message_ids for.
    2) If edit fails, broadcast full edit text as fallback.
    3) Always broadcast short WIN/LOSS notice.
    4) Log outcome.
    """
    if edit_text:
        ok = _edit_all_messages(trade, edit_text)
        if not ok:
            print(f"  edit failed {trade.get('stream')} {trade.get('pair')} — broadcast fallback")
            broadcast(edit_text)
    broadcast(outcome_notice(trade, status, result_r, exit_time))
    log_outcome(
        {
            "engine": "3C_MODEL",
            "stream": trade.get("stream"),
            "pair": trade.get("pair"),
            "side": trade.get("side"),
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
            "signal_sent": trade.get("signal_sent"),
        }
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_to(chat_id, text):
    if not BOT_TOKEN or not chat_id:
        return None
    cid = str(chat_id).strip()
    # refuse dict dumps / garbage ids
    if cid.startswith("{") or cid.startswith("[") or not cid.lstrip("-").isdigit():
        print(f"Telegram skip invalid chat_id={cid!r}")
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": cid,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        data = requests.post(url, json=payload, timeout=20).json()
        if data.get("ok"):
            return data["result"]["message_id"]
        print(f"Telegram error ({chat_id}):", data)
    except Exception as e:
        print(f"Telegram send error ({chat_id}):", e)
    return None



def broadcast(text):
    """Send to all subscribers. Returns message_id from CHAT_ID for edits."""
    subs = load_subscribers()
    if not subs:
        print("No subscribers.")
        return None

    primary = str(CHAT_ID) if CHAT_ID else subs[0]
    primary_mid = None
    for cid in subs:
        mid = send_telegram_to(cid, text)
        if str(cid) == primary and mid:
            primary_mid = mid
        time.sleep(0.05)
    return primary_mid


def edit_telegram(message_id, text, chat_id=None):
    target = str(chat_id or CHAT_ID or "")
    if not BOT_TOKEN or not target or not message_id:
        print(f"Telegram edit skip: token/target/mid missing target={target} mid={message_id}")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": target,
        "message_id": int(message_id) if str(message_id).isdigit() else message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        data = requests.post(url, json=payload, timeout=20).json()
        if not data.get("ok"):
            print(f"Telegram edit fail ({target} mid={message_id}):", data)
            return False
        return True
    except Exception as e:
        print("Telegram edit error:", e)
        return False


def broadcast_message_ids(text):
    """Send to all subscribers. Returns {chat_id: message_id} for multi-chat edits."""
    subs = load_subscribers()
    if not subs:
        print("No subscribers.")
        return {}
    ids = {}
    for cid in subs:
        mid = send_telegram_to(cid, text)
        if mid is not None:
            ids[str(cid)] = mid
        time.sleep(0.05)
    return ids


def process_commands(state):
    """Safe getUpdates with offset stored in state."""
    if not BOT_TOKEN:
        return

    subscribers = load_subscribers()
    offset = int(state.get("last_update_id", 0)) + 1
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        data = requests.get(
            url, params={"offset": offset, "timeout": 0}, timeout=15
        ).json()
    except Exception as e:
        print("getUpdates error:", e)
        return

    if not data.get("ok"):
        return

    for result in data.get("result", []):
        uid = int(result.get("update_id", 0))
        state["last_update_id"] = max(int(state.get("last_update_id", 0)), uid)

        message = result.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            continue

        low = text.lower()

        if low.startswith("/start"):
            if chat_id not in subscribers:
                subscribers.append(chat_id)
                save_subscribers(subscribers)
            send_telegram_to(
                chat_id,
                "🟢 <b>Subscribed — Tension Trading Desk 3C Model</b>\n\n"
                "You will receive dual-stream 3C Model alerts:\n"
                "• <b>3C CRT</b>\n"
                "• <b>3C CRT</b>\n\n"
                "Commands: /help /status /pairs /ping /stop",
            )

        elif low.startswith("/stop"):
            if chat_id in subscribers:
                subscribers = [x for x in subscribers if x != chat_id]
                save_subscribers(subscribers)
            send_telegram_to(
                chat_id,
                "🔴 <b>Unsubscribed.</b>\nYou will no longer receive 3C Model signals.",
            )

        elif low.startswith("/help"):
            send_telegram_to(
                chat_id,
                "📖 <b>How to read 3C Model signals</b>\n\n"
                "• <b>C1</b> — Range candle (H1 or M30)\n"
                "• <b>Entry</b> — After M5 Turtle Soup (sweep + reject)\n"
                "• <b>SL</b> — Beyond sweep extreme + buffer\n"
                "• <b>TP1</b> — C1 midpoint (measured R shown)\n"
                "• <b>TP2</b> — Opposite C1 boundary\n"
                "• <b>RR</b> — Measured from geometry (not fixed)\n\n"
                "H1 and M30 can both fire — they are alternatives.\n"
                "BE after TP1 is a <b>suggestion only</b> (not auto).",
            )

        elif low.startswith("/status"):
            send_telegram_to(
                chat_id,
                "⚡ <b>3C Model Status</b>\n\n"
                "State: <b>ONLINE</b>\n"
                "Streams: <b>3C CRT</b> + <b>3C CRT</b>\n"
                "Entry: M5 TS | Modules: TS ON\n"
                f"Pairs: {len(PAIRS)} | Subscribers: {len(load_subscribers())}",
            )

        elif low.startswith("/pairs"):
            lines = "\n".join([f"• <code>{p}</code>" for p in PAIRS.keys()])
            send_telegram_to(
                chat_id,
                f"📊 <b>3C Model scanned pairs</b>\n\n{lines}\n\n"
                "Streams: 3C CRT · 3C CRT",
            )

        elif low.startswith("/ping"):
            send_telegram_to(chat_id, "pong 🏓 — 3C Model operational.")

    save_subscribers(subscribers)


def send_to_sheet(record):
    if not SHEET_URL:
        return
    try:
        requests.post(SHEET_URL, json=record, timeout=20)
    except Exception as e:
        print("Sheet error:", e)


def tradingview_url(pair):
    tv = PAIRS.get(pair, {}).get("tv")
    if tv:
        return f"https://www.tradingview.com/chart/?symbol={tv}"
    return f"https://www.tradingview.com/symbols/{pair.replace('/', '')}/"


def format_times(dt_like):
    """Always treat naive feed timestamps as UTC, then show WAT (UTC+1) + UTC."""
    try:
        ts = pd.Timestamp(dt_like)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        wat = ts.tz_convert("Africa/Lagos") if hasattr(ts, "tz_convert") else ts + pd.Timedelta(hours=1)
        # Africa/Lagos is WAT year-round
        try:
            wat = ts.tz_convert("Africa/Lagos")
        except Exception:
            wat = ts + pd.Timedelta(hours=1)

        def ampm(x):
            return x.strftime("%I:%M %p").lstrip("0")

        return f"{ampm(wat)} WAT · {ampm(ts)} UTC · {wat.strftime('%d %b %Y')}"
    except Exception:
        return str(dt_like)




# ============================================================
# DATA
# ============================================================

def fetch(symbol, interval, outputsize=500, retries=4):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_KEY,
        "format": "JSON",
    }
    for _ in range(retries):
        try:
            data = requests.get(url, params=params, timeout=30).json()
            if "values" in data:
                df = pd.DataFrame(data["values"])
                df["datetime"] = pd.to_datetime(df["datetime"])
                for col in ("open", "high", "low", "close"):
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna().sort_values("datetime").reset_index(drop=True)
                time.sleep(8)
                return df
            if data.get("code") == 429 or "credits" in str(data).lower():
                print(f"Rate limit {symbol} {interval}, waiting...")
                time.sleep(65)
                continue
            print(f"API error {symbol} {interval}:", data)
            return None
        except Exception as e:
            print(f"Fetch error {symbol} {interval}:", e)
            time.sleep(10)
    return None


def add_atr(df):
    prev = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev).abs(),
            (df["low"] - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out = df.copy()
    out["atr"] = tr.rolling(ATR_PERIOD).mean()
    return out


# ============================================================
# 3C MODEL LOGIC
# ============================================================


# ---- CRT STRATEGY (3C) ----

STATE_FILE = "last_alert_state_crt_set2.json"
STATE_VERSION = 6
MIN_RR = 1.5
OUTCOMES_ENGINE = "3C_CRT"
REPORT_WINDOW_MIN = 12

# PAIRS defined above

# Core pairs from first factorial
CORE_PAIRS = ["GBP/USD", "USD/JPY", "USD/CAD", "GBP/JPY"]

# stream_label -> (htf, ltf, entry_model)  Telegram shows these labels (3C …)
STREAMS = {}

def _add_streams(model, stream_defs):
    for label, htf, ltf in stream_defs:
        STREAMS[f"{label} {model}" if model != "PLAIN" else label] = (htf, ltf, model)

_BASE = [
    ("3C 1H→15m", "1h", "15min"),
    ("3C 1H→5m", "1h", "5min"),
    ("3C 30MIN→5m", "30min", "5min"),
    ("3C 4H→30m", "4h", "30min"),
    ("3C 4H→15m", "4h", "15min"),
]

# MSS everywhere
_add_streams("MSS", _BASE)

# PLAIN only where dominated
STREAMS["3C 4H→15m"] = ("4h", "15min", "PLAIN")
STREAMS["3C 4H→30m"] = ("4h", "30min", "PLAIN")
STREAMS["3C 1H→5m"] = ("1h", "5min", "PLAIN")
STREAMS["3C 1H→15m"] = ("1h", "15min", "PLAIN")

# MODEL1 only where dominated
STREAMS["3C 4H→15m MODEL1"] = ("4h", "15min", "MODEL1")
STREAMS["3C 4H→30m MODEL1"] = ("4h", "30min", "MODEL1")
STREAMS["3C 1H→5m MODEL1"] = ("1h", "5min", "MODEL1")
STREAMS["3C 1H→15m MODEL1"] = ("1h", "15min", "MODEL1")

STREAM_PAIRS = {
    "3C 1H→5m": ["GBP/USD", "USD/JPY", "GBP/JPY"],
    "3C 1H→15m": ["USD/JPY", "GBP/JPY"],
    "3C 4H→15m": ["GBP/USD", "USD/JPY", "USD/CAD", "GBP/JPY"],
    "3C 4H→30m": ["GBP/USD", "USD/JPY", "USD/CAD", "GBP/JPY"],
    "3C 4H→15m MODEL1": ["GBP/JPY"],
    "3C 4H→30m MODEL1": ["USD/JPY", "GBP/JPY"],
    "3C 1H→5m MODEL1": ["GBP/USD", "USD/JPY", "GBP/JPY"],
    "3C 1H→15m MODEL1": ["GBP/JPY", "USD/JPY"],
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
    """Naive feed times = UTC. Show WAT + UTC with emojis. Correct date per TZ."""
    try:
        ts = pd.Timestamp(dt_like)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        try:
            wat = ts.tz_convert("Africa/Lagos")
        except Exception:
            wat = ts + pd.Timedelta(hours=1)

        def ampm(x):
            return x.strftime("%I:%M %p").lstrip("0")

        return (
            f"🇳🇬 {ampm(wat)} WAT · 🌐 {ampm(ts)} UTC · "
            f"{wat.strftime('%d %b %Y')}"
        )
    except Exception:
        return str(dt_like)




def format_dual_times(setup_time, signal_time=None, signal_sent=None):
    """Chart times only — not Telegram delivery time.

    setup_time  = C2 (sweep) on HTF
    signal_time = LTF entry candle (when model confirmed / ignited)
    """
    setup = setup_time or signal_time
    signal = signal_time or setup_time
    return (
        f"🕯 Setup candle (C2): <b>{format_signal_time(setup)}</b>\n"
        f"📡 Signal time: <b>{format_signal_time(signal)}</b>"
    )



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



def entry_is_fresh(candle_time, ltf_name, now=None):
    """Reject stale entries so Telegram is not hours late vs the chart."""
    now = now or utc_now()
    try:
        ct = pd.Timestamp(candle_time)
        if ct.tzinfo is None:
            ct = ct.tz_localize("UTC")
        else:
            ct = ct.tz_convert("UTC")
        now_ts = pd.Timestamp(now)
        if now_ts.tzinfo is None:
            now_ts = now_ts.tz_localize("UTC")
        else:
            now_ts = now_ts.tz_convert("UTC")
        age_min = (now_ts - ct).total_seconds() / 60.0
    except Exception:
        return True, 0.0
    # max age by entry TF (2–3 bars)
    limits = {
        "1min": 15,
        "5min": 45,
        "15min": 90,
        "30min": 150,
        "1h": 240,
    }
    max_age = limits.get(str(ltf_name).lower(), 90)
    return age_min <= max_age, age_min

def drop_forming_bar(df):
    """API includes the live unfinished candle — never use it for entry."""
    if df is None or len(df) < 3:
        return df
    return df.iloc[:-1].copy()


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
    """First LTF close back inside C1 after valid C2 (first match, not last)."""
    w = _ltf_after(ltf, setup, 16)
    if w is None or w.empty:
        return None
    crh, crl = setup["crh"], setup["crl"]
    for _, row in w.iterrows():
        if crl < float(row["close"]) < crh:
            return row
    return None


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
        "c2_time": str(setup.get("c2_time") or candle_time),
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
    """Safe for old pending rows that may lack crh/crl/mid."""
    entry = float(trade.get("entry") or 0)
    stop = float(trade.get("stop") or 0)
    tp1 = float(trade.get("tp1") or 0)
    tp2 = float(trade.get("tp2") or 0)
    crh = trade.get("crh")
    crl = trade.get("crl")
    mid = trade.get("mid")
    if crh is None or crl is None or mid is None:
        # recover from levels we always store
        direction = int(trade.get("direction") or (1 if entry <= tp1 else -1))
        if direction == 1:
            crl = crl if crl is not None else entry
            mid = mid if mid is not None else tp1
            crh = crh if crh is not None else tp2
        else:
            crh = crh if crh is not None else entry
            mid = mid if mid is not None else tp1
            crl = crl if crl is not None else tp2
    return (
        f"C1 High <b>{float(crh):.5f}</b>\n"
        f"C1 Mid  <b>{float(mid):.5f}</b>\n"
        f"C1 Low  <b>{float(crl):.5f}</b>\n\n"
        f"Entry <b>{entry:.5f}</b>\n"
        f"SL    <b>{stop:.5f}</b>"
        f" ({float(trade.get('risk_pips', 0)):.1f} pips)\n"
        f"TP1   <b>{tp1:.5f}</b>"
        f" (mid · {float(trade.get('rr1', 0)):.2f}R)\n"
        f"TP2   <b>{tp2:.5f}</b>"
        f" (opposite · {float(trade.get('rr2', 0)):.2f}R)\n"
    )



def build_signal_message(sig, weekend_limit=False, already_ignited=False, age_min=0.0):
    emoji = "🟢" if sig["direction"] == 1 else "🔴"
    model = sig.get("entry_model", "PLAIN")
    if weekend_limit:
        limit_side = "BUY LIMIT" if sig["direction"] == 1 else "SELL LIMIT"
        status = (
            f"📌 Status: <b>ENTRY PENDING</b>\n"
            f"Order: <b>{limit_side}</b> @ {float(sig['entry']):.5f}\n"
            f"Weekend — activates when price trades through entry\n"
        )
    elif already_ignited:
        status = (
            f"⚠️ Status: <b>ALREADY IGNITED</b>\n"
            f"Entry candle is ~{float(age_min):.0f} min old — move may already be underway.\n"
            f"<b>Enter only if</b> price/structure still makes sense on your chart.\n"
            f"💡 Suggestion only: BE after TP1 (not auto)\n"
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
        f"{format_dual_times(sig.get('c2_time'), sig.get('candle_time'))}\n\n"
        f"{status}\n"
        f"<a href=\"{tradingview_url(sig['pair'])}\">Open {sig['pair']} on TradingView</a>\n\n"
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
        f"{format_dual_times(trade.get('c2_time'), trade.get('candle_time'))}\n\n"
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
        f"{format_dual_times(trade.get('c2_time'), trade.get('candle_time'))}\n\n"
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
        f"{format_dual_times(trade.get('c2_time'), trade.get('candle_time'))}\n\n"
        f"📌 Status: <b>CLOSED — LOSS</b>\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def _edit_all_messages(trade, text):
    """Edit every chat message_id stored for this trade. Returns True if any edit ok."""
    ids = trade.get("message_ids") or {}
    if not ids and trade.get("message_id"):
        chat = trade.get("edit_chat_id") or (str(CHAT_ID) if CHAT_ID else None)
        if chat:
            ids = {str(chat): trade["message_id"]}
    any_ok = False
    for chat, mid in ids.items():
        ok = edit_telegram(mid, text, chat)
        if ok:
            any_ok = True
        else:
            print(f"  edit fail chat={chat} mid={mid} stream={trade.get('stream')}")
    return any_ok


def finalize_trade(trade, status, result_r, exit_time, edit_text):
    if edit_text:
        ok = _edit_all_messages(trade, edit_text)
        if not ok:
            print(f"  all edits failed {trade.get('stream')} {trade.get('pair')} — broadcast fallback")
            broadcast(edit_text)
    if status in ("WIN", "LOSS"):
        broadcast(outcome_notice(trade, status, result_r, exit_time))
        log_outcome(
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
                "signal_sent": trade.get("signal_sent"),
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

def check_pending(state, pair, ltf, ltf_name=None):
    """ACTIVE / TP1_HIT. Same-bar SL+TP2 → SL first. TP1 edit kept."""
    if not state["pending"] or ltf is None:
        return
    remaining = []
    latest = len(ltf) - 1

    for trade in state["pending"]:
        if trade.get("pair") != pair:
            remaining.append(trade)
            continue
        # Only manage this trade on its own entry TF dataframe
        if ltf_name and trade.get("ltf") and str(trade.get("ltf")) != str(ltf_name):
            remaining.append(trade)
            continue
        status = trade.get("status")
        # Weekend LIMIT: only go ACTIVE when price trades through entry (after weekend)
        if status == "ENTRY_PENDING":
            if is_weekend_utc():
                remaining.append(trade)
                continue
            try:
                entry = float(trade["entry"])
                direction = int(trade["direction"])
                high = float(ltf["high"].iloc[-1])
                low = float(ltf["low"].iloc[-1])
                filled = (direction == 1 and low <= entry) or (direction == -1 and high >= entry)
            except Exception:
                filled = False
            if filled:
                trade["status"] = "ACTIVE"
                status = "ACTIVE"
                text = build_signal_message(
                    {**trade, "entry_model": trade.get("entry_model", "PLAIN")}, False
                )
                _edit_all_messages(trade, text)
                print(f"  LIMIT filled -> ACTIVE {trade.get('stream')} {pair}")
            else:
                remaining.append(trade)
                continue
        if status not in (None, "ACTIVE", "TP1_HIT"):
            continue
        try:
            entry_time = pd.Timestamp(trade["candle_time"])
            if entry_time.tzinfo is not None:
                entry_time = entry_time.tz_convert("UTC").tz_localize(None)
            dts = pd.to_datetime(ltf["datetime"])
            if getattr(dts.dt, "tz", None) is not None:
                dts = dts.dt.tz_convert("UTC").dt.tz_localize(None)
            matches = np.where(dts.values >= np.datetime64(entry_time))[0]
        except Exception as e:
            print(f"  pending match error {pair}: {e}")
            remaining.append(trade)
            continue
        if len(matches) == 0:
            remaining.append(trade)
            continue

        signal_index = int(matches[0])
        # Time-based cursor (rolling API windows break integer indices)
        last_checked_time = trade.get("last_checked_time")
        if not last_checked_time:
            last_checked_time = trade.get("candle_time")
        try:
            lct = pd.Timestamp(last_checked_time)
            if lct.tzinfo is not None:
                lct = lct.tz_convert("UTC").tz_localize(None)
            lct64 = np.datetime64(lct)
        except Exception:
            lct64 = None

        direction = int(trade["direction"])
        stop = float(trade["stop"])
        tp1 = float(trade["tp1"])
        tp2 = float(trade["tp2"])
        tp1_hit = bool(trade.get("tp1_hit", False))
        closed = False

        for j in range(signal_index + 1, latest + 1):
            bar_dt = dts.iloc[j] if hasattr(dts, "iloc") else dts[j]
            try:
                bt = pd.Timestamp(bar_dt)
                if bt.tzinfo is not None:
                    bt = bt.tz_convert("UTC").tz_localize(None)
                if lct64 is not None and np.datetime64(bt) <= lct64:
                    continue
            except Exception:
                pass
            high = float(ltf["high"].iloc[j])
            low = float(ltf["low"].iloc[j])
            candle_time = str(ltf["datetime"].iloc[j])
            trade["last_checked_time"] = candle_time
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
                try:
                    text = tp1_update_message(trade)
                    ok = _edit_all_messages(trade, text)
                    if not ok:
                        broadcast(text)
                        print(f"  TP1 edit failed — broadcast fallback {trade.get('stream')} {pair}")
                    else:
                        print(f"  TP1 HIT edited {trade.get('stream')} {pair}")
                except Exception as e:
                    print(f"  TP1 update error {trade.get('stream')} {pair}: {e}")

        if not closed:
            remaining.append(trade)

    state["pending"] = remaining


# ============================================================
# REPORTS
# ============================================================

def _crt_outcomes_between(t0, t1):
    rows = load_outcomes()
    out = []
    for r in rows:
        stream = str(r.get("stream") or "")
        if r.get("engine") != OUTCOMES_ENGINE and not stream.startswith("3C "):
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
        f"<b>3C CRT — {title}</b>\n"
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
            broadcast(build_report("WEEKLY PERFORMANCE", pid, t0, t1, trades))
            state["weekly_report_id"] = pid
            print(f"Weekly report SENT {pid} n={len(trades)}")
    if w.day == 1 and in_wat_send_window(6, 0):
        pid, t0, t1 = monthly_bounds()
        if state.get("monthly_report_id") != pid:
            trades = _crt_outcomes_between(t0, t1)
            broadcast(build_report("MONTHLY PERFORMANCE", pid, t0, t1, trades))
            state["monthly_report_id"] = pid
            print(f"Monthly report SENT {pid} n={len(trades)}")
    if w.month == 1 and w.day == 1 and in_wat_send_window(0, 0):
        pid, t0, t1 = yearly_bounds(state)
        if state.get("yearly_report_id") != pid:
            trades = _crt_outcomes_between(t0, t1)
            broadcast(build_report("YEARLY PERFORMANCE", pid, t0, t1, trades))
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
    print("TENSION TRADING DESK — 3C CRT")
    print("MSS all | PLAIN+MODEL1 selective | dual times | TP1/WIN/LOSS")
    print("Weekend: no new signals | open trades still managed")
    print(f"Streams loaded: {len(STREAMS)}")
    print("=" * 70)

    if not TWELVE_DATA_KEY:
        print("Missing TWELVE_DATA_KEY")
        return

    state = load_state()
    if not state.get("strategy_start"):
        state["strategy_start"] = utc_now().date().isoformat()
        print("strategy_start set:", state["strategy_start"])

    maybe_send_reports(state)

    weekend = is_weekend_utc()
    if weekend:
        print("Weekend UTC — new setups as LIMIT / ENTRY PENDING (times still dual UTC+WAT)")

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
                cache[key] = fetch(pair, tf, 400 if tf != "5min" else 500)
            frames[tf] = cache[key]
            if frames[tf] is None:
                ok = False
        if not ok:
            print("  data failed")
            continue

        for tf in frames:
            frames[tf] = add_atr(frames[tf])

        ltf_seen = set()
        for stream_label, (htf_name, ltf_name, entry_model) in STREAMS.items():
            if ltf_name in ltf_seen:
                continue
            ltf_seen.add(ltf_name)
            check_pending(state, pair, frames[ltf_name], ltf_name)

        for stream_label, (htf_name, ltf_name, entry_model) in STREAMS.items():
            if not _pair_allowed(stream_label, pair):
                continue
            ltf = frames[ltf_name]

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

            ok_fresh, age_min = entry_is_fresh(sig["candle_time"], ltf_name)
            already_ignited = not ok_fresh
            if already_ignited:
                print(
                    f"  {stream_label}: ALREADY IGNITED age={age_min:.0f}m "
                    f"candle={sig['candle_time']} — still sending with label"
                )

            signal_sent = utc_now().isoformat()
            sig["signal_sent"] = signal_sent
            status = "ENTRY_PENDING" if weekend else ("LATE" if already_ignited else "ACTIVE")
            msg_ids = broadcast_message_ids(
                build_signal_message(
                    sig,
                    weekend_limit=weekend,
                    already_ignited=already_ignited and not weekend,
                    age_min=age_min,
                )
            )
            primary = str(CHAT_ID) if CHAT_ID else (next(iter(msg_ids), None))
            message_id = msg_ids.get(primary) if primary else None
            if not message_id and msg_ids:
                message_id = next(iter(msg_ids.values()))

            print(
                f"  {stream_label}: {status} {sig['side']} | "
                f"{sig['session']} | model={entry_model} | {sig['risk_pips']:.1f} pips | "
                f"msgs={len(msg_ids)}"
            )

            state["last_alert_keys"][f"{stream_label}:{pair}"] = key
            if msg_ids:
                try:
                    et = pd.Timestamp(sig["candle_time"])
                    if et.tzinfo is not None:
                        et = et.tz_convert("UTC").tz_localize(None)
                    dts = pd.to_datetime(ltf["datetime"])
                    if getattr(dts.dt, "tz", None) is not None:
                        dts = dts.dt.tz_convert("UTC").dt.tz_localize(None)
                    m = np.where(dts.values >= np.datetime64(et))[0]
                    entry_idx = int(m[0]) if len(m) else max(0, len(ltf) - 2)
                except Exception:
                    entry_idx = max(0, len(ltf) - 2)

                state["pending"].append(
                    {
                        "stream": stream_label,
                        "pair": pair,
                        "message_id": message_id,
                        "message_ids": msg_ids,
                        "edit_chat_id": primary,
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
                        "c2_time": sig.get("c2_time") or sig["candle_time"],
                        "signal_sent": signal_sent,
                        "session": sig["session"],
                        "c1_tf": sig["c1_tf"],
                        "ltf": sig["ltf"],
                        "entry_model": sig["entry_model"],
                        "candle_key": key,
                        "status": status,
                        "tp1_hit": False,
                        "last_checked_index": entry_idx,
                    }
                )

    save_state(state)
    print("\n3C CRT SCAN COMPLETE | pending:", len(state["pending"]))


if __name__ == "__main__":
    main()
