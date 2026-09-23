"""Routines configuration and the configured school-day exclusions.

Uses lazy module-reference so monkeypatches to config._io propagate correctly.
"""
from datetime import date

from . import _io as _io_mod


def get_routine_states() -> dict:
    return _io_mod._machine_load().get("routines", {})


def set_routine_state(routine_id: str, patch: dict):
    def mutate(state):
        routines = state.setdefault("routines", {})
        cur = routines.setdefault(routine_id, {})
        cur.update(patch)

    _io_mod._modify_machine(mutate)


def get_late_sweep_holidays() -> list[str]:
    """Return valid configured holiday dates without exposing other settings."""
    late_sweep = _io_mod._synced_state().get("late_sweep", {})
    values = late_sweep.get("holidays", []) if isinstance(late_sweep, dict) else []
    if not isinstance(values, list):
        return []
    holidays = []
    for value in values:
        try:
            holidays.append(date.fromisoformat(str(value)).isoformat())
        except (TypeError, ValueError):
            continue
    return sorted(set(holidays))
