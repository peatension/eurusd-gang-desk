"""
TENSION TRADING DESK — CRT PLAIN (set 1)
=======================================
Pairs: EUR/USD | AUD/USD | USD/CHF | EUR/JPY

Streams (Telegram: CRT …):
  PLAIN + TS + MSS on all 5 HTF→LTF
  MODEL1 on 1H→15m, 1H→5m, 30MIN→5m, 4H→15m

Core: C1 + C2 close inside | LTF entry | TP1 mid | TP2 opposite | no BE

Features:
  Dual times (setup candle + signal sent, WAT+UTC)
  Same-message edit ACTIVE → TP1 HIT → WIN/LOSS (+ fallback broadcast)
  Multi-chat message_ids | Weekend LIMIT / ENTRY PENDING
  WAT reports once | Infra from 3c_model.py
"""

import os
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import importlib

desk = importlib.import_module("3c_model")

STATE_FILE = "last_alert_state_crt_plain.json"
STATE_VERSION = 7
MIN_RR = 1.5
OUTCOMES_ENGINE = "CRT_PLAIN"
REPORT_WINDOW_MIN = 12

PAIRS = desk.PAIRS

# Full engine (kept intact). Live credit budget uses live_slim.py override.
CORE_PAIRS = ["EUR/USD", "AUD/USD", "USD/CHF", "EUR/JPY", "XAU/USD", "AUD/JPY"]

# Entry models on HTF anchors: MSS + CISD preferred (PLAIN weak live).
# Daily / Weekly / Monthly HTF added (MSS+CISD only).
STREAMS = {
    # --- Intraday FX ---
    "CRT 1H→15m MSS":    ("1h", "15min", "MSS"),
    "CRT 1H→15m CISD":   ("1h", "15min", "CISD"),
    "CRT 1H→5m MODEL1":  ("1h", "5min", "MODEL1"),
    "CRT 4H→30m":        ("4h", "30min", "PLAIN"),
    "CRT 4H→30m MSS":    ("4h", "30min", "MSS"),
    "CRT 4H→15m MSS":    ("4h", "15min", "MSS"),
    "CRT 4H→15m CISD":   ("4h", "15min", "CISD"),
    # --- XAU ---
    "CRT 1H→15m FVG":    ("1h", "15min", "FVG"),
    "CRT 1H→5m CISD":    ("1h", "5min", "CISD"),
    "CRT 4H→15m FVG":    ("4h", "15min", "FVG"),
    "CRT 30MIN→5m CISD": ("30min", "5min", "CISD"),
    # --- Daily / Weekly / Monthly HTF (MSS + CISD only) ---
    "CRT D→4H MSS":      ("1day", "4h", "MSS"),
    "CRT D→4H CISD":     ("1day", "4h", "CISD"),
    "CRT D→1H MSS":      ("1day", "1h", "MSS"),
    "CRT D→1H CISD":     ("1day", "1h", "CISD"),
    "CRT W→D MSS":       ("1week", "1day", "MSS"),
    "CRT W→D CISD":      ("1week", "1day", "CISD"),
    "CRT MN→W MSS":      ("1month", "1week", "MSS"),
    "CRT MN→D CISD":     ("1month", "1day", "CISD"),
}

FX4 = ["EUR/USD", "AUD/USD", "USD/CHF", "EUR/JPY"]
FX6 = FX4 + ["AUD/JPY", "XAU/USD"]
STREAM_PAIRS = {
    "CRT 1H→15m MSS":    FX4 + ["XAU/USD", "AUD/JPY"],
    "CRT 1H→15m CISD":   ["EUR/USD", "AUD/JPY"],
    "CRT 1H→5m MODEL1":  ["EUR/USD"],
    "CRT 4H→30m":        ["EUR/USD", "AUD/USD"],
    "CRT 4H→30m MSS":    FX4 + ["AUD/JPY"],
    "CRT 4H→15m MSS":    ["AUD/USD", "USD/CHF", "EUR/JPY", "XAU/USD", "AUD/JPY"],
    "CRT 4H→15m CISD":   FX4 + ["AUD/JPY"],
    "CRT 1H→15m FVG":    ["XAU/USD"],
    "CRT 1H→5m CISD":    ["XAU/USD"],
    "CRT 4H→15m FVG":    ["XAU/USD"],
    "CRT 30MIN→5m CISD": ["XAU/USD"],
    # Higher TF — solid pairs book
    "CRT D→4H MSS":      FX6,
    "CRT D→4H CISD":     FX6,
    "CRT D→1H MSS":      FX6,
    "CRT D→1H CISD":     FX6,
    "CRT W→D MSS":       FX6,
    "CRT W→D CISD":      FX6,
    "CRT MN→W MSS":      FX6,
    "CRT MN→D CISD":     FX6,
}


# ============================================================
# STATE
# ============================================================

def load_state():
    default = {
        "version": STATE_VERSION,
        "last_alert_keys": {},
        "pending": [],
        "weekly_report_id": None,
        "monthly_report_id": None,
        "yearly_report_id": None,
        "strategy_start": None,
    }
    if not os.path.exists(STATE_FILE):
        return default
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        if state.get("version") != STATE_VERSION:
            print("CRT plain state version bump — merge safe fields.")
            default["last_alert_keys"] = state.get("last_alert_keys", {})
            default["weekly_report_id"] = state.get("weekly_report_id")
            default["monthly_report_id"] = state.get("monthly_report_id")
            default["yearly_report_id"] = state.get("yearly_report_id")
            default["strategy_start"] = state.get("strategy_start")
            if isinstance(state.get("pending"), list):
                default["pending"] = state["pending"]
            return default
        for k, v in default.items():
            state.setdefault(k, v)
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
# TIME / SESSION
# ============================================================

def _as_utc(dt_like):
    ts = pd.Timestamp(dt_like)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def utc_now():
    return datetime.now(timezone.utc)


def wat_now():
    return utc_now() + timedelta(hours=1)


def format_signal_time(dt_like):
    """Naive feed times = UTC. Show WAT + UTC with emojis. Correct date per TZ."""
    try:
        ts = pd.Timestamp(dt_like)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        try:
            wat = ts.tz_convert("Africa/Lagos")
        except Exception:
            wat = ts + pd.Timedelta(hours=1)

        def ampm(x):
            return x.strftime("%I:%M %p").lstrip("0")

        return (
            f"🇳🇬 {ampm(wat)} WAT · 🌐 {ampm(ts)} UTC · "
            f"{wat.strftime('%d %b %Y')}"
        )
    except Exception:
        return str(dt_like)


def format_dual_times(setup_time, signal_time=None, signal_sent=None):
    """Chart times only — not Telegram delivery time.

    setup_time  = C2 (sweep) on HTF
    signal_time = LTF entry candle (when model confirmed / ignited)
    """
    setup = setup_time or signal_time
    signal = signal_time or setup_time
    return (
        f"🕯 Setup candle (C2): <b>{format_signal_time(setup)}</b>\n"
        f"📡 Signal time: <b>{format_signal_time(signal)}</b>"
    )


