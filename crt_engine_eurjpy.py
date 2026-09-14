"""
CRT ENGINE – EUR/JPY
Tension Trading Desk
Classic CRT | TP1 = 50% of range | TP2 = Opposite side
"""

import requests
import os
import json
from datetime import datetime, timezone

# ---------------- CONFIG ----------------
TWELVE_DATA_KEY = os.environ.get("TWELVE_DATA_KEY", "")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SHEET_URL = "https://script.google.com/macros/s/AKfycbzG8tMonpxdGyHgkrvXOaGjDJPpvqgO4Rkuey8wxu5jt7nr7HB4S7fO1fycKIKW4zguQA/exec"

SYMBOL = "EUR/JPY"
PAIR_LABEL = "EURJPY"
STATE_FILE = "crt_state_eurjpy.json"
SL_BUFFER_PIPS = 5
PIP = 0.01

# ---------------- DATA ----------------
def fetch_candles(interval="15min", count=100):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": SYMBOL,
        "interval": interval,
        "outputsize": count,
        "apikey": TWELVE_DATA_KEY,
    }
    resp = requests.get(url, params=params, timeout=15)
    data = resp.json()
    if "values" not in data:
        print("Fetch error:", data.get("message", data))
        return None
    candles = list(reversed(data["values"]))
    for c in candles:
        for k in ("open", "high", "low", "close"):
            c[k] = float(c[k])
    return candles

# ---------------- CRT LOGIC ----------------
def classic_crt(candles):
    if len(candles) < 3:
        return None

    prev = candles[-2]   # defining candle (the range)
    curr = candles[-1]   # current candle

    range_high = prev["high"]
    range_low  = prev["low"]
    range_size = range_high - range_low

    if range_size < 8 * PIP:
        return None

    mid = (range_high + range_low) / 2

    # Bullish CRT
    if curr["low"] < range_low and curr["close"] > range_low:
        entry = curr["close"]
        sl = curr["low"] - SL_BUFFER_PIPS * PIP
        return {
            "direction": "BUY",
            "entry": round(entry, 3),
            "sl": round(sl, 3),
            "tp1": round(mid, 3),
            "tp2": round(range_high, 3),
            "candle_time": curr.get("datetime"),
            "range_size": round(range_size, 3)
        }

    # Bearish CRT
    if curr["high"] > range_high and curr["close"] < range_high:
        entry = curr["close"]
        sl = curr["high"] + SL_BUFFER_PIPS * PIP
        return {
            "direction": "SELL",
            "entry": round(entry, 3),
            "sl": round(sl, 3),
            "tp1": round(mid, 3),
            "tp2": round(range_low, 3),
            "candle_time": curr.get("datetime"),
            "range_size": round(range_size, 3)
        }

    return None

# ---------------- STATE ----------------
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def already_alerted(state, signal):
    key = f"{signal['direction']}_{signal['candle_time']}"
    return state.get("last_alert_key") == key

# ---------------- TELEGRAM ----------------
def build_alert(signal):
    emoji = "🟢" if signal["direction"] == "BUY" else "🔴"
    return (
        f"▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓\n"
        f"  TENSION TRADING DESK\n"
        f"▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓\n\n"
        f"{emoji} <b>{PAIR_LABEL} · {signal['direction']}</b>\n"
        f"<b>CRT Engine</b>\n\n"
        f"<b>LEVELS</b>\n"
        f"Entry   {signal['entry']}\n"
        f"SL      {signal['sl']}\n"
        f"TP1     {signal['tp1']}  (50%)\n"
        f"TP2     {signal['tp2']}  (Opposite)\n\n"
        f"Range Size: {signal['range_size']} pips\n\n"
        f"▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓\n"
        f"<i>Built on Data.\nDriven by Discipline.</i>"
    )

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    })
    try:
        return resp.json()["result"]["message_id"]
    except:
        return None

def send_to_sheet(signal, outcome=None):
    payload = {
        "pair": PAIR_LABEL,
        "direction": signal["direction"],
        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp1": signal["tp1"],
        "tp2": signal["tp2"],
        "model": "Classic CRT"
    }
    if outcome:
        payload["outcome"] = outcome
    try:
        requests.post(SHEET_URL, json=payload, timeout=10)
    except Exception as e:
        print("Sheet error:", e)

# ---------------- MAIN ----------------
if __name__ == "__main__":
    print(f"[{datetime.now(timezone.utc)}] CRT Engine scanning {PAIR_LABEL}...")

    candles = fetch_candles()
    if not candles:
        print("No data – skipping")
    else:
        signal = classic_crt(candles)
        state = load_state()

        if signal is None:
            print("No CRT setup")
            save_state(state)
        else:
            if already_alerted(state, signal):
                print("Already alerted this candle")
                save_state(state)
            else:
                text = build_alert(signal)
                msg_id = send_telegram(text)
                send_to_sheet(signal)

                state["last_alert_key"] = f"{signal['direction']}_{signal['candle_time']}"
                state.setdefault("pending", []).append({
                    "message_id": msg_id,
                    "direction": signal["direction"],
                    "sl": signal["sl"],
                    "tp1": signal["tp1"],
                    "tp2": signal["tp2"],
                    "entry": signal["entry"]
                })
                save_state(state)
                print("CRT Alert sent")
