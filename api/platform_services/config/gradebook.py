"""Gradebook tools configuration — extra-time, tier tags.

Uses lazy module-reference so monkeypatches to config._io propagate correctly.
"""
from . import _io as _io_mod
from engine.rendering.forge.palette import DEFAULT_TIER_COLORS, PALETTES

TIER_NAMES = ["Support", "Core", "Accelerate"]


def get_extra_time(course_id: str) -> list[dict]:
    return _io_mod._synced_state().get("extra_time", {}).get(str(course_id), [])


def set_extra_time(course_id: str, students: list[dict]):
    _io_mod._modify_synced(
        lambda state: state.setdefault("extra_time", {}).__setitem__(str(course_id), students)
    )


def get_grading_policy(course_id: str) -> dict | None:
    policies = _io_mod._synced_state().get("grading_policy", {})
    if not isinstance(policies, dict):
        return None
    value = policies.get(str(course_id))
    return dict(value) if isinstance(value, dict) else None


def set_grading_policy(course_id: str, value: dict | None):
    def update(state):
        policies = state.setdefault("grading_policy", {})
        if not isinstance(policies, dict):
            policies = {}
            state["grading_policy"] = policies
        if value is None:
            policies.pop(str(course_id), None)
        else:
            policies[str(course_id)] = dict(value)
        return state

    _io_mod._modify_synced(update)


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


def get_tier_colors() -> dict:
    saved = _io_mod._synced_state().get("tier_colors", {})
    if not isinstance(saved, dict):
        saved = {}
    colors = {
        name: saved.get(name) if isinstance(saved.get(name), str) and saved.get(name) in PALETTES else default
        for name, default in DEFAULT_TIER_COLORS.items()
    }
    tier_values = [colors[name] for name in TIER_NAMES]
    if len(set(tier_values)) != len(tier_values):
        for name in TIER_NAMES:
            colors[name] = DEFAULT_TIER_COLORS[name]
    return colors


def set_tier_colors(colors: dict):
    """Validate and save the current swatch choices without dropping old keys."""
    if not isinstance(colors, dict):
        raise ValueError("Tier colors must be an object.")
    missing = [name for name in DEFAULT_TIER_COLORS if name not in colors]
    if missing:
        raise ValueError("Choose a color for every tier and for Untiered and pages.")
    clean = {name: colors[name] for name in DEFAULT_TIER_COLORS}
    invalid = [name for name, color in clean.items()
               if not isinstance(color, str) or color not in PALETTES]
    if invalid:
        raise ValueError("Choose one of the available swatches for every color.")
    if len({clean[name] for name in TIER_NAMES}) != len(TIER_NAMES):
        raise ValueError("Support, Core, and Accelerate must use different colors.")

    def update(state):
        saved = state.get("tier_colors")
        preserved = dict(saved) if isinstance(saved, dict) else {}
        preserved.update(clean)
        state["tier_colors"] = preserved
        return state

    _io_mod._modify_synced(update)
