"""
EUR/USD GANG AI DESK — v3 engine (GitHub Actions version)
Adds: URL buttons under each alert (chart, sheet, mark outcome),
and automatic WIN/LOSS detection that edits past alert messages
once price actually hits SL or TP. No always-on server needed —
works within the existing scheduled-run architecture.
"""

import requests
import os
import json
from datetime import datetime

TWELVE_DATA_KEY = os.environ["TWELVE_DATA_KEY"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
SHEET_URL = "https://script.google.com/macros/s/AKfycbzG8tMonpxdGyHgkrvXOaGjDJPpvqgO4Rkuey8wxu5jt7nr7HB4S7fO1fycKIKW4zguQA/exec"
MARK_OUTCOME_FORM_URL = ""  # fill in once the Google Form is created

SYMBOL = "EUR/USD"
PAIR_LABEL = "EURUSD"

TIMEFRAMES = {
    "Weekly": "1week",
    "Daily": "1day",
    "H4": "4h",
    "H1": "1h",
    "M15": "15min",
}

ALERT_THRESHOLD = 7.5
STATE_FILE = "last_alert_state.json"

RR_TP1 = 1.5
RR_TP2 = 3.0
SL_BUFFER_PIPS = 3


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


def equal_highs_lows(swings, tolerance=0.0006):
    highs = [s["price"] for s in swings if s["type"] == "high"][-4:]
    lows = [s["price"] for s in swings if s["type"] == "low"][-4:]
    eq_high = any(abs(highs[i] - highs[i + 1]) <= tolerance for i in range(len(highs) - 1))
    eq_low = any(abs(lows[i] - lows[i + 1]) <= tolerance for i in range(len(lows) - 1))
    return eq_high or eq_low


def calculate_levels(direction, entry, m15_swings, ob):
    pip = 0.0001
    buffer = SL_BUFFER_PIPS * pip

    if direction == "BUY":
        if ob:
            sl = ob["low"] - buffer
        else:
            lows = [s["price"] for s in m15_swings if s["type"] == "low"]
            sl = (min(lows[-3:]) if lows else entry - 0.0020) - buffer
        risk = entry - sl
        tp1 = entry + risk * RR_TP1
        tp2 = entry + risk * RR_TP2
    else:
        if ob:
            sl = ob["high"] + buffer
        else:
            highs = [s["price"] for s in m15_swings if s["type"] == "high"]
            sl = (max(highs[-3:]) if highs else entry + 0.0020) + buffer
        risk = sl - entry
        tp1 = entry - risk * RR_TP1
        tp2 = entry - risk * RR_TP2

    return round(sl, 5), round(tp1, 5), round(tp2, 5)


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
        "candle_high": m15["candles"][-1]["high"],
        "candle_low": m15["candles"][-1]["low"],
    }


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


def build_chart_url():
    return f"https://www.tradingview.com/chart/?symbol=OANDA:{PAIR_LABEL}"


def build_inline_keyboard():
    buttons = [
        [
            {"text": "📊 View Chart", "url": build_chart_url()},
        ],
        [
            {"text": "📋 View Sheet", "url": "https://docs.google.com/spreadsheets/d/1xN8Div5H3r84m-6mie1ln1OdKbeKa1yiRcroRAh2xts/edit?usp=sharing"},
        ],
    ]
    if MARK_OUTCOME_FORM_URL:
        buttons.append([{"text": "✅ Mark Outcome", "url": MARK_OUTCOME_FORM_URL}])
    return {"inline_keyboard": buttons}


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": json.dumps(build_inline_keyboard()),
    })
    try:
        return resp.json()["result"]["message_id"]
    except Exception:
        return None


def edit_telegram(message_id, text):
    if message_id is None:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    requests.post(url, data={
        "chat_id": CHAT_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    })


def send_to_sheet(result, outcome=None):
    payload = {
        "pair": PAIR_LABEL,
        "direction": result["direction"],
        "score": result["score"],
        "entry": result["last_price"],
        "sl": result["sl"],
        "tp1": result["tp1"],
        "tp2": result["tp2"],
        "zone": result["zone"],
    }
    if outcome:
        payload["outcome"] = outcome
    try:
        requests.post(SHEET_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"Sheet log failed: {e}")


