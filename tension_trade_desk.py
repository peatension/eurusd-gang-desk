"""
TENSION TRADING DESK — production engine v4

Multi-TF:
  H4  = bias
  H1  = SMC / Advanced SMC POI scoring
  M15 = entry + trade management

Models (separate signals, NOT combined):
  SMC
  ADV_SMC

Lifecycle:
  SIGNAL (active immediately at close)
    -> TP1 HIT (message edit)
    -> TP2 WIN / SL LOSS (message edit)

No SETUP / WAITING FOR ENTRY stage.
"""

import os
import json
import time
import requests
import pandas as pd
import numpy as np

# ============================================================
# ENV
# ============================================================

TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SHEET_URL = os.getenv("SHEET_URL", "")

STATE_FILE = "last_alert_state.json"
STATE_VERSION = 4

ATR_PERIOD = 14
MAX_HOLD_BARS = 150
SCORE_THRESHOLD = 3.0

# Research shortlist — model-specific pairs
SMC_PAIRS = {
    "USD/CHF": {"rr": 2.0, "pip": 0.0001},
    "NZD/USD": {"rr": 2.0, "pip": 0.0001},
    "AUD/JPY": {"rr": 2.0, "pip": 0.01},
    "USD/JPY": {"rr": 2.0, "pip": 0.01},
    "GBP/USD": {"rr": 2.0, "pip": 0.0001},
}

ADV_PAIRS = {
    "USD/CHF": {"rr": 2.0, "pip": 0.0001},
    "EUR/USD": {"rr": 2.0, "pip": 0.0001},
    "USD/CAD": {"rr": 2.0, "pip": 0.0001},
    "USD/JPY": {"rr": 2.0, "pip": 0.01},
    "GBP/USD": {"rr": 2.0, "pip": 0.0001},
}

ALL_PAIRS = sorted(set(list(SMC_PAIRS.keys()) + list(ADV_PAIRS.keys())))


# ============================================================
# TELEGRAM / SHEET
# ============================================================

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram credentials missing.")
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        data = requests.post(url, json=payload, timeout=20).json()
        if data.get("ok"):
            return data["result"]["message_id"]
        print("Telegram error:", data)
    except Exception as e:
        print("Telegram send error:", e)
    return None


def edit_telegram(message_id, text):
    if not BOT_TOKEN or not CHAT_ID or not message_id:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": CHAT_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        data = requests.post(url, json=payload, timeout=20).json()
        return bool(data.get("ok"))
    except Exception as e:
        print("Telegram edit error:", e)
        return False


def send_to_sheet(record):
    if not SHEET_URL:
        return
    try:
        requests.post(SHEET_URL, json=record, timeout=20)
    except Exception as e:
        print("Google Sheet error:", e)


def tradingview_url(pair):
    return f"https://www.tradingview.com/symbols/{pair.replace('/', '')}/"


# ============================================================
# DATA
# ============================================================

def fetch(symbol, interval, outputsize=1500, retries=4):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_KEY,
        "format": "JSON",
    }
    for attempt in range(retries):
        try:
            data = requests.get(url, params=params, timeout=30).json()
            if "values" in data:
                df = pd.DataFrame(data["values"])
                df["datetime"] = pd.to_datetime(df["datetime"])
                for col in ("open", "high", "low", "close"):
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna().sort_values("datetime").reset_index(drop=True)
                time.sleep(8)
                return df
            if data.get("code") == 429 or "credits" in str(data).lower():
                print(f"Rate limit {symbol} {interval}, waiting...")
                time.sleep(65)
                continue
            print(f"API error {symbol} {interval}:", data)
            return None
        except Exception as e:
            print(f"Fetch error {symbol} {interval}:", e)
            time.sleep(10)
    return None


