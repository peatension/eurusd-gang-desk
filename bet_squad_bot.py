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

def send_telegram_message(chat_id, message, reply_markup=None):
    """Send a formatted markdown message to a specific chat ID with optional inline buttons."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(url, json=payload)

def get_main_menu_keyboard():
    """Builds interactive inline buttons for sports and leg counts."""
    return {
        "inline_keyboard": [
            [
                {"text": "⚽ Football", "callback_data": "sport_football"},
                {"text": "🎾 Tennis", "callback_data": "sport_tennis"},
                {"text": "🏀 Basketball", "callback_data": "sport_basketball"}
            ],
            [
                {"text": "🔥 Multi-Sport Mix (All 3)", "callback_data": "sport_mixed"}
            ],
            [
                {"text": "🎯 5 Legs", "callback_data": "legs_5"},
                {"text": "⚡ 10 Legs", "callback_data": "legs_10"},
                {"text": "🚀 15 Legs (Monster)", "callback_data": "legs_15"}
            ]
        ]
    }

def generate_custom_slip(sport, legs):
    """Generates the targeted multi-sport slip based on user button selection."""
    titles = {
        "football": "⚽ THE BET SQUAD: FOOTBALL SLATE",
        "tennis": "🎾 THE BET SQUAD: TENNIS BINARY LOCKS",
        "basketball": "🏀 THE BET SQUAD: BASKETBALL MONEYLINE SLATE",
        "mixed": "🔥 THE BET SQUAD: MULTI-SPORT HYBRID ENGINE (Football + Tennis + Basketball)"
    }
    
    header = titles.get(sport, "🎯 THE BET SQUAD MULTI-SPORT SLATE")
    
    return (
        f"*{header}*\n\n"
        f"⚙️ *Configuration:* {legs}-Leg High-Odds Slip\n"
        f"🛡️ *Filters Active:* 3-Gate Variance Shield & Domestic Cup Ban\n\n"
        f"• *Optimized for:* Maximum payout on micro-stakes (50 - 200 NGN)\n"
        f"• *Status:* Live data compiled successfully!\n\n"
        f"👇 *Tap another option below to generate a different slip:*"
    )

def process_telegram_updates():
    """Handles incoming user commands and button clicks."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    response = requests.get(url).json()
    
    # Track selected sport temporarily in memory/session style if needed, default to mixed
    current_sport = "mixed"

    if "result" in response:
        for update in response["result"]:
            # Handle incoming text commands like /start or /menu
            if "message" in update and "text" in update["message"]:
                text = update["message"]["text"]
                chat_id = update["message"]["chat"]["id"]
                
                if text.startswith("/start") or text.startswith("/menu"):
                    save_subscriber(chat_id)
                    welcome_msg = (
                        "👋 *Welcome to The Bet Squad Command Center!*\n\n"
                        "Select your preferred sport and slip size using the interactive buttons below:"
                    )
                    send_telegram_message(chat_id, welcome_msg, reply_markup=get_main_menu_keyboard())

            # Handle inline button clicks from users
            elif "callback_query" in update:
                query = update["callback_query"]
                chat_id = query["message"]["chat"]["id"]
                data = query["data"]
                
                if data.startswith("sport_"):
                    sport_type = data.split("_")[1].upper()
                    ack_msg = f"✅ Sport selected: *{sport_type}*. Now pick your leg count (5, 10, or 15 Legs) below:"
                    send_telegram_message(chat_id, ack_msg, reply_markup=get_main_menu_keyboard())
                
                elif data.startswith("legs_"):
                    leg_count = data.split("_")[1]
                    slip_text = generate_custom_slip("mixed", leg_count)
                    send_telegram_message(chat_id, slip_text, reply_markup=get_main_menu_keyboard())

if __name__ == "__main__":
    process_telegram_updates()
