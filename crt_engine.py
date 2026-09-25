"""
TENSION TRADING DESK — CRT Engine (dual stream + multi-user)

Streams (parallel — both can fire same pair/time):
  CRT H1  = H1 C1 + M5 TS entry
  CRT M30 = M30 C1 + M5 TS entry

Modules (walk-forward locked):
  TS ON | OCL OFF | FVG OFF | DOL OFF
  BE = suggestion text only (not automated)

Telegram:
  /start /stop /help /status /pairs /ping
  Multi-subscriber broadcast (subscribers.json)
  Safe getUpdates via last_update_id in state
  Risk pips on signal | OANDA-style TradingView links

Motto: Built on Data. / Driven by Discipline.
"""

import os
import json
import time
import requests
import pandas as pd
import numpy as np

# ============================================================
# ENV
# ============================================================

TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
SHEET_URL = os.getenv("SHEET_URL", "")

STATE_FILE = "last_alert_state_crt.json"
SUBSCRIBERS_FILE = "subscribers.json"
STATE_VERSION = 2

MIN_RR = 1.5
MAX_HOLD_BARS = 200
ATR_PERIOD = 14

PAIRS = {
    "EUR/USD": {"pip": 0.0001, "tv": "OANDA:EURUSD"},
    "AUD/USD": {"pip": 0.0001, "tv": "OANDA:AUDUSD"},
    "USD/CHF": {"pip": 0.0001, "tv": "OANDA:USDCHF"},
    "EUR/JPY": {"pip": 0.01, "tv": "OANDA:EURJPY"},
}

STREAMS = {
    "CRT H1": "1h",
    "CRT M30": "30min",
}


# ============================================================
# SUBSCRIBERS
# ============================================================

def load_subscribers():
    ids = set()
    if CHAT_ID:
        ids.add(str(CHAT_ID))
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                for x in data:
                    ids.add(str(x))
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
# TELEGRAM
# ============================================================

def send_telegram_to(chat_id, text):
    if not BOT_TOKEN or not chat_id:
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
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
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": target,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        data = requests.post(url, json=payload, timeout=20).json()
        return bool(data.get("ok"))
    except Exception as e:
        print("Telegram edit error:", e)
        return False


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
                "🟢 <b>Subscribed — Tension Trading Desk CRT</b>\n\n"
                "You will receive dual-stream CRT alerts:\n"
                "• <b>CRT H1</b>\n"
                "• <b>CRT M30</b>\n\n"
                "Commands: /help /status /pairs /ping /stop",
            )

        elif low.startswith("/stop"):
            if chat_id in subscribers:
                subscribers = [x for x in subscribers if x != chat_id]
                save_subscribers(subscribers)
            send_telegram_to(
                chat_id,
                "🔴 <b>Unsubscribed.</b>\nYou will no longer receive CRT signals.",
            )

        elif low.startswith("/help"):
            send_telegram_to(
                chat_id,
                "📖 <b>How to read CRT signals</b>\n\n"
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
                "⚡ <b>CRT Engine Status</b>\n\n"
                "State: <b>ONLINE</b>\n"
                "Streams: <b>CRT H1</b> + <b>CRT M30</b>\n"
                "Entry: M5 TS | Modules: TS ON\n"
                f"Pairs: {len(PAIRS)} | Subscribers: {len(load_subscribers())}",
            )

        elif low.startswith("/pairs"):
            lines = "\n".join([f"• <code>{p}</code>" for p in PAIRS.keys()])
            send_telegram_to(
                chat_id,
                f"📊 <b>CRT scanned pairs</b>\n\n{lines}\n\n"
                "Streams: CRT H1 · CRT M30",
            )

        elif low.startswith("/ping"):
            send_telegram_to(chat_id, "pong 🏓 — CRT engine operational.")

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
# CRT LOGIC
# ============================================================

def find_c1_row(c1_df, t):
    if c1_df is None or len(c1_df) < 2:
        return None
    completed = c1_df[c1_df["datetime"] < t]
    if len(completed) == 0:
        return None
    return completed.iloc[-1]


