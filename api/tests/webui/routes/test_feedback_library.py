"""Focused coverage for PowerGrader's shared persona library."""

from api.webui.routes import feedback_library


def test_custom_persona_ids_are_constrained_to_config_safe_names():
    assert feedback_library._valid_persona_id("custom_sage_2") is True
    assert feedback_library._valid_persona_id("Custom Sage") is False
