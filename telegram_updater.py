import os
import requests
from state_manager import load_state, save_state, update_trade_status

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def format_alert_text(symbol, action, entry_min, entry_max, tp1, tp2, sl, status="PENDING", pips=0):
    """Formats the signal text matching the dynamic spec."""
    
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
    """Sends initial signal message and returns message_id."""
    text = format_alert_text(symbol, action, entry_min, entry_max, tp1, tp2, sl, "PENDING")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    
    try:
        r = requests.post(url, json=payload).json()
        if r.get("ok"):
            return r["result"]["message_id"]
    except Exception as e:
        print(f"Error sending message: {e}")
    return None

def edit_signal(message_id, symbol, action, entry_min, entry_max, tp1, tp2, sl, new_status, pips=0):
    """Edits existing Telegram message in place."""
    text = format_alert_text(symbol, action, entry_min, entry_max, tp1, tp2, sl, new_status, pips)
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": CHAT_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    try:
        r = requests.post(url, json=payload).json()
        if r.get("ok"):
            print(f"Updated Telegram message {message_id} to {new_status}")
            return True
        else:
            print(f"Failed to edit message: {r}")
    except Exception as e:
        print(f"Error editing message: {e}")
    return False
