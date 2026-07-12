
"""
EUR/USD GANG AI DESK — v1 engine (GitHub Actions version)
Fetches multi-timeframe candles, detects SMC structure heuristically,
scores the setup, and sends a Telegram alert only at 8.0/10 or higher.
Runs once per invocation — the schedule is handled by GitHub Actions.
"""

import requests
import os
from datetime import datetime

# ---------------- CONFIG (read from GitHub secrets) ----------------
TWELVE_DATA_KEY = os.environ["TWELVE_DATA_KEY"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

SYMBOL = "EUR/USD"
PAIR_LABEL = "EURUSD"

TIMEFRAMES = {
    "Weekly": "1week",
    "Daily": "1day",
    "H4": "4h",
    "H1": "1h",
    "M15": "15min",
}

ALERT_THRESHOLD = 8.0

# ---------------- DATA ----------------
def fetch_candles(interval, count=120):
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
        print(f"  ⚠️ fetch error ({interval}): {data.get('message', data)}")
        return None
    candles = list(reversed(data["values"]))
    for c in candles:
        for k in ("open", "high", "low", "close"):
            c[k] = float(c[k])
    return candles


# ---------------- STRUCTURE DETECTION ----------------
def find_swings(candles, left=2, right=2):
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


def detect_fvg(candles, lookback=15):
    fvgs = []
    start = max(2, len(candles) - lookback)
    for i in range(start, len(candles)):
        c1, c3 = candles[i - 2], candles[i]
        if c3["low"] > c1["high"]:
            fvgs.append({"type": "bullish", "top": c3["low"], "bottom": c1["high"], "i": i})
        elif c3["high"] < c1["low"]:
            fvgs.append({"type": "bearish", "top": c1["low"], "bottom": c3["high"], "i": i})

    last_price = candles[-1]["close"]
    for fvg in reversed(fvgs):
        if fvg["bottom"] <= last_price <= fvg["top"]:
            return fvg
    return fvgs[-1] if fvgs else None


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


def detect_order_block(candles, direction, lookback=15):
    start = max(1, len(candles) - lookback)
    for i in range(len(candles) - 1, start, -1):
        c = candles[i]
        is_bull = c["close"] > c["open"]
        if direction == "up" and not is_bull:
            return {"i": i, "high": c["high"], "low": c["low"]}
        if direction == "down" and is_bull:
            return {"i": i, "high": c["high"], "low": c["low"]}
    return None


def premium_discount(candles, lookback=50):
    window = candles[-lookback:]
    hi = max(c["high"] for c in window)
    lo = min(c["low"] for c in window)
    mid = (hi + lo) / 2
    last = candles[-1]["close"]
    zone = "Premium" if last > mid else "Discount"
    return zone, hi, lo, mid


def equal_highs_lows(swings, tolerance=0.0006):
    highs = [s["price"] for s in swings if s["type"] == "high"][-4:]
    lows = [s["price"] for s in swings if s["type"] == "low"][-4:]
    eq_high = any(abs(highs[i] - highs[i + 1]) <= tolerance for i in range(len(highs) - 1))
    eq_low = any(abs(lows[i] - lows[i + 1]) <= tolerance for i in range(len(lows) - 1))
    return eq_high or eq_low


# ---------------- SCORING ----------------
def analyze():
    tf_data = {}
    for label, interval in TIMEFRAMES.items():
        candles = fetch_candles(interval)
        if not candles:
            return None
        swings = find_swings(candles)
        bias = get_bias(swings)
        tf_data[label] = {"candles": candles, "swings": swings, "bias": bias}

    weekly_bias = tf_data["Weekly"]["bias"]
    daily_bias = tf_data["Daily"]["bias"]

    if weekly_bias == "Bullish" and daily_bias in ("Bullish", "Neutral"):
        direction = "BUY"
    elif weekly_bias == "Bearish" and daily_bias in ("Bearish", "Neutral"):
        direction = "SELL"
    elif daily_bias in ("Bullish", "Bearish"):
        direction = "BUY" if daily_bias == "Bullish" else "SELL"
    else:
        direction = None

    h1 = tf_data["H1"]
    m15 = tf_data["M15"]
    h1_signal = detect_bos_choch(h1["candles"], h1["swings"], h1["bias"])
    sweep = detect_liquidity_sweep(m15["candles"], m15["swings"])
    fvg = detect_fvg(m15["candles"])
    zone, hi, lo, mid = premium_discount(m15["candles"])
    eqhl = equal_highs_lows(m15["swings"])
    ob_dir = "up" if direction == "BUY" else "down" if direction == "SELL" else None
    ob = detect_order_block(m15["candles"], ob_dir) if ob_dir else None

    checks = {
        "htf": weekly_bias == daily_bias and weekly_bias in ("Bullish", "Bearish"),
        "bos": h1_signal in ("BOS_up", "BOS_down"),
        "choch": h1_signal in ("CHoCH_up", "CHoCH_down"),
        "sweep": sweep is not None,
        "ob": ob is not None,
        "fvg": fvg is not None,
        "zone": (zone == "Discount" and direction == "BUY") or (zone == "Premium" and direction == "SELL"),
        "eqhl": eqhl,
    }

    weights = {"htf": 2, "bos": 1, "choch": 1, "sweep": 1.5, "ob": 1.5, "fvg": 1, "zone": 1, "eqhl": 0.5}
    score = min(10, sum(weights[k] for k, v in checks.items() if v))

    last_price = m15["candles"][-1]["close"]

    return {
        "direction": direction,
        "score": score,
        "checks": checks,
        "zone": zone,
        "last_price": last_price,
    }


# ---------------- ALERTING ----------------
LABELS = {
    "htf": "Weekly + Daily aligned",
    "bos": "Break of Structure confirmed",
    "choch": "CHoCH confirmed",
    "sweep": "Liquidity sweep detected",
    "ob": "Order Block tapped",
    "fvg": "Fair Value Gap tapped",
    "zone": "Correct Premium/Discount entry",
    "eqhl": "Equal Highs/Lows liquidity present",
}


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": text})


def build_alert(result):
    reasons = "\n".join(f"✓ {LABELS[k]}" for k, v in result["checks"].items() if v)
    return (
        f"🚨 EUR/USD GANG ALERT 🚨\n\n"
        f"Pair: {PAIR_LABEL}\n"
        f"Direction: {result['direction']}\n"
        f"Confidence: {result['score']:.1f}/10\n\n"
        f"Reasons:\n{reasons}\n\n"
        f"Price: {result['last_price']}\n"
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
        if result["direction"] and result["score"] >= ALERT_THRESHOLD:
            send_telegram(build_alert(result))
            print("🚨 Alert sent.")
        else:
            print("No alert — below threshold or no clear direction.")
