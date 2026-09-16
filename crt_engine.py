"""
TEMPORARY FORCE TEST - CRT ENGINE
Sends one dummy alert so you can see the format in Telegram
"""

import requests
import os
from datetime import datetime, timezone

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("CHAT_ID", "")

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }, timeout=10)
    print("Telegram response:", resp.status_code, resp.text)
    return resp.json()

if __name__ == "__main__":
    print("Sending FORCE TEST alert...")

    text = (
        f"<b>TENSION TRADING DESK</b>\n"
        f"{'═'*21}\n"
        f"<b>CRT ENGINE</b>\n"
        f"{'─'*21}\n\n"
        f"🟢 <b>EURJPY · BUY</b> ▲\n"
        f"<i>Classic CRT / Turtle Soup</i>\n\n"
        f"<pre>"
        f"Entry  162.845\n"
        f"SL     162.710\n"
        f"TP1    162.980  (50%)\n"
        f"TP2    163.115  (Opposite)\n"
        f"</pre>\n"
        f"Range Size : 27.0 pips\n\n"
        f"{'═'*21}\n"
        f"<i>Built on Data. Driven by Discipline.</i>\n\n"
        f"<b>THIS IS A FORCE TEST</b>\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )

    result = send_telegram(text)
    print("Done. Check Telegram.")
