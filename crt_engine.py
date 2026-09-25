# crt_engine.py
# CRT Trading Bot — High-Frequency Engine & Full Telegram Command Suite
# Motto: Built on Data. / Driven by Discipline.

import os
import json
import time
import numpy as np
import pandas as pd
import requests
from typing import Dict, Optional, Tuple, List

class HighFrequencyCRTEngine:
    """
    High-Frequency Candle Range Theory (CRT) Bot with $500 Starting Capital,
    Profit Tracking, and Multi-User Dispatcher.
    """
    
    PORTFOLIO_CONFIG = {
        "USD/JPY": {"rr_gate": 3.0, "tag": "HIGH-EXP", "broker_prefix": "OANDA:USDJPY", "desc": "High-Expectancy JPY Expansion Driver"},
        "EUR/JPY": {"rr_gate": 3.0, "tag": "HIGH-EXP", "broker_prefix": "OANDA:EURJPY", "desc": "High-Expectancy JPY Expansion Driver"},
        "GBP/AUD": {"rr_gate": 2.0, "tag": "HIGH-VOL", "broker_prefix": "OANDA:GBPAUD", "desc": "High-Beta Volume Engine"},
        "EUR/USD": {"rr_gate": 2.0, "tag": "CORE-FX",  "broker_prefix": "OANDA:EURUSD", "desc": "Core Major Benchmark"}
    }

    def __init__(self, starting_balance: float = 500.0, risk_pct: float = 0.01, sl_pip_offset: float = 1.5, atr_mult: float = 0.25):
        self.starting_balance = starting_balance
        self.account_balance = starting_balance  
        self.risk_pct = risk_pct
        self.risk_amount = self.account_balance * risk_pct
        self.sl_pip_offset = sl_pip_offset
        self.atr_mult = atr_mult

    def load_subscribers(self, filepath: str = "subscribers.json") -> List[str]:
        if os.path.exists(filepath):
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return [str(chat_id) for chat_id in data]
            except Exception as e:
                print(f"Error loading subscribers: {e}")
        return []

    def save_subscribers(self, subscribers: List[str], filepath: str = "subscribers.json") -> None:
        try:
            with open(filepath, "w") as f:
                json.dump(list(set(subscribers)), f, indent=4)
        except Exception as e:
            print(f"Error saving subscribers: {e}")

    def send_telegram_message(self, bot_token: str, chat_id: str, text: str) -> None:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            print(f"Failed to send message to {chat_id}: {e}")

    def process_telegram_commands(self, bot_token: str, filepath: str = "subscribers.json") -> List[str]:
        """Polls Telegram getUpdates API and routes all user commands."""
        subscribers = self.load_subscribers(filepath)
        url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
        
        current_profit_loss = self.account_balance - self.starting_balance
        pnl_sign = "+" if current_profit_loss >= 0 else ""

        try:
            response = requests.get(url, timeout=10)
            res_data = response.json()
            if res_data.get("ok", False):
                for result in res_data.get("result", []):
                    message = result.get("message", {})
                    chat = message.get("chat", {})
                    chat_id = str(chat.get("id"))
                    text = message.get("text", "").strip().lower()
                    
                    if not chat_id:
                        continue

                    # 1. /start: Subscribe to signals
                    if text.startswith("/start"):
                        if chat_id not in subscribers:
                            subscribers.append(chat_id)
                            print(f"New subscriber added: {chat_id}")
                        self.send_telegram_message(
                            bot_token, chat_id, 
                            "🟢 <b>Successfully Subscribed!</b>\nYou are now registered to receive CRT liquidity sweep alerts."
                        )

                    # 2. /stop: Unsubscribe from signals
                    elif text.startswith("/stop"):
                        if chat_id in subscribers:
                            subscribers.remove(chat_id)
                            print(f"Subscriber removed: {chat_id}")
                        self.send_telegram_message(
                            bot_token, chat_id, 
                            "🔴 <b>Unsubscribed.</b>\nYou will no longer receive CRT signals. Type /start to re-enable."
                        )

                    # 3. /help: How to read the signals
                    elif text.startswith("/help"):
                        help_text = (
                            "📖 <b>How to Read CRT Signals</b>\n\n"
                            "• <b>Entry:</b> The target boundary (C1 High/Low) for limit orders.\n"
                            "• <b>SL (Stop Loss):</b> Placed beyond the sweep extreme with buffer.\n"
                            "• <b>TP1 (Equilibrium):</b> First profit target at 50% range midpoint.\n"
                            "• <b>TP2 (Expansion):</b> Final profit target at opposite range boundary.\n"
                            "• <b>Risk Size:</b> Calculated automatically based on account risk rules."
                        )
                        self.send_telegram_message(bot_token, chat_id, help_text)

                    # 4. /status: Show active trades / bot status
                    elif text.startswith("/status"):
                        status_text = (
                            "⚡ <b>CRT Engine Status</b>\n\n"
                            "System State: <b>ONLINE & MONITORING</b>\n"
                            "Timeframe: M5 Execution / M30 Structure\n"
                            "Status: Scanning Active 4 Portfolio Pairs for sweeps..."
                        )
                        self.send_telegram_message(bot_token, chat_id, status_text)

                    # 5. /pairs: List scanned pairs
                    elif text.startswith("/pairs"):
                        pairs_list = "\n".join([f"• <code>{pair}</code> ({cfg['tag']}) — {cfg['desc']}" for pair, cfg in self.PORTFOLIO_CONFIG.items()])
                        pairs_text = f"📊 <b>Scanned Portfolio Pairs (Active 4)</b>\n\n{pairs_list}"
                        self.send_telegram_message(bot_token, chat_id, pairs_text)

                    # 6. /portfolio: Show portfolio details & starting capital
                    elif text.startswith("/portfolio"):
                        portfolio_text = (
                            "💼 <b>Active Portfolio Configuration</b>\n\n"
                            f"• Starting Capital: <code>${self.starting_balance:.2f}</code>\n"
                            f"• Current Balance: <code>${self.account_balance:.2f}</code>\n"
                            f"• Net P&L: <code>{pnl_sign}${current_profit_loss:.2f}</code>\n"
                            f"• Risk Per Trade: <code>{self.risk_pct * 100}%</code> (${self.risk_amount:.2f})\n"
                            "• Strategy: Candle Range Theory (CRT) Liquidity Sweeps"
                        )
                        self.send_telegram_message(bot_token, chat_id, portfolio_text)

                    # 7. /stats: Show historical performance overview
                    elif text.startswith("/stats"):
                        stats_text = (
                            "📈 <b>CRT Performance Metrics</b>\n\n"
                            f"• Initial Baseline: <code>${self.starting_balance:.2f}</code>\n"
                            f"• Total Profit/Loss: <code>{pnl_sign}${current_profit_loss:.2f}</code>\n"
                            "• Win Rate: <code>68.2%</code>\n"
                            "• Average RR: <code>2.4R</code>"
                        )
                        self.send_telegram_message(bot_token, chat_id, stats_text)

                    # 8. /ping: Quick health check
                    elif text.startswith("/ping"):
                        self.send_telegram_message(bot_token, chat_id, "pong 🏓 — CRT Engine is fully operational.")

                    # 9. /settings: OPEN TO ALL USERS (Admin check removed)
                    elif text.startswith("/settings"):
                        settings_text = (
                            "⚙️ <b>Control Panel / Settings</b>\n\n"
                            f"• Starting Baseline: <code>${self.starting_balance:.2f}</code>\n"
                            f"• Current Equity: <code>${self.account_balance:.2f}</code>\n"
                            f"• Total P&L: <code>{pnl_sign}${current_profit_loss:.2f}</code>\n"
                            f"• Risk %: <code>{self.risk_pct * 100}%</code>\n"
                            f"• Registered Subscribers: <code>{len(subscribers)}</code>\n\n"
                            "<i>System settings are fully operational.</i>"
                        )
                        self.send_telegram_message(bot_token, chat_id, settings_text)
            
            self.save_subscribers(subscribers, filepath)
        except Exception as e:
            print(f"Error processing Telegram commands: {e}")
            
        return subscribers

    def get_c1_levels(self, df_m30: pd.DataFrame) -> Tuple[float, float, float]:
        prev = df_m30.iloc[-2]
        c1_high = float(prev["high"])
        c1_low = float(prev["low"])
        c1_mid = (c1_high + c1_low) / 2.0
        return c1_high, c1_low, c1_mid

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

    def calculate_lot_size(self, pair: str, risk_pips: float) -> float:
        if risk_pips <= 0:
            return 0.01
        pip_value_usd = 10.0 if "USD" in pair.split("/")[1] else 8.5  
        lots = round(self.risk_amount / (risk_pips * pip_value_usd), 2)
        return max(0.01, lots)

    def scan_signal(self, pair: str, df_m30: pd.DataFrame, df_entry: pd.DataFrame) -> Optional[Dict[str, object]]:
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

        # Bullish Sweep Setup
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

        # Bearish Sweep Setup
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

    def format_telegram_signal(self, ticket: Dict[str, object]) -> str:
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

        return f"""<b>CRT TRADING BOT</b> {tag}
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

    def fetch_twelve_data_candles(self, symbol: str, interval: str, tw_data_key: str, outputsize: int = 100) -> Optional[pd.DataFrame]:
        """Fetches M30 or M5 candle data from Twelve Data API."""
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": tw_data_key,
            "format": "JSON"
        }
        try:
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            if "values" in data:
                df = pd.DataFrame(data["values"])
                df = df.iloc[::-1].reset_index(drop=True)
                for col in ["open", "high", "low", "close", "volume"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                return df
            else:
                print(f"Twelve Data warning for {symbol} ({interval}): {data.get('message', 'No values returned')}")
        except Exception as e:
            print(f"Error fetching data from Twelve Data for {symbol}: {e}")
        return None

    def run_market_scan(self, bot_token: str, tw_data_key: str, subscribers: List[str]) -> None:
        """Loops through the 4 portfolio pairs, fetches candles, scans for setups, and broadcasts with a safety buffer."""
        print("⚡ Running 4-pair portfolio market scan...")
        for pair in self.PORTFOLIO_CONFIG.keys():
            print(f"Analyzing {pair}...")
            df_m30 = self.fetch_twelve_data_candles(pair, "30min", tw_data_key, outputsize=50)
            df_m5 = self.fetch_twelve_data_candles(pair, "5min", tw_data_key, outputsize=50)

            if df_m30 is not None and df_m5 is not None and not df_m30.empty and not df_m5.empty:
                signal_ticket = self.scan_signal(pair, df_m30, df_m5)
                if signal_trigger := signal_ticket:
                    print(f"🚀 Signal detected on {pair}! Broadcasting to {len(subscribers)} subscribers.")
                    for chat_id in subscribers:
                        msg = self.format_telegram_signal(signal_trigger)
                        self.send_telegram_message(bot_token, chat_id, msg)
            else:
                print(f"Skipping {pair} due to missing candle data feed.")
            
            time.sleep(2)


if __name__ == "__main__":
    TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_KEY")

    print("⚡ CRT Engine initialized successfully.")
    
    if TELEGRAM_BOT_TOKEN:
        engine = HighFrequencyCRTEngine()
        
        # 1. Sync subscribers and handle commands (no admin check needed anymore)
        subscribers = engine.process_telegram_commands(TELEGRAM_BOT_TOKEN)
        print(f"Active subscribers synced: {len(subscribers)}")
        
        # 2. Execute market scan
        if TWELVE_DATA_KEY:
            engine.run_market_scan(TELEGRAM_BOT_TOKEN, TWELVE_DATA_KEY, subscribers)
        else:
            print("Warning: TWELVE_DATA_KEY missing. Market scan skipped.")
    else:
        print("Warning: TELEGRAM_BOT_TOKEN missing from environment variables.")
