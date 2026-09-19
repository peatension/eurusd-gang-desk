"""
CRT ENGINE - Multi Pair + Multi Subscriber
Tension Trading Desk
Classic CRT + Turtle Soup
Auto /start registration | Admin notifications
"""

import requests
import os
import json
from datetime import datetime, timezone

# ---------------- CONFIG ----------------
TWELVE_DATA_KEY = os.environ.get("TWELVE_DATA_KEY", "")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN", "")
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
SHEET_URL = "https://script.google.com/macros/s/AKfycbzG8tMonpxdGyHgkrvXOaGjDJPpvqgO4Rkuey8wxu5jt7nr7HB4S7fO1fycKIKW4zguQA/exec"

ADMIN_CHAT_ID = "7080941387"          # Your personal ID
STATE_FILE = "crt_state.json"
SUBSCRIBERS_FILE = "subscribers.json"
SL_BUFFER_PIPS = 5

PAIRS = [
    {"symbol": "EUR/JPY", "label": "EURJPY", "pip": 0.01},
    {"symbol": "AUD/JPY", "label": "AUDJPY", "pip": 0.01},
    {"symbol": "USD/JPY", "label": "USDJPY", "pip": 0.01},
    {"symbol": "GBP/JPY", "label": "GBPJPY", "pip": 0.01},
]

