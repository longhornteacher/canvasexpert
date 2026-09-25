"""Gradebook tools configuration — extra-time, tier tags.

Uses lazy module-reference so monkeypatches to config._io propagate correctly.
"""
from . import _io as _io_mod

TIER_NAMES = ["Support", "Core", "Accelerate"]


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

    def update(state):
        # Retain stored keys for tiers retired from the live UI. get_tier_tags
        # exposes only the canonical set, but changing current tags must not
        # erase teacher settings that may still belong to historical records.
        saved = state.get("tier_tags")
        preserved = dict(saved) if isinstance(saved, dict) else {}
        preserved.update(clean)
        state["tier_tags"] = preserved
        return state

    _io_mod._modify_synced(update)
