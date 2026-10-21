"""Command-only runner: polls Telegram commands/buttons without market data fetches."""
import importlib
import json
import os
import traceback

desk = importlib.import_module("3c_model")
COMMAND_STATE_FILE = "telegram_command_state.json"

def _load_command_state():
    try:
        with open(COMMAND_STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _save_command_state(state):
    tmp = COMMAND_STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({
            "cmd_guard": state.get("cmd_guard", {}),
            "risk_wait": state.get("risk_wait", {}),
        }, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, COMMAND_STATE_FILE)

def main():
    state = desk.load_state()
    command_state = _load_command_state()
    state["cmd_guard"] = command_state.get("cmd_guard", state.get("cmd_guard", {}))
    state["risk_wait"] = command_state.get("risk_wait", state.get("risk_wait", {}))
    try:
        desk.process_commands(state)
    except Exception:
        traceback.print_exc()
        raise
    finally:
        _save_command_state(state)
        print("Telegram command cycle complete")

if __name__ == "__main__":
    main()