def forex_session(dt_like):
    try:
        h = int(_as_utc(dt_like).hour)
    except Exception:
        return "Unknown"
    if 0 <= h < 7:
        return "Asia"
    if 7 <= h < 12:
        return "London"
    if 12 <= h < 16:
        return "London/NY Overlap"
    if 16 <= h < 21:
        return "New York"
    return "Off-hours"


def is_weekend_utc(now=None):
    now = now or utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.weekday() >= 5


def is_weekday_bar(dt_like):
    """Mon–Fri only — used so weekend jobs don't fire verdicts on Sat/Sun bars."""
    try:
        ts = pd.Timestamp(dt_like)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        return int(ts.weekday()) < 5
    except Exception:
        return True


# W41 live week: Asia n=15 net=-10.02R — block new Asia only
# Off-hours kept (user request) — still allowed for new signals
BLOCK_ASIA_SESSION = True
BLOCK_OFF_HOURS = False


# --- Signal hygiene (anti-noise) ---
ENTRY_DEDUP_PIPS = 5  # same entry zone across models/streams (FX); gold uses same count

ENTRY_PENDING_MAX_HOURS = 36  # drop weekend limits that never filled


def _pending_expired(trade, now=None):
    """True if ENTRY_PENDING is too old to keep blocking the pair."""
    if trade.get("status") != "ENTRY_PENDING":
        return False
    now = now or utc_now()
    for key in ("signal_sent", "candle_time", "c2_time"):
        raw = trade.get(key)
        if not raw:
            continue
        try:
            ts = pd.Timestamp(raw)
            if ts.tzinfo is not None:
                ts = ts.tz_convert("UTC").tz_localize(None)
            n = pd.Timestamp(now)
            if n.tzinfo is not None:
                n = n.tz_convert("UTC").tz_localize(None)
            age_h = (n - ts).total_seconds() / 3600.0
            if age_h >= ENTRY_PENDING_MAX_HOURS:
                return True
        except Exception:
            continue
    return False


def _entry_touched_since(ltf, trade):
    """True if any bar since signal traded through entry (not only the last bar)."""
    try:
        entry = float(trade["entry"])
        direction = int(trade["direction"])
        anchor = trade.get("signal_sent") or trade.get("candle_time")
        ts = pd.Timestamp(anchor)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        dts = pd.to_datetime(ltf["datetime"])
        if getattr(dts.dt, "tz", None) is not None:
            dts = dts.dt.tz_convert("UTC").dt.tz_localize(None)
        window = ltf[dts.values >= np.datetime64(ts)]
        if window.empty:
            window = ltf.tail(50)
        highs = window["high"].astype(float)
        lows = window["low"].astype(float)
        if direction == 1:
            return bool((lows <= entry).any())
        return bool((highs >= entry).any())
    except Exception:
        return False


# Prop-firm SL buffer (Exness / NairaTrader style) — display only; risk R still uses structure SL
PROP_SPREAD_BUFFER_PIPS = {
    "default": 3.0,
    "XAU/USD": 30.0,
    "EUR/JPY": 4.0,
    "USD/JPY": 4.0,
    "GBP/JPY": 5.0,
}


def prop_spread_buffer_pips(pair):
    return float(PROP_SPREAD_BUFFER_PIPS.get(pair, PROP_SPREAD_BUFFER_PIPS["default"]))


def prop_stop_price(direction, stop, pair, pip):
    """Widen structure SL by buffer for live prop entry. BUY: lower SL; SELL: higher SL."""
    buf = prop_spread_buffer_pips(pair) * float(pip)
    d = int(direction)
    st = float(stop)
    if d == 1:
        return st - buf
    return st + buf



def stream_family(stream_label):
    """Group streams by HTF anchor so 1H→15m and 1H→5m compete."""
    s = (stream_label or "").upper().replace(" ", "")
    if "MN→" in s or "1MONTH" in s or s.startswith("CRT MN"):
        return "MN"
    if "W→" in s or "1WEEK" in s or "CRT W" in s:
        return "W"
    if "30MIN" in s or "30M" in s:
        return "30MIN"
    if "4H" in s and "D→" not in s:
        return "4H"
    if "1H" in s and "D→" not in s:
        return "1H"
    if "D→" in s or s.startswith("CRTD") or "1DAY" in s:
        return "D"
    return "OTHER"


def entries_same_zone(e1, e2, pip, tol_pips=ENTRY_DEDUP_PIPS):
    try:
        return abs(float(e1) - float(e2)) <= float(tol_pips) * float(pip)
    except Exception:
        return False



def _floor_c2(c2_time):
    """Normalize C2 timestamp to minute precision UTC string."""
    try:
        ct = pd.Timestamp(c2_time)
        if ct.tzinfo is not None:
            ct = ct.tz_convert("UTC").tz_localize(None)
        return ct.strftime("%Y-%m-%d %H:%M")
    except Exception:
        s = str(c2_time or "").replace("T", " ").replace("Z", "")
        return s[:16]


def make_setup_uid(pair, direction, c2_time, crh=None, crl=None):
    """
    Stable id for one C1/C2 setup across streams/models/runs.
    Same pair + direction + C2 minute + C1 range → one alert only.
    """
    c2 = _floor_c2(c2_time)
    try:
        crh_s = f"{float(crh):.5f}" if crh is not None else "?"
        crl_s = f"{float(crl):.5f}" if crl is not None else "?"
    except Exception:
        crh_s, crl_s = "?", "?"
    return f"{pair}|{int(direction)}|{c2}|{crh_s}|{crl_s}"


def pending_owns_setup(state, pair, direction, suid, crh, crl, entry, pip):
    """True if an open pending trade is the same setup (any stream)."""
    open_st = (None, "ACTIVE", "TP1_HIT", "ENTRY_PENDING", "LATE")
    for t in state.get("pending") or []:
        if t.get("pair") != pair:
            continue
        if t.get("status") not in open_st:
            continue
        if t.get("setup_uid") and t.get("setup_uid") == suid:
            return True
        if int(t.get("direction") or 0) != int(direction):
            continue
        # same C1 range
        try:
            if crh is not None and t.get("crh") is not None:
                if abs(float(t["crh"]) - float(crh)) <= 3 * float(pip) and abs(
                    float(t.get("crl") or 0) - float(crl or 0)
                ) <= 3 * float(pip):
                    return True
        except Exception:
            pass
        try:
            if entries_same_zone(entry, t.get("entry"), pip, tol_pips=8):
                return True
        except Exception:
            pass
    return False


def signal_blocked(state, sig, pip):
    """Block same-level multi-model spam and bias flips while a trade is open."""
    pair = sig.get("pair")
    direction = int(sig.get("direction") or 0)
    entry = float(sig.get("entry") or 0)
    fam = stream_family(sig.get("stream"))
    open_st = (None, "ACTIVE", "TP1_HIT", "ENTRY_PENDING", "LATE")
    for t in state.get("pending") or []:
        if t.get("pair") != pair:
            continue
        if t.get("status") not in open_st:
            continue
        t_entry = float(t.get("entry") or 0)
        t_dir = int(t.get("direction") or 0)
        t_stream = str(t.get("stream") or "")
        # identical / near-identical entry → one signal only
        if entries_same_zone(entry, t_entry, pip):
            return f"same entry zone as open {t_stream}"
        # opposite direction on same pair while something is live → no flip noise
        if t_dir and direction and t_dir != direction:
            return f"bias conflict with open {t_stream}"
        # same HTF family + same direction + close levels → one fire
        if stream_family(t_stream) == fam and t_dir == direction:
            if entries_same_zone(entry, t_entry, pip, tol_pips=12):
                return f"same family {fam} open {t_stream}"
    return None




