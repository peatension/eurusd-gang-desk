"""
SMC ENGINE — Primary (live)

CORE LOGIC UNCHANGED:
  H4 bias align
  H1 displacement BOS
  OB preferred, else FVG
  First touch only
  M15 entry @ zone 50%
  SL = zone extreme + 2 pips
  TP2 opposing H1/H4 swing liquidity | TP1 = 50% path to TP2 | min RR2 1.5
  ADV stream = OUT

PAIRS — CRT-style WF lock (Oct 2026):
  IN:  EUR/USD GBP/USD USD/JPY USD/CHF AUD/USD NZD/USD
       EUR/JPY XAU/USD USD/CAD
  OUT: AUD/JPY (lagging)
  SWEEP = OUT

Parallel models (labeled on every Telegram signal):
  PRIMARY — OB preferred, FVG fallback
  FVG     — FVG zone only (high-WR WF cells; thinner n)

CRT-style: dual WAT/UTC · session label
State: last_alert_state_smc.json + subscribers_smc.json
Lifecycle: SIGNAL → TP1 HIT → TP2 WIN / SL LOSS
"""

import os
import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta


def utc_now():
    return datetime.now(timezone.utc)


def format_signal_time(dt_like):
    """Naive feed times = UTC. CRT-style dual WAT + UTC display."""
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


def format_dual_times(bos_time, touch_time=None):
    setup = bos_time or touch_time
    signal = touch_time or bos_time
    return (
        f"🕯 BOS candle: <b>{format_signal_time(setup)}</b>\n"
        f"📡 M15 touch: <b>{format_signal_time(signal)}</b>"
    )


def forex_session(dt_like):
    try:
        h = int(pd.Timestamp(dt_like).hour)
    except Exception:
        return "—"
    if 0 <= h < 6:
        return "Asia"
    if 6 <= h < 12:
        return "London"
    if 12 <= h < 16:
        return "London/NY Overlap"
    if 16 <= h < 21:
        return "New York"
    return "Off-hours"


def _px(pair, x):
    try:
        x = float(x)
    except Exception:
        return str(x)
    if "JPY" in str(pair):
        return f"{x:.3f}"
    return f"{x:.5f}"


# ============================================================
# ENV
# ============================================================

# Secrets (SMC bot uses BOT_TOKEN + CHAT_ID; multi-key Twelve from last-week secret)
def _twelve_keys():
    raw = os.getenv("TWELVE_DATA_KEYS") or os.getenv("TWELVE_DATA_KEY") or ""
    keys = [k.strip() for k in raw.replace("\n", ",").split(",") if k.strip()]
    return keys

TWELVE_KEYS = _twelve_keys()
TWELVE_DATA_KEY = TWELVE_KEYS[0] if TWELVE_KEYS else None  # legacy alias
_td_i = 0

# Workflow maps secrets.BOT_TOKEN_SMC → BOT_TOKEN, secrets.CHAT_ID_SMC → CHAT_ID
BOT_TOKEN = (
    os.getenv("BOT_TOKEN_SMC")
    or os.getenv("BOT_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN_SMC")
    or os.getenv("TELEGRAM_BOT_TOKEN")
)
CHAT_ID = (
    os.getenv("CHAT_ID_SMC")
    or os.getenv("CHAT_ID")
    or os.getenv("TELEGRAM_CHAT_ID_SMC")
    or os.getenv("TELEGRAM_CHAT_ID")
)
SHEET_URL = os.getenv("SHEET_URL", "")
ADMIN_ID = os.getenv("ADMIN_ID", "")

STATE_FILE = "last_alert_state_smc.json"
SUBSCRIBERS_FILE = "subscribers_smc.json"
OUTCOMES_FILE = "outcomes_smc.json"
STATE_VERSION = 11  # multi-edit + outcomes + CRT-parity commands

