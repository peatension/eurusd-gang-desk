"""
Same Telegram bot. Live entrypoint.

Default = LIVE SLIM (4 pairs, credit-safe):
  EUR/USD + XAU/USD (CRT) | USD/JPY + USD/CAD (3C)

Full engines stay on disk (crt_plain.py / 3c_model.py) — not spoiled.
To resume full 8–9 pair desk later, swap main() body back to:
    crt_plain.main(); desk.main()

Run: python desk_runner.py
"""

import live_slim


def main():
    live_slim.main()


if __name__ == "__main__":
    main()
