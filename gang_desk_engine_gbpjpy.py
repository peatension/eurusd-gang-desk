import os
import json
import requests
import pandas as pd

# Fetch Environment Variables from GitHub Secrets
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
API_KEY = os.environ.get("TWELVE_DATA_API_KEY")

# Default URLs for Inline Buttons
CHART_URL = "https://www.tradingview.com/chart/?symbol=OANDA:GBPJPY"
SHEET_URL = "https://docs.google.com"

STATE_FILE = "state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {"trades": {}}
    return {"trades": {}}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def fetch_market_data(symbol="GBP/JPY", interval="30min", outputsize=50):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={outputsize}&apikey={API_KEY}"
    response = requests.get(url).json()
    if "values" not in response:
        print(f"Error fetching data: {response}")
        return None
    
    df = pd.DataFrame(response["values"])
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["open"] = df["open"].astype(float)
    return df

def analyze_market(df):
    if df is None or len(df) < 10:
        return None
    
    # Calculate simple price dynamics / mock engine metrics
    current_close = df["close"].iloc[0]
    high_recent = df["high"].max()
    low_recent = df["low"].min()
    
    # Example logic threshold assessment
    confidence_score = 88  # Target confidence level
    action = "SELL"
    entry_low = round(current_close, 3)
    entry_high = round(current_close + 0.050, 3)
    tp1 = round(current_close - 0.250, 3)
    tp2 = round(current_close - 0.550, 3)
    sl = round(entry_high + 0.200, 3)

    return {
        "pair": "GBP/JPY",
        "action": action,
        "confidence": f"{confidence_score}%",
        "entry_low": entry_low,
        "entry_high": entry_high,
        "tp1": tp1,
        "tp2": tp2,
        "sl": sl,
        "status": "Waiting for Entry"
    }

def send_or_update_telegram(signal_data, state):
    message_text = (
        f"🟢 *FOREX SIGNAL ALERT*\n"
        f"______________________\n\n"
        f"*Pair:* {signal_data['pair']}\n"
        f"*Action:* {signal_data['action']}\n"
        f"*Confidence:* ⚡ {signal_data['confidence']}\n"
        f"*Entry Zone:* {signal_data['entry_low']} - {signal_data['entry_high']}\n\n"
        f"*Targets:*\n"
        f"🎯 *TP1:* {signal_data['tp1']} (Pending)\n"
        f"🎯 *TP2:* {signal_data['tp2']} (Pending)\n"
        f"🛑 *SL:* {signal_data['sl']}\n"
        f"______________________\n\n"
        f"*Status:* {signal_data['status']}"
    )

    reply_markup = {
        "inline_keyboard": [
            [{"text": "📊 View Chart", "url": CHART_URL}],
            [{"text": "📋 View Sheet", "url": SHEET_URL}]
        ]
    }

    trade_key = signal_data["pair"]
    existing_msg_id = state.get("trades", {}).get(trade_key, {}).get("message_id")

    if existing_msg_id:
        # Edit existing message
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
        payload = {
            "chat_id": CHAT_ID,
            "message_id": existing_msg_id,
            "text": message_text,
            "parse_mode": "Markdown",
            "reply_markup": reply_markup
        }
    else:
        # Send fresh message
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": message_text,
            "parse_mode": "Markdown",
            "reply_markup": reply_markup
        }

    res = requests.post(url, json=payload).json()

    if res.get("ok"):
        msg_id = res["result"]["message_id"] if "result" in res else existing_msg_id
        if "trades" not in state:
            state["trades"] = {}
        state["trades"][trade_key] = {"message_id": msg_id, "data": signal_data}
        save_state(state)
        print("Telegram alert sent/updated successfully.")
    else:
        print(f"Telegram API Error: {res}")

def main():
    state = load_state()
    df = fetch_market_data()
    signal = analyze_market(df)
    
    if signal:
        send_or_update_telegram(signal, state)
    else:
        print("No setup above threshold.")

if __name__ == "__main__":
    main()
