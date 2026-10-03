"""Shared routine definitions and read-only scheduling state."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from api.platform_services import config


ROUTINE_DEFS = {
    "download": {
        "label": "Auto-download new student work",
        "writes": False,
        "default": {"enabled": False, "every_hours": 24,
                    "params": {"window_days": 14}},
    },
    "curve": {
        "label": "Auto-curve low assignment averages",
        "writes": True,
        "default": {"enabled": False, "every_hours": 168,
                    "params": {"floor": 80, "mode": "flag", "window_days": 30}},
    },
    "student_reports": {
        "label": "Refresh monitored-student reports",
        "writes": False,
        "default": {"enabled": False, "every_hours": 168,
                    "params": {}},
    },
    "sis_bridge_sync": {
        "label": "Differentiated bridge grade sync",
        "writes": True,
        "default": {"enabled": False, "every_hours": 24, "params": {}},
    },
}


def routine_state(routine_id: str) -> dict:
    saved = config.get_routine_states().get(routine_id, {})
    base = json.loads(json.dumps(ROUTINE_DEFS[routine_id]["default"]))
    base["params"].update(saved.get("params", {}))
    for key in ("enabled", "every_hours", "last_run", "last_summary"):
        if key in saved:
            base[key] = saved[key]
    return base


def routine_due(state: dict) -> bool:
    if not state.get("last_run"):
        return True
    try:
        last = datetime.fromisoformat(state["last_run"])
        hours = state.get("every_hours", 24)
        return datetime.now() >= last + timedelta(hours=hours)
    except (ValueError, TypeError):
        return True