PIP_BUF = 2.0
# Non-fixed TP: structure/liquidity targets (not fixed R multiples)
# TP2 = nearest opposing swing liquidity on H1 (fallback H4 extreme)
# TP1 = 50% path entry → TP2 (CRT-style partial)
MIN_RR_TP2 = 1.5
# Display fallback only for legacy pending rows
RR1 = 1.5
RR2 = 2.0   # skip setup if opposing liquidity is too close
MAX_HOLD_BARS = 180
M15_LOOKAHEAD = 96  # ~24h of M15 after H1 BOS

# Parallel models (CRT-style dual stream). Label on every signal.
# PRIMARY = OB preferred, FVG fallback | FVG = FVG zone only (WF high WR cells)
MODELS = ("PRIMARY", "FVG")

# PRIMARY 3/3 lock — AUD/JPY removed (lagging)
PAIRS = {
    "EUR/USD": {"pip": 0.0001, "tv": "OANDA:EURUSD"},
    "GBP/USD": {"pip": 0.0001, "tv": "OANDA:GBPUSD"},
    "USD/JPY": {"pip": 0.01,   "tv": "OANDA:USDJPY"},
    "USD/CHF": {"pip": 0.0001, "tv": "OANDA:USDCHF"},
    "AUD/USD": {"pip": 0.0001, "tv": "OANDA:AUDUSD"},
    "NZD/USD": {"pip": 0.0001, "tv": "OANDA:NZDUSD"},
    "USD/CAD": {"pip": 0.0001, "tv": "OANDA:USDCAD"},
    "EUR/JPY": {"pip": 0.01,   "tv": "OANDA:EURJPY"},
    "XAU/USD": {"pip": 0.01,   "tv": "TVC:GOLD"},
}

