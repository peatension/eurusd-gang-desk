import os
import json
import requests

# Environment Variables matching your GitHub Secrets
TELEGRAM_BOT_TOKEN = os.environ.get("BET_BOT_KEY")
FOOTBALL_API_KEY = os.environ.get("BET_API")
ADMIN_CHAT_ID = os.environ.get("ADMIN_ID")
SUBSCRIBERS_FILE = "subscribers.json"

def load_subscribers():
    """Load all chat IDs of users who have pressed Start."""
    if os.path.exists(SUBSCRIBERS_FILE):
        with open(SUBSCRIBERS_FILE, "r") as f:
            return json.load(f)
    return [ADMIN_CHAT_ID] if ADMIN_CHAT_ID else []

def save_subscriber(chat_id):
    """Save a new user's chat ID if they haven't started the bot yet."""
    subs = load_subscribers()
    if chat_id not in subs:
        subs.append(chat_id)
        with open(SUBSCRIBERS_FILE, "w") as f:
            json.dump(subs, f)

def send_telegram_message(chat_id, message):
    """Send a formatted markdown message to a specific chat ID."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload)

def check_updates_and_handle_start():
    """Check for new /start interactions from users."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    response = requests.get(url).json()
    
    if "result" in response:
        for update in response["result"]:
            if "message" in update and "text" in update["message"]:
                text = update["message"]["text"]
                chat_id = update["message"]["chat"]["id"]
                
                if text.startswith("/start"):
                    save_subscriber(chat_id)
                    welcome_msg = (
                        "👋 *Welcome to The Bet Squad Bot!*\n\n"
                        "You are now locked into our data-driven, structured betting slates. "
                        "You will receive daily automated Core Locks and Multiplier Engines right here!"
                    )
                    send_telegram_message(chat_id, welcome_msg)

def broadcast_daily_slate():
    """Broadcasts today's structured slate to all subscribed users."""
    subs = load_subscribers()

    slate_message = (
        "🚨 *THE BET SQUAD: AUTOMATED SLATE DROP*\n\n"
        "🎯 *Tier 1: Core Locks (High Probability)*\n"
        "• International Straight Mismatches Loaded\n\n"
        "🚀 *Tier 2: 14-Leg Multiplier Engine*\n"
        "• 70/30 Structural-to-Flow Ratio Applied\n"
        "• Zero Domestic Cup Traps Filtered Out\n\n"
        "💰 *Target Stake:* 50 - 200 NGN"
    )

    for chat_id in subs:
        send_telegram_message(chat_id, slate_message)

if __name__ == "__main__":
    check_updates_and_handle_start()
    broadcast_daily_slate()
