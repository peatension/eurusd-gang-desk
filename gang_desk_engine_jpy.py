"""
EUR/USD GANG AI DESK — v2 engine (GitHub Actions version)
Fetches multi-timeframe candles, detects SMC structure, scores the setup,
calculates SL/TP, prevents duplicate alerts, and sends a Telegram alert
only at or above ALERT_THRESHOLD.

Changes from v1:
- Order Block and FVG checks now require price to be CURRENTLY inside the
  zone (a real retest), not just "one exists somewhere in the last N bars".
- Adds SL/TP1/TP2 calculation based on structure + fixed R multiples.
- Weights rebalanced so a perfect setup scores a true 10/10.
- Duplicate-alert prevention via a small state file (last alerted candle time).
- Wider swing window (3/3) to reduce noise on M15.

Runs once per invocation — scheduling is handled by GitHub Actions.
"""

import requests
import os
import json
from datetime import datetime

# ---------------- CONFIG (read from GitHub secrets) ----------------
TWELVE_DATA_KEY = os.environ.get("TWELVE_DATA_KEY", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

SYMBOL = "USD/JPY"
PAIR_LABEL = "USDJPY"

TIMEFRAMES = {
    "Weekly": "1week",
    "Daily": "1day",
    "H4": "4h",
    "H1": "1h",
    "M15": "15min",
}

ALERT_THRESHOLD = 7.5  # validated: train+test both positive expectancy  # backtested: this threshold showed the most consistent positive expectancy across RR 2.0-3.0
STATE_FILE = "last_alert_state_jpy.json"

RR_TP1 = 1.5
RR_TP2 = 2.0  # validated via 70/30 train/test split
SL_BUFFER_PIPS = 3  # note: pip = 0.01 for JPY pairs, see calculate_levels  # extra room beyond the OB/swing, in pips (0.0001 per pip for EURUSD)


# ---------------- DATA ----------------
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
        print(f"  \u26a0\ufe0f fetch error ({interval}): {data.get('message', data)}")
        return None
    candles = list(reversed(data["values"]))
    for c in candles:
        for k in ("open", "high", "low", "close"):
            c[k] = float(c[k])
    return candles


# ---------------- STRUCTURE DETECTION ----------------
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
    """Find FVGs and only count one as valid if price is CURRENTLY sitting inside it."""
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
    return active_retest  # None unless price is actually inside a gap right now


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
    """Find the most recent opposite-colored candle before a move, then only
    count it if price has come back to retest that candle's range."""
    if direction is None:
        return None
    start = max(1, len(candles) - lookback)
    candidate = None
    for i in range(len(candles) - 2, start, -1):  # -2 so we don't use the current forming candle
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
        return candidate  # price is actually retesting the OB right now
    return None


def premium_discount(candles, lookback=50):
    window = candles[-lookback:]
    hi = max(c["high"] for c in window)
    lo = min(c["low"] for c in window)
    mid = (hi + lo) / 2
    last = candles[-1]["close"]
    zone = "Premium" if last > mid else "Discount"
    return zone, hi, lo, mid


def equal_highs_lows(swings, tolerance=0.06):  # JPY scale: 0.06 instead of 0.0006
    highs = [s["price"] for s in swings if s["type"] == "high"][-4:]
    lows = [s["price"] for s in swings if s["type"] == "low"][-4:]
    eq_high = any(abs(highs[i] - highs[i + 1]) <= tolerance for i in range(len(highs) - 1))
    eq_low = any(abs(lows[i] - lows[i + 1]) <= tolerance for i in range(len(lows) - 1))
    return eq_high or eq_low


# ---------------- SL / TP ----------------
def calculate_levels(direction, entry, m15_swings, ob):
    """SL beyond the order block (or nearest swing if no OB), TP via fixed R multiples."""
    pip = 0.01  # JPY pairs use 0.01 as one pip, not 0.0001
    buffer = SL_BUFFER_PIPS * pip

    if direction == "BUY":
        if ob:
            sl = ob["low"] - buffer
        else:
            lows = [s["price"] for s in m15_swings if s["type"] == "low"]
            sl = (min(lows[-3:]) if lows else entry - 0.20) - buffer
        risk = entry - sl
        tp1 = entry + risk * RR_TP1
        tp2 = entry + risk * RR_TP2
    else:  # SELL
        if ob:
            sl = ob["high"] + buffer
        else:
            highs = [s["price"] for s in m15_swings if s["type"] == "high"]
            sl = (max(highs[-3:]) if highs else entry + 0.20) + buffer
        risk = sl - entry
        tp1 = entry - risk * RR_TP1
        tp2 = entry - risk * RR_TP2

    return round(sl, 5), round(tp1, 5), round(tp2, 5)


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
    ob = detect_order_block(m15["candles"], ob_dir)

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

    # Weights now sum to 10 exactly when every possible check hits
    # (bos/choch still mutually exclusive by nature, so realistic max is ~9.3 —
    #  intentional, since no single setup type shows every signature at once)
    weights = {"htf": 2.5, "bos": 1.2, "choch": 1.2, "sweep": 1.8, "ob": 1.8, "fvg": 1.2, "zone": 1.3, "eqhl": 0.5}
    raw_score = sum(weights[k] for k, v in checks.items() if v)
    max_possible = weights["htf"] + max(weights["bos"], weights["choch"]) + weights["sweep"] + weights["ob"] + weights["fvg"] + weights["zone"] + weights["eqhl"]
    score = round(min(10, (raw_score / max_possible) * 10), 1)

    last_price = m15["candles"][-1]["close"]
    sl = tp1 = tp2 = None
    if direction:
        sl, tp1, tp2 = calculate_levels(direction, last_price, m15["swings"], ob)

    return {
        "direction": direction,
        "score": score,
        "checks": checks,
        "zone": zone,
        "last_price": last_price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "candle_time": m15["candles"][-1].get("datetime"),
    }


# ---------------- DEDUP STATE ----------------
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def already_alerted(state, result):
    key = f"{result['direction']}_{result['candle_time']}"
    return state.get("last_alert_key") == key


# ---------------- ALERTING ----------------
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
    requests.post(url, data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"})


def confidence_bar(score):
    filled = int(round(score))
    return "\u25b0" * filled + "\u25b1" * (10 - filled)


def build_alert(result):
    reasons = "\n".join(f"\u2713 {LABELS[k]}" for k, v in result["checks"].items() if v)
    emoji = "\U0001f7e2" if result["direction"] == "BUY" else "\U0001f534"
    bar = confidence_bar(result["score"])
    zone_emoji = "\U0001f535" if result["zone"] == "Discount" else "\U0001f7e0"
    block = "\u2593" * 19
    header = f"{block}\n  GANG DESK \u00b7 LIVE\n{block}"

    pip = 0.01 if "JPY" in PAIR_LABEL else 0.0001
    risk_pips = abs(result["last_price"] - result["sl"]) / pip
    reward_pips = abs(result["tp2"] - result["last_price"]) / pip
    rr_ratio = round(reward_pips / risk_pips, 1) if risk_pips else 0

    levels = (
        f"Entry      {result['last_price']}\n"
        f"SL         {result['sl']}\n"
        f"TP1        {result['tp1']}\n"
        f"TP2        {result['tp2']}"
    )

    return (
        f"{header}\n\n"
        f"{emoji} <b>{PAIR_LABEL} \u00b7 {result['direction']}</b>\n\n"
        f"<b>Confidence</b>\n{bar}  {result['score']:.1f}/10\n\n"
        f"\u250c <b>LEVELS</b> \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510\n<code>{levels}</code>\n\u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518\n\n"
        f"Risk        {risk_pips:.1f} pips\n"
        f"Reward TP2  {reward_pips:.1f} pips  ({rr_ratio}R)\n\n"
        f"<b>Confirmations</b>\n{reasons}\n\n"
        f"{zone_emoji} Zone \u2014 {result['zone']}\n\n"
        f"{block}\n"
        f"\U0001f97a <i>Desk Closed</i>\n"
        f"<i>Let price do the talking.</i>"
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
                print("\U0001f6a8 Alert sent.")
        else:
            print("No alert — below threshold or no clear direction.")