# Base TF label (model name appended at signal time)
PAIR_STREAM = {
    "EUR/USD": "SMC 1H→15m",
    "GBP/USD": "SMC 1H→15m",
    "USD/JPY": "SMC 1H→15m",
    "USD/CHF": "SMC 4H→1H→15m",
    "AUD/USD": "SMC 1H→15m",
    "NZD/USD": "SMC 1H→15m",
    "USD/CAD": "SMC 1H→15m",
    "EUR/JPY": "SMC 1H→15m",
    "XAU/USD": "SMC 1H→15m",
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
    """Send to all subs. Returns (primary_mid, {chat_id: message_id})."""
    subs = load_subscribers()
    if not subs:
        print("No subscribers.")
        return None, {}
    primary = str(CHAT_ID) if CHAT_ID else subs[0]
    ids = {}
    primary_mid = None
    for cid in subs:
        mid = send_telegram_to(cid, text)
        if mid:
            ids[str(cid)] = mid
            if str(cid) == primary:
                primary_mid = mid
        time.sleep(0.05)
    return primary_mid, ids


def edit_telegram(message_id, text, chat_id=None):
    """Edit one Telegram message. Returns True on success."""
    target = str(chat_id or CHAT_ID or "")
    if not BOT_TOKEN or not target or message_id is None:
        return False
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        print(f"Telegram edit skip: bad message_id={message_id!r}")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": target,
        "message_id": mid,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        data = requests.post(url, json=payload, timeout=20).json()
        if data.get("ok"):
            return True
        # Retry without parse_mode if HTML rejected
        desc = str(data.get("description", ""))
        if "parse" in desc.lower() or "entities" in desc.lower():
            payload.pop("parse_mode", None)
            data2 = requests.post(url, json=payload, timeout=20).json()
            if data2.get("ok"):
                return True
            print(f"Telegram edit error ({target}/{mid}):", data2)
            return False
        # "message is not modified" counts as success for lifecycle
        if "not modified" in desc.lower():
            return True
        print(f"Telegram edit error ({target}/{mid}):", data)
        return False
    except Exception as e:
        print("Telegram edit error:", e)
        return False


def edit_all_messages(trade, text):
    """Re-edit original signal on every chat (TP1 / WIN / LOSS). Always try fallbacks."""
    mids = trade.get("message_ids") or {}
    if isinstance(mids, list):
        # legacy shape — discard, use single id
        mids = {}
    if not mids and trade.get("message_id"):
        cid = str(trade.get("edit_chat_id") or CHAT_ID or "")
        if cid:
            mids = {cid: trade["message_id"]}
    # Normalize keys/ids
    norm = {}
    for cid, mid in (mids or {}).items():
        if cid is None or mid is None:
            continue
        norm[str(cid)] = mid
    ok_any = False
    for cid, mid in norm.items():
        if edit_telegram(mid, text, cid):
            ok_any = True
        time.sleep(0.04)
    if not ok_any:
        print(
            f"  edit failed pair={trade.get('pair')} model={trade.get('model')} "
            f"mids={norm} — will broadcast new message"
        )
    return ok_any


def _write_outcome_file(path, rec, keep=800):
    rows = []
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                rows = json.load(f) or []
        except Exception:
            rows = []
    rows.append(rec)
    try:
        with open(path, "w") as f:
            json.dump(rows[-keep:], f, indent=2)
        return True
    except Exception as e:
        print(f"outcomes save error ({path}):", e)
        return False


def append_outcome(trade, status, result_r, exit_time):
    """Persist closed trade for performance metrics (SMC file + shared outcomes.json)."""
    rec = {
        "engine": "SMC",
        "stream": trade.get("stream"),
        "model": trade.get("model"),
        "pair": trade.get("pair"),
        "side": trade.get("side"),
        "direction": trade.get("direction"),
        "status": status,
        "result_r": float(result_r),
        "entry": trade.get("entry"),
        "stop": trade.get("stop"),
        "tp1": trade.get("tp1"),
        "tp2": trade.get("tp2"),
        "rr1": trade.get("rr1"),
        "rr2": trade.get("rr2"),
        "candle_time": trade.get("candle_time"),
        "exit_time": str(exit_time),
        "closed_at": utc_now().isoformat(),
        "session": trade.get("session"),
        "tp1_hit": bool(trade.get("tp1_hit", False)),
    }
    ok1 = _write_outcome_file(OUTCOMES_FILE, rec)
    # Shared book so desk-wide metrics can include SMC without a second file
    ok2 = _write_outcome_file("outcomes.json", rec)
    print(f"  outcome saved status={status} R={float(result_r):+.2f} smc_file={ok1} shared={ok2}")
    send_to_sheet({"action": "OUTCOME", **rec})


def load_outcomes():
    if not os.path.exists(OUTCOMES_FILE):
        return []
    try:
        with open(OUTCOMES_FILE, "r") as f:
            return json.load(f) or []
    except Exception:
        return []


def metrics_block(rows, title):
    if not rows:
        return f"<b>{title}</b>\nNo closed trades yet."
    n = len(rows)
    wins = sum(1 for r in rows if float(r.get("result_r") or 0) > 0)
    losses = n - wins
    wr = 100.0 * wins / n if n else 0
    exp = sum(float(r.get("result_r") or 0) for r in rows) / n if n else 0
    net = sum(float(r.get("result_r") or 0) for r in rows)
    return (
        f"<b>{title}</b>\n"
        f"Trades: <b>{n}</b> · W/L: <b>{wins}/{losses}</b>\n"
        f"WR: <b>{wr:.1f}%</b> · Exp: <b>{exp:+.3f}R</b> · Net: <b>{net:+.2f}R</b>"
    )


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
        low = text.lower().split("@")[0].strip()

        if low.startswith("/start"):
            if chat_id not in subscribers:
                subscribers.append(chat_id)
                save_subscribers(subscribers)
            send_telegram_to(
                chat_id,
                "🟢 <b>Subscribed — SMC Engine</b>\n\n"
                "Models: <b>PRIMARY</b> + <b>FVG</b> (labeled)\n"
                "H4 bias · H1 BOS · OB/FVG · M15 first touch · TP 1.5R/2.0R\n\n"
                "Commands:\n"
                "/help /status /pairs /ping /stop\n"
                "/stats /performance /weekly /monthly\n"
                "/risk",
            )
        elif low.startswith("/stop"):
            subscribers = [x for x in subscribers if x != chat_id]
            save_subscribers(subscribers)
            send_telegram_to(chat_id, "🔴 <b>Unsubscribed</b> from SMC signals.")
        elif low.startswith("/help"):
            send_telegram_to(
                chat_id,
                "📖 <b>SMC Engine</b>\n\n"
                "1. H4 bias must agree\n"
                "2. H1 displacement BOS\n"
                "3. PRIMARY = OB preferred else FVG · FVG = FVG only\n"
                "4. First M15 touch → entry at zone 50%\n"
                "5. SL zone extreme + 2 pips\n"
                "6. TP1 1.5R · TP2 2.0R\n"
                "7. Signal re-edits: TP1 → WIN/LOSS\n\n"
                "/stats /performance /weekly /monthly /risk /pairs /status",
            )
        elif low.startswith("/status"):
            active = sum(1 for x in state.get("pending", []) if x.get("status") == "ACTIVE")
            send_telegram_to(
                chat_id,
                "⚡ <b>SMC Engine Status</b>\n\n"
                "State: <b>ONLINE</b>\n"
                f"Models: <b>{', '.join(MODELS)}</b>\n"
                f"Pairs: <b>{len(PAIRS)}</b> · Subs: <b>{len(load_subscribers())}</b>\n"
                f"Open signals: <b>{active}</b>",
            )
        elif low.startswith("/pairs"):
            lines = "\n".join(
                f"• <code>{p}</code> — {PAIR_STREAM.get(p, 'SMC')}" for p in PAIRS
            )
            send_telegram_to(chat_id, f"📊 <b>SMC pairs</b>\n\n{lines}")
        elif low.startswith("/ping"):
            send_telegram_to(chat_id, "pong 🏓 — SMC engine operational.")
        elif low.startswith("/stats") or low.startswith("/performance") or low.startswith("/report"):
            rows = load_outcomes()
            send_telegram_to(chat_id, metrics_block(rows, "SMC — All-time performance"))
        elif low.startswith("/weekly"):
            rows = load_outcomes()
            cutoff = utc_now() - timedelta(days=7)
            filt = []
            for r in rows:
                try:
                    ts = pd.Timestamp(r.get("closed_at") or r.get("exit_time"))
                    if ts.tzinfo is None:
                        ts = ts.tz_localize("UTC")
                    if ts >= cutoff:
                        filt.append(r)
                except Exception:
                    pass
            send_telegram_to(chat_id, metrics_block(filt, "SMC — Last 7 days"))
        elif low.startswith("/monthly"):
            rows = load_outcomes()
            cutoff = utc_now() - timedelta(days=30)
            filt = []
            for r in rows:
                try:
                    ts = pd.Timestamp(r.get("closed_at") or r.get("exit_time"))
                    if ts.tzinfo is None:
                        ts = ts.tz_localize("UTC")
                    if ts >= cutoff:
                        filt.append(r)
                except Exception:
                    pass
            send_telegram_to(chat_id, metrics_block(filt, "SMC — Last 30 days"))
        elif low.startswith("/risk") or low.startswith("/lot"):
            send_telegram_to(
                chat_id,
                "📐 <b>SMC risk guide</b>\n\n"
                "Risk 0.5%–1% of equity per signal.\n"
                "Lot ≈ (Equity × risk%) / (SL_pips × pip_value).\n"
                "TP1 1.5R · TP2 2.0R · no auto BE.\n\n"
                "Example 1000 account · 1% · 15 pip SL (EURUSD):\n"
                "Risk $10 · ~0.07 lots (broker-dependent).",
            )
        elif low.startswith("/admin") and ADMIN_ID and chat_id == str(ADMIN_ID):
            subs = load_subscribers()
            send_telegram_to(
                chat_id,
                f"🛠 <b>Admin</b>\nSubs: {len(subs)}\n"
                + "\n".join(f"<code>{s}</code>" for s in subs[:40]),
            )

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

def fetch(symbol, interval, outputsize=500, retries=6):
    global _td_i
    if not TWELVE_KEYS:
        print("No Twelve Data keys loaded")
        return None
    url = "https://api.twelvedata.com/time_series"
    for _ in range(retries):
        key = TWELVE_KEYS[_td_i % len(TWELVE_KEYS)]
        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": key,
            "format": "JSON",
        }
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
            msg = str(data).lower()
            if data.get("code") == 429 or "credit" in msg or "limit" in msg:
                print(f"Rate limit {symbol} {interval}, rotate key, wait...")
                _td_i += 1
                time.sleep(65)
                continue
            print(f"API error {symbol} {interval}:", data)
            return None
        except Exception as e:
            print(f"Fetch error {symbol} {interval}:", e)
            _td_i += 1
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



