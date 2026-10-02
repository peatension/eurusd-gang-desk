"""
TENSION TRADING DESK — CRT plain (same-TF)
==========================================
Separate from the hybrid M5-sweep engine (3C Model in 3c_model.py).

Locked research survivors, plain only (no OHP/OLP/OB/FVG):
  CRT 1H→15m
  CRT 1H→5m
  CRT 4H→30m
  CRT 4H→15m

Base:
  C1 = prior closed HTF candle (CRT High / CRT Low)
  C2 = next HTF candle sweeps one side and closes back inside
  Entry on LTF after C2 (first close back inside the C1 range)
  TP1 = 50% of C1
  TP2 = opposite side
  SL  = beyond C2 extreme
  No auto BE
"""

import os
import json

import numpy as np
import pandas as pd

import importlib

desk = importlib.import_module("3c_model")

STATE_FILE = "last_alert_state_crt_plain.json"
STATE_VERSION = 1
MIN_RR = 1.5

PAIRS = desk.PAIRS

STREAMS = {
    "CRT 1H→15m": ("1h", "15min"),
    "CRT 1H→5m": ("1h", "5min"),
    "CRT 4H→30m": ("4h", "30min"),
    "CRT 4H→15m": ("4h", "15min"),
}


def load_state():
    default = {
        "version": STATE_VERSION,
        "last_alert_keys": {},
        "pending": [],
    }
    if not os.path.exists(STATE_FILE):
        return default
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        if state.get("version") != STATE_VERSION:
            return default
        state.setdefault("last_alert_keys", {})
        state.setdefault("pending", [])
        return state
    except Exception as e:
        print("CRT plain state load error:", e)
        return default


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def find_setup(htf, pip):
    """Latest closed C2 only. C2 must sweep C1 and close back inside."""
    if htf is None or len(htf) < 5:
        return None

    c2 = htf.iloc[-2]
    c1 = htf.iloc[-3]
    crh = float(c1["high"])
    crl = float(c1["low"])
    mid = (crh + crl) / 2.0
    if crh - crl < 5 * pip:
        return None

    if float(c2["low"]) < crl and crl < float(c2["close"]) < crh:
        return {
            "crh": crh,
            "crl": crl,
            "mid": mid,
            "extreme": float(c2["low"]),
            "direction": 1,
            "c2_time": str(c2["datetime"]),
        }
    if float(c2["high"]) > crh and crl < float(c2["close"]) < crh:
        return {
            "crh": crh,
            "crl": crl,
            "mid": mid,
            "extreme": float(c2["high"]),
            "direction": -1,
            "c2_time": str(c2["datetime"]),
        }
    return None


def ltf_entry(ltf, setup):
    """First LTF close back inside C1 after C2."""
    if ltf is None or len(ltf) < 10:
        return None
    c2_time = pd.Timestamp(setup["c2_time"])
    after = ltf[ltf["datetime"] > c2_time]
    if after.empty:
        return None
    direction = setup["direction"]
    crh, crl = setup["crh"], setup["crl"]
    window = after.tail(12)
    hit = None
    for _, row in window.iterrows():
        close = float(row["close"])
        if direction == 1 and crl < close < crh:
            hit = row
        elif direction == -1 and crl < close < crh:
            hit = row
    if hit is None:
        return None
    return hit


def analyze(stream_label, htf_name, ltf_name, htf, ltf, pair, pip):
    setup = find_setup(htf, pip)
    if setup is None:
        return None
    bar = ltf_entry(ltf, setup)
    if bar is None:
        return None

    atr = float(bar["atr"]) if "atr" in bar and not pd.isna(bar["atr"]) else 0
    buf = max(1.5 * pip, 0.25 * atr if atr > 0 else 1.5 * pip)
    direction = setup["direction"]
    if direction == 1:
        entry = setup["crl"]
        stop = setup["extreme"] - buf
        tp1, tp2 = setup["mid"], setup["crh"]
    else:
        entry = setup["crh"]
        stop = setup["extreme"] + buf
        tp1, tp2 = setup["mid"], setup["crl"]

    risk = abs(entry - stop)
    if risk <= 0:
        return None
    rr1 = abs(tp1 - entry) / risk
    rr2 = abs(tp2 - entry) / risk
    if rr1 < MIN_RR:
        return None

    candle_time = str(bar["datetime"])
    return {
        "stream": stream_label,
        "pair": pair,
        "direction": direction,
        "side": "BUY" if direction == 1 else "SELL",
        "entry": float(entry),
        "stop": float(stop),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "rr1": float(rr1),
        "rr2": float(rr2),
        "risk_pips": float(risk / pip),
        "crh": setup["crh"],
        "crl": setup["crl"],
        "mid": setup["mid"],
        "candle_time": candle_time,
        "candle_key": f"{stream_label}|{pair}|{setup['c2_time']}|{direction}",
        "c1_tf": htf_name,
        "ltf": ltf_name,
    }


