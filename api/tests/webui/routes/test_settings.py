"""Focused tests for the Settings Forge tier-color routes."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from api.platform_services import config
from api.platform_services.config import _io as config_io
from api.webui.server import app
from engine.rendering.forge.palette import PALETTES, DEFAULT_TIER_COLORS


client = TestClient(app)


def test_tier_color_setting_routes_validate_preserve_and_default_without_writes(monkeypatch):
    state = {"tier_colors": {"Support": "not-a-swatch", "Core": "green", "old-tier": "legacy"}}
    writes = []
    monkeypatch.setattr(config_io, "_synced_state", lambda: state)

    def save(mutator):
        writes.append(True)
        mutator(state)
        return state

    monkeypatch.setattr(config_io, "_modify_synced", save)
    before = dict(state["tier_colors"])
    assert config.get_tier_colors() == {
        "Support": DEFAULT_TIER_COLORS["Support"], "Core": "green",
        "Accelerate": "blue", "untiered": "teal",
    }
    assert state["tier_colors"] == before
    assert writes == []
    state["tier_colors"] = {
        "Support": "purple", "Core": "purple", "Accelerate": "blue", "old-tier": "legacy",
    }
    assert config.get_tier_colors() == DEFAULT_TIER_COLORS
    assert writes == []

    getter = client.get("/api/tier-colors")
    assert getter.status_code == 200
    assert getter.json()["tier_colors"]["Support"] == "silver"

    for key in PALETTES:
        result = client.post("/api/tier-colors", data={"colors": json.dumps({
            "Support": "silver", "Core": "red", "Accelerate": "blue", "untiered": key,
        })})
        assert result.json()["ok"] is True
        assert state["tier_colors"]["untiered"] == key

    saved = dict(state["tier_colors"])
    count = len(writes)
    invalid = client.post("/api/tier-colors", data={"colors": json.dumps({
        "Support": "silver", "Core": "red", "Accelerate": "blue", "untiered": "free-hex",
    })})
    assert invalid.json() == {"ok": False, "error": "Choose one of the available swatches for every color."}
    duplicate = client.post("/api/tier-colors", data={"colors": json.dumps({
        "Support": "silver", "Core": "silver", "Accelerate": "blue", "untiered": "teal",
    })})
    assert duplicate.json() == {"ok": False, "error": "Support, Core, and Accelerate must use different colors."}
    missing = client.post("/api/tier-colors", data={"colors": json.dumps({
        "Support": "silver", "Core": "red", "Accelerate": "blue",
    })})
    assert missing.json() == {"ok": False, "error": "Choose a color for every tier and for Untiered and pages."}
    assert len(writes) == count
    assert state["tier_colors"]["old-tier"] == "legacy"
    assert state["tier_colors"] == saved