def opposing_liquidity_target(h1, h4, bos_i, d, entry, pip):
    """
    Non-fixed TP2: nearest opposing structural liquidity.
    Long  → nearest swing high above entry (H1), else H4 range high
    Short → nearest swing low below entry (H1), else H4 range low
    """
    def _from_df(df, t_cut):
        if df is None or len(df) < 20:
            return None
        w = df[df["datetime"] <= t_cut].tail(100).reset_index(drop=True)
        if len(w) < 15:
            return None
        sh, sl = swings_df(w, 2, 2)
        if d == 1:
            cands = [
                float(w["high"].iloc[j])
                for j in sh
                if float(w["high"].iloc[j]) > entry + 5 * pip
            ]
            if cands:
                return min(cands)
            return float(w["high"].max()) if float(w["high"].max()) > entry + 5 * pip else None
        cands = [
            float(w["low"].iloc[j])
            for j in sl
            if float(w["low"].iloc[j]) < entry - 5 * pip
        ]
        if cands:
            return max(cands)
        return float(w["low"].min()) if float(w["low"].min()) < entry - 5 * pip else None

    t_cut = h1["datetime"].iloc[bos_i]
    tp = _from_df(h1, t_cut)
    if tp is None:
        tp = _from_df(h4, t_cut)
    return tp