def ts_long(m5, i, crl, crh):
    if i < 3:
        return False
    swept = any(float(m5["low"].iloc[k]) < crl for k in range(i - 3, i + 1))
    c_o = float(m5["open"].iloc[i])
    c_h = float(m5["high"].iloc[i])
    c_l = float(m5["low"].iloc[i])
    c_c = float(m5["close"].iloc[i])
    if not swept:
        return False
    if not (c_c > crl and c_c < crh):
        return False
    full = c_h - c_l
    if full <= 0:
        return False
    return ((c_c - c_l) / full >= 0.55) or (c_c > c_o)


def ts_short(m5, i, crl, crh):
    if i < 3:
        return False
    swept = any(float(m5["high"].iloc[k]) > crh for k in range(i - 3, i + 1))
    c_o = float(m5["open"].iloc[i])
    c_h = float(m5["high"].iloc[i])
    c_l = float(m5["low"].iloc[i])
    c_c = float(m5["close"].iloc[i])
    if not swept:
        return False
    if not (c_c < crh and c_c > crl):
        return False
    full = c_h - c_l
    if full <= 0:
        return False
    return ((c_h - c_c) / full >= 0.55) or (c_c < c_o)


def analyze_stream(stream_label, c1_df, m5, pair, pip):
    if c1_df is None or m5 is None or len(m5) < 60:
        return None

    i = len(m5) - 1
    t = m5["datetime"].iloc[i]
    c1 = find_c1_row(c1_df, t)
    if c1 is None:
        return None

    crh = float(c1["high"])
    crl = float(c1["low"])
    mid = (crh + crl) / 2.0
    if crh - crl < 5 * pip:
        return None

    direction = extreme = entry = None
    if ts_long(m5, i, crl, crh):
        direction = 1
        extreme = float(m5["low"].iloc[i - 3 : i + 1].min())
        entry = crl
    elif ts_short(m5, i, crl, crh):
        direction = -1
        extreme = float(m5["high"].iloc[i - 3 : i + 1].max())
        entry = crh
    else:
        return None

    atr = m5["atr"].iloc[i]
    if pd.isna(atr) or atr <= 0:
        buf = 1.5 * pip
    else:
        buf = max(1.5 * pip, 0.25 * float(atr))

    if direction == 1:
        stop = extreme - buf
        tp1, tp2 = mid, crh
    else:
        stop = extreme + buf
        tp1, tp2 = mid, crl

    risk = abs(entry - stop)
    if risk <= 0:
        return None

    rr1 = abs(tp1 - entry) / risk
    rr2 = abs(tp2 - entry) / risk
    if rr1 < MIN_RR:
        return None

    risk_pips = risk / pip
    candle_time = str(m5["datetime"].iloc[i])

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
        "risk_pips": float(risk_pips),
        "crh": crh,
        "crl": crl,
        "mid": mid,
        "candle_time": candle_time,
        "candle_key": f"{stream_label}|{pair}|{candle_time}|{direction}",
        "c1_tf": STREAMS[stream_label],
    }


# ============================================================
# STATE
# ============================================================

def load_state():
    default = {
        "version": STATE_VERSION,
        "last_alert_keys": {},
        "pending": [],
        "last_update_id": 0,
    }
    if not os.path.exists(STATE_FILE):
        return default
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        if state.get("version") != STATE_VERSION:
            print("CRT state version mismatch — reset pending, keep update offset.")
            default["last_update_id"] = int(state.get("last_update_id", 0))
            return default
        state.setdefault("last_alert_keys", {})
        state.setdefault("pending", [])
        state.setdefault("last_update_id", 0)
        return state
    except Exception as e:
        print("State load error:", e)
        return default


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


# ============================================================
# MESSAGES
# ============================================================

