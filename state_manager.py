import json
import os

STATE_FILE = "state.json"

def load_state():
    """Reads saved trade states from state.json."""
    if not os.path.exists(STATE_FILE):
        return {"trades": {}}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading state: {e}")
        return {"trades": {}}

def save_state(data):
    """Saves updated trade states back to state.json."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error saving state: {e}")

def register_trade(symbol, message_id, action, entry_min, entry_max, tp1, tp2, sl):
    """Stores a brand new trade setup when a signal is sent."""
    state = load_state()
    state["trades"][symbol] = {
        "message_id": message_id,
        "action": action,
        "entry_min": entry_min,
        "entry_max": entry_max,
        "tp1": tp1,
        "tp2": tp2,
        "sl": sl,
        "status": "PENDING",  # PENDING -> TRIGGERED -> TP1_HIT -> TP2_HIT / SL_HIT
        "sl_moved_to_be": False
    }
    save_state(state)

def update_trade_status(symbol, new_status, sl_moved_to_be=False):
    """Updates the status and stop-loss state of an active trade."""
    state = load_state()
    if symbol in state["trades"]:
        state["trades"][symbol]["status"] = new_status
        if sl_moved_to_be:
            state["trades"][symbol]["sl_moved_to_be"] = True
        save_state(state)

def get_active_trade(symbol):
    """Retrieves the current active trade for a pair, if any exists."""
    state = load_state()
    trade = state["trades"].get(symbol)
    if trade and trade.get("status") not in ["CLOSED", "SL_HIT", "TP2_HIT"]:
        return trade
    return None
