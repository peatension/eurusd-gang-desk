import os
import json
import time
import requests
import pandas as pd
import numpy as np

# ============================================================
# TENSION TRADING DESK — PRODUCTION ENGINE
#
# MODEL:
# EXACT COMBINED SMC FROM RESEARCH
#
# ENTRY:
# 15m
#
# HTF:
# 1H EMA20 / EMA50
#
# PAIRS:
# AUD/JPY
# GBP/JPY
# NZD/USD
# EUR/JPY
# USD/JPY
#
# RR:
# AUD/JPY  = 3R
# GBP/JPY  = 3R
# NZD/USD  = 3R
# EUR/JPY  = 3R
# USD/JPY  = 2R
#
# TELEGRAM TRADE LIFECYCLE:
#
# SETUP DETECTED
#        ↓
# ENTRY HIT
#        ↓
# HEADING TO TP1
#        ↓
# TP1 HIT
#        ↓
# HEADING TO TP2
#        ↓
# TP2 HIT
#        ↓
# FINAL VERDICT: WIN
#
# OR
#
# ENTRY HIT
#        ↓
# STOP LOSS HIT
#        ↓
# FINAL VERDICT: LOSS
#
# IMPORTANT:
# DO NOT MODIFY THE SMC FUNCTIONS BELOW.
# They are copied from the validated research model.
# ============================================================


# ============================================================
# ENVIRONMENT
# ============================================================

TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SHEET_URL = os.getenv(
    "SHEET_URL",
    ""
)

STATE_FILE = "last_alert_state.json"

OUTPUTSIZE = 5000

ENTRY_TF = "15min"
HTF_TF = "1h"

ATR_PERIOD = 14
LOOKBACK = 100

SWING = 3
LIQUIDITY_LOOKBACK = 20
DEALING_RANGE = 50
DISPLACEMENT_ATR = 0.8

MAX_HOLD_BARS = 150


# ============================================================
# LOCKED TOP 5 CONFIG
# ============================================================

CONFIG = {

    "AUD/JPY": {
        "threshold": 7.5,
        "rr": 3.0
    },

    "GBP/JPY": {
        "threshold": 7.0,
        "rr": 3.0
    },

    "NZD/USD": {
        "threshold": 7.5,
        "rr": 3.0
    },

    "EUR/JPY": {
        "threshold": 7.5,
        "rr": 3.0
    },

    "USD/JPY": {
        "threshold": 7.5,
        "rr": 2.0
    }
}

PAIRS = list(CONFIG.keys())


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram credentials missing.")
        return None

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        data = response.json()

        if data.get("ok"):

            return data["result"]["message_id"]

        print(
            "Telegram error:",
            data
        )

    except Exception as e:

        print(
            "Telegram send error:",
            e
        )

    return None


def edit_telegram(message_id, text):

    if not BOT_TOKEN or not CHAT_ID:
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/editMessageText"
    )

    payload = {
        "chat_id": CHAT_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        data = response.json()

        return bool(
            data.get("ok")
        )

    except Exception as e:

        print(
            "Telegram edit error:",
            e
        )

        return False


# ============================================================
# GOOGLE SHEETS
# ============================================================

def send_to_sheet(record):

    if not SHEET_URL:
        print("SHEET_URL not configured.")
        return

    try:

        requests.post(
            SHEET_URL,
            json=record,
            timeout=20
        )

    except Exception as e:

        print(
            "Google Sheet error:",
            e
        )


# ============================================================
# FETCH — SAME TWELVE DATA SOURCE
# ============================================================

def fetch(
    symbol,
    interval,
    outputsize=5000,
    retries=4
):

    url = (
        "https://api.twelvedata.com/time_series"
    )

    params = {

        "symbol": symbol,

        "interval": interval,

        "outputsize": outputsize,

        "apikey": TWELVE_DATA_KEY,

        "format": "JSON"
    }

    for attempt in range(retries):

        try:

            response = requests.get(
                url,
                params=params,
                timeout=30
            )

            data = response.json()

            if "values" in data:

                df = pd.DataFrame(
                    data["values"]
                )

                df["datetime"] = pd.to_datetime(
                    df["datetime"]
                )

                for column in [
                    "open",
                    "high",
                    "low",
                    "close"
                ]:

                    df[column] = pd.to_numeric(
                        df[column],
                        errors="coerce"
                    )

                df = (
                    df
                    .dropna()
                    .sort_values("datetime")
                    .reset_index(drop=True)
                )

                time.sleep(8)

                return df

            if data.get("code") == 429:

                print(
                    f"Rate limit: {symbol} "
                    f"{interval}. Waiting..."
                )

                time.sleep(65)

                continue

            print(
                f"API error {symbol} "
                f"{interval}: {data}"
            )

            return None

        except Exception as e:

            print(
                f"Fetch error "
                f"{symbol} {interval}: {e}"
            )

            if attempt < retries - 1:
                time.sleep(10)

    return None


# ============================================================
# ATR
# ============================================================

def add_atr(df):

    prev_close = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],

            (
                df["high"]
                -
                prev_close
            ).abs(),

            (
                df["low"]
                -
                prev_close
            ).abs()
        ],
        axis=1
    ).max(axis=1)

    df["atr"] = tr.rolling(
        ATR_PERIOD
    ).mean()

    return df


# ============================================================
# HTF BIAS
# ============================================================