def analyze_pair(h4, h1, m15, pair, pip, model="PRIMARY"):
    """
    Scan completed H1 bars for BOS setups; return signal if M15 just
    completed first touch of zone (last closed M15 bar).

    model=PRIMARY → OB preferred, else FVG (live spine)
    model=FVG     → FVG zone only (parallel stream; labeled on signal)
    """
    if h1 is None or m15 is None or len(h1) < 60 or len(m15) < 80:
        return None
    model = (model or "PRIMARY").upper()
    if model not in ("PRIMARY", "FVG"):
        model = "PRIMARY"

    swing_hi, swing_lo = swings_df(h1, 2, 2)
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

        if model == "FVG":
            zone = fvg_at(h1, i)
            ztype = "FVG"
        else:
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

        for j in range(min(len(m15_after), M15_LOOKAHEAD)):
            row = m15_after.iloc[j]
            hi = float(row["high"])
            lo = float(row["low"])
            cl = float(row["close"])
            if d == 1 and cl < zlo:
                break
            if d == -1 and cl > zhi:
                break
            if lo > zhi or hi < zlo:
                continue

            if j != len(m15_after) - 1:
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

            # Non-fixed: opposing swing liquidity (not fixed 1.5R/2R)
            tp2 = opposing_liquidity_target(h1, h4, i, d, entry, pip)
            if tp2 is None:
                break
            if d == 1 and tp2 <= entry:
                break
            if d == -1 and tp2 >= entry:
                break
            reward2 = abs(tp2 - entry)
            rr2 = reward2 / risk if risk > 0 else 0.0
            if rr2 < MIN_RR_TP2:
                break
            # TP1 = 50% of path to opposing liquidity
            tp1 = entry + 0.5 * (tp2 - entry)
            rr1 = abs(tp1 - entry) / risk if risk > 0 else 0.0
            risk_pips = risk / pip
            candle_time = str(row["datetime"])
            side = "BUY" if d == 1 else "SELL"
            base = PAIR_STREAM.get(pair, "SMC 1H→15m")
            stream = f"{base} {model}"

            return {
                "stream": stream,
                "model": model,
                "pair": pair,
                "direction": d,
                "side": side,
                "zone_type": ztype,
                "entry": float(entry),
                "stop": float(stop),
                "tp1": float(tp1),
                "tp2": float(tp2),
                "rr1": float(rr1),
                "rr2": float(rr2),
                "risk_pips": float(risk_pips),
                "zlo": float(zlo),
                "zhi": float(zhi),
                "zmid": float(zmid),
                "bos_time": str(t),
                "candle_time": candle_time,
                "candle_key": f"SMC|{pair}|{model}|{candle_time}|{d}|{ztype}",
                "session": forex_session(candle_time),
            }
    return None


