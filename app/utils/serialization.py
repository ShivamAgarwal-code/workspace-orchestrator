"""Recursively converts dataclasses/datetimes/enums into JSON-safe primitives, used both for
AuditLog payloads and the response synthesizer's LLM context."""
import dataclasses
from datetime import datetime
from enum import Enum


def json_safe(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return json_safe(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