def create_htf_bias(htf):

    htf = htf.copy()

    htf["ema20"] = (
        htf["close"]
        .ewm(
            span=20,
            adjust=False
        )
        .mean()
    )

    htf["ema50"] = (
        htf["close"]
        .ewm(
            span=50,
            adjust=False
        )
        .mean()
    )

    htf["bias"] = np.where(

        htf["ema20"]
        >
        htf["ema50"],

        1,

        np.where(

            htf["ema20"]
            <
            htf["ema50"],

            -1,

            0
        )
    )

    return htf[
        [
            "datetime",
            "bias"
        ]
    ]


def attach_htf(df, htf):

    bias = create_htf_bias(
        htf
    )

    df = pd.merge_asof(

        df.sort_values(
            "datetime"
        ),

        bias.sort_values(
            "datetime"
        ),

        on="datetime",

        direction="backward"
    )

    df["bias"] = (
        df["bias"]
        .fillna(0)
        .astype(int)
    )

    return df


# ============================================================
# BASIC CANDLE FUNCTIONS
# ============================================================

def candle_body(df, i):

    return abs(
        df["close"].iloc[i]
        -
        df["open"].iloc[i]
    )


def bullish(df, i):

    return (
        df["close"].iloc[i]
        >
        df["open"].iloc[i]
    )


def bearish(df, i):

    return (
        df["close"].iloc[i]
        <
        df["open"].iloc[i]
    )


# ============================================================
# CURRENT SMC
# ============================================================

def liquidity_sweep(df, i):

    if i < LIQUIDITY_LOOKBACK:
        return 0

    previous_high = (
        df["high"]
        .iloc[
            i-LIQUIDITY_LOOKBACK:i
        ]
        .max()
    )

    previous_low = (
        df["low"]
        .iloc[
            i-LIQUIDITY_LOOKBACK:i
        ]
        .min()
    )

    high = df["high"].iloc[i]
    low = df["low"].iloc[i]
    close = df["close"].iloc[i]

    bullish_sweep = (
        low < previous_low
        and
        close > previous_low
    )

    bearish_sweep = (
        high > previous_high
        and
        close < previous_high
    )

    if bullish_sweep:
        return 1

    if bearish_sweep:
        return -1

    return 0


def fvg_signal(df, i):

    if i < 2:
        return 0

    if (
        df["low"].iloc[i]
        >
        df["high"].iloc[i-2]
    ):
        return 1

    if (
        df["high"].iloc[i]
        <
        df["low"].iloc[i-2]
    ):
        return -1

    return 0


def order_block(df, i):

    if i < 3:
        return 0

    atr = df["atr"].iloc[i]

    if pd.isna(atr) or atr <= 0:
        return 0

    body = candle_body(
        df,
        i
    )

    displacement = (
        body
        >=
        atr * DISPLACEMENT_ATR
    )

    if not displacement:
        return 0

    if (
        bullish(df, i)
        and
        bearish(df, i-1)
    ):
        return 1

    if (
        bearish(df, i)
        and
        bullish(df, i-1)
    ):
        return -1

    return 0


def structure_signal(df, i):

    if i < 20:
        return 0

    previous_high = (
        df["high"]
        .iloc[i-20:i]
        .max()
    )

    previous_low = (
        df["low"]
        .iloc[i-20:i]
        .min()
    )

    close = df["close"].iloc[i]

    if close > previous_high:
        return 1

    if close < previous_low:
        return -1

    return 0


def premium_discount(df, i):

    if i < DEALING_RANGE:
        return 0

    high = (
        df["high"]
        .iloc[i-DEALING_RANGE:i]
        .max()
    )

    low = (
        df["low"]
        .iloc[i-DEALING_RANGE:i]
        .min()
    )

    if high <= low:
        return 0

    equilibrium = (
        high + low
    ) / 2

    price = df["close"].iloc[i]

    if price < equilibrium:
        return 1

    if price > equilibrium:
        return -1

    return 0


def displacement_signal(df, i):

    if i < 2:
        return 0

    atr = df["atr"].iloc[i]

    if pd.isna(atr) or atr <= 0:
        return 0

    body = candle_body(
        df,
        i
    )

    if body < atr * DISPLACEMENT_ATR:
        return 0

    if bullish(df, i):
        return 1

    if bearish(df, i):
        return -1

    return 0


# ============================================================
# ADVANCED SMC
# ============================================================

def equal_liquidity(df, i):

    if i < 10:
        return 0

    atr = df["atr"].iloc[i]

    if pd.isna(atr) or atr <= 0:
        return 0

    tolerance = atr * 0.15

    recent_highs = (
        df["high"]
        .iloc[i-10:i]
        .values
    )

    recent_lows = (
        df["low"]
        .iloc[i-10:i]
        .values
    )

    equal_high = False
    equal_low = False

    for x in range(
        len(recent_highs)
    ):

        for y in range(
            x + 1,
            len(recent_highs)
        ):

            if abs(
                recent_highs[x]
                -
                recent_highs[y]
            ) <= tolerance:

                equal_high = True

    for x in range(
        len(recent_lows)
    ):

        for y in range(
            x + 1,
            len(recent_lows)
        ):

            if abs(
                recent_lows[x]
                -
                recent_lows[y]
            ) <= tolerance:

                equal_low = True

    if equal_low and not equal_high:
        return 1

    if equal_high and not equal_low:
        return -1

    return 0


def liquidity_structure(df, i):

    if i < 50:
        return 0

    internal_high = (
        df["high"]
        .iloc[i-10:i]
        .max()
    )

    internal_low = (
        df["low"]
        .iloc[i-10:i]
        .min()
    )

    external_high = (
        df["high"]
        .iloc[i-50:i-20]
        .max()
    )

    external_low = (
        df["low"]
        .iloc[i-50:i-20]
        .min()
    )

    price = df["close"].iloc[i]

    if (
        price > external_high
        and
        price > internal_high
    ):
        return 1

    if (
        price < external_low
        and
        price < internal_low
    ):
        return -1

    return 0


