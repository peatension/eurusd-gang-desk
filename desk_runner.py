"""
Same Telegram bot. Two engines. Two files.

  crt_engine.py  = 3C Model (hybrid M5 sweep) — leave running
  crt_plain.py   = CRT plain (same-TF C1 + C2 close-back-inside)

Run this file on the schedule, not both engines separately.
"""

import crt_engine
import crt_plain


def main():
    crt_engine.process_commands(crt_engine.load_state())
    crt_engine.main()
    crt_plain.main()


if __name__ == "__main__":
    main()
