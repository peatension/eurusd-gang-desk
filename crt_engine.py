# crt_engine.py
# Tension Trading Desk — High-Frequency CRT Engine (Phase 2.3 Production)
# Motto: Built on Data. / Driven by Discipline.

import os
import numpy as np
import pandas as pd
import requests
from typing import Dict, Optional, Tuple

class HighFrequencyCRTEngine:
    """
    High-Frequency Candle Range Theory (CRT) Engine with Outcome Tracking & Telegram Dispatcher.
    Primary Horizon: M30 (C1)
    Execution Horizon: M5 (Colab) / M1-M3 (Live)
    Final 5 Pure Forex Universe: USD/JPY, EUR/JPY, AUD/USD, EUR/USD, GBP/AUD
    """
    
    PORTFOLIO_CONFIG = {
        "USD/JPY": {"rr_gate": 3.0, "tag": "HIGH-EXP", "broker_prefix": "OANDA:USDJPY", "desc": "High-Expectancy JPY Expansion Driver"},
        "EUR/JPY": {"rr_gate": 3.0, "tag": "HIGH-EXP", "broker_prefix": "OANDA:EURJPY", "desc": "High-Expectancy JPY Expansion Driver"},
        "GBP/AUD": {"rr_gate": 2.0, "tag": "HIGH-VOL", "broker_prefix": "OANDA:GBPAUD", "desc": "High-Beta Volume Engine"},
        "EUR/USD": {"rr_gate": 2.0, "tag": "CORE-FX",  "broker_prefix": "OANDA:EURUSD", "desc": "Core Major Benchmark"},
        "AUD/USD": {"rr_gate": 2.0, "tag": "CORE-FX",  "broker_prefix": "OANDA:AUDUSD", "desc": "High Win-Rate Major Engine"}
    }

    def __init__(self, account_balance: float = 10000.0, risk_pct: float = 0.01, sl_pip_offset: float = 1.5, atr_mult: float = 0.25):
        self.account_balance = account_balance
        self.risk_pct = risk_pct
        self.risk_amount = account_balance * risk_pct
        self.sl_pip_offset = sl_pip_offset
        self.atr_mult = atr_mult

    def get_c1_levels(self, df_m30: pd.DataFrame) -> Tuple[float, float, float]:
        """Extracts C1 High, Low, and 50% Equilibrium level from previous completed M30 candle."""
        prev = df_m30.iloc[-2]
        c1_high = float(prev["high"])
        c1_low = float(prev["low"])
        c1_mid = (c1_high + c1_low) / 2.0
        return c1_high, c1_low, c1_mid

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculates Average True Range (ATR) on the entry timeframe."""
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

    def calculate_lot_size(self, pair: str, risk_pips: float) -> float:
        """Calculates standard lot size based on fixed dollar risk."""
        if risk_pips <= 0:
            return 0.01
        pip_value_usd = 10.0 if "USD" in pair.split("/")[1] else 8.5  
        lots = round(self.risk_amount / (risk_pips * pip_value_usd), 2)
        return max(0.01, lots)

    def scan_signal(
        self, 
        pair: str, 
        df_m30: pd.DataFrame, 
        df_entry: pd.DataFrame
    ) -> Optional[Dict[str, object]]:
        """Scans for active boundary sweeps and returns an initial signal ticket."""
        if pair not in self.PORTFOLIO_CONFIG:
            return None

        cfg = self.PORTFOLIO_CONFIG[pair]
        rr_gate = cfg["rr_gate"]
        tag = cfg["tag"]
        tv_symbol = cfg["broker_prefix"]

        c1_high, c1_low, c1_mid = self.get_c1_levels(df_m30)
        curr = df_entry.iloc[-1]
        
        pip_factor = 0.01 if "JPY" in pair else 0.0001
        decimals = 3 if "JPY" in pair else 5
        
        atr_val = self.calculate_atr(df_entry)
        sl_buffer = max(self.sl_pip_offset * pip_factor, self.atr_mult * atr_val)

        # Bullish Sweep (Low breached, Close reclaimed above C1 Low)
        if curr["low"] < c1_low and curr["close"] > c1_low:
            entry_price = c1_low
            stop_loss = curr["low"] - sl_buffer
            risk = entry_price - stop_loss
            risk_pips = risk / pip_factor
            tp1 = c1_mid
            tp2 = c1_high

            if risk > 0 and (tp1 - entry_price) / risk >= rr_gate:
                lots = self.calculate_lot_size(pair, risk_pips)
                tv_url = f"https://www.tradingview.com/chart/?symbol={tv_symbol}"
                return self._build_pro_ticket(
                    pair=pair, order_type="BUY_LIMIT", tag=tag, 
                    entry=entry_price, sl=stop_loss, tp1=tp1, tp2=tp2, 
                    risk=risk, risk_pips=risk_pips, lots=lots, tv_url=tv_url, decimals=decimals
                )

        # Bearish Sweep (High breached, Close reclaimed below C1 High)
        elif curr["high"] > c1_high and curr["close"] < c1_high:
            entry_price = c1_high
            stop_loss = curr["high"] + sl_buffer
            risk = stop_loss - entry_price
            risk_pips = risk / pip_factor
            tp1 = c1_mid
            tp2 = c1_low

            if risk > 0 and (entry_price - tp1) / risk >= rr_gate:
                lots = self.calculate_lot_size(pair, risk_pips)
                tv_url = f"https://www.tradingview.com/chart/?symbol={tv_symbol}"
                return self._build_pro_ticket(
                    pair=pair, order_type="SELL_LIMIT", tag=tag, 
                    entry=entry_price, sl=stop_loss, tp1=tp1, tp2=tp2, 
                    risk=risk, risk_pips=risk_pips, lots=lots, tv_url=tv_url, decimals=decimals
                )

        return None

    def evaluate_trade_outcome(self, active_ticket: Dict[str, object], df_live: pd.DataFrame) -> Dict[str, object]:
        """Monitors active signals against price action to evaluate outcomes."""
        ticket = active_ticket.copy()
        entry = ticket["entry_price"]
        sl = ticket["stop_loss"]
        tp1 = ticket["tp1_equilibrium"]
        tp2 = ticket["tp2_expansion"]
        order_type = ticket["order_type"]

        latest_candle = df_live.iloc[-1]
        high = float(latest_candle["high"])
        low = float(latest_candle["low"])

        if order_type == "BUY_LIMIT":
            if low <= sl:
                ticket["status"] = "CLOSED"
                ticket["verdict"] = "🛑 STOP LOSS HIT (-1.00R)"
                ticket["net_r"] = -1.00
                ticket["pnl_usd"] = f"-${self.risk_amount:.2f}"
            elif high >= tp2:
                rr_val = round((tp2 - entry) / (entry - sl), 2)
                ticket["status"] = "CLOSED"
                ticket["verdict"] = f"🎯 FULL TP2 HIT (+{rr_val}R)"
                ticket["net_r"] = rr_val
                ticket["pnl_usd"] = f"+${self.risk_amount * rr_val:.2f}"
            elif high >= tp1:
                rr_val = round((tp1 - entry) / (entry - sl), 2)
                ticket["status"] = "PARTIAL_CLOSED"
                ticket["verdict"] = f"📈 TP1 REACHED (+{rr_val}R)"
                ticket["net_r"] = rr_val
                ticket["pnl_usd"] = f"+${self.risk_amount * rr_val:.2f}"

        elif order_type == "SELL_LIMIT":
            if high >= sl:
                ticket["status"] = "CLOSED"
                ticket["verdict"] = "🛑 STOP LOSS HIT (-1.00R)"
                ticket["net_r"] = -1.00
                ticket["pnl_usd"] = f"-${self.risk_amount:.2f}"
            elif low <= tp2:
                rr_val = round((entry - tp2) / (sl - entry), 2)
                ticket["status"] = "CLOSED"
                ticket["verdict"] = f"🎯 FULL TP2 HIT (+{rr_val}R)"
                ticket["net_r"] = rr_val
                ticket["pnl_usd"] = f"+${self.risk_amount * rr_val:.2f}"
            elif low <= tp1:
                rr_val = round((entry - tp1) / (sl - entry), 2)
                ticket["status"] = "PARTIAL_CLOSED"
                ticket["verdict"] = f"📈 TP1 REACHED (+{rr_val}R)"
                ticket["net_r"] = rr_val
                ticket["pnl_usd"] = f"+${self.risk_amount * rr_val:.2f}"

        return ticket

    def _build_pro_ticket(
        self, pair: str, order_type: str, tag: str, 
        entry: float, sl: float, tp1: float, tp2: float, 
        risk: float, risk_pips: float, lots: float, tv_url: str, decimals: int
    ) -> Dict[str, object]:
        rr_tp1 = round(abs(tp1 - entry) / risk, 2)
        rr_tp2 = round(abs(tp2 - entry) / risk, 2)

        return {
            "symbol": pair,
            "tag": f"[{tag}]",
            "verdict": "⚡ ACTIVE SETUP",
            "order_type": order_type,
            "entry_price": round(entry, decimals),
            "stop_loss": round(sl, decimals),
            "risk_pips": round(risk_pips, 1),
            "recommended_lots": lots,
            "risk_usd": f"${self.risk_amount:.2f}",
            "tp1_equilibrium": round(tp1, decimals),
            "tp2_expansion": round(tp2, decimals),
            "rr_tp1": f"+{rr_tp1}R",
            "rr_tp2": f"+{rr_tp2}R",
            "net_r": 0.0,
            "pnl_usd": "$0.00",
            "tradingview_link": tv_url,
            "status": "ACTIVE_PENDING"
        }

    def format_terminal_output(self, ticket: Dict[str, object]) -> str:
        """Outputs an executive trade receipt for terminal logs."""
        header_line = "=================================================="
        return f"""
{header_line}
⚡ TTD ORDER TICKET | {ticket['symbol']} {ticket['tag']}
{header_line}
• Status        : {ticket['status']}
• Final Verdict : {ticket['verdict']}
• Realized PnL  : {ticket['pnl_usd']} ({ticket['net_r']}R)
• Action        : {ticket['order_type']}
• Entry Limit   : {ticket['entry_price']}
• Stop Loss     : {ticket['stop_loss']} ({ticket['risk_pips']} pips)
• Position Size : {ticket['recommended_lots']} Lots
--------------------------------------------------
• TP1 (Eq Mid)  : {ticket['tp1_equilibrium']} ({ticket['rr_tp1']})
• TP2 (Full Range): {ticket['tp2_expansion']} ({ticket['rr_tp2']})
--------------------------------------------------
🔗 TradingView  : {ticket['tradingview_link']}
{header_line}
"""

    def format_telegram_signal(self, ticket: Dict[str, object]) -> str:
        """Formats an executive Telegram signal with situational emojis using HTML mode."""
        symbol = ticket.get("symbol", "EUR/USD")
        order_type = ticket.get("order_type", "BUY_LIMIT")
        tag = ticket.get("tag", "[CORE-FX]")
        
        action_emoji = "🟢 <b>BUY LIMIT SWEEP</b>" if "BUY" in order_type else "🔴 <b>SELL LIMIT SWEEP</b>"
        
        entry = ticket.get("entry_price")
        sl = ticket.get("stop_loss")
        tp1 = ticket.get("tp1_equilibrium")
        tp2 = ticket.get("tp2_expansion")
        risk_pips = ticket.get("risk_pips")
        lots = ticket.get("recommended_lots")
        tv_link = ticket.get("tradingview_link")

        return f"""<b>TENSION TRADING DESK</b> {tag}