def entry_is_fresh(candle_time, ltf_name, now=None):
    """Reject stale entries so Telegram is not hours late vs the chart."""
    now = now or utc_now()
    try:
        ct = pd.Timestamp(candle_time)
        if ct.tzinfo is None:
            ct = ct.tz_localize("UTC")
        else:
            ct = ct.tz_convert("UTC")
        now_ts = pd.Timestamp(now)
        if now_ts.tzinfo is None:
            now_ts = now_ts.tz_localize("UTC")
        else:
            now_ts = now_ts.tz_convert("UTC")
        age_min = (now_ts - ct).total_seconds() / 60.0
    except Exception:
        return True, 0.0
    # max age by entry TF (2–3 bars)
    limits = {
        "1min": 15,
        "5min": 45,
        "15min": 90,
        "30min": 150,
        "1h": 240,
        "4h": 600,
        "1day": 2000,
        "1week": 12000,
    }
    max_age = limits.get(str(ltf_name).lower(), 90)
    return age_min <= max_age, age_min

def drop_forming_bar(df):
    """API includes the live unfinished candle — never use it for entry."""
    if df is None or len(df) < 3:
        return df
    return df.iloc[:-1].copy()


def in_wat_send_window(hour, minute, width_min=REPORT_WINDOW_MIN):
    w = wat_now()
    target = w.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta = (w - target).total_seconds()
    return 0 <= delta < width_min * 60


# ============================================================
# CRT STRATEGY
# ============================================================

def find_setup(htf, pip):
    """Latest closed C2: sweep C1 + close back inside. C2 must be closed.
    Expects drop_forming_bar already applied (last row = last closed candle).
    """
    if htf is None or len(htf) < 5:
        return None
    c2 = htf.iloc[-1]
    c1 = htf.iloc[-2]
    crh = float(c1["high"])
    crl = float(c1["low"])
    mid = (crh + crl) / 2.0
    if crh - crl < 5 * pip:
        return None
    if float(c2["low"]) < crl and crl < float(c2["close"]) < crh:
        return {
            "crh": crh, "crl": crl, "mid": mid,
            "extreme": float(c2["low"]), "direction": 1,
            "c2_time": str(c2["datetime"]),
        }
    if float(c2["high"]) > crh and crl < float(c2["close"]) < crh:
        return {
            "crh": crh, "crl": crl, "mid": mid,
            "extreme": float(c2["high"]), "direction": -1,
            "c2_time": str(c2["datetime"]),
        }
    return None


def _ltf_after(ltf, setup, n=24):
    if ltf is None or len(ltf) < 8:
        return None
    c2_time = pd.Timestamp(setup["c2_time"])
    after = ltf[ltf["datetime"] > c2_time]
    if after.empty:
        return None
    return after.tail(n).reset_index(drop=True)


def ltf_entry_plain(ltf, setup):
    """First LTF close back inside C1 after valid C2 (first match, not last)."""
    w = _ltf_after(ltf, setup, 16)
    if w is None or w.empty:
        return None
    crh, crl = setup["crh"], setup["crl"]
    for _, row in w.iterrows():
        if crl < float(row["close"]) < crh:
            return row
    return None


def ltf_entry_ts(ltf, setup, pip):
    """Turtle Soup: wick beyond extreme then reject close back."""
    w = _ltf_after(ltf, setup, 20)
    if w is None or len(w) < 2:
        return None
    d, ext = setup["direction"], setup["extreme"]
    for i in range(1, len(w)):
        prev, row = w.iloc[i - 1], w.iloc[i]
        if d == 1 and float(prev["low"]) <= ext + pip and float(row["close"]) > float(row["open"]) and float(row["close"]) > setup["crl"]:
            return row
        if d == -1 and float(prev["high"]) >= ext - pip and float(row["close"]) < float(row["open"]) and float(row["close"]) < setup["crh"]:
            return row
    return None


def ltf_entry_model1(ltf, setup, pip):
    """Model #1: opposing candle engulfed (close beyond it)."""
    w = _ltf_after(ltf, setup, 24)
    if w is None or len(w) < 3:
        return None
    d = setup["direction"]
    for i in range(1, len(w) - 1):
        prev, cur, nxt = w.iloc[i - 1], w.iloc[i], w.iloc[i + 1]
        if d == 1 and float(cur["close"]) < float(prev["low"]) and float(nxt["close"]) > float(cur["high"]):
            return nxt
        if d == -1 and float(cur["close"]) > float(prev["high"]) and float(nxt["close"]) < float(cur["low"]):
            return nxt
    return None


def ltf_entry_mss(ltf, setup, pip):
    """MSS: break recent LTF swing in trade direction after C2."""
    w = _ltf_after(ltf, setup, 30)
    if w is None or len(w) < 8:
        return None
    d = setup["direction"]
    for i in range(5, len(w)):
        look, row = w.iloc[i - 5 : i], w.iloc[i]
        c = float(row["close"])
        if d == 1 and c > float(look["high"].max()) and c > setup["crl"]:
            return row
        if d == -1 and c < float(look["low"].min()) and c < setup["crh"]:
            return row
    return None


def ltf_entry_cisd(ltf, setup, pip):
    """CISD: break open of last opposing leg candle."""
    w = _ltf_after(ltf, setup, 24)
    if w is None or len(w) < 6:
        return None
    d = setup["direction"]
    for i in range(2, len(w)):
        leg = w.iloc[max(0, i - 4) : i]
        row = w.iloc[i]
        if d == 1:
            bears = leg[leg["close"] < leg["open"]]
            if bears.empty:
                continue
            level = float(bears.iloc[-1]["open"])
            if float(row["close"]) > level and float(row["close"]) > setup["crl"]:
                return row
        else:
            bulls = leg[leg["close"] > leg["open"]]
            if bulls.empty:
                continue
            level = float(bulls.iloc[-1]["open"])
            if float(row["close"]) < level and float(row["close"]) < setup["crh"]:
                return row
    return None


def ltf_entry_fvg(ltf, setup, pip):
    """FVG: displacement gap then first revisit."""
    w = _ltf_after(ltf, setup, 30)
    if w is None or len(w) < 6:
        return None
    d = setup["direction"]
    for i in range(2, len(w) - 1):
        a, c = w.iloc[i - 2], w.iloc[i]
        if d == 1 and float(a["high"]) < float(c["low"]):
            gap_lo, gap_hi = float(a["high"]), float(c["low"])
            for j in range(i + 1, min(i + 12, len(w))):
                row = w.iloc[j]
                if float(row["low"]) <= gap_hi and float(row["high"]) >= gap_lo:
                    if float(row["close"]) > setup["crl"]:
                        return row
        if d == -1 and float(a["low"]) > float(c["high"]):
            gap_lo, gap_hi = float(c["high"]), float(a["low"])
            for j in range(i + 1, min(i + 12, len(w))):
                row = w.iloc[j]
                if float(row["low"]) <= gap_hi and float(row["high"]) >= gap_lo:
                    if float(row["close"]) < setup["crh"]:
                        return row
    return None