def inducement(df, i):

    if i < 20:
        return 0

    previous_high = (
        df["high"]
        .iloc[i-10:i-3]
        .max()
    )

    previous_low = (
        df["low"]
        .iloc[i-10:i-3]
        .min()
    )

    recent_high = (
        df["high"]
        .iloc[i-3:i]
        .max()
    )

    recent_low = (
        df["low"]
        .iloc[i-3:i]
        .min()
    )

    if (
        recent_low < previous_low
        and
        df["close"].iloc[i]
        >
        previous_low
    ):
        return 1

    if (
        recent_high > previous_high
        and
        df["close"].iloc[i]
        <
        previous_high
    ):
        return -1

    return 0


def liquidity_void(df, i):

    if i < 3:
        return 0

    atr = df["atr"].iloc[i]

    if pd.isna(atr) or atr <= 0:
        return 0

    bodies = []

    for j in range(
        i-2,
        i+1
    ):

        bodies.append(
            candle_body(
                df,
                j
            )
        )

    large_count = sum(
        b >= atr * 0.9
        for b in bodies
    )

    if large_count >= 2:

        if bullish(df, i):
            return 1

        if bearish(df, i):
            return -1

    return 0


def breaker_block(df, i):

    if i < 10:
        return 0

    ob = order_block(
        df,
        i-3
    )

    if ob == 0:
        return 0

    if (
        ob == 1
        and
        df["close"].iloc[i]
        <
        df["low"].iloc[i-3]
    ):
        return -1

    if (
        ob == -1
        and
        df["close"].iloc[i]
        >
        df["high"].iloc[i-3]
    ):
        return 1

    return 0


def mitigation_signal(df, i):

    if i < 8:
        return 0

    current = df["close"].iloc[i]

    old_high = df["high"].iloc[i-4]
    old_low = df["low"].iloc[i-4]

    if (
        current > old_low
        and
        current < old_high
        and
        bullish(df, i)
    ):
        return 1

    if (
        current > old_low
        and
        current < old_high
        and
        bearish(df, i)
    ):
        return -1

    return 0


def dealing_range_location(df, i):

    if i < DEALING_RANGE:
        return 0

    high = (
        df["high"]
        .iloc[i-DEALING_RANGE:i]
        .max()
    )

    low = (
        df["low"]
        .iloc[i-DEALING_RANGE:i]
        .min()
    )

    if high <= low:
        return 0

    price = df["close"].iloc[i]

    position = (
        price - low
    ) / (
        high - low
    )

    if position <= 0.30:
        return 1

    if position >= 0.70:
        return -1

    return 0


def mss_signal(df, i):

    if i < 15:
        return 0

    prior_high = (
        df["high"]
        .iloc[i-15:i-5]
        .max()
    )

    prior_low = (
        df["low"]
        .iloc[i-15:i-5]
        .min()
    )

    recent_high = (
        df["high"]
        .iloc[i-5:i]
        .max()
    )

    recent_low = (
        df["low"]
        .iloc[i-5:i]
        .min()
    )

    close = df["close"].iloc[i]

    if (
        recent_low < prior_low
        and
        close > prior_high
    ):
        return 1

    if (
        recent_high > prior_high
        and
        close < prior_low
    ):
        return -1

    return 0


# ============================================================
# CURRENT SCORE
# ============================================================

def current_smc_score(df, i):

    sweep = liquidity_sweep(df, i)
    fvg = fvg_signal(df, i)
    ob = order_block(df, i)
    structure = structure_signal(df, i)
    pd_zone = premium_discount(df, i)
    htf = int(df["bias"].iloc[i])
    displacement = displacement_signal(df, i)

    bull = 0
    bear = 0

    if sweep == 1:
        bull += 2
    elif sweep == -1:
        bear += 2

    if fvg == 1:
        bull += 1.5
    elif fvg == -1:
        bear += 1.5

    if ob == 1:
        bull += 1.5
    elif ob == -1:
        bear += 1.5

    if structure == 1:
        bull += 1.5
    elif structure == -1:
        bear += 1.5

    if pd_zone == 1:
        bull += 1
    elif pd_zone == -1:
        bear += 1

    if htf == 1:
        bull += 1.5
    elif htf == -1:
        bear += 1.5

    if displacement == 1:
        bull += 1
    elif displacement == -1:
        bear += 1

    if bull > bear:

        return {
            "direction": 1,
            "score": bull
        }

    if bear > bull:

        return {
            "direction": -1,
            "score": bear
        }

    return None


# ============================================================
# ADVANCED SCORE
# ============================================================