# ============================================================
# STATE
# ============================================================

def load_state():
    """Load SMC state. Never wipe pending/keys on version bump — only migrate."""
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
        if not isinstance(state, dict):
            return default
        prev_ver = state.get("version")
        state.setdefault("last_alert_keys", {})
        state.setdefault("pending", [])
        state.setdefault("last_update_id", 0)
        if not isinstance(state["last_alert_keys"], dict):
            state["last_alert_keys"] = {}
        if not isinstance(state["pending"], list):
            state["pending"] = []
        # Normalize pending message_ids for edit lifecycle
        for t in state["pending"]:
            if not isinstance(t, dict):
                continue
            mids = t.get("message_ids")
            if not mids and t.get("message_id"):
                cid = str(t.get("edit_chat_id") or CHAT_ID or "")
                if cid:
                    t["message_ids"] = {cid: t["message_id"]}
            elif isinstance(mids, dict):
                t["message_ids"] = {str(k): v for k, v in mids.items() if v is not None}
        if prev_ver != STATE_VERSION:
            print(
                f"SMC state version {prev_ver} → {STATE_VERSION}: "
                f"kept pending={len(state['pending'])} keys={len(state['last_alert_keys'])}"
            )
            state["version"] = STATE_VERSION
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
    pair = sig["pair"]
    model = sig.get("model") or "PRIMARY"
    stream = sig.get("stream") or f"{PAIR_STREAM.get(pair, 'SMC 1H→15m')} {model}"
    sess = sig.get("session") or forex_session(sig.get("candle_time"))
    return (
        f"{emoji} <b>SMC</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{stream}</b>\n"
        f"Model: <b>{model}</b> · Zone: <b>{sig['zone_type']}</b> · Session: <b>{sess}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"{emoji} <b>{sig['side']} {pair}</b>\n\n"
        f"H4 bias · H1 displacement BOS · "
        f"{'FVG only' if model == 'FVG' else 'OB preferred / FVG fallback'} · M15 first touch\n\n"
        f"Zone High <b>{_px(pair, sig['zhi'])}</b>\n"
        f"Entry mid <b>{_px(pair, sig['entry'])}</b>\n"
        f"Zone Low  <b>{_px(pair, sig['zlo'])}</b>\n\n"
        f"SL  <b>{_px(pair, sig['stop'])}</b> ({float(sig['risk_pips']):.1f} pips)\n"
        f"TP1 <b>{_px(pair, sig['tp1'])}</b> ({float(sig['rr1']):.1f}R)\n"
        f"TP2 <b>{_px(pair, sig['tp2'])}</b> ({float(sig['rr2']):.1f}R)\n\n"
        f"{format_dual_times(sig.get('bos_time'), sig.get('candle_time'))}\n\n"
        f"📌 Status: <b>ACTIVE</b> — Waiting for Entry\n"
        f"💡 Suggestion only: BE after TP1 (not auto)\n\n"
        f"<a href=\"{tradingview_url(pair)}\">Open {pair} on TradingView</a>\n\n"
        f"Built on Data · Driven by Discipline"
    )