ENTRY_FN = {
    "PLAIN": lambda ltf, setup, pip: ltf_entry_plain(ltf, setup),
    "TS": ltf_entry_ts,
    "MODEL1": ltf_entry_model1,
    "MSS": ltf_entry_mss,
    "CISD": ltf_entry_cisd,
    "FVG": ltf_entry_fvg,
}


def analyze(stream_label, htf_name, ltf_name, entry_model, htf, ltf, pair, pip):
    setup = find_setup(htf, pip)
    if setup is None:
        return None
    fn = ENTRY_FN.get(entry_model, ENTRY_FN["PLAIN"])
    bar = fn(ltf, setup, pip)
    if bar is None:
        return None

    atr = float(bar["atr"]) if "atr" in bar and not pd.isna(bar.get("atr", np.nan)) else 0
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
        "tp1_pips": float(abs(tp1 - entry) / pip),
        "tp2_pips": float(abs(tp2 - entry) / pip),
        "prop_stop": float(prop_stop_price(direction, stop, pair, pip)),
        "prop_risk_pips": float(abs(entry - prop_stop_price(direction, stop, pair, pip)) / pip),
        "prop_buffer_pips": float(prop_spread_buffer_pips(pair)),
        "crh": setup["crh"],
        "crl": setup["crl"],
        "mid": setup["mid"],
        "candle_time": candle_time,
        "c2_time": str(setup.get("c2_time") or candle_time),
        "session": forex_session(candle_time),
        "candle_key": (
            f"{stream_label}|{pair}|"
            f"{str(setup.get('c2_time') or candle_time)[:19].replace('T', ' ')}|"
            f"{direction}|{entry_model}"
        ),
        "setup_uid": make_setup_uid(
            pair, direction, setup.get("c2_time") or candle_time,
            setup.get("crh"), setup.get("crl"),
        ),
        "c1_tf": htf_name,
        "ltf": ltf_name,
        "entry_model": entry_model,
    }



