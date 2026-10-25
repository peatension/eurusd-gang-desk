"""
SMC Telegram commands only — no market data, no signals.
Run on a short interval (1–5 min) so /start /stop /stats respond fast.
Full signal scan stays on smc_engine.yml (external cron).
"""
from __future__ import annotations

import importlib

smc = importlib.import_module("smc_engine")


def main():
    print("=" * 60)
    print("SMC ENGINE — Telegram commands only")
    print("=" * 60)
    state = smc.load_state()
    try:
        smc.process_commands(state)
    except Exception as e:
        print("process_commands error:", e)
    try:
        smc.save_state(state)
    except Exception as e:
        print("save_state error:", e)
    print(f"Subscribers: {len(smc.load_subscribers())}")
    print("Command pass complete.")


if __name__ == "__main__":
    main()
