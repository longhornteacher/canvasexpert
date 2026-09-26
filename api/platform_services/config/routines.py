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


def get_no_school_dates() -> list[str]:
    """Return valid configured no-school dates, sorted and deduplicated."""
    values = _io_mod._synced_state().get("no_school_dates", [])
    if not isinstance(values, list):
        return []
    dates = []
    for value in values:
        try:
            dates.append(date.fromisoformat(str(value)).isoformat())
        except (TypeError, ValueError):
            continue
    return sorted(set(dates))


def set_no_school_dates(dates: list[str]):
    valid = []
    for value in (dates or []):
        try:
            valid.append(date.fromisoformat(str(value)).isoformat())
        except (TypeError, ValueError):
            continue
    _io_mod._save_synced_key("no_school_dates", sorted(set(valid)))
