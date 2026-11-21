"""
Lightweight Telegram command/callback processor.
Run on a short cron independently of the market scan.

Does NOT fetch market data or send trade signals.
Only: getUpdates → handlers → persist offset + subscribers + CRT state.

LIVE PATH:
  CWD = tension-crt/ so relative paths land next to the engine.
  Uses tension-crt/crt_infra.py handlers + crt_engine load/save_state.
  NOT 3c_model / desk_runner / SMC.

CRITICAL:
  crt_infra.py is a legacy multi-engine file with its own save_state
  writing last_alert_state_crt_set2.json. We force process_commands to
  use crt_engine.save_state → last_alert_state_crt.json.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TCRT = ROOT / "tension-crt"
if not TCRT.is_dir():
    raise SystemExit(f"FATAL: tension-crt/ missing at {TCRT}")

# All relative state files must resolve under tension-crt/
os.chdir(TCRT)
sys.path.insert(0, str(TCRT))

import crt_infra as desk  # noqa: E402
from crt_engine import load_state, save_state  # noqa: E402


def main():
    print("=" * 60)
    print("TENSION CRT — Telegram commands only")
    print(f"cwd: {Path.cwd()}")
    print(f"infra: {TCRT}")
    print("=" * 60)

    if not getattr(desk, "BOT_TOKEN", None):
        print("Missing BOT_TOKEN / TELEGRAM_BOT_TOKEN — abort")
        return

    # Force every internal save_state/load_state inside process_commands
    # to use the LIVE CRT engine files (not set2 / 3c leftovers).
    desk.save_state = save_state
    desk.load_state = load_state
    # process_commands resolves bare `save_state` via module globals
    desk.__dict__["save_state"] = save_state
    desk.__dict__["load_state"] = load_state

    state = load_state()
    print(f"state version={state.get('version')} pending={len(state.get('pending') or [])}")
    print(f"offset file={getattr(desk, 'TELEGRAM_OFFSET_FILE', '?')}")
    print(f"admin_id set={bool(getattr(desk, 'ADMIN_CHAT_ID', '') or '')}")

    try:
        desk.process_commands(state)
        print("process_commands OK")
    except Exception as e:
        print("process_commands error:", e)
        import traceback
        traceback.print_exc()

    try:
        save_state(state)
        print("save_state OK")
    except Exception as e:
        print("save_state error:", e)

    print("Command pass complete.")


if __name__ == "__main__":
    main()