def add_atr(df):
    prev = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev).abs(),
            (df["low"] - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df = df.copy()
    df["atr"] = tr.rolling(ATR_PERIOD).mean()
    return df


# ============================================================
# H4 BIAS
# ============================================================

def h4_bias(h4):
    if h4 is None or len(h4) < 25:
        return 0
    window = h4.iloc[-21:-1]  # completed bars only
    if len(window) < 20:
        return 0
    c0 = float(window["close"].iloc[0])
    c1 = float(window["close"].iloc[-1])
    if c1 > c0 * 1.0008:
        return 1
    if c1 < c0 * 0.9992:
        return -1
    return 0


# ============================================================
# H1 FEATURES
# ============================================================

def feat_sweep(df, i):
    if i < 15:
        return 0
    ph = float(df["high"].iloc[i - 15 : i].max())
    pl = float(df["low"].iloc[i - 15 : i].min())
    hi = float(df["high"].iloc[i])
    lo = float(df["low"].iloc[i])
    cl = float(df["close"].iloc[i])
    if lo < pl and cl > pl:
        return 1
    if hi > ph and cl < ph:
        return -1
    return 0


def feat_fvg(df, i):
    if i < 2:
        return 0
    if float(df["low"].iloc[i]) > float(df["high"].iloc[i - 2]):
        return 1
    if float(df["high"].iloc[i]) < float(df["low"].iloc[i - 2]):
        return -1
    return 0


def feat_ob(df, i):
    if i < 3:
        return 0
    atr = df["atr"].iloc[i]
    if pd.isna(atr) or atr <= 0:
        return 0
    body = abs(float(df["close"].iloc[i]) - float(df["open"].iloc[i]))
    if body < 0.7 * atr:
        return 0
    bull = float(df["close"].iloc[i]) > float(df["open"].iloc[i])
    prev_bear = float(df["close"].iloc[i - 1]) < float(df["open"].iloc[i - 1])
    prev_bull = float(df["close"].iloc[i - 1]) > float(df["open"].iloc[i - 1])
    if bull and prev_bear:
        return 1
    if (not bull) and prev_bull:
        return -1
    return 0


def feat_structure(df, i):
    if i < 20:
        return 0
    ph = float(df["high"].iloc[i - 20 : i].max())
    pl = float(df["low"].iloc[i - 20 : i].min())
    cl = float(df["close"].iloc[i])
    if cl > ph:
        return 1
    if cl < pl:
        return -1
    return 0


def feat_zone(df, i):
    if i < 40:
        return 0
    hi = float(df["high"].iloc[i - 40 : i].max())
    lo = float(df["low"].iloc[i - 40 : i].min())
    if hi <= lo:
        return 0
    mid = (hi + lo) / 2
    cl = float(df["close"].iloc[i])
    if cl < mid:
        return 1
    if cl > mid:
        return -1
    return 0


def feat_eq(df, i):
    if i < 25:
        return 0
    highs = df["high"].iloc[i - 12 : i].astype(float).tolist()
    lows = df["low"].iloc[i - 12 : i].astype(float).tolist()
    mx, mn = max(highs), min(lows)
    if abs(mx - sorted(highs)[-2]) < mx * 0.0003 and float(df["high"].iloc[i]) >= mx:
        return -1
    if abs(mn - sorted(lows)[1]) < mn * 0.0003 and float(df["low"].iloc[i]) <= mn:
        return 1
    return 0


def feat_ind(df, i):
    if i < 4:
        return 0
    if float(df["high"].iloc[i]) > float(df["high"].iloc[i - 1]) and float(df["close"].iloc[i]) < float(df["close"].iloc[i - 1]):
        return -1
    if float(df["low"].iloc[i]) < float(df["low"].iloc[i - 1]) and float(df["close"].iloc[i]) > float(df["close"].iloc[i - 1]):
        return 1
    return 0


def feat_void(df, i):
    if i < 3:
        return 0
    atr = df["atr"].iloc[i]
    if pd.isna(atr) or atr <= 0:
        return 0
    rng = float(df["high"].iloc[i]) - float(df["low"].iloc[i])
    if rng > 1.6 * atr:
        return 1 if float(df["close"].iloc[i]) > float(df["open"].iloc[i]) else -1
    return 0


def score_smc(df, i):
    parts = [feat_sweep(df, i), feat_fvg(df, i), feat_ob(df, i), feat_structure(df, i), feat_zone(df, i)]
    bull = sum(1 for x in parts if x == 1)
    bear = sum(1 for x in parts if x == -1)
    if bull > bear and bull >= 2:
        return 1, float(bull * 1.5)
    if bear > bull and bear >= 2:
        return -1, float(bear * 1.5)
    return 0, 0.0


def score_adv(df, i):
    parts = [feat_eq(df, i), feat_ind(df, i), feat_void(df, i), feat_sweep(df, i), feat_structure(df, i)]
    bull = sum(1 for x in parts if x == 1)
    bear = sum(1 for x in parts if x == -1)
    if bull > bear and bull >= 2:
        return 1, float(bull * 2.0)
    if bear > bull and bear >= 2:
        return -1, float(bear * 2.0)
    return 0, 0.0


# ============================================================
# M15 ENTRY + LEVELS
# ============================================================

def m15_entry_ok(df, i, direction):
    o = float(df["open"].iloc[i])
    h = float(df["high"].iloc[i])
    l = float(df["low"].iloc[i])
    c = float(df["close"].iloc[i])
    full = h - l
    if full <= 0:
        return False
    if direction == 1:
        return c > o and (c - l) / full >= 0.45
    return c < o and (h - c) / full >= 0.45


def calculate_stop(df, i, direction):
    atr = df["atr"].iloc[i]
    if pd.isna(atr) or atr <= 0:
        return None
    start = max(0, i - 20)
    recent = df.iloc[start:i]
    if len(recent) < 5:
        return None
    entry = float(df["close"].iloc[i])
    if direction == 1:
        stop = float(recent["low"].min()) - atr * 0.25
        return stop if stop < entry else None
    stop = float(recent["high"].max()) + atr * 0.25
    return stop if stop > entry else None


def fixed_target(entry, stop, direction, rr):
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    return entry + risk * rr if direction == 1 else entry - risk * rr


# ============================================================
# STATE
# ============================================================

def load_state():
    default = {"version": STATE_VERSION, "last_alert_keys": {}, "pending": []}
    if not os.path.exists(STATE_FILE):
        return default
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        if state.get("version") != STATE_VERSION:
            print("Old state detected — resetting pending lifecycle.")
            return default
        state.setdefault("last_alert_keys", {})
        state.setdefault("pending", [])
        return state
    except Exception as e:
        print("State load error:", e)
        return default


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


# ============================================================
# MESSAGES
# ============================================================

def build_signal_message(trade):
    emoji = "🟢" if trade["direction"] == 1 else "🔴"
    risk = abs(trade["entry"] - trade["stop"])
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"════════════════════\n"
        f"<b>{trade['model']}</b>\n"
        f"────────────────────\n\n"
        f"{emoji} <b>{trade['side']} {trade['pair']}</b>\n\n"
        f"Score: <b>{trade['score']:.1f}</b>\n"
        f"H4 Bias aligned\n\n"
        f"Entry  <b>{trade['entry']:.5f}</b>\n"
        f"SL     <b>{trade['stop']:.5f}</b>\n"
        f"TP1    <b>{trade['tp1']:.5f}</b> (1.5R)\n"
        f"TP2    <b>{trade['tp2']:.5f}</b> ({trade['rr']:.1f}R)\n\n"
        f"Risk: <b>{risk:.5f}</b>\n"
        f"Candle: <b>{trade['candle_time']}</b>\n"
        f"Stack: <b>H4 → H1 → M15</b>\n\n"
        f"🚀 <b>TRADE ACTIVE</b>\n"
        f"➡️ Heading to TP1\n\n"
        f"<a href=\"{tradingview_url(trade['pair'])}\">Open {trade['pair']} on TradingView</a>\n\n"
        f"Built on Data.\n"
        f"Driven by Discipline."
    )


