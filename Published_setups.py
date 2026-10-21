"""Shared persistent at-most-once signal ledger for CRT Plain and 3C.

The ledger identity is pair + direction + UTC C2 minute + entry identity.
It is deliberately not keyed by engine/stream, so the same setup cannot be
published again by another stream or engine. A new C2 minute or a distinct
entry price creates a different key and remains eligible.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

LEDGER_FILE = os.getenv("PUBLISHED_SETUPS_FILE", "published_setups.json")

def _floor_minute(value):
    try:
        import pandas as pd
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts.floor("min").strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value or "unknown").replace("T", " ").replace("Z", "")[:16]

def _entry_id(entry, pip):
    try:
        unit = abs(float(pip)) * 0.1
        if unit <= 0:
            return "unknown"
        return str(int(round(float(entry) / unit)))
    except Exception:
        return "unknown"

def setup_key(pair, direction, c2_time, entry, pip):
    return f"{str(pair).upper()}|{int(direction or 0)}|{_floor_minute(c2_time)}|entry:{_entry_id(entry, pip)}"

def _read():
    try:
        with open(LEDGER_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("setups"), dict):
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"published_setups read warning: {exc}")
    return {"version": 1, "setups": {}}

def _write(data):
    path = Path(LEDGER_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)

def claim(pair, direction, c2_time, entry, pip, *, engine="", stream=""):
    """Persist a claim before Telegram send. False means already claimed."""
    data = _read()
    setups = data.setdefault("setups", {})
    key = setup_key(pair, direction, c2_time, entry, pip)
    if key in setups:
        return False, key
    now = datetime.now(timezone.utc)
    # Keep a rolling 180-day ledger; keys are setup-scoped, never symbol-scoped.
    cutoff = now - timedelta(days=180)
    for old_key, record in list(setups.items()):
        try:
            created = datetime.fromisoformat(str(record.get("claimed_at", "")).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created < cutoff:
                setups.pop(old_key, None)
        except Exception:
            continue
    setups[key] = {
        "claimed_at": now.isoformat(),
        "engine": str(engine or ""),
        "stream": str(stream or ""),
        "pair": str(pair),
        "direction": int(direction or 0),
        "c2_minute": _floor_minute(c2_time),
        "entry_id": _entry_id(entry, pip),
        "status": "claimed_before_send",
    }
    _write(data)
    return True, key

def mark_sent(key):
    data = _read()
    record = data.setdefault("setups", {}).get(key)
    if record is None:
        return False
    record["status"] = "sent"
    record["sent_at"] = datetime.now(timezone.utc).isoformat()
    _write(data)
    return True
