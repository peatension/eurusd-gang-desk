import os
import json
import requests
import pandas as pd

# ==========================================
# CONFIGURATION & ENVIRONMENT VARIABLES
# ==========================================
SYMBOL = "GBP/JPY"
ALERT_THRESHOLD = 7.0
STATE_FILE = "state.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")

# ==========================================
# 1. STATE MANAGER FUNCTIONS
# ==========================================
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"trades": {}}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"trades": {}}

def save_state(data):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error saving state: {e}")

def register_trade(symbol, message_id, action, entry_min, entry_max, tp1, tp2, sl):
    state = load_state()
    state["trades"][symbol] = {
        "message_id": message_id,
        "action": action,
        "entry_min": entry_min,
        "entry_max": entry_max,
        "tp1": tp1,
        "tp2": tp2,
        "sl": sl,
        "status": "PENDING",
        "sl_moved_to_be": False
    }
    save_state(state)

def update_trade_status(symbol, new_status, sl_moved_to_be=False):
    state = load_state()
    if symbol in state["trades"]:
        state["trades"][symbol]["status"] = new_status
        if sl_moved_to_be:
            state["trades"][symbol]["sl_moved_to_be"] = True
        save_state(state)

def get_active_trade(symbol):
    state = load_state()
    trade = state["trades"].get(symbol)
    if trade and trade.get("status") not in ["CLOSED", "SL_HIT", "TP2_HIT"]:
        return trade
    return None

# ==========================================
# 2. TELEGRAM DYNAMIC MESSAGE UPDATER
# ==========================================
def format_alert_text(symbol, action, entry_min, entry_max, tp1, tp2, sl, status="PENDING", pips=0):
    if status == "PENDING":
        return (
            f"🟢 *FOREX SIGNAL ALERT*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"*Pair:* {symbol}\n"
            f"*Action:* {action.upper()}\n"
            f"*Entry Zone:* {entry_min} - {entry_max}\n\n"
            f"*Targets:*\n"
            f"🎯 *TP1:* {tp1} *(Pending)*\n"
            f"🎯 *TP2:* {tp2} *(Pending)*\n"
            f"🛑 *SL:* {sl}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"_Status: Waiting for Entry_"
        )
    elif status == "TP1_HIT":
        return (
            f"🟢 *FOREX SIGNAL ALERT*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"*Pair:* {symbol}\n"
            f"*Action:* {action.upper()}\n"
            f"*Entry Zone:* {entry_min} - {entry_max} *(Triggered)*\n\n"
            f"*Targets:*\n"
            f"🎯 *TP1:* {tp1} *(✅ ACHIEVED)*\n"
            f"🎯 *TP2:* {tp2} *(Pending)*\n"
            f"🛑 *SL:* {sl} *(Moved to BE)*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"*Status: TP1 Hit (+{pips} Pips)*"
        )
    elif status == "TP2_HIT":
        return (
            f"🟢 *FOREX SIGNAL ALERT*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"*Pair:* {symbol}\n"
            f"*Action:* {action.upper()}\n"
            f"*Entry Zone:* {entry_min} - {entry_max} *(Triggered)*\n\n"
            f"*Targets:*\n"
            f"🎯 *TP1:* {tp1} *(✅ ACHIEVED)*\n"
            f"🎯 *TP2:* {tp2} *(✅ ACHIEVED)*\n"
            f"🛑 *SL:* {sl}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"*Status: TP2 Hit (+{pips} Pips - Trade Closed)*"
        )
    elif status == "SL_HIT":
        return (
            f"🔴 *FOREX SIGNAL ALERT*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"*Pair:* {symbol}\n"
            f"*Action:* {action.upper()}\n"
            f"*Entry Zone:* {entry_min} - {entry_max}\n\n"
            f"*Targets:*\n"
            f"🎯 *TP1:* {tp1}\n"
            f"🎯 *TP2:* {tp2}\n"
            f"🛑 *SL:* {sl} *(HIT)*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"*Status: Stop Loss Hit ({pips} Pips)*"
        )

def send_new_signal(symbol, action, entry_min, entry_max, tp1, tp2, sl):
    text = format_alert_text(symbol, action, entry_min, entry_max, tp1, tp2, sl, "PENDING")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload).json()
        if r.get("ok"):
            return r["result"]["message_id"]
    except Exception as e:
        print(f"Error sending message: {e}")
    return None

def edit_signal(message_id, symbol, action, entry_min, entry_max, tp1, tp2, sl, new_status, pips=0):
    text = format_alert_text(symbol, action, entry_min, entry_max, tp1, tp2, sl, new_status, pips)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        r = requests.post(url, json=payload).json()
        return r.get("ok", False)
    except Exception as e:
        print(f"Error editing message: {e}")
        return False

# ==========================================
# 3. MARKET DATA & SMC ANALYSIS
# ==========================================
def fetch_candles(interval="15min", outputsize=30):
    url = f"https://api.twelvedata.com/time_series?symbol={SYMBOL}&interval={interval}&outputsize={outputsize}&apikey={TWELVE_DATA_API_KEY}"
    try:
        r = requests.get(url).json()
        if "values" in r:
            df = pd.DataFrame(r["values"])
            for col in ["open", "high", "low", "close"]:
                df[col] = df[col].astype(float)
            return df.iloc[::-1].reset_index(drop=True)
    except Exception as e:
        print(f"Data fetch error: {e}")
    return None