# ---------------- TELEGRAM HELPERS ----------------
def tg_api(method, data=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        resp = requests.post(url, data=data or {}, timeout=10)
        return resp.json()
    except Exception as e:
        print("Telegram API error:", e)
        return {}

def send_message(chat_id, text, parse_mode="HTML"):
    return tg_api("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    })

def edit_message(chat_id, message_id, text):
    return tg_api("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    })

def notify_admin(text):
    send_message(ADMIN_CHAT_ID, f"<b>ADMIN</b>\n{text}")

# ---------------- SUBSCRIBERS ----------------
def load_subscribers():
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE) as f:
                return json.load(f)
        except:
            pass
    return []

def save_subscribers(subs):
    with open(SUBSCRIBERS_FILE, "w") as f:
        json.dump(subs, f, indent=2)

def add_subscriber(chat_id, name=""):
    subs = load_subscribers()
    chat_id = str(chat_id)
    if chat_id not in [str(s["id"]) for s in subs]:
        subs.append({"id": chat_id, "name": name, "joined": str(datetime.now(timezone.utc))})
        save_subscribers(subs)
        notify_admin(f"New subscriber: {name or chat_id}")
        return True
    return False

def remove_subscriber(chat_id):
    subs = load_subscribers()
    chat_id = str(chat_id)
    new_subs = [s for s in subs if str(s["id"]) != chat_id]
    if len(new_subs) != len(subs):
        save_subscribers(new_subs)
        notify_admin(f"Subscriber removed: {chat_id}")
        return True
    return False

# ---------------- COMMAND HANDLER ----------------
def process_commands():
    """Check for new /start /stop /help /status /pairs messages"""
    data = tg_api("getUpdates", {"timeout": 0, "limit": 20})
    results = data.get("result", [])
    if not results:
        return

    offset = None
    for upd in results:
        offset = upd["update_id"] + 1
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            continue

        chat_id = str(msg["chat"]["id"])
        text = (msg.get("text") or "").strip().lower()
        name = msg.get("from", {}).get("first_name", "")

        if text.startswith("/start"):
            added = add_subscriber(chat_id, name)
            if added:
                send_message(chat_id,
                    "<b>TENSION TRADING DESK</b>\n"
                    "CRT Engine activated.\n\n"
                    "You will now receive CRT signals.\n"
                    "Send /help for more info.")
            else:
                send_message(chat_id, "You are already registered for CRT signals.")

        elif text.startswith("/stop"):
            remove_subscriber(chat_id)
            send_message(chat_id, "You have been unsubscribed from CRT signals.")

        elif text.startswith("/help"):
            send_message(chat_id,
                "<b>CRT ENGINE – Help</b>\n\n"
                "Signals are based on Classic CRT + Turtle Soup.\n"
                "TP1 = 50% of the candle range\n"
                "TP2 = Opposite side of the range\n\n"
                "<b>Commands</b>\n"
                "/start – Receive signals\n"
                "/stop – Stop receiving signals\n"
                "/status – Show active trades\n"
                "/pairs – List scanned pairs")

        elif text.startswith("/pairs"):
            pairs_text = "\n".join([p["label"] for p in PAIRS])
            send_message(chat_id, f"<b>Active Pairs</b>\n{pairs_text}")

        elif text.startswith("/status"):
            state = load_state()
            pending = state.get("pending", [])
            if not pending:
                send_message(chat_id, "No active CRT trades at the moment.")
            else:
                lines = []
                for t in pending:
                    lines.append(f"{t['pair']} {t['direction']} | Entry {t['entry']}")
                send_message(chat_id, "<b>Active Trades</b>\n" + "\n".join(lines))

    # Clear processed updates
    if offset:
        tg_api("getUpdates", {"offset": offset, "timeout": 0})

# ---------------- NEWS IMPACT ----------------
def get_news_impact():
    if FINNHUB_API_KEY:
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            url = "https://finnhub.io/api/v1/calendar/economic"
            params = {"from": today, "to": today, "token": FINNHUB_API_KEY}
            resp = requests.get(url, params=params, timeout=8)
            events = resp.json().get("economicCalendar", [])
            for ev in events:
                if str(ev.get("impact", "")).lower() == "high" and str(ev.get("country", "")).upper() in ("US", "EU", "GB"):
                    return f"🔴 HIGH IMPACT – {ev.get('event', 'High Impact Event')}"
        except Exception as e:
            print("Finnhub error:", e)

    hour = datetime.now(timezone.utc).hour
    if hour in (12, 13, 14, 18, 19):
        return "🔴 HIGH IMPACT – Caution"
    if hour in (11, 15, 17, 20):
        return "🟡 MEDIUM IMPACT"
    return "🟢 LOW IMPACT"

# ---------------- DATA & CRT LOGIC ----------------
def fetch_candles(symbol, count=80):
    url = "https://api.twelvedata.com/time_series"
    params = {"symbol": symbol, "interval": "15min", "outputsize": count, "apikey": TWELVE_DATA_KEY}
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if "values" not in data:
            return None
        candles = list(reversed(data["values"]))
        for c in candles:
            for k in ("open", "high", "low", "close"):
                c[k] = float(c[k])
        return candles
    except:
        return None

def get_crt_signal(candles, pip):
    if len(candles) < 3:
        return None
    prev, curr = candles[-2], candles[-1]
    range_high, range_low = prev["high"], prev["low"]
    range_size = range_high - range_low
    if range_size < 8 * pip:
        return None
    mid = (range_high + range_low) / 2

    if curr["low"] < range_low and curr["close"] > range_low:
        entry = curr["close"]
        sl = curr["low"] - SL_BUFFER_PIPS * pip
        return {"direction": "BUY", "entry": round(entry,5), "sl": round(sl,5),
                "tp1": round(mid,5), "tp2": round(range_high,5),
                "candle_time": curr.get("datetime"), "range_size": round(range_size/pip,1),
                "model": "Classic CRT / Turtle Soup"}

    if curr["high"] > range_high and curr["close"] < range_high:
        entry = curr["close"]
        sl = curr["high"] + SL_BUFFER_PIPS * pip
        return {"direction": "SELL", "entry": round(entry,5), "sl": round(sl,5),
                "tp1": round(mid,5), "tp2": round(range_low,5),
                "candle_time": curr.get("datetime"), "range_size": round(range_size/pip,1),
                "model": "Classic CRT / Turtle Soup"}
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

# ---------------- ALERT BUILDER ----------------
def build_alert(pair_label, signal):
    emoji = "🟢" if signal["direction"] == "BUY" else "🔴"
    arrow = "▲" if signal["direction"] == "BUY" else "▼"
    news = get_news_impact()

    return (
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

def broadcast(text):
    """Send to all subscribers + keep admin informed"""
    subs = load_subscribers()
    for s in subs:
        send_message(s["id"], text)
    # Also send to admin if admin is not already in subscribers
    if ADMIN_CHAT_ID not in [str(s["id"]) for s in subs]:
        send_message(ADMIN_CHAT_ID, text)

# ---------------- PENDING MANAGEMENT ----------------
def check_pending_trades(state, pair_label, candles):
    if not candles:
        return
    latest = candles[-1]
    high, low = latest["high"], latest["low"]
    still_pending = []

    for trade in state.get("pending", []):
        if trade.get("pair") != pair_label:
            still_pending.append(trade)
            continue

        direction = trade["direction"]
        sl, tp1, tp2 = trade["sl"], trade["tp1"], trade["tp2"]
        msg_id = trade.get("message_id")
        original = trade.get("original_text", "")
        tp1_hit = trade.get("tp1_hit", False)
        chat_ids = trade.get("chat_ids", [])

        outcome = None
        banner = None

        if direction == "BUY":
            if low <= sl:
                outcome, banner = "LOSS", "❌ <b>LOSS — SL hit</b>"
            elif high >= tp2:
                outcome, banner = "WIN", "✅ <b>WIN — TP2 hit</b>"
            elif high >= tp1 and not tp1_hit:
                trade["tp1_hit"] = True
                banner = ("🟡 <b>TP1 HIT — running for TP2</b>\n"
                          "Consider moving SL to breakeven if you want to protect the trade.")
        else:
            if high >= sl:
                outcome, banner = "LOSS", "❌ <b>LOSS — SL hit</b>"
            elif low <= tp2:
                outcome, banner = "WIN", "✅ <b>WIN — TP2 hit</b>"
            elif low <= tp1 and not tp1_hit:
                trade["tp1_hit"] = True
                banner = ("🟡 <b>TP1 HIT — running for TP2</b>\n"
                          "Consider moving SL to breakeven if you want to protect the trade.")

        if banner:
            new_text = original + f"\n\n{'─'*21}\n{banner}"
            # Edit on all chats that received the original (simplified: broadcast edit is limited,
            # so we mainly rely on original message_id if single, else just log)
            if msg_id:
                # We store only one message_id for simplicity in this version
                pass
            if outcome in ("WIN", "LOSS"):
                pass  # closed
            else:
                still_pending.append(trade)
        else:
            still_pending.append(trade)

    state["pending"] = still_pending

# ---------------- MAIN ----------------
if __name__ == "__main__":
    print(f"[{datetime.now(timezone.utc)}] CRT Engine starting...")

    # 1. Process any new commands (/start, /stop, etc.)
    process_commands()

    # 2. Load state
    state = load_state()
    if "alerts" not in state:
        state["alerts"] = {}
    if "pending" not in state:
        state["pending"] = []

    # 3. Scan pairs
    for pair in PAIRS:
        symbol, label, pip = pair["symbol"], pair["label"], pair["pip"]
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
        broadcast(text)          # send to all subscribers

        state["alerts"][alert_key] = True
        state["pending"].append({
            "pair": label,
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
