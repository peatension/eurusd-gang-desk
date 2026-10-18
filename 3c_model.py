"""
TENSION TRADING DESK — 3c_model.py
CRT on GBP/USD | USD/JPY | USD/CAD | GBP/JPY
Telegram labels: 3C …
Also provides fetch/Telegram for crt_plain.py
"""

import os
import json
import re
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

# ============================================================
# ENV
# ============================================================

TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_KEY")
# Optional multi-key: TWELVE_DATA_KEYS=key1,key2,key3 (or semicolon). Rotates on daily empty / 429.
def _twelve_keys():
    keys = []
    multi = os.getenv("TWELVE_DATA_KEYS") or ""
    for part in multi.replace(";", ",").split(","):
        k = part.strip()
        if k and k not in keys:
            keys.append(k)
    single = (os.getenv("TWELVE_DATA_KEY") or "").strip()
    if single and single not in keys:
        keys.insert(0, single)
    return keys

TWELVE_DATA_KEYS = _twelve_keys()
_td_key_i = 0
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
SHEET_URL = os.getenv("SHEET_URL", "")

# --- Finnhub economic calendar (label only — never blocks signal) ---
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY") or os.getenv("FINNHUB_KEY") or ""
NEWS_WINDOW_HOURS = 2
_NEWS_CACHE = {"day": None, "events": []}

_PAIR_CCY = {
    "EUR/USD": ("EUR", "USD"),
    "AUD/USD": ("AUD", "USD"),
    "USD/CHF": ("USD", "CHF"),
    "EUR/JPY": ("EUR", "JPY"),
    "GBP/USD": ("GBP", "USD"),
    "USD/JPY": ("USD", "JPY"),
    "USD/CAD": ("USD", "CAD"),
    "GBP/JPY": ("GBP", "JPY"),
    "XAU/USD": ("USD",),
}

_NEWS_KEYWORDS = (
    "non-farm", "nonfarm", "nfp", "cpi", "fomc", "interest rate", "rate decision",
    "gdp", "unemployment", "jobless", "pce", "retail sales", "ppi", "payroll",
    "employment", "inflation", "ecb", "boe", "boj", "rba", "snb", "fed ",
)


def _finnhub_calendar(day_str):
    """Fetch economic calendar for one UTC day. Cached per process."""
    global _NEWS_CACHE
    if _NEWS_CACHE.get("day") == day_str and _NEWS_CACHE.get("events") is not None:
        return _NEWS_CACHE["events"]
    if not FINNHUB_API_KEY:
        return []
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={"from": day_str, "to": day_str, "token": FINNHUB_API_KEY},
            timeout=15,
        )
        data = r.json() if r.ok else {}
        events = data.get("economicCalendar") or data.get("economic_calendar") or []
        if not isinstance(events, list):
            events = []
        _NEWS_CACHE = {"day": day_str, "events": events}
        return events
    except Exception as e:
        print("finnhub calendar error:", e)
        return []


