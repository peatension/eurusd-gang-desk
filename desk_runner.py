"""
Same Telegram bot. Live entrypoint.

LIVE SLIM (8 pairs via live_slim.py):
  CRT: XAU/USD 路 EUR/USD 路 AUD/JPY 路 GBP/JPY
  3C : USD/JPY 路 EUR/JPY 路 USD/CAD 路 GBP/USD

Full engines stay on disk (crt_plain.py / 3c_model.py).
live_slim only overrides CORE_PAIRS + STREAMS for credit budget.

Run: python desk_runner.py
"""

import live_slim


def main():
    live_slim.main()


if __name__ == "__main__":
    main()
