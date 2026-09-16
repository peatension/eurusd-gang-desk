"""
CRT ENGINE - Multi Pair
Tension Trading Desk
Pairs: EUR/JPY | AUD/JPY | USD/JPY | GBP/JPY
TP1 = 50% of range | TP2 = Opposite side
"""

import requests
import os
import json
from datetime import datetime, timezone

# ---------------- CONFIG ----------------
TWELVE_DATA_KEY = os.environ.get("TWELVE_DATA_KEY", "")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("CHAT_ID", "")
SHEET_URL = "https://script.google.com/macros/s/AKfycbzG8tMonpxdGyHgkrvXOaGjDJPpvqgO4Rkuey8wxu5jt7nr7HB4S7fO1fycKIKW4zguQA/exec"

STATE_FILE = "crt_state.json"
SL_BUFFER_PIPS = 5

PAIRS = [
    {"symbol": "EUR/JPY", "label": "EURJPY", "pip": 0.01},
    {"symbol": "AUD/JPY", "label": "AUDJPY", "pip": 0.01},
    {"symbol": "USD/JPY", "label": "USDJPY", "pip": 0.01},
    {"symbol": "GBP/JPY", "label": "GBPJPY", "pip": 0.01},
]

# ---------------- DATA ----------------
def fetch_candles(symbol, interval="15min", count=50):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": count,
        "apikey": TWELVE_DATA_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if "values" not in data:
            print(f"Fetch error {symbol}:", data.get("message", data))
            return None
        candles = list(reversed(data["values"]))
        for c in candles:
            for k in ("open", "high", "low", "close"):
                c[k] = float(c[k])
        return candles
    except Exception as e:
        print(f"Request failed {symbol}:", e)
        return None

# ---------------- CRT LOGIC ----------------
def classic_crt(candles, pip):
    if len(candles) < 3:
        return None

    prev = candles[-2]
    curr = candles[-1]

    range_high = prev["high"]
    range_low = prev["low"]
    range_size = range_high - range_low

    if range_size < 8 * pip:
        return None

    mid = (range_high + range_low) / 2

    # Bullish CRT
    if curr["low"] < range_low and curr["close"] > range_low:
        entry = curr["close"]
        sl = curr["low"] - SL_BUFFER_PIPS * pip
        return {
            "direction": "BUY",
            "entry": round(entry, 5),
            "sl": round(sl, 5),
            "tp1": round(mid, 5),
            "tp2": round(range_high, 5),
            "candle_time": curr.get("datetime"),
            "range_size": round(range_size / pip, 1)
        }

    # Bearish CRT
    if curr["high"] > range_high and curr["close"] < range_high:
        entry = curr["close"]
        sl = curr["high"] + SL_BUFFER_PIPS * pip
        return {
            "direction": "SELL",
            "entry": round(entry, 5),
            "sl": round(sl, 5),
            "tp1": round(mid, 5),
            "tp2": round(range_low, 5),
            "candle_time": curr.get("datetime"),
            "range_size": round(range_size / pip, 1)
        }

    return None

# ---------------- STATE ----------------
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except:
            return {"alerts": {}}
    return {"alerts": {}}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ---------------- TELEGRAM ----------------
def build_alert(pair_label, signal):
    emoji = "🟢" if signal["direction"] == "BUY" else "🔴"
    return (
        f"▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓\n"
        f"  TENSION TRADING DESK\n"
        f"▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓\n\n"
        f"{emoji} <b>{pair_label} · {signal['direction']}</b>\n"
        f"<b>CRT Engine</b>\n\n"
        f"<b>LEVELS</b>\n"
        f"Entry   {signal['entry']}\n"
        f"SL      {signal['sl']}\n"
        f"TP1     {signal['tp1']}  (50%)\n"
        f"TP2     {signal['tp2']}  (Opposite)\n\n"
        f"Range: {signal['range_size']} pips\n\n"
        f"▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓\n"
        f"<i>Built on Data.\nDriven by Discipline.</i>"
    )

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing Telegram credentials")
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, data={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }, timeout=10)
        return resp.json().get("result", {}).get("message_id")
    except Exception as e:
        print("Telegram error:", e)
        return None

def send_to_sheet(pair_label, signal):
    payload = {
        "pair": pair_label,
        "direction": signal["direction"],
        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp1": signal["tp1"],
        "tp2": signal["tp2"],
        "model": "Classic CRT"
    }
    try:
        requests.post(SHEET_URL, json=payload, timeout=10)
    except Exception as e:
        print("Sheet error:", e)

# ---------------- MAIN ----------------
if __name__ == "__main__":
    print(f"[{datetime.now(timezone.utc)}] CRT Engine scanning {len(PAIRS)} pairs...")

    state = load_state()
    if "alerts" not in state:
        state["alerts"] = {}

    for pair in PAIRS:
        symbol = pair["symbol"]
        label = pair["label"]
        pip = pair["pip"]

        print(f"\n--- {label} ---")
        candles = fetch_candles(symbol)
        if not candles:
            continue

        signal = classic_crt(candles, pip)
        if signal is None:
            print("No setup")
            continue

        alert_key = f"{label}_{signal['direction']}_{signal['candle_time']}"

        if state["alerts"].get(alert_key):
            print("Already alerted")
            continue

        # New signal
        text = build_alert(label, signal)
        msg_id = send_telegram(text)
        send_to_sheet(label, signal)

        state["alerts"][alert_key] = {
            "message_id": msg_id,
            "time": str(datetime.now(timezone.utc))
        }
        print(f"Alert sent → {signal['direction']}")

    # Keep state file from growing forever (optional clean)
    if len(state["alerts"]) > 200:
        # keep only last 100
        keys = list(state["alerts"].keys())[-100:]
        state["alerts"] = {k: state["alerts"][k] for k in keys}

    save_state(state)
    print("\nDone.")
