"""Loads the JSON fixtures and resolves their relative day offsets into absolute UTC
timestamps at call time. Both the DB seed script and the mock Google clients import from here,
so "tomorrow's meeting" in the demo is actually tomorrow no matter when the stack is started.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).parent


def _load_json(name: str) -> list[dict]:
    with open(_DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _resolve(base: datetime, offset_days: float, hour: int | None = None, minute: int = 0) -> datetime:
    dt = base + timedelta(days=offset_days)
    if hour is not None:
        dt = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return dt


def load_emails(now: datetime) -> list[dict[str, Any]]:
    resolved = []
    for e in _load_json("emails.json"):
        item = dict(e)
        item["received_at"] = _resolve(now, e["received_offset_days"], e.get("received_hour", 9))
        resolved.append(item)
    return resolved


def load_events(now: datetime) -> list[dict[str, Any]]:
    resolved = []
    for e in _load_json("events.json"):
        item = dict(e)
        start = _resolve(now, e["start_offset_days"], e.get("start_hour", 9), e.get("start_minute", 0))
        item["start_time"] = start
        item["end_time"] = start + timedelta(hours=e.get("duration_hours", 1))
        resolved.append(item)
    return resolved


def load_files(now: datetime) -> list[dict[str, Any]]:
    resolved = []
    for f in _load_json("files.json"):
        item = dict(f)
        item["modified_at"] = _resolve(now, f["modified_offset_days"])
        if "ooo_start_offset_days" in f:
            item["ooo_start"] = _resolve(now, f["ooo_start_offset_days"], hour=0)
            item["ooo_end"] = _resolve(now, f["ooo_end_offset_days"], hour=23, minute=59)
        resolved.append(item)
    return resolved
