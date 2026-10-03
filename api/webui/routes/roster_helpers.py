"""Pure roster normalization helpers."""

from __future__ import annotations

def _as_int(value, field_name: str) -> tuple[int | None, str | None]:
    """Best-effort int parsing for form JSON values."""
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, f"{field_name} must be an integer."


def _value_name(value: dict | None, user_id: str) -> str:
    """Return a per-user display name from a bulk value payload."""
    if not isinstance(value, dict):
        return ""
    names = value.get("names")
    if isinstance(names, dict):
        return str(names.get(str(user_id), "") or "")
    return str(value.get("name", "") or "")
