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

Weekly performance:
  Monday → Friday market close
  CRT Plain streams only
  Uses shared outcomes.json
  Report once per completed week
  🇳🇬 WAT + 🌐 UTC timestamps
"""

import os
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import importlib

desk = importlib.import_module("3c_model")

STATE_FILE = "last_alert_state_crt_plain.json"
STATE_VERSION = 2
MIN_RR = 1.5

# Forex Friday close used for the weekly scorecard.
# 21:00 UTC = 22:00 Nigeria WAT.
WEEK_CLOSE_UTC_HOUR = 21

PAIRS = desk.PAIRS

STREAMS = {
    "CRT 1H→15m": ("1h", "15min"),
    "CRT 1H→5m": ("1h", "5min"),
    "CRT 4H→30m": ("4h", "30min"),
    "CRT 4H→15m": ("4h", "15min"),
}


# ============================================================
# STATE
# ============================================================

def load_state():
    default = {
        "version": STATE_VERSION,
        "last_alert_keys": {},
        "pending": [],
        "last_weekly_report": "",
    }
    if not os.path.exists(STATE_FILE):
        return default
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        if state.get("version") != STATE_VERSION:
            # Preserve nothing from an incompatible state version.
            return default
        state.setdefault("last_alert_keys", {})
        state.setdefault("pending", [])
        state.setdefault("last_weekly_report", "")
        return state
    except Exception as e:
        print("CRT plain state load error:", e)
        return default


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


# ============================================================
# CRT LOGIC — UNCHANGED
# ============================================================

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


# ============================================================
# MESSAGES
# ============================================================

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
        f"Candle: <b>{desk.format_times(sig['candle_time'])}</b>\n\n"
        f"🚀 <b>TRADE ACTIVE</b>\n"
        f"💡 Suggestion only: BE after TP1 (not auto)\n\n"
        f"<a href=\"{desk.tradingview_url(sig['pair'])}\">Open {sig['pair']} on TradingView</a>\n\n"
        f"Built on Data.\n"
        f"Driven by Discipline."
    )


# ============================================================
# PENDING TRADE MANAGEMENT
# ============================================================

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
            matches = np.where(
                ltf["datetime"].values >= entry_time.to_datetime64()
            )[0]
        except Exception:
            remaining.append(trade)
            continue

        if len(matches) == 0:
            remaining.append(trade)
            continue

        signal_index = int(matches[0])
        last_checked = int(
            trade.get("last_checked_index", signal_index)
        )
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
                hit_sl = low <= stop
                hit_tp2 = high >= tp2
                hit_tp1 = high >= tp1
            else:
                hit_sl = high >= stop
                hit_tp2 = low <= tp2
                hit_tp1 = low <= tp1

            if hit_sl:
                desk.resolve_trade(
                    trade,
                    "LOSS",
                    -1.0,
                    candle_time,
                    desk.sl_message(trade, candle_time),
                )
                closed = True
                break

            if hit_tp2:
                desk.resolve_trade(
                    trade,
                    "WIN",
                    float(trade.get("rr2", 0)),
                    candle_time,
                    desk.tp2_message(trade, candle_time),
                )
                closed = True
                break

            if hit_tp1 and not trade.get("tp1_hit"):
                trade["tp1_hit"] = True
                desk.edit_telegram(
                    trade.get("message_id"),
                    desk.tp1_message(trade),
                    edit_chat,
                )

        if not closed:
            remaining.append(trade)

    state["pending"] = remaining


# ============================================================
# WEEKLY PERFORMANCE ENGINE
# ============================================================

def _parse_utc(value):
    try:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            return ts.tz_localize("UTC")
        return ts.tz_convert("UTC")
    except Exception:
        return None


def _week_close_for_date(date_obj):
    """
    Return the Friday 21:00 UTC close for the ISO week containing date_obj.
    """
    monday = date_obj - timedelta(days=date_obj.weekday())
    friday = monday + timedelta(days=4)
    return datetime(
        friday.year,
        friday.month,
        friday.day,
        WEEK_CLOSE_UTC_HOUR,
        0,
        0,
        tzinfo=timezone.utc,
    )


def _previous_week_window(now_utc):
    """
    Return:
      week_start = previous Monday 00:00 UTC
      week_close = previous Friday 21:00 UTC
      week_key   = YYYY-Www
    """
    current_monday = (
        now_utc.date() - timedelta(days=now_utc.weekday())
    )
    previous_monday = current_monday - timedelta(days=7)

    week_start = datetime(
        previous_monday.year,
        previous_monday.month,
        previous_monday.day,
        0, 0, 0,
        tzinfo=timezone.utc,
    )

    week_close = _week_close_for_date(previous_monday)
    iso = previous_monday.isocalendar()
    week_key = f"{iso.year}-W{iso.week:02d}"

    return week_start, week_close, week_key


def _format_dual_time(ts):
    """🇳🇬 Nigeria/WAT + 🌐 UTC."""
    if ts is None:
        return "n/a"

    ts = _parse_utc(ts)
    if ts is None:
        return "n/a"

    wat = ts + pd.Timedelta(hours=1)

    def fmt(x):
        return x.strftime("%I:%M %p").lstrip("0")

    return (
        f"🇳🇬 {fmt(wat)} WAT · "
        f"🌐 {fmt(ts)} UTC · {wat.strftime('%d %b %Y')}"
    )


def _is_crt_plain_record(record):
    stream = str(record.get("stream", ""))
    return stream.startswith("CRT ")


def _load_crt_outcomes():
    """
    Read only CRT Plain outcome records from shared outcomes.json.
    """
    try:
        rows = desk.load_outcomes()
        if not isinstance(rows, list):
            return []
        return [r for r in rows if _is_crt_plain_record(r)]
    except Exception as e:
        print("CRT weekly outcomes load error:", e)
        return []


def _weekly_metrics(rows, start, close):
    selected = []

    for row in rows:
        exit_ts = _parse_utc(row.get("exit_time"))
        if exit_ts is None:
            continue

        if start <= exit_ts <= close:
            selected.append((row, exit_ts))

    total = len(selected)
    wins = sum(
        1 for row, _ in selected
        if str(row.get("status", "")).upper() == "WIN"
    )
    losses = sum(
        1 for row, _ in selected
        if str(row.get("status", "")).upper() == "LOSS"
    )
    time_exits = sum(
        1 for row, _ in selected
        if str(row.get("status", "")).upper() == "TIME EXIT"
    )

    rs = []
    winning_rs = []
    losing_rs = []

    for row, _ in selected:
        try:
            r = float(row.get("result_r"))
        except Exception:
            continue

        rs.append(r)

        if r > 0:
            winning_rs.append(r)
        elif r < 0:
            losing_rs.append(r)

    total_r = sum(rs)
    avg_r = total_r / len(rs) if rs else 0.0
    avg_win_r = (
        sum(winning_rs) / len(winning_rs)
        if winning_rs else 0.0
    )
    avg_loss_r = (
        sum(losing_rs) / len(losing_rs)
        if losing_rs else 0.0
    )

    gross_profit = sum(r for r in rs if r > 0)
    gross_loss_abs = abs(sum(r for r in rs if r < 0))
    profit_factor = (
        gross_profit / gross_loss_abs
        if gross_loss_abs > 0
        else (float("inf") if gross_profit > 0 else 0.0)
    )

    win_rate = (wins / total * 100) if total else 0.0
    best_r = max(rs) if rs else 0.0
    worst_r = min(rs) if rs else 0.0

    tp1_touches = 0
    tp2_wins = 0

    for row, _ in selected:
        status = str(row.get("status", "")).upper()
        if status == "WIN":
            tp2_wins += 1

    # TP1 touches are not outcome rows in the shared outcomes log.
    # We therefore count TP1 only when an outcome record contains
    # explicit tp1_hit information in future versions. Existing data
    # remains fully compatible.
    for row, _ in selected:
        if bool(row.get("tp1_hit", False)):
            tp1_touches += 1

    pairs = {}
    streams = {}

    for row, _ in selected:
        pair = row.get("pair", "Unknown")
        stream = row.get("stream", "Unknown")
        pairs[pair] = pairs.get(pair, 0) + 1
        streams[stream] = streams.get(stream, 0) + 1

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "time_exits": time_exits,
        "win_rate": win_rate,
        "total_r": total_r,
        "avg_r": avg_r,
        "avg_win_r": avg_win_r,
        "avg_loss_r": avg_loss_r,
        "profit_factor": profit_factor,
        "best_r": best_r,
        "worst_r": worst_r,
        "tp1_touches": tp1_touches,
        "tp2_wins": tp2_wins,
        "pairs": pairs,
        "streams": streams,
    }


def _weekly_verdict(m):
    """
    Descriptive rule-based label, not a prediction.
    """
    if m["total"] == 0:
        return "⚪ NO TRADES", "No completed CRT Plain trades this week."

    if m["total_r"] >= 5 and m["win_rate"] >= 60:
        return "🟢 VERY NICE WEEK", "Strong positive R with a solid win rate."
    if m["total_r"] >= 2 and m["win_rate"] >= 50:
        return "🟢 GOOD WEEK", "Positive R and more wins than losses."
    if m["total_r"] > 0:
        return "🟡 POSITIVE WEEK", "The week finished above zero R."
    if m["total_r"] == 0:
        return "🟡 MIXED WEEK", "The completed trades finished around breakeven."
    if m["total_r"] <= -5:
        return "🔴 VERY BAD WEEK", "Large negative R for the completed sample."
    return "🔴 BAD WEEK", "The completed trades finished below zero R."


def build_weekly_report(start, close, week_key, metrics):
    verdict, explanation = _weekly_verdict(metrics)

    pf = metrics["profit_factor"]
    pf_text = "∞" if pf == float("inf") else f"{pf:.2f}"

    if metrics["total"]:
        result_line = (
            f"{metrics['wins']}W / {metrics['losses']}L"
            + (
                f" / {metrics['time_exits']} TIME"
                if metrics["time_exits"]
                else ""
            )
        )
    else:
        result_line = "0W / 0L"

    pair_lines = "\n".join(
        f"• {p}: {n}"
        for p, n in sorted(metrics["pairs"].items())
    ) or "• None"

    stream_lines = "\n".join(
        f"• {s}: {n}"
        for s, n in sorted(metrics["streams"].items())
    ) or "• None"

    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"════════════════════\n"
        f"📊 <b>CRT PLAIN — WEEKLY SCORECARD</b>\n"
        f"<b>{week_key}</b>\n"
        f"────────────────────\n\n"
        f"📅 <b>Trading window</b>\n"
        f"{_format_dual_time(start)}\n"
        f"→ {_format_dual_time(close)}\n\n"
        f"🏁 <b>WEEKLY VERDICT</b>\n"
        f"{verdict}\n"
        f"{explanation}\n\n"
        f"<b>PERFORMANCE</b>\n"
        f"Trades: <b>{metrics['total']}</b>\n"
        f"Record: <b>{result_line}</b>\n"
        f"Win Rate: <b>{metrics['win_rate']:.1f}%</b>\n"
        f"Total R: <b>{metrics['total_r']:+.2f}R</b>\n"
        f"Average R: <b>{metrics['avg_r']:+.2f}R</b>\n"
        f"Avg Win: <b>+{metrics['avg_win_r']:.2f}R</b>\n"
        f"Avg Loss: <b>{metrics['avg_loss_r']:.2f}R</b>\n"
        f"Profit Factor: <b>{pf_text}</b>\n"
        f"Best Trade: <b>{metrics['best_r']:+.2f}R</b>\n"
        f"Worst Trade: <b>{metrics['worst_r']:+.2f}R</b>\n\n"
        f"<b>TRADE EVENTS</b>\n"
        f"TP2 Wins: <b>{metrics['tp2_wins']}</b>\n"
        f"TP1 Touches Logged: <b>{metrics['tp1_touches']}</b>\n"
        f"Time Exits: <b>{metrics['time_exits']}</b>\n\n"
        f"<b>PAIRS</b>\n"
        f"{pair_lines}\n\n"
        f"<b>STREAMS</b>\n"
        f"{stream_lines}\n\n"
        f"🇳🇬 WAT · 🌐 UTC\n"
        f"Built on Data.\n"
        f"Driven by Discipline."
    )


def maybe_send_weekly_report(state):
    """
    Send the previous Monday→Friday scorecard once the Friday close
    has passed. Since GitHub Actions runs every 5 minutes, this will
    naturally be picked up on the first run after the weekly close.
    """
    now_utc = datetime.now(timezone.utc)

    start, close, week_key = _previous_week_window(now_utc)

    if now_utc < close:
        return

    if state.get("last_weekly_report") == week_key:
        return

    rows = _load_crt_outcomes()
    metrics = _weekly_metrics(rows, start, close)
    report = build_weekly_report(start, close, week_key, metrics)

    message_id = desk.broadcast(report)

    if message_id or desk.CHAT_ID or desk.load_subscribers():
        state["last_weekly_report"] = week_key
        print(
            f"CRT weekly report sent: {week_key} | "
            f"{metrics['total']} trades | {metrics['total_r']:+.2f}R"
        )
    else:
        print("CRT weekly report not sent — no Telegram subscribers.")

# ============================================================
# MAIN
# ============================================================

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
                cache[key] = desk.fetch(
                    pair,
                    tf,
                    400 if tf != "5min" else 500
                )
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

            sig = analyze(
                stream_label,
                htf_name,
                ltf_name,
                frames[htf_name],
                ltf,
                pair,
                pip,
            )

            if sig is None:
                print(f"  {stream_label}: no setup")
                continue

            key = sig["candle_key"]

            if state["last_alert_keys"].get(
                f"{stream_label}:{pair}"
            ) == key:
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

            print(
                f"  {stream_label}: signal -> "
                f"{sig['side']} | {sig['risk_pips']:.1f} pips"
            )

            state["last_alert_keys"][
                f"{stream_label}:{pair}"
            ] = key

            if message_id:
                state["pending"].append(
                    {
                        "stream": stream_label,
                        "pair": pair,
                        "message_id": message_id,
                        "edit_chat_id": (
                            str(desk.CHAT_ID)
                            if desk.CHAT_ID
                            else None
                        ),
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

    # Weekly report is deliberately after trade processing so that
    # any Friday outcome already captured in this run is included.
    maybe_send_weekly_report(state)

    save_state(state)

    print(
        "\nCRT PLAIN SCAN COMPLETE | pending:",
        len(state["pending"])
    )


if __name__ == "__main__":
    main()
