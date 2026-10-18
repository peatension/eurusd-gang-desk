"""
Same Telegram bot. Live entrypoint.

LIVE SLIM (8 pairs via live_slim.py):
  CRT: XAU/USD · EUR/USD · AUD/JPY · GBP/JPY
  3C : USD/JPY · EUR/JPY · USD/CAD · GBP/USD

Full engines stay on disk (crt_plain.py / 3c_model.py).
live_slim only overrides CORE_PAIRS + STREAMS for credit budget.

Run: python desk_runner.py
"""

import live_slim


def main():
    live_slim.main()


if __name__ == "__main__":
    main()