def render_setup_chart(ltf, sig, out_path="/tmp/crt_setup_chart.png"):
    """Actual LTF candlesticks + C1 levels. Best-effort; never fails the signal."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except Exception as e:
        print("chart skip (matplotlib):", e)
        return None
    try:
        df = ltf.tail(48).reset_index(drop=True)
        if df is None or len(df) < 5:
            return None
        fig, ax = plt.subplots(figsize=(10, 4.5), dpi=120)
        width = 0.6
        for i, row in df.iterrows():
            o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
            color = "#26a69a" if c >= o else "#ef5350"
            ax.plot([i, i], [l, h], color=color, linewidth=1)
            bottom = min(o, c)
            height = abs(c - o) or (h - l) * 0.02 or 1e-6
            ax.add_patch(Rectangle((i - width / 2, bottom), width, height, facecolor=color, edgecolor=color))
        # levels
        for key, col, style in (
            ("crh", "#42a5f5", "--"),
            ("mid", "#ab47bc", ":"),
            ("crl", "#42a5f5", "--"),
            ("entry", "#ffee58", "-"),
            ("stop", "#ef5350", "-"),
            ("tp1", "#66bb6a", "-"),
            ("tp2", "#26a69a", "-"),
        ):
            if sig.get(key) is not None:
                ax.axhline(float(sig[key]), color=col, linestyle=style, linewidth=1.0, alpha=0.9, label=key.upper())
        ax.set_title(f"{sig.get('stream')} · {sig.get('side')} {sig.get('pair')} · LTF {sig.get('ltf')}", fontsize=10)
        ax.set_xlim(-1, len(df))
        ax.legend(loc="upper left", fontsize=7, ncol=4)
        ax.grid(True, alpha=0.25)
        ax.set_xlabel("bars (oldest → newest)")
        fig.tight_layout()
        fig.savefig(out_path, facecolor="#1e1e1e", edgecolor="none")
        plt.close(fig)
        # dark-ish axes
        return out_path
    except Exception as e:
        print("chart render error:", e)
        return None


# ============================================================
# MESSAGES
# ============================================================

def _core_levels(trade):
    """Safe for old pending rows that may lack crh/crl/mid."""
    entry = float(trade.get("entry") or 0)
    stop = float(trade.get("stop") or 0)
    tp1 = float(trade.get("tp1") or 0)
    tp2 = float(trade.get("tp2") or 0)
    crh = trade.get("crh")
    crl = trade.get("crl")
    mid = trade.get("mid")
    if crh is None or crl is None or mid is None:
        # recover from levels we always store
        direction = int(trade.get("direction") or (1 if entry <= tp1 else -1))
        if direction == 1:
            crl = crl if crl is not None else entry
            mid = mid if mid is not None else tp1
            crh = crh if crh is not None else tp2
        else:
            crh = crh if crh is not None else entry
            mid = mid if mid is not None else tp1
            crl = crl if crl is not None else tp2
    return (
        f"C1 High <b>{float(crh):.5f}</b>\n"
        f"C1 Mid  <b>{float(mid):.5f}</b>\n"
        f"C1 Low  <b>{float(crl):.5f}</b>\n\n"
        f"Entry <b>{entry:.5f}</b>\n"
        f"SL (structure)     <b>{stop:.5f}</b>"
        f" ({float(trade.get('risk_pips', 0)):.1f} pips)\n"
        f"SL (Profirm Spread) <b>{float(trade.get('prop_stop', stop)):.5f}</b>"
        f" ({float(trade.get('prop_risk_pips', trade.get('risk_pips', 0))):.1f} pips"
        f" · buf {float(trade.get('prop_buffer_pips', 0)):.0f})\n"
        f"TP1   <b>{tp1:.5f}</b>"
        f" ({float(trade.get('tp1_pips', 0)):.1f} pips · {float(trade.get('rr1', 0)):.2f}R)\n"
        f"TP2   <b>{tp2:.5f}</b>"
        f" ({float(trade.get('tp2_pips', 0)):.1f} pips · {float(trade.get('rr2', 0)):.2f}R)\n"
    )



def build_signal_message(sig, weekend_limit=False, already_ignited=False, age_min=0.0):
    emoji = "🟢" if sig["direction"] == 1 else "🔴"
    model = sig.get("entry_model", "PLAIN")
    if weekend_limit:
        limit_side = "BUY LIMIT" if sig["direction"] == 1 else "SELL LIMIT"
        status = (
            f"📌 Status: <b>ENTRY PENDING</b>\n"
            f"Order: <b>{limit_side}</b> @ {float(sig['entry']):.5f}\n"
            f"Weekend — activates when price trades through entry\n"
        )
    elif already_ignited:
        status = (
            f"⚠️ Status: <b>ALREADY IGNITED</b>\n"
            f"Entry candle is ~{float(age_min):.0f} min old — move may already be underway.\n"
            f"<b>Enter only if</b> price/structure still makes sense on your chart.\n"
            f"💡 Suggestion only: BE after TP1 (not auto)\n"
        )
    else:
        status = (
            f"📌 Status: <b>ACTIVE</b>\n"
            f"💡 Suggestion only: BE after TP1 (not auto)\n"
        )
    try:
        news = desk.news_label_for_signal(sig.get("pair"), sig.get("candle_time") or sig.get("c2_time"))
    except Exception:
        news = ""
    news_line = f"News: <b>{news}</b>\n" if news else ""
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"════════════════════\n"
        f"<b>{sig['stream']}</b>\n"
        f"────────────────────\n\n"
        f"{emoji} <b>{sig['side']} {sig['pair']}</b>\n\n"
        f"C1 TF: <b>{sig['c1_tf'].upper()}</b> · Entry TF: <b>{sig['ltf']}</b>\n"
        f"Entry model: <b>{model}</b>\n"
        f"Session: <b>{sig.get('session', '—')}</b>\n"
        f"Sweep: same TF as C1, close back inside\n"
        f"{news_line}"
        f"\n"
        f"{_core_levels(sig)}\n"
        f"{format_dual_times(sig.get('c2_time'), sig.get('candle_time'))}\n\n"
        f"{status}\n"
        f"<a href=\"{desk.tradingview_url(sig['pair'])}\">Open {sig['pair']} on TradingView</a>\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def tp1_update_message(trade):
    emoji = "🟢" if int(trade.get("direction", 1)) == 1 else "🔴"
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"════════════════════\n"
        f"<b>{trade['stream']}</b>\n"
        f"────────────────────\n\n"
        f"🎯 <b>TP1 HIT — HEADING TO TP2</b>\n\n"
        f"{emoji} <b>{trade['side']} {trade['pair']}</b>\n\n"
        f"Session: <b>{trade.get('session', '—')}</b>\n\n"
        f"{_core_levels(trade)}\n"
        f"{format_dual_times(trade.get('c2_time'), trade.get('candle_time'))}\n\n"
        f"📌 Status: <b>TP1 HIT — ACTIVE</b>\n"
        f"Still heading TP2 <b>{float(trade['tp2']):.5f}</b>\n"
        f"💡 No automatic BE\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def tp2_final_message(trade, exit_time):
    emoji = "🟢" if int(trade.get("direction", 1)) == 1 else "🔴"
    rr2 = float(trade.get("rr2", 0))
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"════════════════════\n"
        f"<b>{trade['stream']}</b>\n"
        f"────────────────────\n\n"
        f"🏆 <b>TP2 HIT — WIN</b>\n\n"
        f"{emoji} <b>{trade['side']} {trade['pair']}</b>\n\n"
        f"Session: <b>{trade.get('session', '—')}</b>\n"
        f"Exit: <b>{format_signal_time(exit_time)}</b>\n"
        f"Result: <b>+{rr2:.2f}R</b>\n\n"
        f"{_core_levels(trade)}\n"
        f"{format_dual_times(trade.get('c2_time'), trade.get('candle_time'))}\n\n"
        f"📌 Status: <b>CLOSED — WIN</b>\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def sl_final_message(trade, exit_time):
    emoji = "🟢" if int(trade.get("direction", 1)) == 1 else "🔴"
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"════════════════════\n"
        f"<b>{trade['stream']}</b>\n"
        f"────────────────────\n\n"
        f"❌ <b>SL HIT — LOSS</b>\n\n"
        f"{emoji} <b>{trade['side']} {trade['pair']}</b>\n\n"
        f"Session: <b>{trade.get('session', '—')}</b>\n"
        f"Exit: <b>{format_signal_time(exit_time)}</b>\n"
        f"Result: <b>-1.00R</b>\n\n"
        f"{_core_levels(trade)}\n"
        f"{format_dual_times(trade.get('c2_time'), trade.get('candle_time'))}\n\n"
        f"📌 Status: <b>CLOSED — LOSS</b>\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def _edit_all_messages(trade, text):
    """Edit every chat message_id stored for this trade. Returns True if any edit ok."""
    ids = trade.get("message_ids") or {}
    if not ids and trade.get("message_id"):
        chat = trade.get("edit_chat_id") or (str(desk.CHAT_ID) if desk.CHAT_ID else None)
        if chat:
            ids = {str(chat): trade["message_id"]}
    any_ok = False
    for chat, mid in ids.items():
        ok = desk.edit_telegram(mid, text, chat)
        if ok:
            any_ok = True
        else:
            print(f"  edit fail chat={chat} mid={mid} stream={trade.get('stream')}")
    return any_ok


def finalize_trade(trade, status, result_r, exit_time, edit_text):
    if edit_text:
        ok = _edit_all_messages(trade, edit_text)
        if not ok:
            print(f"  all edits failed {trade.get('stream')} {trade.get('pair')} — broadcast fallback")
            desk.broadcast(edit_text)
    if status in ("WIN", "LOSS"):
        desk.broadcast(desk.outcome_notice(trade, status, result_r, exit_time))
        desk.log_outcome(
            {
                "engine": OUTCOMES_ENGINE,
                "stream": trade.get("stream"),
                "pair": trade.get("pair"),
                "side": trade.get("side"),
                "direction": trade.get("direction"),
                "status": status,
                "result_r": float(result_r) if result_r is not None else None,
                "exit_time": exit_time,
                "entry": trade.get("entry"),
                "stop": trade.get("stop"),
                "tp1": trade.get("tp1"),
                "tp2": trade.get("tp2"),
                "rr1": trade.get("rr1"),
                "rr2": trade.get("rr2"),
                "candle_time": trade.get("candle_time"),
                "signal_sent": trade.get("signal_sent"),
                "session": trade.get("session"),
                "tp1_hit": bool(trade.get("tp1_hit")),
                "c1_tf": trade.get("c1_tf"),
                "ltf": trade.get("ltf"),
                "entry_model": trade.get("entry_model"),
            }
        )


# ============================================================
# PENDING
# ============================================================


def purge_expired_pending(state):
    """Drop stale ENTRY_PENDING so they stop blocking new signals."""
    pend = state.get("pending") or []
    kept = []
    for t in pend:
        if _pending_expired(t):
            print(f"  purge expired ENTRY_PENDING {t.get('stream')} {t.get('pair')}")
            continue
        kept.append(t)
    state["pending"] = kept

def check_pending(state, pair, ltf, ltf_name=None):
    """ACTIVE / TP1_HIT. Same-bar SL+TP2 → SL first. TP1 edit kept."""
    if not state["pending"] or ltf is None:
        return
    remaining = []
    latest = len(ltf) - 1

    for trade in state["pending"]:
        if trade.get("pair") != pair:
            remaining.append(trade)
            continue
        # Only manage this trade on its own entry TF dataframe
        if ltf_name and trade.get("ltf") and str(trade.get("ltf")) != str(ltf_name):
            remaining.append(trade)
            continue
        status = trade.get("status")
        # Weekend LIMIT: activate when price traded through entry; expire stale limits
        if status == "ENTRY_PENDING":
            if _pending_expired(trade):
                print(f"  ENTRY_PENDING expired {trade.get('stream')} {pair} — dropped")
                continue  # do not keep in remaining → unblocks pair
            if is_weekend_utc():
                remaining.append(trade)
                continue
            filled = _entry_touched_since(ltf, trade)
            if filled:
                trade["status"] = "ACTIVE"
                status = "ACTIVE"
                text = build_signal_message(
                    {**trade, "entry_model": trade.get("entry_model", "PLAIN")}, False
                )
                _edit_all_messages(trade, text)
                print(f"  LIMIT filled -> ACTIVE {trade.get('stream')} {pair}")
            else:
                remaining.append(trade)
                continue
        # LATE = already-ignited label only — still manage TP1/SL as ACTIVE
        if status == "LATE":
            trade["status"] = "ACTIVE"
            status = "ACTIVE"
        if status not in (None, "ACTIVE", "TP1_HIT"):
            continue
        try:
            entry_time = pd.Timestamp(trade["candle_time"])
            if entry_time.tzinfo is not None:
                entry_time = entry_time.tz_convert("UTC").tz_localize(None)
            dts = pd.to_datetime(ltf["datetime"])
            if getattr(dts.dt, "tz", None) is not None:
                dts = dts.dt.tz_convert("UTC").dt.tz_localize(None)
            matches = np.where(dts.values >= np.datetime64(entry_time))[0]
        except Exception as e:
            print(f"  pending match error {pair}: {e}")
            remaining.append(trade)
            continue
        if len(matches) == 0:
            remaining.append(trade)
            continue

        signal_index = int(matches[0])
        # Time-based cursor (rolling API windows break integer indices)
        last_checked_time = trade.get("last_checked_time")
        if not last_checked_time:
            last_checked_time = trade.get("candle_time")
        try:
            lct = pd.Timestamp(last_checked_time)
            if lct.tzinfo is not None:
                lct = lct.tz_convert("UTC").tz_localize(None)
            lct64 = np.datetime64(lct)
        except Exception:
            lct64 = None

        direction = int(trade["direction"])
        stop = float(trade["stop"])
        tp1 = float(trade["tp1"])
        tp2 = float(trade["tp2"])
        tp1_hit = bool(trade.get("tp1_hit", False))
        closed = False

        for j in range(signal_index + 1, latest + 1):
            bar_dt = dts.iloc[j] if hasattr(dts, "iloc") else dts[j]
            try:
                bt = pd.Timestamp(bar_dt)
                if bt.tzinfo is not None:
                    bt = bt.tz_convert("UTC").tz_localize(None)
                if lct64 is not None and np.datetime64(bt) <= lct64:
                    continue
            except Exception:
                bt = None
            candle_time = str(ltf["datetime"].iloc[j])
            # Option A: advance cursor on weekend bars but never resolve TP/SL/verdict from them
            if bt is not None and not is_weekday_bar(bt):
                trade["last_checked_time"] = candle_time
                trade["last_checked_index"] = j
                continue
            high = float(ltf["high"].iloc[j])
            low = float(ltf["low"].iloc[j])
            trade["last_checked_time"] = candle_time
            trade["last_checked_index"] = j
            if direction == 1:
                hit_sl, hit_tp2, hit_tp1 = low <= stop, high >= tp2, high >= tp1
            else:
                hit_sl, hit_tp2, hit_tp1 = high >= stop, low <= tp2, low <= tp1

            if hit_sl:
                trade["status"] = "CLOSED_LOSS"
                finalize_trade(trade, "LOSS", -1.0, candle_time, sl_final_message(trade, candle_time))
                closed = True
                break
            if hit_tp2:
                trade["status"] = "CLOSED_WIN"
                finalize_trade(
                    trade, "WIN", float(trade.get("rr2", 0)), candle_time,
                    tp2_final_message(trade, candle_time),
                )
                closed = True
                break
            if hit_tp1 and not tp1_hit:
                trade["tp1_hit"] = True
                tp1_hit = True
                trade["status"] = "TP1_HIT"
                try:
                    text = tp1_update_message(trade)
                    ok = _edit_all_messages(trade, text)
                    if not ok:
                        try:
                            desk.broadcast(text)
                        except NameError:
                            broadcast(text)
                        print(f"  TP1 edit failed — broadcast fallback {trade.get('stream')} {pair}")
                    else:
                        print(f"  TP1 HIT edited {trade.get('stream')} {pair}")
                except Exception as e:
                    print(f"  TP1 update error {trade.get('stream')} {pair}: {e}")

        if not closed:
            remaining.append(trade)

    state["pending"] = remaining


# ============================================================
# REPORTS
# ============================================================

def _crt_outcomes_between(t0, t1):
    rows = desk.load_outcomes()
    out = []
    for r in rows:
        stream = str(r.get("stream") or "")
        if r.get("engine") != OUTCOMES_ENGINE and not stream.startswith("CRT "):
            continue
        try:
            et = _as_utc(r.get("exit_time") or r.get("candle_time"))
            et_dt = et.to_pydatetime()
            if et_dt.tzinfo is None:
                et_dt = et_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if t0 <= et_dt <= t1:
            out.append(r)
    return out


def _metrics_block(trades):
    if not trades:
        return "No completed CRT Plain trades in this period."
    wins = [t for t in trades if str(t.get("status", "")).upper() == "WIN"]
    losses = [t for t in trades if str(t.get("status", "")).upper() == "LOSS"]
    n = len(trades)
    nw, nl = len(wins), len(losses)
    wr = 100.0 * nw / n if n else 0.0
    rs = [float(t["result_r"]) for t in trades if t.get("result_r") is not None]
    net = sum(rs) if rs else 0.0
    exp = net / n if n else 0.0
    by_session = {}
    for t in trades:
        s = t.get("session") or "Unknown"
        by_session.setdefault(s, []).append(float(t.get("result_r") or 0))
    sess = "\n".join(
        f"• {s}: n={len(xs)} net={sum(xs):+.2f}R" for s, xs in sorted(by_session.items())
    ) or "—"
    by_stream = {}
    for t in trades:
        s = t.get("stream") or "?"
        by_stream.setdefault(s, []).append(float(t.get("result_r") or 0))
    streams = "\n".join(
        f"• {s}: n={len(xs)} net={sum(xs):+.2f}R" for s, xs in sorted(by_stream.items())
    ) or "—"
    return (
        f"Trades: <b>{n}</b> | Wins: <b>{nw}</b> | Losses: <b>{nl}</b>\n"
        f"WR: <b>{wr:.1f}%</b> | Net: <b>{net:+.2f}R</b> | Exp: <b>{exp:+.2f}R</b>\n\n"
        f"<b>By session</b>\n{sess}\n\n"
        f"<b>By stream</b>\n{streams}"
    )


def build_report(title, period_label, t0, t1, trades):
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"════════════════════\n"
        f"<b>CRT PLAIN — {title}</b>\n"
        f"Period: <b>{period_label}</b>\n"
        f"{t0.strftime('%d %b %Y %H:%M')} → {t1.strftime('%d %b %Y %H:%M')} UTC\n"
        f"────────────────────\n\n"
        f"{_metrics_block(trades)}\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def weekly_bounds(now=None):
    now = now or utc_now()
    d = now.date()
    monday = d - timedelta(days=d.weekday())
    week_start = datetime(monday.year, monday.month, monday.day, 0, 0, tzinfo=timezone.utc)
    period_monday = week_start if now.weekday() >= 5 else week_start - timedelta(days=7)
    period_friday_end = period_monday + timedelta(days=4, hours=21)
    iso = period_monday.isocalendar()
    pid = f"{iso[0]}-W{iso[1]:02d}"
    return pid, period_monday, period_friday_end


def monthly_bounds(now=None):
    now = now or utc_now()
    first_this = datetime(now.year, now.month, 1, 0, 0, tzinfo=timezone.utc)
    last_prev = first_this - timedelta(seconds=1)
    first_prev = datetime(last_prev.year, last_prev.month, 1, 0, 0, tzinfo=timezone.utc)
    pid = f"{last_prev.year}-{last_prev.month:02d}"
    return pid, first_prev, last_prev


def yearly_bounds(state, now=None):
    now = now or utc_now()
    start_s = state.get("strategy_start")
    if not start_s:
        start_s = now.date().isoformat()
        state["strategy_start"] = start_s
    try:
        y, m, d = map(int, start_s.split("-")[:3])
        t0 = datetime(y, m, d, 0, 0, tzinfo=timezone.utc)
    except Exception:
        t0 = datetime(now.year, 1, 1, 0, 0, tzinfo=timezone.utc)
    year = now.year - 1 if (now.month == 1 and now.day == 1) else now.year
    if t0.year == now.year:
        year = now.year
        t1 = datetime(year, 12, 31, 23, 59, tzinfo=timezone.utc)
        return f"Y{year}", t0, t1
    t0 = max(t0, datetime(year, 1, 1, 0, 0, tzinfo=timezone.utc))
    t1 = datetime(year, 12, 31, 23, 59, tzinfo=timezone.utc)
    return f"Y{year}", t0, t1


def maybe_send_reports(state):
    w = wat_now()
    if w.weekday() == 5 and in_wat_send_window(12, 0):
        pid, t0, t1 = weekly_bounds()
        if state.get("weekly_report_id") != pid:
            trades = _crt_outcomes_between(t0, t1)
            desk.broadcast(build_report("WEEKLY PERFORMANCE", pid, t0, t1, trades))
            state["weekly_report_id"] = pid
            print(f"Weekly report SENT {pid} n={len(trades)}")
    if w.day == 1 and in_wat_send_window(6, 0):
        pid, t0, t1 = monthly_bounds()
        if state.get("monthly_report_id") != pid:
            trades = _crt_outcomes_between(t0, t1)
            desk.broadcast(build_report("MONTHLY PERFORMANCE", pid, t0, t1, trades))
            state["monthly_report_id"] = pid
            print(f"Monthly report SENT {pid} n={len(trades)}")
    if w.month == 1 and w.day == 1 and in_wat_send_window(0, 0):
        pid, t0, t1 = yearly_bounds(state)
        if state.get("yearly_report_id") != pid:
            trades = _crt_outcomes_between(t0, t1)
            desk.broadcast(build_report("YEARLY PERFORMANCE", pid, t0, t1, trades))
            state["yearly_report_id"] = pid
            print(f"Yearly report SENT {pid} n={len(trades)}")


def _pair_allowed(stream_label, pair):
    allowed = STREAM_PAIRS.get(stream_label)
    if allowed is None:
        return pair in CORE_PAIRS or pair in PAIRS
    return pair in allowed


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("TENSION TRADING DESK — CRT PLAIN")
    print("First-factorial streams | dual times | TP1/WIN/LOSS edit")
    print("Weekend: no new signals | open trades still managed")
    print(f"Streams loaded: {len(STREAMS)}")
    print("=" * 70)

    if not (getattr(desk, "TWELVE_DATA_KEYS", None) or desk.TWELVE_DATA_KEY):
        print("Missing TWELVE_DATA_KEY / TWELVE_DATA_KEYS")
        return

    state = load_state()
    if not state.get("strategy_start"):
        state["strategy_start"] = utc_now().date().isoformat()
        print("strategy_start set:", state["strategy_start"])

    purge_expired_pending(state)
    maybe_send_reports(state)

    weekend = is_weekend_utc()
    if weekend:
        print("Weekend UTC — open trades managed only; NO new signals")

    cache = {}
    scan_pairs = [p for p in CORE_PAIRS if p in PAIRS]
    if not scan_pairs:
        scan_pairs = list(PAIRS.keys())
    # CRITICAL: always manage open trades even if pair was removed from slim CORE
    pending_pairs = []
    for t in state.get("pending") or []:
        p = t.get("pair")
        if p and p not in scan_pairs and p not in pending_pairs:
            pending_pairs.append(p)
    if pending_pairs:
        print("Managing pending-only pairs (SL/TP edit):", ", ".join(pending_pairs))

    def _fetch_tf(pair, tf):
        key = (pair, tf)
        if key not in cache:
            # smaller history for slow HTF
            size = 200 if tf in ("1day", "1week", "1month") else (500 if tf == "5min" else 400)
            cache[key] = desk.fetch(pair, tf, size)
        fr = drop_forming_bar(cache[key]) if cache[key] is not None else None
        if fr is not None:
            fr = desk.add_atr(fr)
        return fr

    # --- Pass A: resolve SL/TP for ANY open pending (including pairs left out of slim) ---
    manage_list = list(scan_pairs) + pending_pairs
    for pair in manage_list:
        if pair not in PAIRS:
            continue
        print(f"\n--- {pair} {'(manage-only)' if pair in pending_pairs else ''} ---")
        # Collect LTFs needed for this pair's open trades + streams
        needed_ltf = set()
        for t in state.get("pending") or []:
            if t.get("pair") == pair and t.get("ltf"):
                needed_ltf.add(str(t["ltf"]))
        if pair in scan_pairs:
            for _h, ltf_name, _m in STREAMS.values():
                needed_ltf.add(ltf_name)
        if not needed_ltf:
            needed_ltf.add("15min")
        frames_ltf = {}
        for tf in needed_ltf:
            frames_ltf[tf] = _fetch_tf(pair, tf)
        for tf, fr in frames_ltf.items():
            if fr is not None:
                check_pending(state, pair, fr, tf)

    # --- Pass B: new signals only on CORE / slim scan pairs ---
    for pair in scan_pairs:
        if pair not in PAIRS:
            continue
        cfg = PAIRS[pair]
        pip = cfg["pip"]
        print(f"\n--- scan {pair} ---")

        needed = set()
        for stream_label, (htf_name, ltf_name, _) in STREAMS.items():
            if _pair_allowed(stream_label, pair):
                needed.add(htf_name)
                needed.add(ltf_name)

        frames = {}
        ok = True
        for tf in needed:
            frames[tf] = _fetch_tf(pair, tf)
            if frames[tf] is None:
                ok = False
        if not ok:
            print("  data failed")
            continue

        # Weekend: manage pending only — do not invent new Telegram signals
        if weekend:
            continue

        for stream_label, (htf_name, ltf_name, entry_model) in STREAMS.items():
            if not _pair_allowed(stream_label, pair):
                continue
            ltf = frames[ltf_name]

            sig = analyze(
                stream_label, htf_name, ltf_name, entry_model,
                frames[htf_name], ltf, pair, pip,
            )
            if sig is None:
                print(f"  {stream_label}: no setup")
                continue

            key = sig["candle_key"]
            sess = sig.get("session")
            if BLOCK_ASIA_SESSION and sess == "Asia":
                print(f"  {stream_label}: Asia session blocked")
                continue
            if BLOCK_OFF_HOURS and sess == "Off-hours":
                print(f"  {stream_label}: Off-hours blocked")
                continue
            suid = sig.get("setup_uid") or make_setup_uid(
                pair,
                sig.get("direction"),
                sig.get("c2_time") or sig.get("candle_time"),
                sig.get("crh"),
                sig.get("crl"),
            )
            sig["setup_uid"] = suid

            # HARD lock: same C1/C2 already claimed this process or prior run
            if state["last_alert_keys"].get(f"setup:{suid}"):
                print(f"  {stream_label}: already alerted (setup_uid)")
                continue
            if state["last_alert_keys"].get(f"{stream_label}:{pair}") == key:
                print(f"  {stream_label}: already alerted")
                continue
            if pending_owns_setup(
                state, pair, sig.get("direction"), suid,
                sig.get("crh"), sig.get("crl"), sig.get("entry"), pip,
            ):
                print(f"  {stream_label}: open pending same setup — skip")
                state["last_alert_keys"][f"setup:{suid}"] = key
                state["last_alert_keys"][f"{stream_label}:{pair}"] = key
                save_state(state)  # persist skip so next cron does not re-fire
                continue

            block_reason = signal_blocked(state, sig, pip)
            if block_reason:
                print(f"  {stream_label}: blocked — {block_reason}")
                state["last_alert_keys"][f"setup:{suid}"] = key
                state["last_alert_keys"][f"{stream_label}:{pair}"] = key
                save_state(state)
                continue

            # hard reject future-dated candles (feed/TZ bug)
            try:
                _ct = pd.Timestamp(sig["candle_time"])
                if _ct.tzinfo is not None:
                    _ct = _ct.tz_convert("UTC").tz_localize(None)
                if _ct > pd.Timestamp.utcnow().tz_localize(None) + pd.Timedelta(minutes=3):
                    print(f"  {stream_label}: FUTURE candle skipped {_ct} > now")
                    continue
            except Exception as e:
                print(f"  {stream_label}: candle time check error {e}")

            ok_fresh, age_min = entry_is_fresh(sig["candle_time"], ltf_name)
            already_ignited = not ok_fresh

            # CLAIM + PERSIST before any Telegram send.
            # Previous bug: claim only lived in memory until end of scan →
            # next cron (or overlap) reloaded empty keys and re-fired same setup.
            state["last_alert_keys"][f"setup:{suid}"] = key
            state["last_alert_keys"][f"{stream_label}:{pair}"] = key
            save_state(state)

            # Stale: already claimed on disk — NEVER rebroadcast
            if already_ignited:
                print(
                    f"  {stream_label}: skip ignited age={age_min:.0f}m "
                    f"(claimed on disk, no Telegram)"
                )
                continue

            signal_sent = utc_now().isoformat()
            sig["signal_sent"] = signal_sent
            # Never use LATE as status — it was dropped from pending and TP1 never updated
            status = "ENTRY_PENDING" if weekend else "ACTIVE"
            msg_ids = desk.broadcast_message_ids(
                build_signal_message(
                    sig,
                    weekend_limit=weekend,
                    already_ignited=False,
                    age_min=age_min,
                )
            )
            try:
                chart = render_setup_chart(ltf, sig)
                if chart:
                    desk.broadcast_photo(
                        chart,
                        caption=f"{sig.get('stream')} · {sig.get('side')} {sig.get('pair')}",
                    )
            except Exception as e:
                print("chart send error:", e)
            primary = str(desk.CHAT_ID) if desk.CHAT_ID else (next(iter(msg_ids), None))
            message_id = msg_ids.get(primary) if primary else None
            if not message_id and msg_ids:
                message_id = next(iter(msg_ids.values()))

            print(
                f"  {stream_label}: {status} {sig['side']} | "
                f"{sig['session']} | model={entry_model} | {sig['risk_pips']:.1f} pips | "
                f"msgs={len(msg_ids)}"
            )

            state["last_alert_keys"][f"setup:{suid}"] = key
            state["last_alert_keys"][f"{stream_label}:{pair}"] = key
            # Always track pending so TP1/SL edits work even if Telegram hiccups
            if True:
                try:
                    et = pd.Timestamp(sig["candle_time"])
                    if et.tzinfo is not None:
                        et = et.tz_convert("UTC").tz_localize(None)
                    dts = pd.to_datetime(ltf["datetime"])
                    if getattr(dts.dt, "tz", None) is not None:
                        dts = dts.dt.tz_convert("UTC").dt.tz_localize(None)
                    m = np.where(dts.values >= np.datetime64(et))[0]
                    entry_idx = int(m[0]) if len(m) else max(0, len(ltf) - 2)
                except Exception:
                    entry_idx = max(0, len(ltf) - 2)

                state["pending"].append(
                    {
                        "stream": stream_label,
                        "pair": pair,
                        "message_id": message_id,
                        "message_ids": msg_ids,
                        "edit_chat_id": primary,
                        "direction": sig["direction"],
                        "side": sig["side"],
                        "entry": sig["entry"],
                        "stop": sig["stop"],
                        "tp1": sig["tp1"],
                        "tp2": sig["tp2"],
                        "rr1": sig["rr1"],
                        "rr2": sig["rr2"],
                        "risk_pips": sig["risk_pips"],
                        "tp1_pips": sig.get("tp1_pips", 0),
                        "tp2_pips": sig.get("tp2_pips", 0),
                        "prop_stop": sig.get("prop_stop", sig["stop"]),
                        "prop_risk_pips": sig.get("prop_risk_pips", sig["risk_pips"]),
                        "prop_buffer_pips": sig.get("prop_buffer_pips", 0),
                        "crh": sig["crh"],
                        "crl": sig["crl"],
                        "mid": sig["mid"],
                        "candle_time": sig["candle_time"],
                        "c2_time": sig.get("c2_time") or sig["candle_time"],
                        "signal_sent": signal_sent,
                        "session": sig["session"],
                        "c1_tf": sig["c1_tf"],
                        "ltf": sig["ltf"],
                        "entry_model": sig["entry_model"],
                        "candle_key": key,
                        "setup_uid": suid,
                        "status": status,
                        "tp1_hit": False,
                        "last_checked_index": entry_idx,
                    }
                )
                # Persist pending immediately so TP1/SL + dedup survive mid-scan crash
                save_state(state)

    save_state(state)
    print("\nCRT PLAIN SCAN COMPLETE | pending:", len(state["pending"]))


if __name__ == "__main__":
    main()