def advanced_smc_score(df, i):

    eq = equal_liquidity(df, i)
    liq = liquidity_structure(df, i)
    ind = inducement(df, i)
    void = liquidity_void(df, i)
    breaker = breaker_block(df, i)
    mitigation = mitigation_signal(df, i)
    range_loc = dealing_range_location(df, i)
    mss = mss_signal(df, i)

    htf = int(df["bias"].iloc[i])

    bull = 0
    bear = 0

    if eq == 1:
        bull += 1.0
    elif eq == -1:
        bear += 1.0

    if liq == 1:
        bull += 1.5
    elif liq == -1:
        bear += 1.5

    if ind == 1:
        bull += 1.0
    elif ind == -1:
        bear += 1.0

    if void == 1:
        bull += 1.0
    elif void == -1:
        bear += 1.0

    if breaker == 1:
        bull += 1.5
    elif breaker == -1:
        bear += 1.5

    if mitigation == 1:
        bull += 1.0
    elif mitigation == -1:
        bear += 1.0

    if range_loc == 1:
        bull += 1.0
    elif range_loc == -1:
        bear += 1.0

    if mss == 1:
        bull += 2.0
    elif mss == -1:
        bear += 2.0

    if htf == 1:
        bull += 1.0
    elif htf == -1:
        bear += 1.0

    if bull > bear:

        return {
            "direction": 1,
            "score": bull
        }

    if bear > bull:

        return {
            "direction": -1,
            "score": bear
        }

    return None


# ============================================================
# EXACT COMBINED MODEL
# ============================================================

def combined_score(df, i):

    current = current_smc_score(
        df,
        i
    )

    advanced = advanced_smc_score(
        df,
        i
    )

    if (
        current is None
        and
        advanced is None
    ):
        return None

    current_bull = 0
    current_bear = 0

    advanced_bull = 0
    advanced_bear = 0

    if current:

        if current["direction"] == 1:
            current_bull = current["score"]

        else:
            current_bear = current["score"]

    if advanced:

        if advanced["direction"] == 1:
            advanced_bull = advanced["score"]

        else:
            advanced_bear = advanced["score"]

    bull = (
        current_bull
        +
        advanced_bull
    )

    bear = (
        current_bear
        +
        advanced_bear
    )

    if bull > bear:

        return {
            "direction": 1,
            "score": bull,
            "current_score": current_bull,
            "advanced_score": advanced_bull
        }

    if bear > bull:

        return {
            "direction": -1,
            "score": bear,
            "current_score": current_bear,
            "advanced_score": advanced_bear
        }

    return None


# ============================================================
# EXACT ORIGINAL SL
# ============================================================

def calculate_stop(
    df,
    i,
    direction
):

    atr = df["atr"].iloc[i]

    if pd.isna(atr) or atr <= 0:
        return None

    start = max(
        0,
        i - 20
    )

    recent = df.iloc[
        start:i
    ]

    if len(recent) < 5:
        return None

    entry = df["close"].iloc[i]

    if direction == 1:

        structural_low = (
            recent["low"].min()
        )

        stop = (
            structural_low
            -
            atr * 0.25
        )

        if stop >= entry:
            return None

        return stop

    structural_high = (
        recent["high"].max()
    )

    stop = (
        structural_high
        +
        atr * 0.25
    )

    if stop <= entry:
        return None

    return stop


# ============================================================
# FIXED TARGET
# ============================================================

def fixed_target(
    entry,
    stop,
    direction,
    rr
):

    risk = abs(
        entry - stop
    )

    if risk <= 0:
        return None

    if direction == 1:

        return (
            entry
            +
            risk * rr
        )

    return (
        entry
        -
        risk * rr
    )


# ============================================================
# STATE
# ============================================================

def load_state():

    default = {
        "version": 3,
        "last_alert_keys": {},
        "pending": []
    }

    if not os.path.exists(
        STATE_FILE
    ):
        return default

    try:

        with open(
            STATE_FILE,
            "r"
        ) as f:

            state = json.load(f)

        # Version 3 introduces the
        # SETUP -> ENTRY -> TP lifecycle.
        #
        # Old v2 trades do not contain
        # reliable entry-state information,
        # so start fresh.

        if state.get("version") != 3:

            print(
                "Old state format detected."
            )

            print(
                "Starting new trade-lifecycle state."
            )

            return default

        state.setdefault(
            "last_alert_keys",
            {}
        )

        state.setdefault(
            "pending",
            []
        )

        return state

    except Exception as e:

        print(
            "State load error:",
            e
        )

        return default


def save_state(state):

    temp_file = (
        STATE_FILE
        +
        ".tmp"
    )

    with open(
        temp_file,
        "w"
    ) as f:

        json.dump(
            state,
            f,
            indent=2
        )

    os.replace(
        temp_file,
        STATE_FILE
    )


# ============================================================
# TRADINGVIEW
# ============================================================

def tradingview_symbol(pair):

    return pair.replace(
        "/",
        ""
    )


def tradingview_url(pair):

    symbol = tradingview_symbol(
        pair
    )

    return (
        "https://www.tradingview.com/"
        f"symbols/{symbol}/"
    )


# ============================================================
# SETUP MESSAGE
# ============================================================