def confidence_bar(score):
    filled = int(round(score))
    return "▰" * filled + "▱" * (10 - filled)


def build_alert(result):
    reasons = "\n".join(f"✓ {LABELS[k]}" for k, v in result["checks"].items() if v)
    emoji = "🟢" if result["direction"] == "BUY" else "🔴"
    bar = confidence_bar(result["score"])
    zone_emoji = "🔵" if result["zone"] == "Discount" else "🟠"
    divider = "━" * 19

    levels = (
        f"Entry      {result['last_price']}\n"
        f"SL         {result['sl']}\n"
        f"TP1        {result['tp1']}\n"
        f"TP2        {result['tp2']}"
    )

    return (
        f"📡 <b>TENSION TRADING DESK</b>\n"
        f"{divider}\n"
        f"{emoji}  <b>{PAIR_LABEL} · {result['direction']}</b>\n"
        f"{divider}\n\n"
        f"<b>Confidence</b>\n{bar}  {result['score']:.1f}/10\n\n"
        f"<code>{levels}</code>\n\n"
        f"<b>Confirmations</b>\n{reasons}\n\n"
        f"{zone_emoji} Zone — {result['zone']}\n\n"
        f"{divider}\n"
        f"<i>Built on Data.</i>\n"
        f"<i>Driven by Discipline.</i>"
    )


def build_resolved_text(original_text, outcome, hit_price):
    banner = "✅ <b>WIN — TP hit</b>" if outcome == "WIN" else "❌ <b>LOSS — SL hit</b>"
    return f"{original_text}\n\n━━━━━━━━━━━━━━━━━━━\n{banner}\nClosed at: {hit_price}"


def check_pending_trades(state, latest_high, latest_low):
    """Check all open trades against the latest candle's high/low. Edit
    the Telegram message and log to Sheet once a trade resolves."""
    pending = state.get("pending", [])
    still_open = []

    for trade in pending:
        direction = trade["direction"]
        sl = trade["sl"]
        tp2 = trade["tp2"]
        outcome = None
        hit_price = None

        if direction == "BUY":
            if latest_low <= sl:
                outcome = "LOSS"
                hit_price = sl
            elif latest_high >= tp2:
                outcome = "WIN"
                hit_price = tp2
        else:
            if latest_high >= sl:
                outcome = "LOSS"
                hit_price = sl
            elif latest_low <= tp2:
                outcome = "WIN"
                hit_price = tp2

        if outcome:
            resolved_text = build_resolved_text(trade["original_text"], outcome, hit_price)
            edit_telegram(trade.get("message_id"), resolved_text)
            send_to_sheet(trade["result_snapshot"], outcome=outcome)
            print(f"Trade resolved: {outcome} at {hit_price}")
        else:
            still_open.append(trade)

    state["pending"] = still_open
    return state


if __name__ == "__main__":
    print(f"[{datetime.now()}] Scanning {PAIR_LABEL}...")
    result = analyze()
    if not result:
        print("Skipped — data fetch issue this run.")
    else:
        state = load_state()

        # First, check if any previously sent alerts have resolved
        state = check_pending_trades(state, result["candle_high"], result["candle_low"])

        print(f"Direction: {result['direction']}  Score: {result['score']:.1f}/10")
        if result["direction"] and result["score"] >= ALERT_THRESHOLD:
            if already_alerted(state, result):
                print("Already alerted this candle — skipping duplicate.")
            else:
                alert_text = build_alert(result)
                message_id = send_telegram(alert_text)
                send_to_sheet(result)

                pending = state.get("pending", [])
                pending.append({
                    "message_id": message_id,
                    "direction": result["direction"],
                    "sl": result["sl"],
                    "tp2": result["tp2"],
                    "original_text": alert_text,
                    "result_snapshot": result,
                })
                state["pending"] = pending

                state["last_alert_key"] = f"{result['direction']}_{result['candle_time']}"
                save_state(state)
                print("🚨 Alert sent.")
        else:
            print("No alert — below threshold or no clear direction.")

        save_state(state)
