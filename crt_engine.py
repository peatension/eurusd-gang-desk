"""
CRT ENGINE - Multi Pair
Tension Trading Desk
Classic CRT + Turtle Soup
News Impact indicator (🔴 High / 🟡 Medium / 🟢 Low)
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

# ---------------- NEWS IMPACT ----------------
def get_news_impact():
    """
    Simple time-based news impact.
    🔴 HIGH   = major USD news windows
    🟡 MEDIUM = 1 hour around those windows
    🟢 LOW    = everything else
    """
    hour = datetime.now(timezone.utc).hour

    # Core high-impact windows (UTC)
    if hour in (12, 13, 14, 18, 19):
        return "🔴 HIGH IMPACT – Caution"
    # Buffer zones
    if hour in (11, 15, 17, 20):
        return "🟡 MEDIUM IMPACT"
    return "🟢 LOW IMPACT"

# ---------------- DATA ----------------
def fetch_candles(symbol, count=80):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": "15min",
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

# ---------------- CRT + TURTLE SOUP ----------------
def get_crt_signal(candles, pip):
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

    # Bullish
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
            "range_size": round(range_size / pip, 1),
            "model": "Classic CRT / Turtle Soup"
        }

    # Bearish
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
            "range_size": round(range_size / pip, 1),
            "model": "Classic CRT / Turtle Soup"
        }

    return None

# ---------------- STATE ----------------
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except:
            pass
    return {"alerts": {}, "pending": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ---------------- TELEGRAM ----------------
def build_alert(pair_label, signal):
    emoji = "🟢" if signal["direction"] == "BUY" else "🔴"
    arrow = "▲" if signal["direction"] == "BUY" else "▼"
    news = get_news_impact()

    text = (
        f"<b>TENSION TRADING DESK</b>\n"
        f"{'═'*21}\n"
        f"<b>CRT ENGINE</b>\n"
        f"{'─'*21}\n\n"
        f"{emoji} <b>{pair_label} · {signal['direction']}</b> {arrow}\n"
        f"<i>{signal['model']}</i>\n\n"
        f"<pre>"
        f"Entry  {signal['entry']}\n"
        f"SL     {signal['sl']}\n"
        f"TP1    {signal['tp1']}  (50%)\n"
        f"TP2    {signal['tp2']}  (Opposite)\n"
        f"</pre>\n"
        f"Range Size : {signal['range_size']} pips\n"
        f"News Impact : {news}\n\n"
        f"{'═'*21}\n"
        f"<i>Built on Data. Driven by Discipline.</i>"
    )
    return text

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

def edit_telegram(message_id, new_text):
    if not BOT_TOKEN or not CHAT_ID or not message_id:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    try:
        requests.post(url, data={
            "chat_id": CHAT_ID,
            "message_id": message_id,
            "text": new_text,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        print("Edit error:", e)

def send_to_sheet(pair_label, signal, outcome=None):
    payload = {
        "pair": pair_label,
        "direction": signal["direction"],
        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp1": signal["tp1"],
        "tp2": signal["tp2"],
        "model": signal.get("model", "CRT")
    }
    if outcome:
        payload["outcome"] = outcome
    try:
        requests.post(SHEET_URL, json=payload, timeout=10)
    except Exception as e:
        print("Sheet error:", e)

# ---------------- PENDING MANAGEMENT ----------------
def check_pending_trades(state, pair_label, candles):
    if not candles:
        return

    latest = candles[-1]
    high = latest["high"]
    low = latest["low"]

    still_pending = []

    for trade in state.get("pending", []):
        if trade.get("pair") != pair_label:
            still_pending.append(trade)
            continue

        direction = trade["direction"]
        sl = trade["sl"]
        tp1 = trade["tp1"]
        tp2 = trade["tp2"]
        msg_id = trade.get("message_id")
        original = trade.get("original_text", "")
        tp1_hit = trade.get("tp1_hit", False)

        outcome = None
        banner = None

        if direction == "BUY":
            if low <= sl:
                outcome = "LOSS"
                banner = "❌ <b>LOSS — SL hit</b>"
            elif high >= tp2:
                outcome = "WIN"
                banner = "✅ <b>WIN — TP2 hit</b>"
            elif high >= tp1 and not tp1_hit:
                trade["tp1_hit"] = True
                banner = "🟡 <b>TP1 HIT — running for TP2</b>"
        else:
            if high >= sl:
                outcome = "LOSS"
                banner = "❌ <b>LOSS — SL hit</b>"
            elif low <= tp2:
                outcome = "WIN"
                banner = "✅ <b>WIN — TP2 hit</b>"
            elif low <= tp1 and not tp1_hit:
                trade["tp1_hit"] = True
                banner = "🟡 <b>TP1 HIT — running for TP2</b>"

        if banner:
            new_text = original + f"\n\n{'─'*21}\n{banner}"
            edit_telegram(msg_id, new_text)

            if outcome in ("WIN", "LOSS"):
                send_to_sheet(pair_label, trade, outcome)
            else:
                still_pending.append(trade)
        else:
            still_pending.append(trade)

    state["pending"] = still_pending

# ---------------- MAIN ----------------
if __name__ == "__main__":
    print(f"[{datetime.now(timezone.utc)}] CRT Engine scanning {len(PAIRS)} pairs...")

    state = load_state()
    if "alerts" not in state:
        state["alerts"] = {}
    if "pending" not in state:
        state["pending"] = []

    for pair in PAIRS:
        symbol = pair["symbol"]
        label = pair["label"]
        pip = pair["pip"]

        print(f"\n--- {label} ---")
        candles = fetch_candles(symbol)
        if not candles:
            continue

        check_pending_trades(state, label, candles)

        signal = get_crt_signal(candles, pip)
        if signal is None:
            print("No setup")
            continue

        alert_key = f"{label}_{signal['direction']}_{signal['candle_time']}"
        if state["alerts"].get(alert_key):
            print("Already alerted")
            continue

        text = build_alert(label, signal)
        msg_id = send_telegram(text)
        send_to_sheet(label, signal)

        state["alerts"][alert_key] = True
        state["pending"].append({
            "pair": label,
            "message_id": msg_id,
            "direction": signal["direction"],
            "entry": signal["entry"],
            "sl": signal["sl"],
            "tp1": signal["tp1"],
            "tp2": signal["tp2"],
            "original_text": text,
            "tp1_hit": False,
            "model": signal["model"]
        })
        print(f"Alert sent → {signal['direction']}")

    if len(state["alerts"]) > 300:
        keys = list(state["alerts"].keys())[-150:]
        state["alerts"] = {k: state["alerts"][k] for k in keys}

    save_state(state)
    print("\nDone.")