def build_signal_message(sig):
    emoji = "🟢" if sig["direction"] == 1 else "🔴"
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"════════════════════\n"
        f"<b>{sig['stream']}</b> · TS\n"
        f"────────────────────\n\n"
        f"{emoji} <b>{sig['side']} {sig['pair']}</b>\n\n"
        f"C1 TF: <b>{sig['c1_tf'].upper()}</b> · Entry: <b>M5</b>\n\n"
        f"C1 High <b>{sig['crh']:.5f}</b>\n"
        f"C1 Mid  <b>{sig['mid']:.5f}</b>\n"
        f"C1 Low  <b>{sig['crl']:.5f}</b>\n\n"
        f"Entry <b>{sig['entry']:.5f}</b>\n"
        f"SL    <b>{sig['stop']:.5f}</b> ({sig['risk_pips']:.1f} pips)\n"
        f"TP1   <b>{sig['tp1']:.5f}</b> (mid · {sig['rr1']:.2f}R measured)\n"
        f"TP2   <b>{sig['tp2']:.5f}</b> (opposite · {sig['rr2']:.2f}R measured)\n\n"
        f"Candle: <b>{sig['candle_time']}</b>\n\n"
        f"🚀 <b>TRADE ACTIVE</b>\n"
        f"💡 Suggestion only: BE after TP1 (not auto)\n\n"
        f"<a href=\"{tradingview_url(sig['pair'])}\">Open {sig['pair']} on TradingView</a>\n\n"
        f"Built on Data.\n"
        f"Driven by Discipline."
    )


def tp1_message(trade):
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"<b>{trade['stream']}</b>\n\n"
        f"✅ <b>TP1 TOUCHED</b> (mid)\n\n"
        f"<b>{trade['side']} {trade['pair']}</b>\n"
        f"TP1 <b>{float(trade['tp1']):.5f}</b>\n\n"
        f"SL unchanged (no auto BE)\n"
        f"Still heading TP2 <b>{float(trade['tp2']):.5f}</b>\n\n"
        f"💡 Suggestion: move SL to BE manually if you want\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def tp2_message(trade, exit_time):
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"<b>{trade['stream']}</b>\n\n"
        f"🏆 <b>TP2 HIT</b>\n\n"
        f"<b>{trade['side']} {trade['pair']}</b>\n"
        f"Exit <b>{exit_time}</b>\n"
        f"Result <b>+{float(trade['rr2']):.2f}R</b> (measured)\n\n"
        f"🏆 FINAL VERDICT: WIN\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def sl_message(trade, exit_time):
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"<b>{trade['stream']}</b>\n\n"
        f"🔴 <b>STOP LOSS HIT</b>\n\n"
        f"<b>{trade['side']} {trade['pair']}</b>\n"
        f"SL <b>{float(trade['stop']):.5f}</b>\n"
        f"Exit <b>{exit_time}</b>\n"
        f"Result <b>-1R</b>\n\n"
        f"🔴 FINAL VERDICT: LOSS\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


# ============================================================
# PENDING
# ============================================================