def build_alert(
    pair,
    direction,
    score,
    current_score,
    advanced_score,
    entry,
    stop,
    tp1,
    tp2,
    rr,
    candle_time
):

    side = (
        "BUY"
        if direction == 1
        else
        "SELL"
    )

    emoji = (
        "🟢"
        if direction == 1
        else
        "🔴"
    )

    threshold = CONFIG[pair][
        "threshold"
    ]

    risk = abs(
        entry - stop
    )

    return (
        "🟡 <b>TENSION TRADING DESK</b>\n"
        "\n"
        "<b>SETUP DETECTED</b>\n"
        "\n"
        f"{emoji} <b>{side} {pair}</b>\n"
        "\n"
        f"📊 Combined Score: "
        f"<b>{score:.1f}</b>\n"
        f"🎯 Threshold: "
        f"<b>{threshold:.1f}</b>\n"
        f"🧠 Current SMC: "
        f"<b>{current_score:.1f}</b>\n"
        f"🔬 Advanced SMC: "
        f"<b>{advanced_score:.1f}</b>\n"
        "\n"
        f"💰 Planned Entry: <b>{entry:.5f}</b>\n"
        f"🛑 Stop Loss: <b>{stop:.5f}</b>\n"
        f"⚡ Risk: <b>{risk:.5f}</b>\n"
        "\n"
        f"🎯 TP1: <b>{tp1:.5f}</b> "
        f"(1.5R)\n"
        f"🏆 TP2: <b>{tp2:.5f}</b> "
        f"({rr:.1f}R)\n"
        "\n"
        f"⏱️ Signal Candle: "
        f"<b>{candle_time}</b>\n"
        f"📈 Timeframe: <b>15M</b>\n"
        f"🧭 HTF: <b>1H</b>\n"
        "\n"
        "⏳ <b>WAITING FOR ENTRY...</b>\n"
        "\n"
        f"📊 <a href=\"{tradingview_url(pair)}\">"
        f"Open {pair} on TradingView"
        f"</a>\n"
        "\n"
        "⚠️ Alert only — manage risk manually.\n"
        "\n"
        "<b>Built on Data.</b>\n"
        "<b>Driven by Discipline.</b>"
    )


# ============================================================
# ANALYZE LATEST CANDLE
# ============================================================

def analyze_pair(
    df,
    pair
):

    minimum = max(
        LOOKBACK,
        70
    )

    if len(df) <= minimum:
        return None

    i = len(df) - 1

    signal = combined_score(
        df,
        i
    )

    if signal is None:
        return None

    threshold = CONFIG[pair][
        "threshold"
    ]

    if signal["score"] < threshold:
        return None

    direction = signal[
        "direction"
    ]

    entry = float(
        df["close"].iloc[i]
    )

    stop = calculate_stop(
        df,
        i,
        direction
    )

    if stop is None:
        return None

    rr = CONFIG[pair]["rr"]

    tp2 = fixed_target(
        entry,
        stop,
        direction,
        rr
    )

    if tp2 is None:
        return None

    tp1 = fixed_target(
        entry,
        stop,
        direction,
        1.5
    )

    candle_time = str(
        df["datetime"].iloc[i]
    )

    return {

        "pair": pair,

        "direction": direction,

        "side":
            "BUY"
            if direction == 1
            else
            "SELL",

        "score":
            float(signal["score"]),

        "current_score":
            float(
                signal["current_score"]
            ),

        "advanced_score":
            float(
                signal["advanced_score"]
            ),

        "entry":
            entry,

        "stop":
            float(stop),

        "tp1":
            float(tp1),

        "tp2":
            float(tp2),

        "rr":
            float(rr),

        "candle_time":
            candle_time,

        "candle_key":
            f"{pair}|"
            f"{candle_time}|"
            f"{direction}"
    }


# ============================================================
# TELEGRAM STATUS MESSAGES
# ============================================================

def entry_hit_message(trade):

    return (
        "🟢 <b>ENTRY HIT — TRADE ACTIVE</b>\n"
        "\n"
        f"<b>{trade['side']} "
        f"{trade['pair']}</b>\n"
        "\n"
        f"✅ Entry: "
        f"<b>{float(trade['entry']):.5f}</b>\n"
        f"🛑 Stop Loss: "
        f"<b>{float(trade['stop']):.5f}</b>\n"
        f"🎯 TP1: "
        f"<b>{float(trade['tp1']):.5f}</b>\n"
        f"🏆 TP2: "
        f"<b>{float(trade['tp2']):.5f}</b>\n"
        "\n"
        "🚀 <b>TRADE ACTIVE</b>\n"
        "➡️ <b>HEADING TO TP1</b>\n"
        "\n"
        "<b>Built on Data.</b>\n"
        "<b>Driven by Discipline.</b>"
    )


def tp1_hit_message(trade):

    return (
        "✅ <b>TP1 HIT</b>\n"
        "\n"
        f"<b>{trade['side']} "
        f"{trade['pair']}</b>\n"
        "\n"
        f"🎯 TP1: "
        f"<b>{float(trade['tp1']):.5f}</b>\n"
        "📈 Result: <b>+1.5R</b>\n"
        "\n"
        "🚀 <b>HEADING TO TP2</b>\n"
        f"🏆 Final TP: "
        f"<b>{float(trade['tp2']):.5f}</b>\n"
        f"🎯 Final RR: "
        f"<b>{float(trade['rr']):.1f}R</b>\n"
        "\n"
        "<b>Built on Data.</b>\n"
        "<b>Driven by Discipline.</b>"
    )


def tp2_win_message(
    trade,
    candle_time
):

    return (
        "🏆 <b>TP2 HIT</b>\n"
        "\n"
        f"<b>{trade['side']} "
        f"{trade['pair']}</b>\n"
        "\n"
        f"✅ TP2: "
        f"<b>{float(trade['tp2']):.5f}</b>\n"
        f"📈 Result: "
        f"<b>+{float(trade['rr']):.1f}R</b>\n"
        f"⏱️ Exit: <b>{candle_time}</b>\n"
        "\n"
        "🏆 <b>FINAL VERDICT: WIN</b>\n"
        "\n"
        "<b>Built on Data.</b>\n"
        "<b>Driven by Discipline.</b>"
    )