def news_label_for_signal(pair, when=None, window_hours=NEWS_WINDOW_HOURS):
    """
    Return a short label if high-impact news is near signal time for this pair.
    Never blocks — empty string if none / no key / error.
    """
    if not FINNHUB_API_KEY:
        return ""
    try:
        when_ts = pd.Timestamp(when) if when is not None else pd.Timestamp.utcnow()
        if when_ts.tzinfo is not None:
            when_ts = when_ts.tz_convert("UTC").tz_localize(None)
        else:
            when_ts = pd.Timestamp(when_ts)
    except Exception:
        when_ts = pd.Timestamp.utcnow().tz_localize(None)

    day = when_ts.strftime("%Y-%m-%d")
    events = list(_finnhub_calendar(day))
    # also pull adjacent day if near midnight
    try:
        if when_ts.hour >= 22:
            events += _finnhub_calendar((when_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
        if when_ts.hour <= 2:
            events += _finnhub_calendar((when_ts - pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    except Exception:
        pass

    ccys = set(_PAIR_CCY.get(pair, ()))
    if not ccys:
        base = pair.replace("-", "/").split("/")
        ccys = set(base)

    hits = []
    for ev in events:
        try:
            impact = str(ev.get("impact") or ev.get("importance") or "").lower()
            country = str(ev.get("country") or ev.get("currency") or "").upper()
            event = str(ev.get("event") or ev.get("name") or "")
            tstr = ev.get("time") or ev.get("date") or ""
            # Finnhub country often is ISO country code; map common FX
            country_ccy = {
                "US": "USD", "USA": "USD", "UNITED STATES": "USD",
                "EU": "EUR", "EMU": "EUR", "EUR": "EUR",
                "GB": "GBP", "UK": "GBP", "GBP": "GBP",
                "JP": "JPY", "JPY": "JPY",
                "AU": "AUD", "AUD": "AUD",
                "CA": "CAD", "CAD": "CAD",
                "CH": "CHF", "CHF": "CHF",
            }.get(country, country if len(country) == 3 else "")

            if impact not in ("high", "3", "major"):
                # still allow keyword hit for medium if classic NFP/CPI name
                el = event.lower()
                if not any(k in el for k in _NEWS_KEYWORDS):
                    continue
            else:
                el = event.lower()

            if country_ccy and country_ccy not in ccys and not any(c in country for c in ccys):
                # high impact USD still tags all USD pairs
                if "USD" not in ccys or country_ccy not in ("USD", ""):
                    if not any(k in el for k in ("nfp", "non-farm", "fomc", "cpi")):
                        continue

            # time proximity
            try:
                et = pd.Timestamp(str(tstr).replace("T", " ").replace("Z", "")[:19])
                delta_h = abs((et - when_ts).total_seconds()) / 3600.0
            except Exception:
                delta_h = 0.0  # unknown time → still mention if high impact same day

            if delta_h > float(window_hours) and impact in ("high", "3", "major"):
                # same-day high impact outside window: softer tag
                if delta_h > 6:
                    continue

            short = event.strip()
            for a, b in (
                ("Non-Farm Payrolls", "NFP"),
                ("Nonfarm Payroll", "NFP"),
                ("Consumer Price Index", "CPI"),
                ("Interest Rate Decision", "Rate Decision"),
                ("Federal Funds Rate", "FOMC"),
            ):
                if a.lower() in short.lower():
                    short = b
                    break
            if len(short) > 28:
                short = short[:26] + "…"
            if delta_h <= window_hours:
                hits.append(f"{short} ({delta_h:.0f}h)")
            else:
                hits.append(short)
        except Exception:
            continue

    if not hits:
        return ""
    # unique preserve order
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return " · ".join(out[:3])


STATE_FILE = "last_alert_state_crt.json"
SUBSCRIBERS_FILE = "subscribers.json"
OUTCOMES_FILE = "outcomes.json"
STATE_VERSION = 2

MIN_RR = 1.5
MAX_HOLD_BARS = 200
ATR_PERIOD = 14

# All 8 pairs shared by CRT set A + CRT set B (4+4). 3C strategy no longer runs.
PAIRS = {
    "EUR/USD": {"pip": 0.0001, "tv": "OANDA:EURUSD"},
    "AUD/USD": {"pip": 0.0001, "tv": "OANDA:AUDUSD"},
    "USD/CHF": {"pip": 0.0001, "tv": "OANDA:USDCHF"},
    "EUR/JPY": {"pip": 0.01, "tv": "OANDA:EURJPY"},
    "GBP/USD": {"pip": 0.0001, "tv": "OANDA:GBPUSD"},
    "USD/JPY": {"pip": 0.01, "tv": "OANDA:USDJPY"},
    "USD/CAD": {"pip": 0.0001, "tv": "OANDA:USDCAD"},
    "GBP/JPY": {"pip": 0.01, "tv": "OANDA:GBPJPY"},
    "XAU/USD": {"pip": 0.01, "tv": "OANDA:XAUUSD"},
    "AUD/JPY": {"pip": 0.01, "tv": "OANDA:AUDJPY"},
    "AUD/CAD": {"pip": 0.0001, "tv": "OANDA:AUDCAD"},
}

STREAMS = {}  # filled by CRT strategy below


# ============================================================
# SUBSCRIBERS
# ============================================================

def load_subscribers():
    """Return clean Telegram chat_id strings only (never user dict dumps)."""
    ids = set()

    def _ok(x):
        s = str(x).strip()
        # reject dict-looking garbage saved by old /start bugs
        if not s or s.startswith("{") or s.startswith("["):
            return None
        # allow numeric chat ids and negative group ids
        if re.fullmatch(r"-?\d+", s):
            return s
        return None

    if CHAT_ID:
        c = _ok(CHAT_ID)
        if c:
            ids.add(c)
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                for x in data:
                    if isinstance(x, dict):
                        x = x.get("id") or x.get("chat_id")
                    c = _ok(x)
                    if c:
                        ids.add(c)
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
# OUTCOMES LOG (shared by 3C Model + CRT plain)
# ============================================================

def load_outcomes():
    if not os.path.exists(OUTCOMES_FILE):
        return []
    try:
        with open(OUTCOMES_FILE, "r") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        print("Outcomes load error:", e)
        return []


def log_outcome(record):
    """Append a closed-trade record. Used for weekly metrics later."""
    rows = load_outcomes()
    rows.append(record)
    try:
        tmp = OUTCOMES_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rows, f, indent=2)
        os.replace(tmp, OUTCOMES_FILE)
    except Exception as e:
        print("Outcomes save error:", e)


def outcome_notice(trade, status, result_r, exit_time):
    """Short WIN/LOSS message sent to ALL subscribers (edit alone is not enough)."""
    if status == "WIN":
        head = "🏆 FINAL VERDICT: WIN"
        line = f"Result <b>+{float(result_r):.2f}R</b>"
    elif status == "LOSS":
        head = "🔴 FINAL VERDICT: LOSS"
        line = "Result <b>-1.00R</b>"
    else:
        head = f"⏱️ {status}"
        line = f"Result <b>{float(result_r):+.2f}R</b>" if result_r is not None else "Result n/a"
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"<b>{trade.get('stream')}</b>\n\n"
        f"{head}\n\n"
        f"<b>{trade.get('side')} {trade.get('pair')}</b>\n"
        f"Exit <b>{format_times(exit_time)}</b>\n"
        f"{line}\n\n"
        f"Built on Data.\nDriven by Discipline."
    )


def resolve_trade(trade, status, result_r, exit_time, edit_text):
    """
    1) Edit original signal on every chat we have message_ids for.
    2) If edit fails, broadcast full edit text as fallback.
    3) Always broadcast short WIN/LOSS notice.
    4) Log outcome.
    """
    if edit_text:
        ok = _edit_all_messages(trade, edit_text)
        if not ok:
            print(f"  edit failed {trade.get('stream')} {trade.get('pair')} — broadcast fallback")
            broadcast(edit_text)
    broadcast(outcome_notice(trade, status, result_r, exit_time))
    log_outcome(
        {
            "engine": "3C_MODEL",
            "stream": trade.get("stream"),
            "pair": trade.get("pair"),
            "side": trade.get("side"),
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
        }
    )


# ============================================================
# TELEGRAM
# ============================================================

def tv_inline_keyboard(pair):
    """Telegram inline URL button — shows as a tappable block under the message."""
    if not pair:
        return None
    try:
        url = tradingview_url(pair)
    except Exception:
        return None
    return {
        "inline_keyboard": [[
            {"text": f"📊 Open {pair} on TradingView", "url": url}
        ]]
    }


def send_telegram_to(chat_id, text, reply_markup=None):
    if not BOT_TOKEN or not chat_id:
        return None
    cid = str(chat_id).strip()
    # refuse dict dumps / garbage ids
    if cid.startswith("{") or cid.startswith("[") or not cid.lstrip("-").isdigit():
        print(f"Telegram skip invalid chat_id={cid!r}")
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": cid,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        data = requests.post(url, json=payload, timeout=20).json()
        if data.get("ok"):
            return data["result"]["message_id"]
        print(f"Telegram error ({chat_id}):", data)
    except Exception as e:
        print(f"Telegram send error ({chat_id}):", e)
    return None



def broadcast(text, pair=None):
    """Send to all subscribers. Returns message_id from CHAT_ID for edits.
    pair → attaches TradingView button block under the message."""
    subs = load_subscribers()
    if not subs:
        print("No subscribers.")
        return None

    primary = str(CHAT_ID) if CHAT_ID else subs[0]
    primary_mid = None
    kb = tv_inline_keyboard(pair) if pair else None
    for cid in subs:
        mid = send_telegram_to(cid, text, reply_markup=kb)
        if str(cid) == primary and mid:
            primary_mid = mid
        time.sleep(0.05)
    return primary_mid


def edit_telegram(message_id, text, chat_id=None, pair=None):
    target = str(chat_id or CHAT_ID or "")
    if not BOT_TOKEN or not target or not message_id:
        print(f"Telegram edit skip: token/target/mid missing target={target} mid={message_id}")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": target,
        "message_id": int(message_id) if str(message_id).isdigit() else message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    kb = tv_inline_keyboard(pair) if pair else None
    if kb:
        payload["reply_markup"] = kb
    try:
        data = requests.post(url, json=payload, timeout=20).json()
        if not data.get("ok"):
            print(f"Telegram edit fail ({target} mid={message_id}):", data)
            return False
        return True
    except Exception as e:
        print("Telegram edit error:", e)
        return False


def broadcast_message_ids(text, pair=None):
    """Send to all subscribers. Returns {chat_id: message_id} for multi-chat edits.
    pair → TradingView button block under each message."""
    subs = load_subscribers()
    if not subs:
        print("No subscribers.")
        return {}
    ids = {}
    kb = tv_inline_keyboard(pair) if pair else None
    for cid in subs:
        mid = send_telegram_to(cid, text, reply_markup=kb)
        if mid is not None:
            ids[str(cid)] = mid
        time.sleep(0.05)
    return ids


TELEGRAM_OFFSET_FILE = "telegram_offset.json"
CMD_MAX_AGE_SEC = 90  # ignore /start /pairs etc older than this (stops 50x replay)


def _load_tg_offset():
    try:
        if os.path.exists(TELEGRAM_OFFSET_FILE):
            with open(TELEGRAM_OFFSET_FILE, "r") as f:
                return int(json.load(f).get("last_update_id") or 0)
    except Exception:
        pass
    return 0


def _save_tg_offset(uid):
    try:
        with open(TELEGRAM_OFFSET_FILE, "w") as f:
            json.dump({"last_update_id": int(uid)}, f)
    except Exception as e:
        print("telegram_offset save error:", e)


def process_commands(state):
    """getUpdates with hard anti-spam:

    1) Offset stored in telegram_offset.json (always committed)
    2) ACK entire batch on Telegram before replying
    3) ONLY reply if message is < 90 seconds old (old cmds = silent ACK)
    4) Throttle same cmd per chat for 3 minutes

    Overlapping GH schedule + cron still bad — use cron only (yml has no schedule).
    """
    if not BOT_TOKEN:
        return

    import time as _time

    subscribers = load_subscribers()
    file_off = _load_tg_offset()
    state_off = int(state.get("last_update_id") or 0)
    offset = max(file_off, state_off) + 1
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        data = requests.get(
            url, params={"offset": offset, "timeout": 0}, timeout=15
        ).json()
    except Exception as e:
        print("getUpdates error:", e)
        return

    if not data.get("ok"):
        return

    results = data.get("result") or []
    if not results:
        return

    max_uid = max(int(r.get("update_id", 0)) for r in results)
    state["last_update_id"] = max(state_off, max_uid)
    _save_tg_offset(max_uid)
    # ACK on Telegram so the queue is drained even if we reply to nothing
    try:
        requests.get(
            url, params={"offset": max_uid + 1, "timeout": 0}, timeout=15
        )
    except Exception as e:
        print("getUpdates ACK error:", e)
    try:
        save_state(state)
    except Exception as e:
        print("offset state save error:", e)

    guard = state.setdefault("cmd_guard", {})
    now = _time.time()

    def _allow_reply(chat_id, cmd, uid, window_sec=180):
        gkey = f"{cmd}:{chat_id}"
        prev = guard.get(gkey) or {}
        if int(prev.get("uid") or 0) == int(uid):
            return False
        if now - float(prev.get("ts") or 0) < window_sec:
            return False
        guard[gkey] = {"uid": int(uid), "ts": now}
        return True

    replied = 0
    skipped_old = 0
    for result in results:
        uid = int(result.get("update_id", 0))
        message = result.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            continue

        # HARD GATE: never answer stale commands (this was the 50+ spam)
        msg_ts = message.get("date")
        try:
            age = now - float(msg_ts)
        except Exception:
            age = 0
        if msg_ts is not None and age > CMD_MAX_AGE_SEC:
            skipped_old += 1
            continue

        low = text.lower().split("@")[0].strip()

        if low.startswith("/start"):
            if chat_id not in subscribers:
                subscribers.append(chat_id)
                save_subscribers(subscribers)
            if not _allow_reply(chat_id, "start", uid):
                print(f"  /start suppressed chat={chat_id}")
                continue
            send_telegram_to(
                chat_id,
                "🟢 <b>Subscribed — Tension Trading Desk</b>\n\n"
                "You will receive alerts from both engines:\n"
                "• <b>CRT Plain</b> — EUR/USD AUD/USD USD/CHF EUR/JPY XAU/USD\n"
                "• <b>3C</b> — GBP/USD USD/JPY USD/CAD GBP/JPY\n\n"
                "<i>Live slim desk (if active): EUR/USD XAU/USD USD/JPY USD/CAD only</i>\n\n"
                "Commands: /help /status /pairs /ping /stop",
            )
            replied += 1

        elif low.startswith("/stop"):
            if chat_id in subscribers:
                subscribers = [x for x in subscribers if x != chat_id]
                save_subscribers(subscribers)
            if not _allow_reply(chat_id, "stop", uid):
                continue
            send_telegram_to(
                chat_id,
                "🔴 <b>Unsubscribed.</b>\nYou will no longer receive desk signals.",
            )
            replied += 1

        elif low.startswith("/help"):
            if not _allow_reply(chat_id, "help", uid):
                continue
            send_telegram_to(
                chat_id,
                "📖 <b>How to read signals</b>\n\n"
                "• <b>C1</b> — HTF range candle\n"
                "• <b>C2</b> — Sweep + close back inside (same TF)\n"
                "• <b>Entry</b> — LTF model (MSS / CISD / PLAIN / MODEL1 / FVG)\n"
                "• <b>SL</b> — Beyond sweep extreme (+ prop buffer shown)\n"
                "• <b>TP1</b> — C1 midpoint\n"
                "• <b>TP2</b> — Opposite C1 boundary\n\n"
                "Same setup is sent <b>once</b>; message is edited for TP1 / WIN / LOSS.\n"
                "BE after TP1 is a <b>suggestion only</b> (not auto).",
            )
            replied += 1

        elif low.startswith("/status"):
            if not _allow_reply(chat_id, "status", uid):
                continue
            send_telegram_to(
                chat_id,
                "⚡ <b>Desk Status</b>\n\n"
                "State: <b>ONLINE</b>\n"
                "Engines: <b>CRT Plain</b> + <b>3C</b>\n"
                f"Pairs mapped: {len(PAIRS)} | Subscribers: {len(load_subscribers())}",
            )
            replied += 1

        elif low.startswith("/pairs"):
            if not _allow_reply(chat_id, "pairs", uid):
                continue
            lines = "\n".join([f"• <code>{p}</code>" for p in PAIRS.keys()])
            send_telegram_to(
                chat_id,
                f"📊 <b>Scanned pairs</b>\n\n{lines}\n\n"
                "CRT Plain + 3C streams share this bot.",
            )
            replied += 1

        elif low.startswith("/ping"):
            if not _allow_reply(chat_id, "ping", uid):
                continue
            send_telegram_to(chat_id, "pong 🏓 — desk operational.")
            replied += 1

    print(f"commands: replied={replied} skipped_old={skipped_old} ack_uid={max_uid}")
    try:
        save_state(state)
    except Exception as e:
        print("cmd_guard save error:", e)
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


def format_times(dt_like):
    """Always treat naive feed timestamps as UTC, then show WAT (UTC+1) + UTC."""
    try:
        ts = pd.Timestamp(dt_like)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        wat = ts.tz_convert("Africa/Lagos") if hasattr(ts, "tz_convert") else ts + pd.Timedelta(hours=1)
        # Africa/Lagos is WAT year-round
        try:
            wat = ts.tz_convert("Africa/Lagos")
        except Exception:
            wat = ts + pd.Timedelta(hours=1)

        def ampm(x):
            return x.strftime("%I:%M %p").lstrip("0")

        return f"{ampm(wat)} WAT · {ampm(ts)} UTC · {wat.strftime('%d %b %Y')}"
    except Exception:
        return str(dt_like)




# ============================================================
# DATA
# ============================================================

def fetch(symbol, interval, outputsize=500, retries=3):
    """Fetch OHLCV. Rotates TWELVE_DATA_KEYS on daily empty / hard limit."""
    global _td_key_i
    if not TWELVE_DATA_KEYS:
        print("Missing TWELVE_DATA_KEY or TWELVE_DATA_KEYS")
        return None
    url = "https://api.twelvedata.com/time_series"
    n_keys = len(TWELVE_DATA_KEYS)
    attempts = max(retries, n_keys)
    for attempt in range(attempts):
        apikey = TWELVE_DATA_KEYS[_td_key_i % n_keys]
        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": apikey,
            "format": "JSON",
            "timezone": "UTC",
        }
        try:
            data = requests.get(url, params=params, timeout=30).json()
            if "values" in data:
                df = pd.DataFrame(data["values"])
                df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
                df["datetime"] = df["datetime"].dt.tz_convert("UTC").dt.tz_localize(None)
                for col in ("open", "high", "low", "close"):
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna().sort_values("datetime").reset_index(drop=True)
                now_utc = pd.Timestamp.utcnow().tz_localize(None)
                df = df[df["datetime"] <= now_utc + pd.Timedelta(minutes=2)].reset_index(drop=True)
                if len(df) < 3:
                    return None
                time.sleep(8)
                return df
            msg = str(data).lower()
            if "run out of api credits for the day" in msg or "api credits for the day" in msg:
                prev = _td_key_i % n_keys
                _td_key_i = (prev + 1) % n_keys
                print(
                    f"DAILY CREDITS EMPTY {symbol} {interval} "
                    f"(key {prev + 1}/{n_keys}) → rotate to key {_td_key_i + 1}/{n_keys}"
                )
                if _td_key_i == 0 and attempt + 1 >= n_keys:
                    print("All Twelve Data keys empty for the day — fail fast")
                    return None
                continue
            if data.get("code") == 429 or "credits" in msg or "limit" in msg:
                wait = 20 + attempt * 15
                print(f"Rate limit {symbol} {interval}, wait {wait}s...")
                time.sleep(wait)
                # soft rotate after repeated 429
                if attempt >= 1 and n_keys > 1:
                    _td_key_i = (_td_key_i + 1) % n_keys
                continue
            print(f"API error {symbol} {interval}:", data)
            return None
        except Exception as e:
            print(f"Fetch error {symbol} {interval}:", e)
            time.sleep(8)
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
    out = df.copy()
    out["atr"] = tr.rolling(ATR_PERIOD).mean()
    return out


# ============================================================
# 3C MODEL LOGIC
# ============================================================


# ---- CRT STRATEGY (3C) ----

STATE_FILE = "last_alert_state_crt_set2.json"
STATE_VERSION = 7
MIN_RR = 1.5
OUTCOMES_ENGINE = "3C_CRT"
REPORT_WINDOW_MIN = 12

# PAIRS defined above

# Full engine (kept intact). Live credit budget uses live_slim.py override.
CORE_PAIRS = ["GBP/USD", "USD/JPY", "USD/CAD", "GBP/JPY"]

# WR > 50% on W1+W2+W3 only (3C filter colab). 30MIN out. MODEL1/1H-PLAIN out.
# Daily / Weekly / Monthly HTF: MSS + CISD only (same as CRT).
STREAMS = {
    "3C 1H→15m MSS":   ("1h", "15min", "MSS"),
    "3C 1H→15m CISD":  ("1h", "15min", "CISD"),
    "3C 1H→5m MSS":    ("1h", "5min", "MSS"),
    "3C 4H→30m":       ("4h", "30min", "PLAIN"),
    "3C 4H→30m MSS":   ("4h", "30min", "MSS"),
    "3C 4H→30m CISD":  ("4h", "30min", "CISD"),
    "3C 4H→15m MSS":   ("4h", "15min", "MSS"),
    "3C 4H→15m CISD":  ("4h", "15min", "CISD"),
    # --- Daily / Weekly / Monthly (MSS + CISD only) ---
    "3C D→4H MSS":     ("1day", "4h", "MSS"),
    "3C D→4H CISD":    ("1day", "4h", "CISD"),
    "3C D→1H MSS":     ("1day", "1h", "MSS"),
    "3C D→1H CISD":    ("1day", "1h", "CISD"),
    "3C W→D MSS":      ("1week", "1day", "MSS"),
    "3C W→D CISD":     ("1week", "1day", "CISD"),
    "3C MN→W MSS":     ("1month", "1week", "MSS"),
    "3C MN→D CISD":    ("1month", "1day", "CISD"),
}

C3_ALL = ["GBP/USD", "USD/JPY", "USD/CAD", "GBP/JPY"]
STREAM_PAIRS = {
    "3C 1H→15m CISD": ["GBP/USD", "USD/JPY", "USD/CAD"],  # GBP/JPY W3 fail
    "3C 1H→5m MSS":   ["GBP/USD", "USD/JPY", "USD/CAD"],  # GBP/JPY W3 fail
    "3C 4H→30m":      ["USD/CAD", "GBP/JPY"],              # GBP/USD + USD/JPY fail
    "3C 4H→30m MSS":  ["USD/JPY", "USD/CAD", "GBP/JPY"],   # GBP/USD W3 fail
    "3C D→4H MSS":    C3_ALL,
    "3C D→4H CISD":   C3_ALL,
    "3C D→1H MSS":    C3_ALL,
    "3C D→1H CISD":   C3_ALL,
    "3C W→D MSS":     C3_ALL,
    "3C W→D CISD":    C3_ALL,
    "3C MN→W MSS":    C3_ALL,
    "3C MN→D CISD":   C3_ALL,
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
    """UTC hour buckets. London opens at 06:00 UTC (= 07:00 WAT) so
    07:30 WAT scans are not Asia-blocked. NY ends 21:00 UTC (= 22:00 WAT)."""
    try:
        h = int(_as_utc(dt_like).hour)
    except Exception:
        return "Unknown"
    if 0 <= h < 6:
        return "Asia"
    if 6 <= h < 12:
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
    """Mon–Fri only — weekend jobs must not fire verdicts on Sat/Sun bars."""
    try:
        ts = pd.Timestamp(dt_like)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        return int(ts.weekday()) < 5
    except Exception:
        return True


# W41 live: Asia n=15 net=-10.02R — block new Asia only
# Off-hours kept (user request) — still allowed for new signals
BLOCK_ASIA_SESSION = True
BLOCK_OFF_HOURS = False


# --- Signal hygiene (anti-noise) ---
ENTRY_DEDUP_PIPS = 5  # same entry zone across models/streams

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
    if "MN→" in s or "1MONTH" in s or " MN" in (stream_label or "").upper():
        return "MN"
    if "W→" in s or "1WEEK" in s or s.startswith("3CW"):
        return "W"
    if "30MIN" in s or "30M" in s:
        return "30MIN"
    if "4H" in s and "D→" not in s:
        return "4H"
    if "1H" in s and "D→" not in s:
        return "1H"
    if "D→" in s or "1DAY" in s:
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
    # ~2 bars max — keep signals sharp
    limits = {
        "1min": 10,
        "5min": 25,
        "15min": 50,
        "30min": 90,
        "1h": 150,
        "4h": 400,
        "1day": 1500,
    }
    max_age = limits.get(str(ltf_name).lower(), 50)
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
        # normalized c2 string so keys match across runs
        "candle_key": (
            f"{stream_label}|{pair}|"
            f"{str(setup.get('c2_time') or candle_time)[:19].replace('T', ' ')}|"
            f"{direction}|{entry_model}"
        ),
        # setup-level id: same C2+direction = one alert forever (all streams/models)
        "setup_uid": make_setup_uid(
            pair, direction, setup.get("c2_time") or candle_time,
            setup.get("crh"), setup.get("crl"),
        ),
        "c1_tf": htf_name,
        "ltf": ltf_name,
        "entry_model": entry_model,
    }


# ============================================================
# MESSAGES  (PDF-style coordinates + dual WAT/UTC times kept)
# ============================================================

def _px(pair, v):
    try:
        x = float(v)
    except Exception:
        return str(v)
    p = str(pair or "")
    if "XAU" in p:
        return f"{x:.2f}"
    if "JPY" in p:
        return f"{x:.3f}"
    return f"{x:.5f}"


def _entry_zone(trade):
    entry = float(trade.get("entry") or 0)
    pair = str(trade.get("pair") or "")
    if "XAU" in pair:
        pip = 0.1
    elif "JPY" in pair:
        pip = 0.01
    else:
        pip = 0.0001
    lo, hi = entry - 3 * pip, entry + 3 * pip
    return f"{_px(pair, lo)} - {_px(pair, hi)}"


def _core_levels(trade, tp1_state="Pending", tp2_state="Pending", sl_note=""):
    pair = trade.get("pair")
    entry = float(trade.get("entry") or 0)
    stop = float(trade.get("stop") or 0)
    tp1 = float(trade.get("tp1") or 0)
    tp2 = float(trade.get("tp2") or 0)
    prop = trade.get("prop_stop")
    risk = float(trade.get("risk_pips") or 0)
    tp1_pips = float(trade.get("tp1_pips") or 0)
    tp2_pips = float(trade.get("tp2_pips") or 0)
    rr1 = float(trade.get("rr1") or 0)
    rr2 = float(trade.get("rr2") or 0)
    crh, crl, mid = trade.get("crh"), trade.get("crl"), trade.get("mid")
    if crh is None or crl is None or mid is None:
        direction = int(trade.get("direction") or (1 if entry <= tp1 else -1))
        if direction == 1:
            crl = crl if crl is not None else entry
            mid = mid if mid is not None else tp1
            crh = crh if crh is not None else tp2
        else:
            crh = crh if crh is not None else entry
            mid = mid if mid is not None else tp1
            crl = crl if crl is not None else tp2
    sl_line = f"🛑 SL: <b>{_px(pair, stop)}</b> ({risk:.1f} pips)"
    if sl_note:
        sl_line += f" <i>({sl_note})</i>"
    if prop is not None:
        try:
            if abs(float(prop) - stop) > 1e-9:
                sl_line += f"\n   Prop SL: <b>{_px(pair, prop)}</b>"
        except Exception:
            pass
    return (
        f"Pair: <b>{pair}</b>\n"
        f"Action: <b>{trade.get('side', '—')}</b>\n"
        f"Entry Zone: <b>{_entry_zone(trade)}</b>\n"
        f"Entry: <b>{_px(pair, entry)}</b>\n\n"
        f"<b>Targets:</b>\n"
        f"🎯 TP1: <b>{_px(pair, tp1)}</b> ({tp1_pips:.1f} pips · {rr1:.2f}R) — {tp1_state}\n"
        f"🎯 TP2: <b>{_px(pair, tp2)}</b> ({tp2_pips:.1f} pips · {rr2:.2f}R) — {tp2_state}\n"
        f"{sl_line}\n\n"
        f"C1 High <b>{_px(pair, crh)}</b> · Mid <b>{_px(pair, mid)}</b> · Low <b>{_px(pair, crl)}</b>\n"
    )


def build_signal_message(sig, weekend_limit=False, already_ignited=False, age_min=0.0):
    emoji = "🟢" if int(sig.get("direction", 1)) == 1 else "🔴"
    model = sig.get("entry_model", "PLAIN")
    if weekend_limit:
        limit_side = "BUY LIMIT" if int(sig.get("direction", 1)) == 1 else "SELL LIMIT"
        status = (
            f"📌 Status: <b>Waiting for Entry</b>\n"
            f"Order: <b>{limit_side}</b> @ {_px(sig.get('pair'), sig.get('entry'))}\n"
            f"Weekend — activates when price trades through entry"
        )
    elif already_ignited:
        status = (
            f"⚠️ Status: <b>ALREADY IGNITED</b>\n"
            f"Entry candle ~{float(age_min):.0f} min old — move may already be underway.\n"
            f"<b>Enter only if</b> structure still valid on your chart."
        )
    else:
        status = "📌 Status: <b>ACTIVE</b> — Waiting for Entry"
    try:
        news = news_label_for_signal(sig.get("pair"), sig.get("candle_time") or sig.get("c2_time"))
    except Exception:
        news = ""
    news_line = f"News: <b>{news}</b>\n" if news else ""
    return (
        f"{emoji} <b>TENSION TRADING DESK</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{sig.get('stream', '3C')}</b>\n"
        f"Model: <b>{model}</b> · Session: <b>{sig.get('session', '—')}</b>\n"
        f"C1 TF: <b>{str(sig.get('c1_tf', '')).upper()}</b> → Entry TF: <b>{sig.get('ltf', '')}</b>\n"
        f"{news_line}"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"{_core_levels(sig, tp1_state='Pending', tp2_state='Pending')}\n"
        f"{format_dual_times(sig.get('c2_time'), sig.get('candle_time'))}\n\n"
        f"{status}\n\n"
        f"Built on Data · Driven by Discipline"
    )


def tp1_update_message(trade):
    emoji = "🟢" if int(trade.get("direction", 1)) == 1 else "🔴"
    pips = float(trade.get("tp1_pips") or 0)
    return (
        f"{emoji} <b>TENSION TRADING DESK</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{trade.get('stream', '3C')}</b>\n"
        f"Model: <b>{trade.get('entry_model', 'PLAIN')}</b> · Session: <b>{trade.get('session', '—')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"{_core_levels(trade, tp1_state='✅ ACHIEVED', tp2_state='Pending')}\n"
        f"{format_dual_times(trade.get('c2_time'), trade.get('candle_time'))}\n\n"
        f"📌 Status: <b>TP1 Hit (+{pips:.1f} pips)</b> — heading TP2\n"
        f"💡 Suggestion only: BE after TP1 (not auto)\n\n"
        f"Built on Data · Driven by Discipline"
    )


def tp2_final_message(trade, exit_time):
    emoji = "🟢" if int(trade.get("direction", 1)) == 1 else "🔴"
    rr = float(trade.get("rr2") or 0)
    pips = float(trade.get("tp2_pips") or 0)
    return (
        f"{emoji} <b>TENSION TRADING DESK</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{trade.get('stream', '3C')}</b>\n"
        f"Model: <b>{trade.get('entry_model', 'PLAIN')}</b> · Session: <b>{trade.get('session', '—')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"{_core_levels(trade, tp1_state='✅ ACHIEVED', tp2_state='✅ ACHIEVED')}\n"
        f"{format_dual_times(trade.get('c2_time'), trade.get('candle_time'))}\n"
        f"Exit: <b>{format_signal_time(exit_time)}</b>\n\n"
        f"📌 Status: <b>CLOSED — WIN</b> (+{rr:.2f}R · +{pips:.1f} pips)\n\n"
        f"Built on Data · Driven by Discipline"
    )


def sl_final_message(trade, exit_time):
    emoji = "🟢" if int(trade.get("direction", 1)) == 1 else "🔴"
    pips = float(trade.get("risk_pips") or 0)
    return (
        f"{emoji} <b>TENSION TRADING DESK</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{trade.get('stream', '3C')}</b>\n"
        f"Model: <b>{trade.get('entry_model', 'PLAIN')}</b> · Session: <b>{trade.get('session', '—')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"{_core_levels(trade, tp1_state='Pending', tp2_state='Pending', sl_note='HIT')}\n"
        f"{format_dual_times(trade.get('c2_time'), trade.get('candle_time'))}\n"
        f"Exit: <b>{format_signal_time(exit_time)}</b>\n\n"
        f"📌 Status: <b>CLOSED — LOSS</b> (-1.00R · -{pips:.1f} pips)\n\n"
        f"Built on Data · Driven by Discipline"
    )


def _edit_all_messages(trade, text):
    """Edit every chat message_id stored for this trade. Returns True if any edit ok."""
    ids = trade.get("message_ids") or {}
    if not ids and trade.get("message_id"):
        chat = trade.get("edit_chat_id") or (str(CHAT_ID) if CHAT_ID else None)
        if chat:
            ids = {str(chat): trade["message_id"]}
    any_ok = False
    pair = trade.get("pair")
    for chat, mid in ids.items():
        ok = edit_telegram(mid, text, chat, pair=pair)
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
            broadcast(edit_text)
    if status in ("WIN", "LOSS"):
        broadcast(outcome_notice(trade, status, result_r, exit_time))
        log_outcome(
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
            # Option A: skip TP/SL/verdict on Sat/Sun bars; still advance cursor
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
    rows = load_outcomes()
    out = []
    for r in rows:
        stream = str(r.get("stream") or "")
        if r.get("engine") != OUTCOMES_ENGINE and not stream.startswith("3C "):
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
    """Journal-style metrics (PDF blueprint)."""
    if not trades:
        return "No completed 3C trades in this period."
    wins = [t for t in trades if str(t.get("status", "")).upper() == "WIN"]
    losses = [t for t in trades if str(t.get("status", "")).upper() == "LOSS"]
    n = len(trades)
    nw, nl = len(wins), len(losses)
    wr = 100.0 * nw / n if n else 0.0
    rs = [float(t["result_r"]) for t in trades if t.get("result_r") is not None]
    net = sum(rs) if rs else 0.0
    exp = net / n if n else 0.0
    net_pips = 0.0
    for t in trades:
        try:
            net_pips += float(t.get("result_r") or 0) * float(t.get("risk_pips") or 0)
        except Exception:
            pass
    by_session = {}
    for t in trades:
        s = t.get("session") or "Unknown"
        by_session.setdefault(s, []).append(float(t.get("result_r") or 0))
    sess = "\n".join(
        f"• {s}: n={len(xs)} net={sum(xs):+.2f}R" for s, xs in sorted(by_session.items())
    ) or "—"
    rows = []
    for i, t in enumerate(trades[-8:], 1):
        tid = f"#3C-{i:03d}"
        side = t.get("side") or ("BUY" if int(t.get("direction") or 1) == 1 else "SELL")
        st = str(t.get("status", "")).upper()
        outcome = "Win (TP2)" if st == "WIN" else ("Loss (SL)" if st == "LOSS" else st)
        rr = t.get("result_r")
        rr_s = f"{float(rr):+.2f}R" if rr is not None else "—"
        rows.append(f"{tid}  {t.get('pair','?')}  {side}  {rr_s}  {outcome}")
    journal = "\n".join(rows) if rows else "—"
    return (
        f"<b>TOTAL SIGNALS</b>  {n}     <b>WIN RATE</b>  {wr:.1f}%     <b>NET</b>  {net:+.2f}R\n"
        f"Exp: <b>{exp:+.2f}R</b> · Net pips (approx): <b>{net_pips:+.0f}</b>\n\n"
        f"<b>Recent journal</b>\n{journal}\n\n"
        f"<b>By session</b>\n{sess}"
    )



def build_report(title, period_label, t0, t1, trades):
    return (
        f"<b>TENSION TRADING DESK</b>\n"
        f"════════════════════\n"
        f"<b>3C CRT — {title}</b>\n"
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
    # Fri (4)+ = this week ending; Mon–Thu = previous full week
    period_monday = week_start if now.weekday() >= 4 else week_start - timedelta(days=7)
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
    if w.weekday() == 4 and in_wat_send_window(22, 0):  # Friday after NY close (WAT)
        pid, t0, t1 = weekly_bounds()
        if state.get("weekly_report_id") != pid:
            trades = _crt_outcomes_between(t0, t1)
            broadcast(build_report("WEEKLY PERFORMANCE", pid, t0, t1, trades))
            state["weekly_report_id"] = pid
            print(f"Weekly report SENT {pid} n={len(trades)}")
    if w.day == 1 and in_wat_send_window(6, 0):
        pid, t0, t1 = monthly_bounds()
        if state.get("monthly_report_id") != pid:
            trades = _crt_outcomes_between(t0, t1)
            broadcast(build_report("MONTHLY PERFORMANCE", pid, t0, t1, trades))
            state["monthly_report_id"] = pid
            print(f"Monthly report SENT {pid} n={len(trades)}")
    if w.month == 1 and w.day == 1 and in_wat_send_window(0, 0):
        pid, t0, t1 = yearly_bounds(state)
        if state.get("yearly_report_id") != pid:
            trades = _crt_outcomes_between(t0, t1)
            broadcast(build_report("YEARLY PERFORMANCE", pid, t0, t1, trades))
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
    print("TENSION TRADING DESK — 3C CRT")
    print("MSS all | PLAIN+MODEL1 selective | dual times | TP1/WIN/LOSS")
    print("Weekend: no new signals | open trades still managed")
    print(f"Streams loaded: {len(STREAMS)}")
    print("=" * 70)

    if not TWELVE_DATA_KEYS and not TWELVE_DATA_KEY:
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
            size = 200 if tf in ("1day", "1week", "1month") else (500 if tf == "5min" else 400)
            cache[key] = fetch(pair, tf, size)
        fr = drop_forming_bar(cache[key]) if cache[key] is not None else None
        if fr is not None:
            fr = add_atr(fr)
        return fr

    # Pass A: resolve open trades even if pair left slim CORE
    for pair in list(scan_pairs) + pending_pairs:
        if pair not in PAIRS:
            continue
        print(f"\n--- {pair} {'(manage-only)' if pair in pending_pairs else ''} ---")
        needed_ltf = set()
        for t in state.get("pending") or []:
            if t.get("pair") == pair and t.get("ltf"):
                needed_ltf.add(str(t["ltf"]))
        if pair in scan_pairs:
            for _h, ltf_name, _m in STREAMS.values():
                needed_ltf.add(ltf_name)
        if not needed_ltf:
            needed_ltf.add("15min")
        for tf in needed_ltf:
            fr = _fetch_tf(pair, tf)
            if fr is not None:
                check_pending(state, pair, fr, tf)
                save_state(state)

    for pair in scan_pairs:
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

        for tf in frames:
            frames[tf] = add_atr(frames[tf])

        ltf_seen = set()
        for stream_label, (htf_name, ltf_name, entry_model) in STREAMS.items():
            if ltf_name in ltf_seen:
                continue
            ltf_seen.add(ltf_name)
            check_pending(state, pair, frames[ltf_name], ltf_name)
            save_state(state)

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
                save_state(state)
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

            # CLAIM + PERSIST before any Telegram send (stops re-fire across cron runs)
            state["last_alert_keys"][f"setup:{suid}"] = key
            state["last_alert_keys"][f"{stream_label}:{pair}"] = key
            save_state(state)

            signal_sent = utc_now().isoformat()
            sig["signal_sent"] = signal_sent
            status = "ENTRY_PENDING" if weekend else "ACTIVE"
            msg_ids = broadcast_message_ids(
                build_signal_message(
                    sig,
                    weekend_limit=weekend,
                    already_ignited=already_ignited,
                    age_min=age_min,
                ),
                pair=sig.get("pair"),
            )
            if already_ignited:
                print(
                    f"  {stream_label}: ALREADY IGNITED age={age_min:.0f}m "
                    f"(sent once, claimed)"
                )
            primary = str(CHAT_ID) if CHAT_ID else (next(iter(msg_ids), None))
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
                save_state(state)

    save_state(state)
    print("\n3C CRT SCAN COMPLETE | pending:", len(state["pending"]))


if __name__ == "__main__":
    main()