def tp1_hit_message(trade):
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"<b>{trade['model']}</b>\n\n"
        f"✅ <b>TP1 HIT</b>\n\n"
        f"<b>{trade['side']} {trade['pair']}</b>\n"
        f"TP1 <b>{trade['tp1']:.5f}</b> (+1.5R)\n\n"
        f"🚀 Heading to TP2 <b>{trade['tp2']:.5f}</b> ({trade['rr']:.1f}R)\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def tp2_win_message(trade, exit_time):
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"<b>{trade['model']}</b>\n\n"
        f"🏆 <b>TP2 HIT</b>\n\n"
        f"<b>{trade['side']} {trade['pair']}</b>\n"
        f"Exit <b>{exit_time}</b>\n"
        f"Result <b>+{trade['rr']:.1f}R</b>\n\n"
        f"🏆 FINAL VERDICT: WIN\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def stop_loss_message(trade, exit_time):
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"<b>{trade['model']}</b>\n\n"
        f"🔴 <b>STOP LOSS HIT</b>\n\n"
        f"<b>{trade['side']} {trade['pair']}</b>\n"
        f"SL <b>{trade['stop']:.5f}</b>\n"
        f"Exit <b>{exit_time}</b>\n"
        f"Result <b>-1R</b>\n\n"
        f"🔴 FINAL VERDICT: LOSS\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