def stop_loss_message(
    trade,
    candle_time
):

    return (
        "🔴 <b>STOP LOSS HIT</b>\n"
        "\n"
        f"<b>{trade['side']} "
        f"{trade['pair']}</b>\n"
        "\n"
        f"🛑 SL: "
        f"<b>{float(trade['stop']):.5f}</b>\n"
        f"⏱️ Exit: <b>{candle_time}</b>\n"
        "\n"
        "📉 Result: <b>-1R</b>\n"
        "\n"
        "🔴 <b>FINAL VERDICT: LOSS</b>\n"
        "\n"
        "<b>Built on Data.</b>\n"
        "<b>Driven by Discipline.</b>"
    )


# ============================================================
# PENDING TRADE MANAGEMENT
# ============================================================

def check_pending_trades(
    state,
    pair,
    df
):

    if not state["pending"]:
        return

    latest_index = (
        len(df) - 1
    )

    remaining = []

    for trade in state["pending"]:

        if trade.get(
            "pair"
        ) != pair:

            remaining.append(
                trade
            )

            continue

        entry_time = pd.Timestamp(
            trade["candle_time"]
        )

        matches = np.where(
            df["datetime"].values
            >=
            entry_time.to_datetime64()
        )[0]

        if len(matches) == 0:

            remaining.append(
                trade
            )

            continue

        signal_index = int(
            matches[0]
        )

        status = trade.get(
            "status",
            "WAITING_ENTRY"
        )

        entry_hit = bool(
            trade.get(
                "entry_hit",
                False
            )
        )

        tp1_hit = bool(
            trade.get(
                "tp1_hit",
                False
            )
        )

        # =====================================================
        # DETERMINE WHERE TO START
        # =====================================================

        last_checked = int(
            trade.get(
                "last_checked_index",
                signal_index
            )
        )

        start_index = max(
            signal_index + 1,
            last_checked + 1
        )

        final_tp = float(
            trade["tp2"]
        )

        stop = float(
            trade["stop"]
        )

        entry = float(
            trade["entry"]
        )

        tp1 = float(
            trade["tp1"]
        )

        direction = int(
            trade["direction"]
        )

        closed = False

        # =====================================================
        # WAIT FOR ACTUAL ENTRY
        # =====================================================

        if not entry_hit:

            for j in range(
                start_index,
                latest_index + 1
            ):

                high = float(
                    df["high"].iloc[j]
                )

                low = float(
                    df["low"].iloc[j]
                )

                candle_time = str(
                    df["datetime"].iloc[j]
                )

                # ---------------------------------------------
                # PRICE MUST ACTUALLY REACH ENTRY
                # ---------------------------------------------

                entry_reached = (
                    low <= entry <= high
                )

                if not entry_reached:

                    trade[
                        "last_checked_index"
                    ] = j

                    continue

                # ---------------------------------------------
                # ENTRY HIT
                # ---------------------------------------------

                trade["entry_hit"] = True

                trade["status"] = "ACTIVE"

                trade["entry_hit_time"] = (
                    candle_time
                )

                trade["entry_hit_index"] = (
                    j
                )

                entry_hit = True

                edit_telegram(

                    trade["message_id"],

                    entry_hit_message(
                        trade
                    )
                )

                send_to_sheet({

                    "action":
                        "ENTRY_HIT",

                    "pair":
                        pair,

                    "side":
                        trade["side"],

                    "entry":
                        entry,

                    "stop":
                        stop,

                    "tp1":
                        tp1,

                    "tp2":
                        final_tp,

                    "score":
                        trade["score"],

                    "entry_time":
                        candle_time
                })

                # =============================================
                # SAME-CANDLE SAFETY CHECK
                #
                # If the same candle reaches SL after entry,
                # SL gets priority.
                # =============================================

                if direction == 1:

                    hit_sl = (
                        low <= stop
                    )

                    hit_tp2 = (
                        high >= final_tp
                    )

                    hit_tp1 = (
                        high >= tp1
                    )

                else:

                    hit_sl = (
                        high >= stop
                    )

                    hit_tp2 = (
                        low <= final_tp
                    )

                    hit_tp1 = (
                        low <= tp1
                    )

                if hit_sl:

                    edit_telegram(

                        trade["message_id"],

                        stop_loss_message(
                            trade,
                            candle_time
                        )
                    )

                    send_to_sheet({

                        "action":
                            "OUTCOME",

                        "pair":
                            pair,

                        "side":
                            trade["side"],

                        "entry":
                            entry,

                        "stop":
                            stop,

                        "tp1":
                            tp1,

                        "tp2":
                            final_tp,

                        "outcome":
                            "LOSS",

                        "result_r":
                            -1,

                        "entry_time":
                            trade[
                                "entry_hit_time"
                            ],

                        "exit_time":
                            candle_time
                    })

                    closed = True

                    break

                if hit_tp2:

                    edit_telegram(

                        trade["message_id"],

                        tp2_win_message(
                            trade,
                            candle_time
                        )
                    )

                    send_to_sheet({

                        "action":
                            "OUTCOME",

                        "pair":
                            pair,

                        "side":
                            trade["side"],

                        "entry":
                            entry,

                        "stop":
                            stop,

                        "tp1":
                            tp1,

                        "tp2":
                            final_tp,

                        "outcome":
                            "WIN",

                        "result_r":
                            float(
                                trade["rr"]
                            ),

                        "entry_time":
                            trade[
                                "entry_hit_time"
                            ],

                        "exit_time":
                            candle_time
                    })

                    closed = True

                    break

                if hit_tp1 and not tp1_hit:

                    trade["tp1_hit"] = True

                    trade["tp1_time"] = (
                        candle_time
                    )

                    tp1_hit = True

                    edit_telegram(

                        trade["message_id"],

                        tp1_hit_message(
                            trade
                        )
                    )

                trade[
                    "last_checked_index"
                ] = j

                # ---------------------------------------------
                # Entry has been reached.
                # Continue normally from next candle.
                # ---------------------------------------------

                break

        # =====================================================
        # IF ENTRY WAS NOT HIT YET
        # =====================================================

        if not entry_hit:

            trade[
                "last_checked_index"
            ] = latest_index

            remaining.append(
                trade
            )

            continue

        if closed:
            continue

        # =====================================================
        # ACTIVE TRADE
        # =====================================================

        active_start = max(

            int(
                trade.get(
                    "entry_hit_index",
                    signal_index
                )
            ) + 1,

            int(
                trade.get(
                    "last_checked_index",
                    signal_index
                )
            ) + 1
        )

        for j in range(
            active_start,
            latest_index + 1
        ):

            high = float(
                df["high"].iloc[j]
            )

            low = float(
                df["low"].iloc[j]
            )

            candle_time = str(
                df["datetime"].iloc[j]
            )

            if direction == 1:

                hit_sl = (
                    low <= stop
                )

                hit_tp2 = (
                    high >= final_tp
                )

                hit_tp1 = (
                    high >= tp1
                )

            else:

                hit_sl = (
                    high >= stop
                )

                hit_tp2 = (
                    low <= final_tp
                )

                hit_tp1 = (
                    low <= tp1
                )

            # =================================================
            # STOP LOSS
            # =================================================

            if hit_sl:

                edit_telegram(

                    trade["message_id"],

                    stop_loss_message(
                        trade,
                        candle_time
                    )
                )

                send_to_sheet({

                    "action":
                        "OUTCOME",

                    "pair":
                        pair,

                    "side":
                        trade["side"],

                    "entry":
                        entry,

                    "stop":
                        stop,

                    "tp1":
                        tp1,

                    "tp2":
                        final_tp,

                    "outcome":
                        "LOSS",

                    "result_r":
                        -1,

                    "entry_time":
                        trade[
                            "entry_hit_time"
                        ],

                    "exit_time":
                        candle_time
                })

                closed = True

                break

            # =================================================
            # TP2
            # =================================================

            if hit_tp2:

                edit_telegram(

                    trade["message_id"],

                    tp2_win_message(
                        trade,
                        candle_time
                    )
                )

                send_to_sheet({

                    "action":
                        "OUTCOME",

                    "pair":
                        pair,

                    "side":
                        trade["side"],

                    "entry":
                        entry,

                    "stop":
                        stop,

                    "tp1":
                        tp1,

                    "tp2":
                        final_tp,

                    "outcome":
                        "WIN",

                    "result_r":
                        float(
                            trade["rr"]
                        ),

                    "entry_time":
                        trade[
                            "entry_hit_time"
                        ],

                    "exit_time":
                        candle_time
                })

                closed = True

                break

            # =================================================
            # TP1
            # =================================================

            if (
                hit_tp1
                and
                not tp1_hit
            ):

                trade["tp1_hit"] = True

                trade["tp1_time"] = (
                    candle_time
                )

                tp1_hit = True

                edit_telegram(

                    trade["message_id"],

                    tp1_hit_message(
                        trade
                    )
                )

            trade[
                "last_checked_index"
            ] = j

        if closed:
            continue

        # =====================================================
        # MAX HOLD
        # =====================================================

        entry_index = int(
            trade.get(
                "entry_hit_index",
                signal_index
            )
        )

        bars_held = (
            latest_index
            -
            entry_index
        )

        if bars_held >= MAX_HOLD_BARS:

            exit_price = float(
                df["close"].iloc[
                    latest_index
                ]
            )

            risk = abs(
                entry
                -
                stop
            )

            if risk <= 0:

                result_r = 0.0

            elif direction == 1:

                result_r = (
                    exit_price
                    -
                    entry
                ) / risk

            else:

                result_r = (
                    entry
                    -
                    exit_price
                ) / risk

            result_r = float(
                result_r
            )

            if result_r > 0:
                outcome = "WIN"

            elif result_r < 0:
                outcome = "LOSS"

            else:
                outcome = "BE"

            text = (
                "⏱️ <b>TRADE CLOSED — MAX HOLD</b>\n"
                "\n"
                f"<b>{trade['side']} "
                f"{pair}</b>\n"
                "\n"
                f"Entry: <b>{entry:.5f}</b>\n"
                f"Exit: <b>{exit_price:.5f}</b>\n"
                f"Bars Held: <b>{bars_held}</b>\n"
                "\n"
                f"Result: <b>{result_r:+.2f}R</b>\n"
                "\n"
                f"<b>FINAL VERDICT: {outcome}</b>\n"
                "\n"
                "<b>Built on Data.</b>\n"
                "<b>Driven by Discipline.</b>"
            )

            edit_telegram(
                trade["message_id"],
                text
            )

            send_to_sheet({

                "action":
                    "OUTCOME",

                "pair":
                    pair,

                "side":
                    trade["side"],

                "entry":
                    entry,

                "stop":
                    stop,

                "tp1":
                    tp1,

                "tp2":
                    final_tp,

                "outcome":
                    "TIMEOUT",

                "result_r":
                    result_r,

                "entry_time":
                    trade[
                        "entry_hit_time"
                    ],

                "exit_time":
                    str(
                        df["datetime"].iloc[
                            latest_index
                        ]
                    )
            })

            continue

        trade[
            "last_checked_index"
        ] = latest_index

        remaining.append(
            trade
        )

    state["pending"] = remaining


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)

    print(
        "TENSION TRADING DESK"
    )

    print(
        "COMBINED SMC — TOP 5"
    )

    print(
        "FIXED RR PRODUCTION ENGINE"
    )

    print(
        "TRADE LIFECYCLE v3"
    )

    print("=" * 70)

    if not TWELVE_DATA_KEY:

        print(
            "ERROR: TWELVE_DATA_KEY missing."
        )

        return

    if not BOT_TOKEN:

        print(
            "WARNING: BOT_TOKEN missing."
        )

    if not CHAT_ID:

        print(
            "WARNING: CHAT_ID missing."
        )

    state = load_state()

    for pair in PAIRS:

        print("\n")
        print("-" * 70)

        print(
            f"SCANNING {pair}"
        )

        print("-" * 70)

        # ====================================================
        # 15M
        # ====================================================

        print(
            "Downloading 15m..."
        )

        ltf = fetch(
            pair,
            ENTRY_TF,
            OUTPUTSIZE
        )

        if ltf is None:

            print(
                f"{pair}: 15m failed."
            )

            continue

        # ====================================================
        # 1H
        # ====================================================

        print(
            "Downloading 1h..."
        )

        htf = fetch(
            pair,
            HTF_TF,
            2500
        )

        if htf is None:

            print(
                f"{pair}: 1h failed."
            )

            continue

        # ====================================================
        # PREPARE
        # ====================================================

        df = attach_htf(
            ltf,
            htf
        )

        df = add_atr(
            df
        )

        print(
            f"Candles: {len(df):,}"
        )

        if len(df) < 100:

            print(
                f"{pair}: insufficient data."
            )

            continue

        # ====================================================
        # RESOLVE EXISTING TRADES FIRST
        # ====================================================

        check_pending_trades(
            state,
            pair,
            df
        )

        # ====================================================
        # CURRENT SIGNAL
        # ====================================================

        signal = analyze_pair(
            df,
            pair
        )

        if signal is None:

            print(
                f"{pair}: no qualifying signal."
            )

            continue

        print(
            f"{pair}: SIGNAL FOUND"
        )

        print(
            f"Side: {signal['side']}"
        )

        print(
            f"Score: {signal['score']}"
        )

        print(
            f"Threshold: "
            f"{CONFIG[pair]['threshold']}"
        )

        print(
            f"RR: {signal['rr']}R"
        )

        # ====================================================
        # DUPLICATE PROTECTION
        # ====================================================

        last_key = (
            state[
                "last_alert_keys"
            ].get(pair)
        )

        if last_key == signal[
            "candle_key"
        ]:

            print(
                f"{pair}: duplicate signal "
                "already alerted."
            )

            continue

        # ====================================================
        # BUILD SETUP TELEGRAM
        # ====================================================

        alert = build_alert(

            pair=pair,

            direction=
                signal["direction"],

            score=
                signal["score"],

            current_score=
                signal["current_score"],

            advanced_score=
                signal["advanced_score"],

            entry=
                signal["entry"],

            stop=
                signal["stop"],

            tp1=
                signal["tp1"],

            tp2=
                signal["tp2"],

            rr=
                signal["rr"],

            candle_time=
                signal["candle_time"]
        )

        message_id = send_telegram(
            alert
        )

        # ====================================================
        # SAVE ALERT KEY
        # ====================================================

        state[
            "last_alert_keys"
        ][pair] = signal[
            "candle_key"
        ]

        # ====================================================
        # CREATE WAITING-ENTRY TRADE
        # ====================================================

        if message_id:

            state["pending"].append({

                "pair":
                    pair,

                "message_id":
                    int(message_id),

                "direction":
                    int(
                        signal["direction"]
                    ),

                "side":
                    signal["side"],

                "score":
                    signal["score"],

                "current_score":
                    signal["current_score"],

                "advanced_score":
                    signal["advanced_score"],

                "entry":
                    signal["entry"],

                "stop":
                    signal["stop"],

                "tp1":
                    signal["tp1"],

                "tp2":
                    signal["tp2"],

                "rr":
                    signal["rr"],

                "candle_time":
                    signal["candle_time"],

                "candle_key":
                    signal["candle_key"],

                "status":
                    "WAITING_ENTRY",

                "entry_hit":
                    False,

                "entry_hit_time":
                    None,

                "entry_hit_index":
                    None,

                "tp1_hit":
                    False,

                "tp1_time":
                    None,

                "last_checked_index":
                    len(df) - 1
            })

        # ====================================================
        # SHEET — NEW SETUP
        # ====================================================

        send_to_sheet({

            "action":
                "NEW_SIGNAL",

            "status":
                "WAITING_ENTRY",

            "pair":
                pair,

            "side":
                signal["side"],

            "score":
                signal["score"],

            "current_score":
                signal["current_score"],

            "advanced_score":
                signal["advanced_score"],

            "threshold":
                CONFIG[pair][
                    "threshold"
                ],

            "entry":
                signal["entry"],

            "stop":
                signal["stop"],

            "tp1":
                signal["tp1"],

            "tp2":
                signal["tp2"],

            "rr":
                signal["rr"],

            "candle_time":
                signal["candle_time"]
        })

        print(
            f"{pair}: setup processed."
        )

    # ========================================================
    # SAVE STATE
    # ========================================================

    save_state(
        state
    )

    print("\n")

    print("=" * 70)

    print(
        "SCAN COMPLETE"
    )

    print(
        f"Pending trades: "
        f"{len(state['pending'])}"
    )

    print("=" * 70)


if __name__ == "__main__":
    main()
