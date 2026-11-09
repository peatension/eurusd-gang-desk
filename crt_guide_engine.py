"""
TENSION TRADING DESK — CRT GUIDE ENGINE
Built strictly from the COMPLETE CRT SYSTEM & STRUCTURAL GUIDE.
No research matrix. No Colab ranking.

PAIRS (guide):
  EUR/USD · GBP/USD · AUD/USD · USD/JPY

HTF: H1 | H4
LTF: M15 only

MODELS:
  2C   — Aggressive: same bar sweep + close inside + expansion bias
  3C   — Classic: C1 range, C2 purge+close inside, C3 delivery
  MULTI — Compression 2–4 bars / inside-bar, then raid + close inside

FILTERS (guide):
  - Structural validation on body (open/close), wicks = liquidity only
  - Session: London + London/NY Overlap + New York only (Asia blocked)
  - One-touch freshness: each CRH/CRL range used once (touch_count == 0)
  - Target = opposite extremity of the reference range
  - SL beyond purge extreme + buffer

Telegram + state lifecycle aligned with existing desk patterns.
State: last_alert_state_crt_guide.json
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import importlib

desk = importlib.import_module("3c_model")

# ---------------------------------------------------------------------------
# ENV / STATE
# ---------------------------------------------------------------------------
STATE_FILE = "last_alert_state_crt_guide.json"
STATE_VERSION = 1
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")

PAIRS = {
    "EUR/USD": {"pip": 0.0001, "tv": "OANDA:EURUSD"},
    "GBP/USD": {"pip": 0.0001, "tv": "OANDA:GBPUSD"},
    "AUD/USD": {"pip": 0.0001, "tv": "OANDA:AUDUSD"},
    "USD/JPY": {"pip": 0.01, "tv": "OANDA:USDJPY"},
}
CORE_PAIRS = list(PAIRS.keys())

# Stream label → (htf_interval, ltf_interval, model)
STREAMS = {
    "CRT Guide H1→15m 2C":    ("1h", "15min", "2C"),
    "CRT Guide H1→15m 3C":    ("1h", "15min", "3C"),
    "CRT Guide H1→15m MULTI": ("1h", "15min", "MULTI"),
    "CRT Guide H4→15m 2C":    ("4h", "15min", "2C"),
    "CRT Guide H4→15m 3C":    ("4h", "15min", "3C"),
    "CRT Guide H4→15m MULTI": ("4h", "15min", "MULTI"),
}

PIP_BUF = 2.0
MAX_HOLD_BARS = 96  # M15 bars \~ 24h
OUTPUT = {"4h": 400, "1h": 600, "15min": 1200}


# ---------------------------------------------------------------------------
# TIME / SESSION (guide: London + NY only)
# ---------------------------------------------------------------------------

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def wat_now():
    return utc_now() + timedelta(hours=1)


def _as_utc(dt_like):
    if dt_like is None:
        return None
    t = pd.Timestamp(dt_like)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t.to_pydatetime() if hasattr(t, "to_pydatetime") else t


def forex_session(dt_like):
    h = int(_as_utc(dt_like).hour)
    if 0 <= h < 6:
        return "Asia"
    if 6 <= h < 12:
        return "London"
    if 12 <= h < 16:
        return "London/NY Overlap"
    if 16 <= h < 21:
        return "New York"
    return "Off-hours"


def session_allowed(dt_like):
    s = forex_session(dt_like)
    return s in ("London", "London/NY Overlap", "New York")


def is_weekend_utc(now=None):
    now = now or utc_now()
    return now.weekday() >= 5


def format_dual_times(setup_time, signal_time=None):
    def _fmt(dt):
        if dt is None:
            return "—"
        u = _as_utc(dt)
        w = u + timedelta(hours=1)
        return (
            f"🕐 WAT {w.strftime('%I:%M %p').lstrip('0')} | "
            f"UTC {u.strftime('%I:%M %p').lstrip('0')}\n"
            f"📅 {w.strftime('%d %b %Y')}"
        )
    lines = [f"Setup candle:\n{_fmt(setup_time)}"]
    if signal_time is not None:
        lines.append(f"Signal time:\n{_fmt(signal_time)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# STATE
# ---------------------------------------------------------------------------

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            st = json.load(f)
    except Exception:
        st = {}
    if st.get("version") != STATE_VERSION:
        st = {
            "version": STATE_VERSION,
            "pending": [],
            "fired_locks": {},
            "range_touches": {},
            "ticket_seq": int(st.get("ticket_seq") or 0),
        }
    st.setdefault("pending", [])
    st.setdefault("fired_locks", {})
    st.setdefault("range_touches", {})
    st.setdefault("ticket_seq", 0)
    return st


def save_state(state):
    state["version"] = STATE_VERSION
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    os.replace(tmp, STATE_FILE)


def next_ticket(state):
    state["ticket_seq"] = int(state.get("ticket_seq") or 0) + 1
    return f"TTD-G-{state['ticket_seq']:04d}"


def range_key(pair, crh, crl, direction):
    return f"{pair}|{direction}|{round(float(crh), 5)}|{round(float(crl), 5)}"


def fire_lock_key(stream, pair, direction, c2_time, model):
    t = str(c2_time)[:16]
    return f"{stream}|{pair}|{direction}|{t}|{model}"


# ---------------------------------------------------------------------------
# CRT GUIDE DETECTORS — body-based validation
# ---------------------------------------------------------------------------

def _body_high(row):
    return max(float(row["open"]), float(row["close"]))


def _body_low(row):
    return min(float(row["open"]), float(row["close"]))


def _closed_inside_body(row, crh, crl):
    """Guide: close (body) back inside range — not wick-only."""
    c = float(row["close"])
    return crl < c < crh


def detect_2c(htf):
    """Aggressive 2-candle: C2 sweeps wick beyond C1 and closes body inside."""
    if htf is None or len(htf) < 4:
        return None
    c2 = htf.iloc[-2]
    c1 = htf.iloc[-3]
    crh, crl = float(c1["high"]), float(c1["low"])
    if crh <= crl:
        return None
    if float(c2["low"]) < crl and _closed_inside_body(c2, crh, crl):
        return {
            "model": "2C",
            "direction": 1,
            "crh": crh,
            "crl": crl,
            "mid": 0.5 * (crh + crl),
            "c1_time": c1["datetime"],
            "c2_time": c2["datetime"],
            "purge_extreme": float(c2["low"]),
        }
    if float(c2["high"]) > crh and _closed_inside_body(c2, crh, crl):
        return {
            "model": "2C",
            "direction": -1,
            "crh": crh,
            "crl": crl,
            "mid": 0.5 * (crh + crl),
            "c1_time": c1["datetime"],
            "c2_time": c2["datetime"],
            "purge_extreme": float(c2["high"]),
        }
    return None


def detect_3c(htf):
    """Classic 3-candle: C1 range, C2 purge + body close inside, C3 starts delivery."""
    if htf is None or len(htf) < 5:
        return None
    c3 = htf.iloc[-2]
    c2 = htf.iloc[-3]
    c1 = htf.iloc[-4]
    crh, crl = float(c1["high"]), float(c1["low"])
    if crh <= crl:
        return None
    if float(c2["low"]) < crl and _closed_inside_body(c2, crh, crl):
        if float(c3["close"]) > float(c3["open"]) or float(c3["close"]) > _body_low(c2):
            return {
                "model": "3C",
                "direction": 1,
                "crh": crh,
                "crl": crl,
                "mid": 0.5 * (crh + crl),
                "c1_time": c1["datetime"],
                "c2_time": c2["datetime"],
                "c3_time": c3["datetime"],
                "purge_extreme": float(c2["low"]),
            }
    if float(c2["high"]) > crh and _closed_inside_body(c2, crh, crl):
        if float(c3["close"]) < float(c3["open"]) or float(c3["close"]) < _body_high(c2):
            return {
                "model": "3C",
                "direction": -1,
                "crh": crh,
                "crl": crl,
                "mid": 0.5 * (crh + crl),
                "c1_time": c1["datetime"],
                "c2_time": c2["datetime"],
                "c3_time": c3["datetime"],
                "purge_extreme": float(c2["high"]),
            }
    return None


def detect_multi(htf):
    """Multiple-candle / inside-bar compression (2–4 bars), then raid + body close inside."""
    if htf is None or len(htf) < 8:
        return None
    raid = htf.iloc[-2]
    for width in (2, 3, 4):
        start = -2 - width
        if len(htf) + start < 0:
            continue
        block = htf.iloc[start:-2]
        if len(block) < width:
            continue
        crh = float(block["high"].max())
        crl = float(block["low"].min())
        if crh <= crl:
            continue
        if float(raid["low"]) < crl and _closed_inside_body(raid, crh, crl):
            return {
                "model": "MULTI",
                "direction": 1,
                "crh": crh,
                "crl": crl,
                "mid": 0.5 * (crh + crl),
                "c1_time": block.iloc[0]["datetime"],
                "c2_time": raid["datetime"],
                "purge_extreme": float(raid["low"]),
                "width": width,
            }
        if float(raid["high"]) > crh and _closed_inside_body(raid, crh, crl):
            return {
                "model": "MULTI",
                "direction": -1,
                "crh": crh,
                "crl": crl,
                "mid": 0.5 * (crh + crl),
                "c1_time": block.iloc[0]["datetime"],
                "c2_time": raid["datetime"],
                "purge_extreme": float(raid["high"]),
                "width": width,
            }
    return None


DETECT = {"2C": detect_2c, "3C": detect_3c, "MULTI": detect_multi}


def m15_entry(ltf, setup):
    if ltf is None or ltf.empty:
        return None
    t = pd.Timestamp(setup["c2_time"])
    after = ltf[ltf["datetime"] > t]
    if after.empty:
        return None
    row = after.iloc[0]
    return {
        "entry": float(row["open"]),
        "candle_time": row["datetime"],
    }


def build_levels(setup, entry, pip):
    d = int(setup["direction"])
    crh, crl = float(setup["crh"]), float(setup["crl"])
    e = float(entry["entry"])
    extreme = float(setup["purge_extreme"])
    if d == 1:
        sl = min(extreme, crl) - PIP_BUF * pip
        if e <= sl:
            return None
        risk = e - sl
        tp1 = 0.5 * (crh + crl)
        if tp1 <= e:
            tp1 = e + risk
        tp2 = crh
        if tp2 <= e:
            return None
    else:
        sl = max(extreme, crh) + PIP_BUF * pip
        if e >= sl:
            return None
        risk = sl - e
        tp1 = 0.5 * (crh + crl)
        if tp1 >= e:
            tp1 = e - risk
        tp2 = crl
        if tp2 >= e:
            return None
    r1 = abs(tp1 - e) / risk
    r2 = abs(tp2 - e) / risk
    return {
        "entry": e,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr1": r1,
        "rr2": r2,
        "risk": risk,
                    }
  
# ---------------------------------------------------------------------------
# MESSAGES
# ---------------------------------------------------------------------------

def _px(pair, v):
    if v is None:
        return "—"
    dec = 3 if "JPY" in pair else 5
    if pair.startswith("XAU"):
        dec = 2
    return f"{float(v):.{dec}f}"


def build_signal_message(sig):
    emoji = "🟢" if int(sig["direction"]) == 1 else "🔴"
    side = "BUY" if int(sig["direction"]) == 1 else "SELL"
    return (
        f"{emoji} <b>TENSION TRADING DESK</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Ticket: <b>{sig.get('ticket_id', '—')}</b>\n"
        f"<b>{sig['stream']}</b>\n"
        f"Model: <b>{sig['model']}</b> · Session: <b>{sig['session']}</b>\n"
        f"C1 TF: <b>{sig['htf'].upper()}</b> → Entry TF: <b>15min</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>{side} {sig['pair']}</b>\n"
        f"C1 High: <b>{_px(sig['pair'], sig['crh'])}</b>\n"
        f"C1 Mid:  <b>{_px(sig['pair'], sig['mid'])}</b>\n"
        f"C1 Low:  <b>{_px(sig['pair'], sig['crl'])}</b>\n"
        f"Entry: <b>{_px(sig['pair'], sig['entry'])}</b>\n"
        f"SL:    <b>{_px(sig['pair'], sig['sl'])}</b>\n"
        f"TP1:   <b>{_px(sig['pair'], sig['tp1'])}</b> (mid)\n"
        f"TP2:   <b>{_px(sig['pair'], sig['tp2'])}</b> (opposite)\n\n"
        f"{format_dual_times(sig.get('c2_time'), sig.get('candle_time'))}\n\n"
        f"📌 Status: <b>ACTIVE</b> — Guide CRT (body close · one-touch · London/NY)\n\n"
        f"Built on Data · Driven by Discipline"
    )


def tp1_msg(trade):
    emoji = "🟢" if int(trade["direction"]) == 1 else "🔴"
    return (
        f"{emoji} <b>TENSION TRADING DESK</b>\n"
        f"Ticket: <b>{trade.get('ticket_id', '—')}</b>\n"
        f"<b>{trade['stream']}</b> · {trade['pair']}\n\n"
        f"🎯 <b>TP1 HIT</b> — heading to TP2 (opposite range)\n"
        f"Entry: {_px(trade['pair'], trade['entry'])}\n"
        f"TP1: {_px(trade['pair'], trade['tp1'])}\n"
        f"TP2: {_px(trade['pair'], trade['tp2'])}\n"
    )


def final_msg(trade, win=True):
    emoji = "🏆" if win else "❌"
    label = "TP2 HIT — WIN" if win else "SL HIT — LOSS"
    rr = float(trade.get("rr2") or 0) if win else -1.0
    return (
        f"{emoji} <b>TENSION TRADING DESK</b>\n"
        f"Ticket: <b>{trade.get('ticket_id', '—')}</b>\n"
        f"<b>{trade['stream']}</b> · {trade['pair']}\n\n"
        f"<b>{label}</b>\n"
        f"Result: <b>{rr:+.2f}R</b>\n"
        f"Status: CLOSED\n"
    )


def tv_keyboard(pair):
    tv = PAIRS.get(pair, {}).get("tv", f"OANDA:{pair.replace('/', '')}")
    url = f"https://www.tradingview.com/chart/?symbol={tv}"
    return {"inline_keyboard": [[{"text": "📊 Open TradingView", "url": url}]]}


def send_all(text, reply_markup=None):
    subs = []
    try:
        subs = desk.load_subscribers()
    except Exception:
        pass
    if not subs and CHAT_ID:
        subs = [{"id": str(CHAT_ID)}]
    mids = []
    for s in subs:
        cid = str(s.get("id") or s.get("chat_id") or "")
        if not cid:
            continue
        try:
            mid = desk.send_telegram_to(cid, text, reply_markup=reply_markup)
            if mid:
                mids.append((cid, mid))
        except Exception as e:
            print(f"Telegram error ({s}): {e}")
    return mids


def edit_all(trade, text):
    msgs = trade.get("messages") or []
    if not msgs and trade.get("message_id") and trade.get("edit_chat_id"):
        msgs = [(trade["edit_chat_id"], trade["message_id"])]
    for cid, mid in msgs:
        try:
            if hasattr(desk, "edit_telegram"):
                desk.edit_telegram(cid, mid, text)
            else:
                import requests
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
                    json={
                        "chat_id": cid,
                        "message_id": mid,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=20,
                )
        except Exception as e:
            print(f"edit fail {cid}/{mid}: {e}")


# ---------------------------------------------------------------------------
# PENDING MANAGEMENT
# ---------------------------------------------------------------------------

def check_pending(state, pair, ltf):
    if ltf is None or ltf.empty:
        return
    still = []
    for trade in state.get("pending", []):
        if trade.get("pair") != pair:
            still.append(trade)
            continue
        if trade.get("status") == "CLOSED":
            continue
        d = int(trade["direction"])
        entry = float(trade["entry"])
        sl = float(trade["sl"])
        tp1 = float(trade["tp1"])
        tp2 = float(trade["tp2"])
        tp1_hit = bool(trade.get("tp1_hit"))
        start = pd.Timestamp(trade.get("candle_time") or trade.get("c2_time"))
        path = ltf[ltf["datetime"] > start]
        closed = False
        for _, r in path.iterrows():
            h, l = float(r["high"]), float(r["low"])
            if d == 1:
                if l <= sl:
                    trade["status"] = "CLOSED"
                    edit_all(trade, final_msg(trade, win=False))
                    send_all(final_msg(trade, win=False))
                    closed = True
                    break
                if not tp1_hit and h >= tp1:
                    trade["tp1_hit"] = True
                    tp1_hit = True
                    edit_all(trade, tp1_msg(trade))
                    save_state(state)
                if h >= tp2:
                    trade["status"] = "CLOSED"
                    edit_all(trade, final_msg(trade, win=True))
                    send_all(final_msg(trade, win=True))
                    closed = True
                    break
            else:
                if h >= sl:
                    trade["status"] = "CLOSED"
                    edit_all(trade, final_msg(trade, win=False))
                    send_all(final_msg(trade, win=False))
                    closed = True
                    break
                if not tp1_hit and l <= tp1:
                    trade["tp1_hit"] = True
                    tp1_hit = True
                    edit_all(trade, tp1_msg(trade))
                    save_state(state)
                if l <= tp2:
                    trade["status"] = "CLOSED"
                    edit_all(trade, final_msg(trade, win=True))
                    send_all(final_msg(trade, win=True))
                    closed = True
                    break
        if not closed:
            still.append(trade)
    state["pending"] = still


# ---------------------------------------------------------------------------
# SCAN
# ---------------------------------------------------------------------------

def analyze(stream, htf_name, model, htf, ltf, pair, pip, state):
    fn = DETECT.get(model)
    if not fn:
        return None
    setup = fn(htf)
    if not setup:
        return None
    if not session_allowed(setup["c2_time"]):
        return None
    if is_weekend_utc():
        return None
    rk = range_key(pair, setup["crh"], setup["crl"], setup["direction"])
    if state.get("range_touches", {}).get(rk):
        return None
    ent = m15_entry(ltf, setup)
    if not ent:
        return None
    lv = build_levels(setup, ent, pip)
    if not lv:
        return None
    lock = fire_lock_key(stream, pair, setup["direction"], setup["c2_time"], model)
    if state.get("fired_locks", {}).get(lock):
        return None
    sess = forex_session(setup["c2_time"])
    return {
        "stream": stream,
        "model": model,
        "pair": pair,
        "htf": htf_name,
        "direction": setup["direction"],
        "crh": setup["crh"],
        "crl": setup["crl"],
        "mid": setup["mid"],
        "c2_time": setup["c2_time"],
        "candle_time": ent["candle_time"],
        "session": sess,
        "purge_extreme": setup["purge_extreme"],
        "range_key": rk,
        "fire_lock": lock,
        **lv,
    }


def main():
    print("=" * 70)
    print("TENSION TRADING DESK — CRT GUIDE ENGINE")
    print("2C / 3C / MULTI | H1+H4 → M15 | London+NY | body close | one-touch")
    print("Pairs:", ", ".join(CORE_PAIRS))
    print("=" * 70)

    state = load_state()
    try:
        if hasattr(desk, "process_commands"):
            desk.process_commands(state)
    except Exception as e:
        print("process_commands:", e)

    weekend = is_weekend_utc()
    if weekend:
        print("Weekend: no new signals | managing open trades only")

    cache = {}
    need = set()
    for _, (h, l, _) in STREAMS.items():
        need.add(h)
        need.add(l)

    for pair in CORE_PAIRS:
        print(f"\n--- {pair} ---")
        pip = PAIRS[pair]["pip"]
        frames = {}
        ok = True
        for iv in sorted(need):
            key = (pair, iv)
            if key not in cache:
                cache[key] = desk.fetch(pair, iv, OUTPUT.get(iv, 500))
            frames[iv] = cache[key]
            if frames[iv] is None or len(frames[iv]) < 10:
                print(f"  data fail {iv}")
                ok = False
        if not ok:
            continue

        check_pending(state, pair, frames.get("15min"))
        save_state(state)

        if weekend:
            continue

        for stream, (htf_i, ltf_i, model) in STREAMS.items():
            htf, ltf = frames.get(htf_i), frames.get(ltf_i)
            if htf is None or ltf is None:
                print(f"  {stream}: no data")
                continue
            sig = analyze(stream, htf_i, model, htf, ltf, pair, pip, state)
            if not sig:
                print(f"  {stream}: no setup")
                continue

            state.setdefault("fired_locks", {})[sig["fire_lock"]] = str(utc_now())
            state.setdefault("range_touches", {})[sig["range_key"]] = 1
            sig["ticket_id"] = next_ticket(state)
            save_state(state)

            text = build_signal_message(sig)
            kb = tv_keyboard(pair)
            mids = send_all(text, reply_markup=kb)
            trade = {
                **sig,
                "messages": mids,
                "message_id": mids[0][1] if mids else None,
                "edit_chat_id": mids[0][0] if mids else None,
                "status": "ACTIVE",
                "tp1_hit": False,
            }
            state.setdefault("pending", []).append(trade)
            save_state(state)
            print(f"  {stream}: FIRED {('BUY' if sig['direction']==1 else 'SELL')} | {sig['session']}")

    save_state(state)
    print("\n" + "=" * 70)
    print(f"CRT GUIDE SCAN COMPLETE | pending: {len(state.get('pending', []))}")
    print("=" * 70)


if __name__ == "__main__":
    main()
