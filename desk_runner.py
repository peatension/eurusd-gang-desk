"""
Same Telegram bot. Two engines. Two files.

  3c_model.py   = 3C Model (hybrid M5 sweep)
  crt_plain.py  = CRT plain (same-TF)

Run this file on the schedule.
"""

import importlib

model = importlib.import_module("3c_model")
import crt_plain


def main():
    model.process_commands(model.load_state())
    model.main()
    crt_plain.main()


if __name__ == "__main__":
    main()