# ============================================================
# PENDING MANAGEMENT (re-edit only — no entry wait)
# ============================================================

def check_pending_trades(state, pair, m15):
    if not state["pending"]:
        return

    remaining = []
    latest = len(m15) - 1

    for trade in state["pending"]:
        if trade.get("pair") != pair:
            remaining.append(trade)
            continue

        try:
            entry_time = pd.Timestamp(trade["candle_time"])
            matches = np.where(m15["datetime"].values >= entry_time.to_datetime64())[0]
        except Exception:
            remaining.append(trade)
            continue

        if len(matches) == 0:
            remaining.append(trade)
            continue

        signal_index = int(matches[0])
        last_checked = int(trade.get("last_checked_index", signal_index))
        start_index = max(signal_index + 1, last_checked + 1)

        direction = int(trade["direction"])
        entry = float(trade["entry"])
        stop = float(trade["stop"])
        tp1 = float(trade["tp1"])
        tp2 = float(trade["tp2"])
        tp1_hit = bool(trade.get("tp1_hit", False))
        closed = False

        for j in range(start_index, latest + 1):
            high = float(m15["high"].iloc[j])
            low = float(m15["low"].iloc[j])
            candle_time = str(m15["datetime"].iloc[j])
            trade["last_checked_index"] = j

            if direction == 1:
                hit_sl = low <= stop
                hit_tp2 = high >= tp2
                hit_tp1 = high >= tp1
            else:
                hit_sl = high >= stop
                hit_tp2 = low <= tp2
                hit_tp1 = low <= tp1

            # SL priority
            if hit_sl:
                edit_telegram(trade.get("message_id"), stop_loss_message(trade, candle_time))
                send_to_sheet({
                    "action": "OUTCOME",
                    "status": "LOSS",
                    "pair": pair,
                    "model": trade.get("model"),
                    "side": trade["side"],
                    "entry": entry,
                    "stop": stop,
                    "tp1": tp1,
                    "tp2": tp2,
                    "result_r": -1,
                    "exit_time": candle_time,
                })
                closed = True
                break

            if hit_tp2:
                edit_telegram(trade.get("message_id"), tp2_win_message(trade, candle_time))
                send_to_sheet({
                    "action": "OUTCOME",
                    "status": "WIN",
                    "pair": pair,
                    "model": trade.get("model"),
                    "side": trade["side"],
                    "entry": entry,
                    "stop": stop,
                    "tp1": tp1,
                    "tp2": tp2,
                    "result_r": float(trade["rr"]),
                    "exit_time": candle_time,
                })
                closed = True
                break

            if hit_tp1 and not tp1_hit:
                trade["tp1_hit"] = True
                tp1_hit = True
                edit_telegram(trade.get("message_id"), tp1_hit_message(trade))
                send_to_sheet({
                    "action": "TP1_HIT",
                    "pair": pair,
                    "model": trade.get("model"),
                    "side": trade["side"],
                    "tp1": tp1,
                    "exit_time": candle_time,
                })

            # max hold
            if j - signal_index >= MAX_HOLD_BARS:
                edit_telegram(
                    trade.get("message_id"),
                    f"<b>TENSION TRADING DESK</b>\n<b>{trade.get('model')}</b>\n\n"
                    f"⏱️ <b>TIME EXIT</b>\n\n<b>{trade['side']} {pair}</b>\n"
                    f"No TP2/SL within max hold.\n\nBuilt on Data.\nDriven by Discipline.",
                )
                closed = True
                break

        if not closed:
            remaining.append(trade)

    state["pending"] = remaining


# ============================================================
# ANALYZE ONE MODEL ON ONE PAIR
# ============================================================

