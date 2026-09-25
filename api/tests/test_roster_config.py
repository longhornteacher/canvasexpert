"""Offline tests for Roster Console config helpers."""
import pytest

from api.platform_services import config


@pytest.fixture(autouse=True)
def isolated_roster_settings(monkeypatch):
    state = {"roster_student_settings": {}}

    def fake_synced_state():
        return state

    def fake_save_synced_key(key, value):
        state[key] = value

    def fake_modify_synced(mutator):
        updated = mutator(state)
        if updated is not None and updated is not state:
            state.clear()
            state.update(updated)
        return state

    monkeypatch.setattr(config._io, "_synced_state", fake_synced_state)
    monkeypatch.setattr(config._io, "_save_synced_key", fake_save_synced_key)
    monkeypatch.setattr(config._io, "_modify_synced", fake_modify_synced)


def test_get_returns_empty_for_unknown_course():
    result = config.get_roster_student_settings("99999")
    assert result == {}


def test_set_and_get_roundtrip():
    settings = {
        "101": {"classroom_profile": {"birthday": "04-10"}},
        "102": {"classroom_profile": {"birthday": "09-03"}},
    }
    config.set_roster_student_settings("100", settings)

    result = config.get_roster_student_settings("100")
    assert result == settings
    assert config.get_roster_student_settings("200") == {}


def test_tier_tags_expose_three_canonical_keys_and_preserve_retired_storage():
    historical_key = "Ex" + "tend"
    config._io._save_synced_key("tier_tags", {
        "Support": "Silver", "Core": "Red", "Accelerate": "Blue",
        historical_key: "Gold",
    })

    config.set_tier_tags({"Support": "S", "Core": "C", "Accelerate": "A", historical_key: "Changed"})

    assert config.TIER_NAMES == ["Support", "Core", "Accelerate"]
    assert config.get_tier_tags() == {"Support": "S", "Core": "C", "Accelerate": "A"}
    assert config._io._synced_state()["tier_tags"][historical_key] == "Gold"


def test_update_patches_one_student():
    config.set_roster_student_settings("100", {
        "101": {"classroom_profile": {"birthday": "04-10"}},
        "102": {"classroom_profile": {"birthday": "09-03"}},
    })

    config.update_roster_student_settings("100", "101", {"classroom_profile": {"birthday": "05-11"}})

    result = config.get_roster_student_settings("100")
    assert result["101"]["classroom_profile"]["birthday"] == "05-11"
    assert result["102"]["classroom_profile"]["birthday"] == "09-03"


def test_update_adds_new_student():
    config.set_roster_student_settings("100", {"101": {"classroom_profile": {"birthday": "04-10"}}})

    config.update_roster_student_settings("100", "103", {"classroom_profile": {"birthday": "07-13"}})

    result = config.get_roster_student_settings("100")
    assert result["101"]["classroom_profile"]["birthday"] == "04-10"
    assert result["103"]["classroom_profile"]["birthday"] == "07-13"


def test_update_removes_key_when_none():
    config.set_roster_student_settings("100", {
        "101": {"classroom_profile": {"birthday": "04-10"}, "temporary": "value"},
    })

    config.update_roster_student_settings("100", "101", {"temporary": None})

    result = config.get_roster_student_settings("100")
    assert "temporary" not in result["101"]
    assert result["101"]["classroom_profile"]["birthday"] == "04-10"


def test_classroom_profile_round_trip_and_clear_preserves_other_settings():
    profile = {
        "birthday": "09-08",
        "celebrations": [{
            "id": "celebration-1", "label": "Helpful teammate",
            "start": "2026-09-08", "end": "2026-09-12",
        }],
    }
    config.set_roster_student_settings("100", {"101": {"tier": "Support"}})
    config.update_roster_student_settings("100", "101", {"classroom_profile": profile})
    assert config.get_roster_student_settings("100")["101"]["classroom_profile"] == profile

    config.update_roster_student_settings("100", "101", {"classroom_profile": None})
    result = config.get_roster_student_settings("100")["101"]
    assert "classroom_profile" not in result
    assert result["tier"] == "Support"


@pytest.mark.parametrize("profile", [
    {"birthday": "2026-09-08", "celebrations": []},
    {"birthday": "02-30", "celebrations": []},
    {"birthday": "", "celebrations": [{"id": "x", "label": "<b>bad</b>", "start": "2026-09-08", "end": "2026-09-08"}]},
    {"birthday": "", "celebrations": [{"id": "x", "label": "Bad", "start": "2026-09-09", "end": "2026-09-08"}]},
    {"birthday": "", "celebrations": [{"id": "x", "label": "One", "start": "2026-09-08", "end": "2026-09-08"}, {"id": "x", "label": "Two", "start": "2026-09-09", "end": "2026-09-09"}]},
])
def test_classroom_profile_rejects_invalid_values(profile):
    with pytest.raises(ValueError):
        config.validate_classroom_profile(profile)


def test_score_matrix_round_trip_is_course_scoped():
    first = {
        "columns": [{"id": "score-writing", "label": "Writing"}],
        "values_by_section": {"section-a": {"student-a": {"score-writing": 12.5}}},
    }
    second = {
        "columns": [{"id": "score-reading", "label": "Reading"}],
        "values_by_section": {},
    }

    assert config.get_roster_score_matrix("missing") == config.ROSTER_SCORE_MATRIX_DEFAULT
    config.set_roster_score_matrix("course-a", first)
    config.set_roster_score_matrix("course-b", second)

    assert config.get_roster_score_matrix("course-a") == first
    assert config.get_roster_score_matrix("course-b") == second
    assert "roster_score_matrices" in config.SYNCED_KEYS


def test_relationships_round_trip_is_course_scoped():
    first = {"by_section": {"section-a": [{
        "student_a": "student-a", "student_b": "student-b",
        "type": "keep_apart", "reason": "private",
    }]}}
    second = {"by_section": {}}

    assert config.get_roster_relationships("missing") == config.ROSTER_RELATIONSHIPS_DEFAULT
    config.set_roster_relationships("course-a", first)
    config.set_roster_relationships("course-b", second)

    assert config.get_roster_relationships("course-a") == first
    assert config.get_roster_relationships("course-b") == second
    assert "roster_relationships" in config.SYNCED_KEYS


def test_roster_baseline_round_trip_is_course_scoped():
    first = {"acknowledged_at": "2026-08-01T00:00:00", "students": {"101": ["44"]}}
    second = {"acknowledged_at": "2026-08-02T00:00:00", "students": {}}

    assert config.get_roster_baseline("missing") == config.ROSTER_BASELINE_DEFAULT
    config.set_roster_baseline("course-a", first)
    config.set_roster_baseline("course-b", second)

    assert config.get_roster_baseline("course-a") == first
    assert config.get_roster_baseline("course-b") == second
    assert "roster_baselines" in config.SYNCED_KEYS


def test_courses_are_independent():
    config.set_roster_student_settings("100", {"101": {"tier": "Support"}})
    config.set_roster_student_settings("200", {"201": {"tier": "Core"}})

    r1 = config.get_roster_student_settings("100")
    r2 = config.get_roster_student_settings("200")
    assert "201" not in r1
    assert "101" not in r2


def test_replace_overwrites_course():
    config.set_roster_student_settings("100", {"101": {"tier": "Support"}})
    config.set_roster_student_settings("100", {"102": {"tier": "Core"}})

    result = config.get_roster_student_settings("100")
    assert "101" not in result
    assert result["102"]["tier"] == "Core"


def test_valid_tier_names():
    assert config.TIER_NAMES == ["Support", "Core", "Accelerate"]