def build_signal_message(sig):
    emoji = "🟢" if sig["direction"] == 1 else "🔴"
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"════════════════════\n"
        f"<b>{sig['stream']}</b> · plain\n"
        f"────────────────────\n\n"
        f"{emoji} <b>{sig['side']} {sig['pair']}</b>\n\n"
        f"C1 TF: <b>{sig['c1_tf'].upper()}</b> · Entry: <b>{sig['ltf']}</b>\n"
        f"Sweep: same TF as C1, close back inside\n\n"
        f"C1 High <b>{sig['crh']:.5f}</b>\n"
        f"C1 Mid  <b>{sig['mid']:.5f}</b>\n"
        f"C1 Low  <b>{sig['crl']:.5f}</b>\n\n"
        f"Entry <b>{sig['entry']:.5f}</b>\n"
        f"SL    <b>{sig['stop']:.5f}</b> ({sig['risk_pips']:.1f} pips)\n"
        f"TP1   <b>{sig['tp1']:.5f}</b> (mid · {sig['rr1']:.2f}R measured)\n"
        f"TP2   <b>{sig['tp2']:.5f}</b> (opposite · {sig['rr2']:.2f}R measured)\n\n"
        f"Candle: <b>{sig['candle_time']}</b>\n\n"
        f"🚀 <b>TRADE ACTIVE</b>\n"
        f"💡 Suggestion only: BE after TP1 (not auto)\n\n"
        f"<a href=\"{desk.tradingview_url(sig['pair'])}\">Open {sig['pair']} on TradingView</a>\n\n"
        f"Built on Data.\n"
        f"Driven by Discipline."
    )


def check_pending(state, pair, ltf):
    if not state["pending"] or ltf is None:
        return
    remaining = []
    latest = len(ltf) - 1
    primary = str(desk.CHAT_ID) if desk.CHAT_ID else None
    for trade in state["pending"]:
        if trade.get("pair") != pair:
            remaining.append(trade)
            continue
        try:
            entry_time = pd.Timestamp(trade["candle_time"])
            matches = np.where(ltf["datetime"].values >= entry_time.to_datetime64())[0]
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
        closed = False
        edit_chat = trade.get("edit_chat_id") or primary
        for j in range(start, latest + 1):
            high = float(ltf["high"].iloc[j])
            low = float(ltf["low"].iloc[j])
            candle_time = str(ltf["datetime"].iloc[j])
            trade["last_checked_index"] = j
            if direction == 1:
                hit_sl, hit_tp2, hit_tp1 = low <= stop, high >= tp2, high >= tp1
            else:
                hit_sl, hit_tp2, hit_tp1 = high >= stop, low <= tp2, low <= tp1
            if hit_sl:
                desk.edit_telegram(trade.get("message_id"), desk.sl_message(trade, candle_time), edit_chat)
                closed = True
                break
            if hit_tp2:
                desk.edit_telegram(trade.get("message_id"), desk.tp2_message(trade, candle_time), edit_chat)
                closed = True
                break
            if hit_tp1 and not trade.get("tp1_hit"):
                trade["tp1_hit"] = True
                desk.edit_telegram(trade.get("message_id"), desk.tp1_message(trade), edit_chat)
        if not closed:
            remaining.append(trade)
    state["pending"] = remaining


def main():
    print("=" * 70)
    print("TENSION TRADING DESK — CRT PLAIN (same-TF)")
    print("1H→15m | 1H→5m | 4H→30m | 4H→15m | no keylevel filter")
    print("=" * 70)

    if not desk.TWELVE_DATA_KEY:
        print("Missing TWELVE_DATA_KEY")
        return

    state = load_state()
    cache = {}

    for pair, cfg in PAIRS.items():
        pip = cfg["pip"]
        print(f"\n--- {pair} ---")
        needed = set()
        for htf_name, ltf_name in STREAMS.values():
            needed.add(htf_name)
            needed.add(ltf_name)

        frames = {}
        ok = True
        for tf in needed:
            key = (pair, tf)
            if key not in cache:
                cache[key] = desk.fetch(pair, tf, 400 if tf != "5min" else 500)
            frames[tf] = cache[key]
            if frames[tf] is None:
                ok = False
        if not ok:
            print("  data failed")
            continue

        for tf in frames:
            frames[tf] = desk.add_atr(frames[tf])

        for stream_label, (htf_name, ltf_name) in STREAMS.items():
            ltf = frames[ltf_name]
            check_pending(state, pair, ltf)
            sig = analyze(stream_label, htf_name, ltf_name, frames[htf_name], ltf, pair, pip)
            if sig is None:
                print(f"  {stream_label}: no setup")
                continue
            key = sig["candle_key"]
            if state["last_alert_keys"].get(f"{stream_label}:{pair}") == key:
                print(f"  {stream_label}: already alerted")
                continue
            dup = any(
                t.get("pair") == pair
                and t.get("stream") == stream_label
                and t.get("status") == "ACTIVE"
                for t in state["pending"]
            )
            if dup:
                print(f"  {stream_label}: active trade open")
                continue

            message_id = desk.broadcast(build_signal_message(sig))
            print(f"  {stream_label}: signal -> {sig['side']} | {sig['risk_pips']:.1f} pips")
            state["last_alert_keys"][f"{stream_label}:{pair}"] = key
            if message_id:
                state["pending"].append(
                    {
                        "stream": stream_label,
                        "pair": pair,
                        "message_id": message_id,
                        "edit_chat_id": str(desk.CHAT_ID) if desk.CHAT_ID else None,
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
                        "last_checked_index": len(ltf) - 1,
                    }
                )

    save_state(state)
    print("\nCRT PLAIN SCAN COMPLETE | pending:", len(state["pending"]))


if __name__ == "__main__":
    main()