def check_pending(state, pair, m5):
    if not state["pending"]:
        return

    remaining = []
    latest = len(m5) - 1
    primary_chat = str(CHAT_ID) if CHAT_ID else None

    for trade in state["pending"]:
        if trade.get("pair") != pair:
            remaining.append(trade)
            continue

        try:
            entry_time = pd.Timestamp(trade["candle_time"])
            matches = np.where(m5["datetime"].values >= entry_time.to_datetime64())[0]
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
        edit_chat = trade.get("edit_chat_id") or primary_chat

        for j in range(start, latest + 1):
            high = float(m5["high"].iloc[j])
            low = float(m5["low"].iloc[j])
            candle_time = str(m5["datetime"].iloc[j])
            trade["last_checked_index"] = j

            if direction == 1:
                hit_sl = low <= stop
                hit_tp2 = high >= tp2
                hit_tp1 = high >= tp1
            else:
                hit_sl = high >= stop
                hit_tp2 = low <= tp2
                hit_tp1 = low <= tp1

            if hit_sl:
                edit_telegram(
                    trade.get("message_id"), sl_message(trade, candle_time), edit_chat
                )
                send_to_sheet(
                    {
                        "action": "OUTCOME",
                        "status": "LOSS",
                        "stream": trade.get("stream"),
                        "pair": pair,
                        "result_r": -1,
                        "exit_time": candle_time,
                    }
                )
                closed = True
                break

            if hit_tp2:
                edit_telegram(
                    trade.get("message_id"), tp2_message(trade, candle_time), edit_chat
                )
                send_to_sheet(
                    {
                        "action": "OUTCOME",
                        "status": "WIN",
                        "stream": trade.get("stream"),
                        "pair": pair,
                        "result_r": float(trade["rr2"]),
                        "exit_time": candle_time,
                    }
                )
                closed = True
                break

            if hit_tp1 and not tp1_hit:
                trade["tp1_hit"] = True
                tp1_hit = True
                edit_telegram(
                    trade.get("message_id"), tp1_message(trade), edit_chat
                )
                send_to_sheet(
                    {
                        "action": "TP1_TOUCH",
                        "stream": trade.get("stream"),
                        "pair": pair,
                        "exit_time": candle_time,
                    }
                )

            if j - signal_index >= MAX_HOLD_BARS:
                edit_telegram(
                    trade.get("message_id"),
                    f"<b>TENSION TRADING DESK</b>\n<b>{trade.get('stream')}</b>\n\n"
                    f"⏱️ <b>TIME EXIT</b>\n\n<b>{trade['side']} {pair}</b>\n"
                    f"Max hold reached.\n\nBuilt on Data.\nDriven by Discipline.",
                    edit_chat,
                )
                closed = True
                break

        if not closed:
            remaining.append(trade)

    state["pending"] = remaining


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("TENSION TRADING DESK — CRT DUAL STREAM + MULTI-USER")
    print("CRT H1 + CRT M30 | TS ON | no auto BE")
    print("Commands: /start /stop /help /status /pairs /ping")
    print("=" * 70)

    if not TWELVE_DATA_KEY:
        print("Missing TWELVE_DATA_KEY")
        return

    state = load_state()

    if BOT_TOKEN:
        process_commands(state)
        print(f"Subscribers: {len(load_subscribers())}")
    else:
        print("BOT_TOKEN missing — scan only, no Telegram.")

    for pair, cfg in PAIRS.items():
        pip = cfg["pip"]
        print(f"\n--- {pair} ---")

        h1 = fetch(pair, "1h", 300)
        m30 = fetch(pair, "30min", 400)
        m5 = fetch(pair, "5min", 500)

        if m5 is None:
            print("  M5 failed")
            continue

        m5 = add_atr(m5)
        check_pending(state, pair, m5)

        c1_map = {"1h": h1, "30min": m30}

        for stream_label, c1_interval in STREAMS.items():
            c1_df = c1_map.get(c1_interval)
            sig = analyze_stream(stream_label, c1_df, m5, pair, pip)
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
                and t.get("direction") == sig["direction"]
                and t.get("status") == "ACTIVE"
                for t in state["pending"]
            )
            if dup:
                print(f"  {stream_label}: active trade open")
                continue

            msg = build_signal_message(sig)
            message_id = broadcast(msg)
            print(
                f"  {stream_label}: signal -> {sig['side']} | "
                f"{sig['risk_pips']:.1f} pips | RR1 {sig['rr1']:.2f}"
            )

            state["last_alert_keys"][f"{stream_label}:{pair}"] = key

            if message_id:
                state["pending"].append(
                    {
                        "stream": stream_label,
                        "pair": pair,
                        "message_id": message_id,
                        "edit_chat_id": str(CHAT_ID) if CHAT_ID else None,
                        "direction": sig["direction"],
                        "side": sig["side"],
                        "entry": sig["entry"],
                        "stop": sig["stop"],
                        "tp1": sig["tp1"],
                        "tp2": sig["tp2"],
                        "rr1": sig["rr1"],
                        "rr2": sig["rr2"],
                        "risk_pips": sig["risk_pips"],
                        "candle_time": sig["candle_time"],
                        "candle_key": key,
                        "status": "ACTIVE",
                        "tp1_hit": False,
                        "last_checked_index": len(m5) - 1,
                    }
                )

            send_to_sheet(
                {
                    "action": "NEW_SIGNAL",
                    "stream": stream_label,
                    "pair": pair,
                    "side": sig["side"],
                    "entry": sig["entry"],
                    "stop": sig["stop"],
                    "tp1": sig["tp1"],
                    "tp2": sig["tp2"],
                    "rr1": sig["rr1"],
                    "rr2": sig["rr2"],
                    "risk_pips": sig["risk_pips"],
                    "candle_time": sig["candle_time"],
                }
            )

    save_state(state)
    print("\n" + "=" * 70)
    print(
        f"CRT SCAN COMPLETE | pending: {len(state['pending'])} | "
        f"subs: {len(load_subscribers())}"
    )
    print("=" * 70)


if __name__ == "__main__":
    main()