━━━━━━━━━━━━━━━━━━━━

🚨 <b>CRT LIQUIDITY SWEEP DETECTED</b>

<b>PAIR:</b> <code>{symbol}</code>
<b>ACTION:</b> {action_emoji}

🎯 <b>ENTRY:</b> <code>{entry}</code>
🛡️ <b>SL:</b>    <code>{sl}</code> ({risk_pips} pips)
⚖️ <b>TP1:</b>   <code>{tp1}</code> (Equilibrium)
🚀 <b>TP2:</b>   <code>{tp2}</code> (Expansion)

💰 <b>RISK SIZE:</b> <code>{lots} Lots</code>
━━━━━━━━━━━━━━━━━━━━
📊 <a href="{tv_link}">Open Live TradingView Chart</a>"""

    def send_telegram_broadcast(self, ticket: Dict[str, object], bot_token: str, chat_id: str) -> bool:
        """Transmits the formatted signal ticket directly to Telegram."""
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": self.format_telegram_signal(ticket),
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            res_data = response.json()
            return res_data.get("ok", False)
        except Exception as e:
            print(f"Error broadcasting to Telegram: {e}")
            return False


if __name__ == "__main__":
    # Environment execution runner for GitHub Actions workflow integration
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_KEY")
    CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    print("⚡ TTD CRT Engine initialized successfully.")
    print("Ready for live market polling loops via GitHub Actions.")
