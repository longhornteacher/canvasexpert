"""MCP adapter for the canonical Web UI roster mutation use case."""

import json

from api.platform_services import config
from .routes import roster as roster_routes


def update_student(course_id: str, user_id: str, patch: dict, vault) -> dict:
    """Call the exact route updater with the route's injected dependencies."""
    return roster_routes.roster_updates.update_student(
        course_id, user_id, json.dumps(patch),
        vault_factory=lambda: vault,
        get_extra_time=config.get_extra_time,
        set_extra_time=config.set_extra_time,
        set_monitored_student=config.set_monitored_student,
        remove_monitored_student=config.remove_monitored_student,
        update_roster_student_settings=config.update_roster_student_settings,
        validate_classroom_profile=config.validate_classroom_profile,
        as_int=roster_routes._as_int,
        # The replacing nickname path is unreachable from this adapter, not just
        # from the MCP tool that currently calls it. set_nicknames overwrites the
        # teacher's scrub-coverage list, and a caller that skipped the tool-layer
        # validator would otherwise be able to erase it through here.
        allowed_keys=(roster_routes.ALLOWED_STUDENT_PATCH_KEYS - {"nicknames"}) | {"add_nicknames"},
    )