def evaluate_setup(df):
    """SMC Confluence Evaluation logic."""
    if df is None or len(df) < 10:
        return None
    
    last_close = df["close"].iloc[-1]
    prev_high = df["high"].iloc[-5:-1].max()
    prev_low = df["low"].iloc[-5:-1].min()
    
    score = 0.0
    action = None
    
    # Simple SMC Sweep & Shift Logic Example
    if last_close > prev_high:
        score += 4.0
        action = "BUY"
    elif last_close < prev_low:
        score += 4.0
        action = "SELL"
        
    if action:
        # FVG / Order block confluence boost
        score += 3.5  
        
        entry_min = round(last_close, 3)
        entry_max = round(last_close + (0.05 if action == "BUY" else -0.05), 3)
        
        if action == "BUY":
            tp1 = round(entry_min + 0.30, 3)
            tp2 = round(entry_min + 0.60, 3)
            sl = round(entry_min - 0.20, 3)
        else:
            tp1 = round(entry_min - 0.30, 3)
            tp2 = round(entry_min - 0.60, 3)
            sl = round(entry_min + 0.20, 3)
            
        return {
            "score": score,
            "action": action,
            "entry_min": min(entry_min, entry_max),
            "entry_max": max(entry_min, entry_max),
            "tp1": tp1,
            "tp2": tp2,
            "sl": sl
        }
    return None

# ==========================================
# 4. MAIN EXECUTION ENGINE
# ==========================================
def main():
    df = fetch_candles()
    if df is None:
        print("Failed to fetch market data.")
        return
        
    current_price = df["close"].iloc[-1]
    active_trade = get_active_trade(SYMBOL)

    # --------------------------------------
    # A. MONITOR ACTIVE TRADE FOR EDITS
    # --------------------------------------
    if active_trade:
        msg_id = active_trade["message_id"]
        status = active_trade["status"]
        action = active_trade["action"]
        entry = active_trade["entry_min"]
        tp1 = active_trade["tp1"]
        tp2 = active_trade["tp2"]
        sl = active_trade["sl"]

        # Check TP1 Hit
        if status == "PENDING":
            tp1_triggered = (action == "BUY" and current_price >= tp1) or (action == "SELL" and current_price <= tp1)
            sl_triggered = (action == "BUY" and current_price <= sl) or (action == "SELL" and current_price >= sl)

            if tp1_triggered:
                pips = round(abs(tp1 - entry) * 100, 1)
                if edit_signal(msg_id, SYMBOL, action, entry, active_trade["entry_max"], tp1, tp2, sl, "TP1_HIT", pips):
                    update_trade_status(SYMBOL, "TP1_HIT", sl_moved_to_be=True)
                    print(f"Updated {SYMBOL} to TP1_HIT")
            elif sl_triggered:
                pips = round(-abs(entry - sl) * 100, 1)
                if edit_signal(msg_id, SYMBOL, action, entry, active_trade["entry_max"], tp1, tp2, sl, "SL_HIT", pips):
                    update_trade_status(SYMBOL, "SL_HIT")
                    print(f"Updated {SYMBOL} to SL_HIT")

        # Check TP2 Hit
        elif status == "TP1_HIT":
            tp2_triggered = (action == "BUY" and current_price >= tp2) or (action == "SELL" and current_price <= tp2)
            be_sl_triggered = (action == "BUY" and current_price <= entry) or (action == "SELL" and current_price >= entry)

            if tp2_triggered:
                pips = round(abs(tp2 - entry) * 100, 1)
                if edit_signal(msg_id, SYMBOL, action, entry, active_trade["entry_max"], tp1, tp2, sl, "TP2_HIT", pips):
                    update_trade_status(SYMBOL, "CLOSED")
                    print(f"Updated {SYMBOL} to TP2_HIT (Closed)")
            elif be_sl_triggered:
                if edit_signal(msg_id, SYMBOL, action, entry, active_trade["entry_max"], tp1, tp2, sl, "SL_HIT", pips=0):
                    update_trade_status(SYMBOL, "CLOSED")
                    print(f"Trade closed at Breakeven")

    # --------------------------------------
    # B. SCAN FOR NEW SIGNALS
    # --------------------------------------
    else:
        setup = evaluate_setup(df)
        if setup and setup["score"] >= ALERT_THRESHOLD:
            print(f"High-quality setup detected! Score: {setup['score']}")
            msg_id = send_new_signal(
                SYMBOL, setup["action"], setup["entry_min"], setup["entry_max"],
                setup["tp1"], setup["tp2"], setup["sl"]
            )
            if msg_id:
                register_trade(
                    SYMBOL, msg_id, setup["action"], setup["entry_min"], setup["entry_max"],
                    setup["tp1"], setup["tp2"], setup["sl"]
                )
                print(f"Registered new signal. Telegram Message ID: {msg_id}")
        else:
            print(f"No setup above threshold {ALERT_THRESHOLD}.")

if __name__ == "__main__":
    main()