def analyze(model, pair, cfg, h4, h1, m15):
    if h4 is None or h1 is None or m15 is None:
        return None
    if len(h1) < 60 or len(m15) < 80:
        return None

    bias = h4_bias(h4)
    if bias == 0:
        return None

    hi = len(h1) - 2  # last completed H1
    mi = len(m15) - 1

    if model == "SMC":
        direction, score = score_smc(h1, hi)
    else:
        direction, score = score_adv(h1, hi)

    if direction == 0 or score < SCORE_THRESHOLD:
        return None
    if direction != bias:
        return None
    if not m15_entry_ok(m15, mi, direction):
        return None

    entry = float(m15["close"].iloc[mi])
    stop = calculate_stop(m15, mi, direction)
    if stop is None:
        return None

    rr = float(cfg["rr"])
    tp1 = fixed_target(entry, stop, direction, 1.5)
    tp2 = fixed_target(entry, stop, direction, rr)
    if tp1 is None or tp2 is None:
        return None

    # reject absurd geometry
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    if abs(tp1 - entry) / risk < 0.9:
        return None

    candle_time = str(m15["datetime"].iloc[mi])
    return {
        "model": model,
        "pair": pair,
        "direction": direction,
        "side": "BUY" if direction == 1 else "SELL",
        "score": score,
        "entry": entry,
        "stop": float(stop),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "rr": rr,
        "candle_time": candle_time,
        "candle_key": f"{model}|{pair}|{candle_time}|{direction}",
    }


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("TENSION TRADING DESK v4")
    print("H4 bias + H1 POI + M15 entry")
    print("Models: SMC | ADV_SMC (separate)")
    print("No setup-wait | Re-edit TP/SL enabled")
    print("=" * 70)

    if not TWELVE_DATA_KEY:
        print("Missing TWELVE_DATA_KEY")
        return

    state = load_state()

    for pair in ALL_PAIRS:
        print(f"\n--- {pair} ---")
        h4 = fetch(pair, "4h", 400)
        h1 = fetch(pair, "1h", 800)
        m15 = fetch(pair, "15min", 1500)

        if m15 is None:
            print("  data failed")
            continue

        h1 = add_atr(h1) if h1 is not None else None
        m15 = add_atr(m15)

        # manage existing active trades first
        check_pending_trades(state, pair, m15)

        models_to_run = []
        if pair in SMC_PAIRS:
            models_to_run.append(("SMC", SMC_PAIRS[pair]))
        if pair in ADV_PAIRS:
            models_to_run.append(("ADV_SMC", ADV_PAIRS[pair]))

        for model, cfg in models_to_run:
            signal = analyze(model, pair, cfg, h4, h1, m15)
            if signal is None:
                print(f"  {model}: no setup")
                continue

            key = signal["candle_key"]
            if state["last_alert_keys"].get(f"{model}:{pair}") == key:
                print(f"  {model}: already alerted")
                continue

            # avoid duplicate active same side/model/pair
            dup = any(
                t.get("pair") == pair
                and t.get("model") == model
                and t.get("direction") == signal["direction"]
                and t.get("status") == "ACTIVE"
                for t in state["pending"]
            )
            if dup:
                print(f"  {model}: active trade already open")
                continue

            msg = build_signal_message(signal)
            message_id = send_telegram(msg)
            print(f"  {model}: signal sent -> {signal['side']}")

            state["last_alert_keys"][f"{model}:{pair}"] = key

            if message_id:
                state["pending"].append({
                    "model": model,
                    "pair": pair,
                    "message_id": message_id,
                    "direction": signal["direction"],
                    "side": signal["side"],
                    "score": signal["score"],
                    "entry": signal["entry"],
                    "stop": signal["stop"],
                    "tp1": signal["tp1"],
                    "tp2": signal["tp2"],
                    "rr": signal["rr"],
                    "candle_time": signal["candle_time"],
                    "candle_key": key,
                    "status": "ACTIVE",
                    "entry_hit": True,
                    "tp1_hit": False,
                    "last_checked_index": len(m15) - 1,
                })

            send_to_sheet({
                "action": "NEW_SIGNAL",
                "status": "ACTIVE",
                "model": model,
                "pair": pair,
                "side": signal["side"],
                "score": signal["score"],
                "entry": signal["entry"],
                "stop": signal["stop"],
                "tp1": signal["tp1"],
                "tp2": signal["tp2"],
                "rr": signal["rr"],
                "candle_time": signal["candle_time"],
            })

    save_state(state)
    print("\n" + "=" * 70)
    print(f"SCAN COMPLETE | pending active trades: {len(state['pending'])}")
    print("=" * 70)


if __name__ == "__main__":
    main()
