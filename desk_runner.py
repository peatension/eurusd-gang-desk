"""
Same Telegram bot. Live entrypoint.

CRT GUIDE ENGINE (guide-locked, no research matrix):
  Pairs: EUR/USD · GBP/USD · AUD/USD · USD/JPY
  Models: 2C · 3C · MULTI
  HTF: H1 | H4 → LTF M15
  Filters: body close · London/NY only · one-touch range

Optional: set RUN_SLIM=1 to also run legacy live_slim after guide.

Run: python desk_runner.py
"""

import os


def main():
    import crt_guide_engine

    crt_guide_engine.main()

    if os.getenv("RUN_SLIM", "").strip() in ("1", "true", "TRUE", "yes"):
        print("\n[desk] RUN_SLIM=1 → legacy live_slim")
        import live_slim

        live_slim.main()


if __name__ == "__main__":
    main()
