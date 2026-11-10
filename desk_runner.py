"""
Same Telegram bot. Live entrypoint.

LIVE SLIM (pre-guide setup):
  CRT Plain: XAU/USD · AUD/JPY  — MSS + CISD
  3C:        USD/JPY · EUR/JPY  — MSS + CISD

Guide engine is OFF — not imported.

Run: python desk_runner.py
"""


def main():
    import live_slim

    live_slim.main()


if __name__ == "__main__":
    main()