def tp1_message(trade):
    pair = trade["pair"]
    stream = trade.get("stream") or PAIR_STREAM.get(pair, "SMC 1H→15m")
    return (
        f"<b>SMC</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{stream}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ <b>TP1 HIT</b> (+{float(trade.get('rr1', RR1)):.1f}R)\n\n"
        f"<b>{trade['side']} {pair}</b>\n"
        f"TP1 <b>{_px(pair, trade['tp1'])}</b>\n"
        f"Still heading TP2 <b>{_px(pair, trade['tp2'])}</b>\n\n"
        f"📌 Status: <b>TP1 HIT — ACTIVE</b>\n"
        f"💡 Suggestion only: BE after TP1 (not auto)\n\n"
        f"Built on Data · Driven by Discipline"
    )


def tp2_message(trade, exit_time):
    pair = trade["pair"]
    stream = trade.get("stream") or PAIR_STREAM.get(pair, "SMC 1H→15m")
    rr = float(trade.get("rr2", RR2))
    return (
        f"<b>SMC</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{stream}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏆 <b>TP2 HIT — WIN</b>\n\n"
        f"<b>{trade['side']} {pair}</b>\n"
        f"Exit <b>{format_signal_time(exit_time)}</b>\n"
        f"Result <b>+{rr:.2f}R</b>\n\n"
        f"📌 Status: <b>CLOSED — WIN</b>\n\n"
        f"Built on Data · Driven by Discipline"
    )


def sl_message(trade, exit_time):
    pair = trade["pair"]
    stream = trade.get("stream") or PAIR_STREAM.get(pair, "SMC 1H→15m")
    return (
        f"<b>SMC</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{stream}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"❌ <b>SL HIT — LOSS</b>\n\n"
        f"<b>{trade['side']} {pair}</b>\n"
        f"SL <b>{_px(pair, trade['stop'])}</b>\n"
        f"Exit <b>{format_signal_time(exit_time)}</b>\n"
        f"Result <b>-1.00R</b>\n\n"
        f"📌 Status: <b>CLOSED — LOSS</b>\n\n"
        f"Built on Data · Driven by Discipline"
    )


# ============================================================
# PENDING
# ============================================================

