"""
TENSION TRADING DESK — SMC Primary Engine (replaces v4 score models)

LOCKED research (walk-forward 12/12 positive, mean ~+1.17R):
  H4 bias align
  H1 displacement BOS
  OB preferred, else FVG
  First touch only
  M15 entry @ zone 50%
  SL = zone extreme + 2 pips
  TP1 1.5R | TP2 2.0R
  ADV stream = OUT

Pairs: EUR/USD, GBP/USD, USD/JPY, AUD/USD

Lifecycle:
  SIGNAL (active on M15 touch)
    -> TP1 HIT (message edit)
    -> TP2 WIN / SL LOSS (message edit)

Telegram multi-user: /start /stop /help /status /pairs /ping
State: last_alert_state_smc.json + subscribers_smc.json
(Can share BOT_TOKEN with CRT; separate state files.)

Motto: Built on Data. / Driven by Discipline.
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
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
SHEET_URL = os.getenv("SHEET_URL", "")

STATE_FILE = "last_alert_state_smc.json"
SUBSCRIBERS_FILE = "subscribers_smc.json"
STATE_VERSION = 5

PIP_BUF = 2.0
RR1 = 1.5
RR2 = 2.0
MAX_HOLD_BARS = 180
M15_LOOKAHEAD = 96  # ~24h of M15 after H1 BOS

PAIRS = {
    "EUR/USD": {"pip": 0.0001, "tv": "OANDA:EURUSD"},
    "GBP/USD": {"pip": 0.0001, "tv": "OANDA:GBPUSD"},
    "USD/JPY": {"pip": 0.01, "tv": "OANDA:USDJPY"},
    "AUD/USD": {"pip": 0.0001, "tv": "OANDA:AUDUSD"},
}


# ============================================================
# SUBSCRIBERS
# ============================================================

def load_subscribers():
    ids = set()
    if CHAT_ID:
        ids.add(str(CHAT_ID))
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                for x in data:
                    ids.add(str(x))
        except Exception as e:
            print("Subscriber load error:", e)
    return sorted(ids)


def save_subscribers(subscribers):
    try:
        with open(SUBSCRIBERS_FILE, "w") as f:
            json.dump(sorted(set(str(x) for x in subscribers)), f, indent=2)
    except Exception as e:
        print("Subscriber save error:", e)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_to(chat_id, text):
    if not BOT_TOKEN or not chat_id:
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        data = requests.post(url, json=payload, timeout=20).json()
        if data.get("ok"):
            return data["result"]["message_id"]
        print(f"Telegram error ({chat_id}):", data)
    except Exception as e:
        print(f"Telegram send error ({chat_id}):", e)
    return None


def broadcast(text):
    subs = load_subscribers()
    if not subs:
        print("No subscribers.")
        return None
    primary = str(CHAT_ID) if CHAT_ID else subs[0]
    primary_mid = None
    for cid in subs:
        mid = send_telegram_to(cid, text)
        if str(cid) == primary and mid:
            primary_mid = mid
        time.sleep(0.05)
    return primary_mid


def edit_telegram(message_id, text, chat_id=None):
    target = str(chat_id or CHAT_ID or "")
    if not BOT_TOKEN or not target or not message_id:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": target,
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


def process_commands(state):
    if not BOT_TOKEN:
        return
    subscribers = load_subscribers()
    offset = int(state.get("last_update_id", 0)) + 1
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        data = requests.get(url, params={"offset": offset, "timeout": 0}, timeout=15).json()
    except Exception as e:
        print("getUpdates error:", e)
        return
    if not data.get("ok"):
        return

    for result in data.get("result", []):
        uid = int(result.get("update_id", 0))
        state["last_update_id"] = max(int(state.get("last_update_id", 0)), uid)
        message = result.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            continue
        low = text.lower()

        if low.startswith("/start"):
            if chat_id not in subscribers:
                subscribers.append(chat_id)
                save_subscribers(subscribers)
            send_telegram_to(
                chat_id,
                "🟢 <b>Subscribed — Tension Trading Desk SMC</b>\n\n"
                "Stream: <b>SMC Primary</b>\n"
                "H4 bias · H1 displacement BOS · OB/FVG first touch · M15\n\n"
                "Commands: /help /status /pairs /ping /stop",
            )
        elif low.startswith("/stop"):
            subscribers = [x for x in subscribers if x != chat_id]
            save_subscribers(subscribers)
            send_telegram_to(chat_id, "🔴 <b>Unsubscribed</b> from SMC signals.")
        elif low.startswith("/help"):
            send_telegram_to(
                chat_id,
                "📖 <b>SMC Primary</b>\n\n"
                "1. H4 bias must agree\n"
                "2. H1 displacement BOS (impulsive close)\n"
                "3. Zone = Order Block (else FVG)\n"
                "4. First M15 touch → entry at <b>50%</b> of zone\n"
                "5. SL beyond zone + 2 pips\n"
                "6. TP1 1.5R · TP2 2.0R\n\n"
                "BE after TP1 = suggestion only (not auto).",
            )
        elif low.startswith("/status"):
            send_telegram_to(
                chat_id,
                "⚡ <b>SMC Engine Status</b>\n\n"
                "State: <b>ONLINE</b>\n"
                "Model: <b>SMC Primary</b> (ADV off)\n"
                f"Pairs: {len(PAIRS)} | Subscribers: {len(load_subscribers())}",
            )
        elif low.startswith("/pairs"):
            lines = "\n".join(f"• <code>{p}</code>" for p in PAIRS)
            send_telegram_to(chat_id, f"📊 <b>SMC pairs</b>\n\n{lines}")
        elif low.startswith("/ping"):
            send_telegram_to(chat_id, "pong 🏓 — SMC engine operational.")

    save_subscribers(subscribers)


def send_to_sheet(record):
    if not SHEET_URL:
        return
    try:
        requests.post(SHEET_URL, json=record, timeout=20)
    except Exception as e:
        print("Sheet error:", e)


def tradingview_url(pair):
    tv = PAIRS.get(pair, {}).get("tv")
    if tv:
        return f"https://www.tradingview.com/chart/?symbol={tv}"
    return f"https://www.tradingview.com/symbols/{pair.replace('/', '')}/"


# ============================================================
# DATA
# ============================================================

def fetch(symbol, interval, outputsize=500, retries=4):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_KEY,
        "format": "JSON",
    }
    for _ in range(retries):
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


# ============================================================
# SMC PRIMARY LOGIC
# ============================================================

def swings_df(df, left=2, right=2):
    sh, slo = [], []
    n = len(df)
    highs = df["high"].values
    lows = df["low"].values
    for i in range(left, n - right):
        h, l = highs[i], lows[i]
        if all(h >= highs[i - k] for k in range(1, left + 1)) and all(
            h >= highs[i + k] for k in range(1, right + 1)
        ):
            sh.append(i)
        if all(l <= lows[i - k] for k in range(1, left + 1)) and all(
            l <= lows[i + k] for k in range(1, right + 1)
        ):
            slo.append(i)
    return sh, slo


def last_swings_before(swing_hi, swing_lo, i):
    ph = [x for x in swing_hi if x < i]
    pl = [x for x in swing_lo if x < i]
    return (ph[-1] if ph else None), (pl[-1] if pl else None)


def h4_bias(h4, t):
    if h4 is None or len(h4) < 20:
        return 0
    w = h4[h4["datetime"] < t].tail(40)
    if len(w) < 15:
        return 0
    c0 = float(w["close"].iloc[0])
    c1 = float(w["close"].iloc[-1])
    if c1 > c0 * 1.0008:
        return 1
    if c1 < c0 * 0.9992:
        return -1
    return 0


def displacement_bos(h1, i, d):
    o = float(h1["open"].iloc[i])
    h = float(h1["high"].iloc[i])
    l = float(h1["low"].iloc[i])
    c = float(h1["close"].iloc[i])
    full = h - l
    if full <= 0:
        return False
    body = abs(c - o)
    if body / full < 0.45:
        return False
    if d == 1:
        return c > o
    return c < o


def fvg_at(h1, i):
    if i < 2:
        return None
    h0 = float(h1["high"].iloc[i - 2])
    l0 = float(h1["low"].iloc[i - 2])
    h2 = float(h1["high"].iloc[i])
    l2 = float(h1["low"].iloc[i])
    if h0 < l2:
        return 1, h0, l2
    if l0 > h2:
        return -1, h2, l0
    return None


def ob_before_impulse(h1, i):
    if i < 2:
        return None
    o = float(h1["open"].iloc[i])
    h = float(h1["high"].iloc[i])
    l = float(h1["low"].iloc[i])
    c = float(h1["close"].iloc[i])
    full = h - l
    if full <= 0 or abs(c - o) / full < 0.4:
        return None
    if c > o:
        for k in range(i - 1, max(0, i - 8), -1):
            if float(h1["close"].iloc[k]) < float(h1["open"].iloc[k]):
                lo = min(float(h1["low"].iloc[k]), float(h1["close"].iloc[k]))
                hi = max(float(h1["high"].iloc[k]), float(h1["open"].iloc[k]))
                return 1, lo, hi
    if c < o:
        for k in range(i - 1, max(0, i - 8), -1):
            if float(h1["close"].iloc[k]) > float(h1["open"].iloc[k]):
                lo = min(float(h1["low"].iloc[k]), float(h1["open"].iloc[k]))
                hi = max(float(h1["high"].iloc[k]), float(h1["close"].iloc[k]))
                return -1, lo, hi
    return None


def analyze_pair(h4, h1, m15, pair, pip):
    """
    Scan completed H1 bars for BOS setups; return signal if M15 just
    completed first touch of zone (last closed M15 bar).
    """
    if h1 is None or m15 is None or len(h1) < 60 or len(m15) < 80:
        return None

    swing_hi, swing_lo = swings_df(h1, 2, 2)
    # Use last closed H1 (exclude forming if any ambiguity — last row is latest)
    # Scan recent H1 events for a zone still waiting first M15 touch
    for i in range(len(h1) - 2, max(40, len(h1) - 30), -1):
        t = h1["datetime"].iloc[i]
        bias = h4_bias(h4, t)
        if bias == 0:
            continue

        phi, plo = last_swings_before(swing_hi, swing_lo, i)
        if phi is None or plo is None:
            continue

        d = 0
        if float(h1["close"].iloc[i]) > float(h1["high"].iloc[phi]):
            d = 1
        elif float(h1["close"].iloc[i]) < float(h1["low"].iloc[plo]):
            d = -1
        if d == 0 or d != bias:
            continue
        if not displacement_bos(h1, i, d):
            continue

        zone = ob_before_impulse(h1, i)
        ztype = "OB"
        if zone is None or zone[0] != d:
            zone = fvg_at(h1, i)
            ztype = "FVG"
        if zone is None or zone[0] != d:
            continue
        _, zlo, zhi = zone
        if zhi - zlo < 2 * pip:
            continue

        zmid = (zlo + zhi) / 2.0
        m15_after = m15[m15["datetime"] > t].reset_index(drop=True)
        if len(m15_after) == 0:
            continue

        # First touch among M15 bars after BOS; signal only if touch is the latest closed bar
        for j in range(min(len(m15_after), M15_LOOKAHEAD)):
            row = m15_after.iloc[j]
            hi = float(row["high"])
            lo = float(row["low"])
            cl = float(row["close"])
            # mitigated through zone
            if d == 1 and cl < zlo:
                break
            if d == -1 and cl > zhi:
                break
            if lo > zhi or hi < zlo:
                continue

            # first touch at bar j — only alert if this is the most recent M15 bar
            if j != len(m15_after) - 1:
                # already touched earlier; setup consumed
                break

            entry = zmid
            if d == 1:
                stop = zlo - PIP_BUF * pip
                risk = entry - stop
            else:
                stop = zhi + PIP_BUF * pip
                risk = stop - entry
            if risk < 3 * pip:
                break

            tp1 = entry + RR1 * risk if d == 1 else entry - RR1 * risk
            tp2 = entry + RR2 * risk if d == 1 else entry - RR2 * risk
            risk_pips = risk / pip
            candle_time = str(row["datetime"])
            side = "BUY" if d == 1 else "SELL"

            return {
                "stream": "SMC Primary",
                "pair": pair,
                "direction": d,
                "side": side,
                "zone_type": ztype,
                "entry": float(entry),
                "stop": float(stop),
                "tp1": float(tp1),
                "tp2": float(tp2),
                "rr1": RR1,
                "rr2": RR2,
                "risk_pips": float(risk_pips),
                "zlo": float(zlo),
                "zhi": float(zhi),
                "zmid": float(zmid),
                "bos_time": str(t),
                "candle_time": candle_time,
                "candle_key": f"SMC|{pair}|{candle_time}|{d}|{ztype}",
            }
        # only consider most recent viable BOS path
        # continue scanning older only if no touch window yet — for live we break after first candidate window
    return None


# ============================================================
# STATE
# ============================================================

def load_state():
    default = {
        "version": STATE_VERSION,
        "last_alert_keys": {},
        "pending": [],
        "last_update_id": 0,
    }
    if not os.path.exists(STATE_FILE):
        return default
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        if state.get("version") != STATE_VERSION:
            print("SMC state version mismatch — reset pending, keep update offset.")
            default["last_update_id"] = int(state.get("last_update_id", 0))
            return default
        state.setdefault("last_alert_keys", {})
        state.setdefault("pending", [])
        state.setdefault("last_update_id", 0)
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

def build_signal_message(sig):
    emoji = "🟢" if sig["direction"] == 1 else "🔴"
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"════════════════════\n"
        f"<b>SMC Primary</b>\n"
        f"────────────────────\n\n"
        f"{emoji} <b>{sig['side']} {sig['pair']}</b>\n\n"
        f"Zone: <b>{sig['zone_type']}</b> (first touch)\n"
        f"H4 bias · H1 displacement BOS · M15 entry\n\n"
        f"Zone High <b>{sig['zhi']:.5f}</b>\n"
        f"Entry mid <b>{sig['entry']:.5f}</b>\n"
        f"Zone Low  <b>{sig['zlo']:.5f}</b>\n\n"
        f"SL  <b>{sig['stop']:.5f}</b> ({sig['risk_pips']:.1f} pips)\n"
        f"TP1 <b>{sig['tp1']:.5f}</b> ({sig['rr1']:.1f}R)\n"
        f"TP2 <b>{sig['tp2']:.5f}</b> ({sig['rr2']:.1f}R)\n\n"
        f"BOS <b>{sig['bos_time']}</b>\n"
        f"Touch <b>{sig['candle_time']}</b>\n\n"
        f"🚀 <b>TRADE ACTIVE</b>\n"
        f"💡 Suggestion only: BE after TP1 (not auto)\n\n"
        f"<a href=\"{tradingview_url(sig['pair'])}\">Open {sig['pair']} on TradingView</a>\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def tp1_message(trade):
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"<b>SMC Primary</b>\n\n"
        f"✅ <b>TP1 TOUCHED</b> ({trade.get('rr1', RR1)}R)\n\n"
        f"<b>{trade['side']} {trade['pair']}</b>\n"
        f"TP1 <b>{float(trade['tp1']):.5f}</b>\n\n"
        f"Still heading TP2 <b>{float(trade['tp2']):.5f}</b>\n"
        f"💡 Suggestion: BE manually if desired\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def tp2_message(trade, exit_time):
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"<b>SMC Primary</b>\n\n"
        f"🏆 <b>TP2 HIT</b>\n\n"
        f"<b>{trade['side']} {trade['pair']}</b>\n"
        f"Exit <b>{exit_time}</b>\n"
        f"Result <b>+{float(trade.get('rr2', RR2)):.1f}R</b>\n\n"
        f"🏆 FINAL VERDICT: WIN\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def sl_message(trade, exit_time):
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"<b>SMC Primary</b>\n\n"
        f"🔴 <b>STOP LOSS HIT</b>\n\n"
        f"<b>{trade['side']} {trade['pair']}</b>\n"
        f"SL <b>{float(trade['stop']):.5f}</b>\n"
        f"Exit <b>{exit_time}</b>\n"
        f"Result <b>-1R</b>\n\n"
        f"🔴 FINAL VERDICT: LOSS\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


# ============================================================
# PENDING
# ============================================================

def check_pending(state, pair, m15):
    if not state["pending"]:
        return
    remaining = []
    latest = len(m15) - 1
    primary_chat = str(CHAT_ID) if CHAT_ID else None

    for trade in state["pending"]:
        if trade.get("pair") != pair:
            remaining.append(trade)
            continue
        try:
            entry_time = pd.Timestamp(trade["candle_time"])
            matches = np.where(m15["datetime"].values >= np.datetime64(entry_time))[0]
        except Exception:
            remaining.append(trade)
            continue
        if len(matches) == 0:
            remaining.append(trade)
            continue

        signal_index = int(matches[0])
        last_checked = int(trade.get("last_checked_index", signal_index))
        start = max(signal_index + 1, last_checked + 1)
        direction = int(trade["direction"])
        stop = float(trade["stop"])
        tp1 = float(trade["tp1"])
        tp2 = float(trade["tp2"])
        tp1_hit = bool(trade.get("tp1_hit", False))
        closed = False
        edit_chat = trade.get("edit_chat_id") or primary_chat

        for j in range(start, latest + 1):
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

            if hit_sl:
                edit_telegram(trade.get("message_id"), sl_message(trade, candle_time), edit_chat)
                send_to_sheet({
                    "action": "OUTCOME", "status": "LOSS", "stream": "SMC Primary",
                    "pair": pair, "result_r": -1, "exit_time": candle_time,
                })
                closed = True
                break
            if hit_tp2:
                edit_telegram(trade.get("message_id"), tp2_message(trade, candle_time), edit_chat)
                send_to_sheet({
                    "action": "OUTCOME", "status": "WIN", "stream": "SMC Primary",
                    "pair": pair, "result_r": float(trade.get("rr2", RR2)), "exit_time": candle_time,
                })
                closed = True
                break
            if hit_tp1 and not tp1_hit:
                trade["tp1_hit"] = True
                tp1_hit = True
                edit_telegram(trade.get("message_id"), tp1_message(trade), edit_chat)
                send_to_sheet({
                    "action": "TP1_TOUCH", "stream": "SMC Primary",
                    "pair": pair, "exit_time": candle_time,
                })
            if j - signal_index >= MAX_HOLD_BARS:
                edit_telegram(
                    trade.get("message_id"),
                    f"<b>TENSION TRADING DESK</b>\n<b>SMC Primary</b>\n\n"
                    f"⏱️ <b>TIME EXIT</b>\n\n<b>{trade['side']} {pair}</b>\n"
                    f"Max hold reached.\n\nBuilt on Data.\nDriven by Discipline.",
                    edit_chat,
                )
                closed = True
                break
        if not closed:
            remaining.append(trade)

    state["pending"] = remaining


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("TENSION TRADING DESK — SMC PRIMARY (v4 score models REPLACED)")
    print("H4 bias | displacement BOS | OB/FVG first touch | M15 | 1.5R/2.0R")
    print("ADV = OFF")
    print("=" * 70)

    if not TWELVE_DATA_KEY:
        print("Missing TWELVE_DATA_KEY")
        return

    state = load_state()
    if BOT_TOKEN:
        process_commands(state)
        print(f"Subscribers: {len(load_subscribers())}")
    else:
        print("BOT_TOKEN missing — scan only.")

    for pair, cfg in PAIRS.items():
        pip = cfg["pip"]
        print(f"\n--- {pair} ---")
        h4 = fetch(pair, "4h", 300)
        h1 = fetch(pair, "1h", 400)
        m15 = fetch(pair, "15min", 500)
        if m15 is None or h1 is None:
            print("  data failed")
            continue

        check_pending(state, pair, m15)
        sig = analyze_pair(h4, h1, m15, pair, pip)
        if sig is None:
            print("  no setup")
            continue

        key = sig["candle_key"]
        if state["last_alert_keys"].get(pair) == key:
            print("  already alerted")
            continue

        dup = any(
            t.get("pair") == pair and t.get("status") == "ACTIVE"
            for t in state["pending"]
        )
        if dup:
            print("  active trade open")
            continue

        msg = build_signal_message(sig)
        message_id = broadcast(msg)
        print(f"  SIGNAL {sig['side']} | {sig['zone_type']} | {sig['risk_pips']:.1f} pips")

        state["last_alert_keys"][pair] = key
        if message_id:
            state["pending"].append({
                "stream": "SMC Primary",
                "pair": pair,
                "message_id": message_id,
                "edit_chat_id": str(CHAT_ID) if CHAT_ID else None,
                "direction": sig["direction"],
                "side": sig["side"],
                "entry": sig["entry"],
                "stop": sig["stop"],
                "tp1": sig["tp1"],
                "tp2": sig["tp2"],
                "rr1": sig["rr1"],
                "rr2": sig["rr2"],
                "risk_pips": sig["risk_pips"],
                "candle_time": sig["candle_time"],
                "candle_key": key,
                "status": "ACTIVE",
                "tp1_hit": False,
                "last_checked_index": len(m15) - 1,
            })
        send_to_sheet({
            "action": "NEW_SIGNAL",
            "stream": "SMC Primary",
            "pair": pair,
            "side": sig["side"],
            "entry": sig["entry"],
            "stop": sig["stop"],
            "tp1": sig["tp1"],
            "tp2": sig["tp2"],
            "candle_time": sig["candle_time"],
        })

    save_state(state)
    print("\n" + "=" * 70)
    print(f"SMC SCAN COMPLETE | pending: {len(state['pending'])} | subs: {len(load_subscribers())}")
    print("=" * 70)


if __name__ == "__main__":
    main()
