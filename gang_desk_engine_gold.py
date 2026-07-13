"""
XAU/USD GANG AI DESK — v2 engine (GitHub Actions version)
Fetches multi-timeframe candles, detects SMC structure, scores the setup,
calculates SL/TP, prevents duplicate alerts, and sends a Telegram alert
only at or above ALERT_THRESHOLD.
"""

import requests
import os
import json
from datetime import datetime

TWELVE_DATA_KEY = os.environ["TWELVE_DATA_KEY"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

SYMBOL = "XAU/USD"
PAIR_LABEL = "XAUUSD"

TIMEFRAMES = {
    "Weekly": "1week",
    "Daily": "1day",
    "H4": "4h",
    "H1": "1h",
    "M15": "15min",
}

ALERT_THRESHOLD = 7.5
STATE_FILE = "last_alert_state_gold.json"

RR_TP1 = 1.5
RR_TP2 = 3.0
SL_BUFFER_PIPS = 30


def fetch_candles(interval, count=150):
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
        print(f"  fetch error ({interval}): {data.get('message', data)}")
        return None
    candles = list(reversed(data["values"]))
    for c in candles:
        for k in ("open", "high", "low", "close"):
            c[k] = float(c[k])
    return candles


def find_swings(candles, left=3, right=3):
    swings = []
    for i in range(left, len(candles) - right):
        window_highs = [candles[j]["high"] for j in range(i - left, i + right + 1)]
        window_lows = [candles[j]["low"] for j in range(i - left, i + right + 1)]
        if candles[i]["high"] == max(window_highs):
            swings.append({"i": i, "type": "high", "price": candles[i]["high"]})
        if candles[i]["low"] == min(window_lows):
            swings.append({"i": i, "type": "low", "price": candles[i]["low"]})
    return swings


def get_bias(swings):
    highs = [s for s in swings if s["type"] == "high"][-3:]
    lows = [s for s in swings if s["type"] == "low"][-3:]
    if len(highs) < 2 or len(lows) < 2:
        return "Neutral"
    higher_highs = highs[-1]["price"] > highs[-2]["price"]
    higher_lows = lows[-1]["price"] > lows[-2]["price"]
    lower_highs = highs[-1]["price"] < highs[-2]["price"]
    lower_lows = lows[-1]["price"] < lows[-2]["price"]
    if higher_highs and higher_lows:
        return "Bullish"
    if lower_highs and lower_lows:
        return "Bearish"
    return "Neutral"


def detect_bos_choch(candles, swings, prior_bias):
    if len(swings) < 2:
        return None
    last_close = candles[-1]["close"]
    last_high_swing = next((s for s in reversed(swings) if s["type"] == "high"), None)
    last_low_swing = next((s for s in reversed(swings) if s["type"] == "low"), None)

    if last_high_swing and last_close > last_high_swing["price"]:
        return "BOS_up" if prior_bias in ("Bullish", "Neutral") else "CHoCH_up"
    if last_low_swing and last_close < last_low_swing["price"]:
        return "BOS_down" if prior_bias in ("Bearish", "Neutral") else "CHoCH_down"
    return None


def detect_fvg(candles, lookback=20):
    fvgs = []
    start = max(2, len(candles) - lookback)
    for i in range(start, len(candles)):
        c1, c3 = candles[i - 2], candles[i]
        if c3["low"] > c1["high"]:
            fvgs.append({"type": "bullish", "top": c3["low"], "bottom": c1["high"], "i": i})
        elif c3["high"] < c1["low"]:
            fvgs.append({"type": "bearish", "top": c1["low"], "bottom": c3["high"], "i": i})

    last_price = candles[-1]["close"]
    active_retest = None
    for fvg in reversed(fvgs):
        if fvg["bottom"] <= last_price <= fvg["top"]:
            active_retest = fvg
            break
    return active_retest


def detect_liquidity_sweep(candles, swings):
    if len(swings) < 2:
        return None
    recent = candles[-1]
    prior_highs = [s["price"] for s in swings if s["type"] == "high"][:-1][-3:]
    prior_lows = [s["price"] for s in swings if s["type"] == "low"][:-1][-3:]

    if prior_highs and recent["high"] > max(prior_highs) and recent["close"] < max(prior_highs):
        return "sell_side_sweep"
    if prior_lows and recent["low"] < min(prior_lows) and recent["close"] > min(prior_lows):
        return "buy_side_sweep"
    return None


def detect_order_block(candles, direction, lookback=20):
    if direction is None:
        return None
    start = max(1, len(candles) - lookback)
    candidate = None
    for i in range(len(candles) - 2, start, -1):
        c = candles[i]
        is_bull = c["close"] > c["open"]
        if direction == "up" and not is_bull:
            candidate = {"i": i, "high": c["high"], "low": c["low"]}
            break
        if direction == "down" and is_bull:
            candidate = {"i": i, "high": c["high"], "low": c["low"]}
            break
    if not candidate:
        return None

    last_price = candles[-1]["close"]
    if candidate["low"] <= last_price <= candidate["high"]:
        return candidate
    return None


def premium_discount(candles, lookback=50):
    window = candles[-lookback:]
    hi = max(c["high"] for c in window)
    lo = min(c["low"] for c in window)
    mid = (hi + lo) / 2
    last = candles[-1]["close"]
    zone = "Premium" if last > mid else "Discount"
    return zone, hi, lo, mid


def equal_highs_lows(swings, tolerance=0.5):
    highs = [s["price"] for s in swings if s["type"] == "high"][-4:]
    lows = [s["price"] for s in swings if s["type"] == "low"][-4:]
    eq_high = any(abs(highs[i] - highs[i + 1]) <= tolerance for i in range(len(highs) - 1))
    eq_low = any(abs(lows[i] - lows[i + 1]) <= tolerance for i in range(len(lows) - 1))
    return eq_high or eq_low


def calculate_levels(direction, entry, m15_swings, ob):
    pip = 0.1
    buffer = SL_BUFFER_PIPS * pip

    if direction == "BUY":
        if ob:
            sl = ob["low"] - buffer
        else:
            lows = [s["price"] for s in m15_swings if s["type"] == "low"]
            sl = (min(lows[-3:]) if lows else entry - 5.0) - buffer
        risk = entry - sl
        tp1 = entry + risk * RR_TP1
        tp2 = entry + risk * RR_TP2
    else:
        if ob:
            sl = ob["high"] + buffer
        else:
            highs = [s["price"] for s in m15_swings if s["type"] == "high"]
            sl = (max(highs[-3:]) if highs else entry + 5.0) + buffer
        risk = sl - entry
        tp1 = entry - risk * RR_TP1
        tp2 = entry - risk * RR_TP2

    return round(sl, 5), round(tp1, 5), round(tp2, 5)


LABELS = {
    "htf": "Weekly + Daily aligned",
    "bos": "Break of Structure confirmed",
    "choch": "CHoCH confirmed",
    "sweep": "Liquidity sweep detected",
    "ob": "Order Block retest confirmed",
    "fvg": "Fair Value Gap retest confirmed",
    "zone": "Correct Premium/Discount entry",
    "eqhl": "Equal Highs/Lows liquidity present",
}


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": text})


def build_alert(result):
    reasons = "\n".join(f"✓ {LABELS[k]}" for k, v in result["checks"].items() if v)
    return (
        f"🚨 {PAIR_LABEL} GANG ALERT 🚨\n\n"
        f"Pair: {PAIR_LABEL}\n"
        f"Direction: {result['direction']}\n"
        f"Confidence: {result['score']:.1f}/10\n\n"
        f"Entry: {result['last_price']}\n"
        f"SL: {result['sl']}\n"
        f"TP1: {result['tp1']}\n"
        f"TP2: {result['tp2']}\n\n"
        f"Reasons:\n{reasons}\n\n"
        f"Zone: {result['zone']}\n\n"
        f"🫡 Desk Closed.\nLet price do the talking."
    )


if __name__ == "__main__":
    print(f"[{datetime.now()}] Scanning {PAIR_LABEL}...")
    result = analyze()
    if not result:
        print("Skipped — data fetch issue this run.")
    else:
        print(f"Direction: {result['direction']}  Score: {result['score']:.1f}/10")
        state = load_state()
        if result["direction"] and result["score"] >= ALERT_THRESHOLD:
            if already_alerted(state, result):
                print("Already alerted this candle — skipping duplicate.")
            else:
                send_telegram(build_alert(result))
                state["last_alert_key"] = f"{result['direction']}_{result['candle_time']}"
                save_state(state)
                print("🚨 Alert sent.")
        else:
            print("No alert — below threshold or no clear direction.")