def check_pending(state, pair, m15):
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
            matches = np.where(m15["datetime"].values >= np.datetime64(entry_time))[0]
        except Exception:
            remaining.append(trade)
            continue
        if len(matches) == 0:
            remaining.append(trade)
            continue

        signal_index = int(matches[0])
        last_checked = int(trade.get("last_checked_index", signal_index))
        start_i = max(signal_index + 1, last_checked + 1)
        direction = int(trade["direction"])
        stop = float(trade["stop"])
        tp1 = float(trade["tp1"])
        tp2 = float(trade["tp2"])
        tp1_hit = bool(trade.get("tp1_hit", False))
        closed = False

        for j in range(start_i, latest + 1):
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
                text = sl_message(trade, candle_time)
                ok = edit_all_messages(trade, text)
                if not ok:
                    broadcast(text)
                append_outcome(trade, "LOSS", -1.0, candle_time)
                print(f"  CLOSE LOSS {trade.get('model')} {pair} edit_ok={ok}")
                closed = True
                break
            if hit_tp2:
                # If TP1 not marked yet but TP2 hit, still close as full win
                text = tp2_message(trade, candle_time)
                ok = edit_all_messages(trade, text)
                if not ok:
                    broadcast(text)
                r_out = float(trade.get("rr2") or RR2)
                append_outcome(trade, "WIN", r_out, candle_time)
                print(f"  CLOSE WIN {trade.get('model')} {pair} edit_ok={ok} R={r_out:+.2f}")
                closed = True
                break
            if hit_tp1 and not tp1_hit:
                trade["tp1_hit"] = True
                tp1_hit = True
                trade["status"] = "TP1_HIT"
                text = tp1_message(trade)
                ok = edit_all_messages(trade, text)
                if not ok:
                    broadcast(text)
                send_to_sheet({
                    "action": "TP1_TOUCH",
                    "stream": trade.get("stream"),
                    "pair": pair,
                    "exit_time": candle_time,
                })
                print(f"  TP1 HIT {trade.get('model')} {pair} edit_ok={ok}")
                # Persist TP1 flag so next run does not re-fire TP1 edit
                save_state(state)
            if j - signal_index >= MAX_HOLD_BARS:
                text = (
                    f"<b>SMC</b>\n━━━━━━━━━━━━━━━━━━━\n"
                    f"<b>{trade.get('stream')}</b>\n\n"
                    f"⏱️ <b>TIME EXIT</b>\n\n"
                    f"<b>{trade['side']} {pair}</b>\n"
                    f"Max hold reached.\n\nBuilt on Data · Driven by Discipline."
                )
                ok = edit_all_messages(trade, text)
                if not ok:
                    broadcast(text)
                append_outcome(trade, "TIME", 0.0, candle_time)
                print(f"  TIME EXIT {trade.get('model')} {pair} edit_ok={ok}")
                closed = True
                break
        if not closed:
            remaining.append(trade)

    state["pending"] = remaining
    # Always persist after managing this pair (TP1 flags + closed removals)
    save_state(state)


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("SMC ENGINE — PRIMARY + FVG (labeled)")
    print("H4 bias | displacement BOS | M15 first touch | TP liquidity (non-fixed R)")
    print(f"Models: {list(MODELS)} | AUD/JPY out | ADV = OFF")
    print("=" * 70)

    state = load_state()
    # Commands always first (fast path even if data keys missing)
    if BOT_TOKEN:
        process_commands(state)
        save_state(state)
        print(f"Subscribers: {len(load_subscribers())}")
    else:
        print("BOT_TOKEN missing — scan only.")

    if not TWELVE_KEYS:
        print("Missing TWELVE_DATA_KEYS / TWELVE_DATA_KEY — skip market scan")
        return
    print(f"Twelve keys loaded: {len(TWELVE_KEYS)}")

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

        for model in MODELS:
            sig = analyze_pair(h4, h1, m15, pair, pip, model=model)
            if sig is None:
                print(f"  {model}: no setup")
                continue

            key = sig["candle_key"]
            if state["last_alert_keys"].get(f"fired:{key}"):
                print(f"  {model}: fire-locked")
                continue

            # one ACTIVE per pair+model (PRIMARY and FVG can both be open)
            dup = any(
                t.get("pair") == pair
                and t.get("status") == "ACTIVE"
                and (t.get("model") or "PRIMARY") == model
                for t in state["pending"]
            )
            if dup:
                print(f"  {model}: active trade open")
                continue

            state["last_alert_keys"][f"fired:{key}"] = True
            state["last_alert_keys"][f"{pair}|{model}"] = key
            save_state(state)

            msg = build_signal_message(sig)
            message_id, message_ids = broadcast(msg)
            # Ensure message_ids is always a dict for later WIN/LOSS edits
            if not message_ids and message_id and CHAT_ID:
                message_ids = {str(CHAT_ID): message_id}
            message_ids = {str(k): v for k, v in (message_ids or {}).items() if v is not None}
            print(
                f"  SIGNAL {model} {sig['side']} | zone={sig['zone_type']} | "
                f"{sig['risk_pips']:.1f} pips | msgs={len(message_ids)}"
            )

            # Track even if Telegram partially failed — still need metrics path if mid exists
            if message_id or message_ids:
                primary_cid = str(CHAT_ID) if CHAT_ID else (
                    next(iter(message_ids.keys()), None)
                )
                state["pending"].append({
                    "stream": sig.get("stream"),
                    "model": model,
                    "pair": pair,
                    "message_id": message_id or next(iter(message_ids.values()), None),
                    "message_ids": message_ids,
                    "edit_chat_id": primary_cid,
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
                    "session": sig.get("session"),
                    "last_checked_index": len(m15) - 1,
                })
                save_state(state)
                print(f"  pending stored | edit targets={list(message_ids.keys())}")
            else:
                print("  WARNING: signal not tracked — no Telegram message_id (edit/metrics skip)")
            send_to_sheet({
                "action": "NEW_SIGNAL",
                "stream": sig.get("stream"),
                "model": model,
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
