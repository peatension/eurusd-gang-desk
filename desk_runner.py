"""
Same Telegram bot. Two CRT engines (8 pairs).

  crt_plain.py  → CRT …  EUR/USD AUD/USD USD/CHF EUR/JPY
  3c_model.py   → 3C …   GBP/USD USD/JPY USD/CAD GBP/JPY
                  (also shared fetch/Telegram for crt_plain)

Run: python desk_runner.py
"""

import importlib

desk = importlib.import_module("3c_model")
import crt_plain


def main():
    state = desk.load_state()
    desk.process_commands(state)
    desk.save_state(state)

    crt_plain.main()   # set 1 — CRT …
    desk.main()        # set 2 — 3C …


if __name__ == "__main__":
    main()
