"""Gradebook tools configuration — extra-time, tier tags.

Uses lazy module-reference so monkeypatches to config._io propagate correctly.
"""
from . import _io as _io_mod

TIER_NAMES = ["Support", "Core", "Accelerate", "Extend"]


def get_extra_time(course_id: str) -> list[dict]:
    return _io_mod._synced_state().get("extra_time", {}).get(str(course_id), [])


def set_extra_time(course_id: str, students: list[dict]):
    _io_mod._modify_synced(
        lambda state: state.setdefault("extra_time", {}).__setitem__(str(course_id), students)
    )


def get_tier_tags() -> dict:
    saved = _io_mod._synced_state().get("tier_tags", {})
    return {name: str(saved.get(name, "")).strip() for name in TIER_NAMES}


def set_tier_tags(tags: dict):
    clean = {name: str(tags.get(name, "")).strip() for name in TIER_NAMES}
    _io_mod._modify_synced(lambda state: state.__setitem__("tier_tags", clean) or state)
