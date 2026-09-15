"""Built-in differentiated bridge grade-sync Routine example."""

import json

from api.platform_services import config
from api.webui.routes import routines, routines_builtin


def test_builtin_bridge_sync_runs_each_registered_family_with_safe_counts(monkeypatch):
    monkeypatch.setattr(config, "active_courses", lambda: [{"id": "course-1"}])
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course_id: [
        {"family_title": "Invented Check"},
    ])
    calls = []

    def preview(course_id, family_title, *, write_origin):
        calls.append(("preview", course_id, family_title, write_origin))
        return {
            "ok": True, "operation_id": "operation-1",
            "batch_id": "batch-1", "review_digest": "digest-1",
        }

    def apply(operation_id, batch_id, review_digest):
        calls.append(("apply", operation_id, batch_id, review_digest))
        return {
            "ok": True,
            "counts": {
                "copied_scores": 1,
                "copied_excused": 0,
                "missing_zeroes": 1,
                "cleared_prior_values": 0,
                "already_matching": 1,
                "held": 0,
                "conflicting_final_values": 0,
            },
        }

    monkeypatch.setattr(routines_builtin.sis_grade_bridge, "preview_sis_grade_bridge", preview)
    monkeypatch.setattr(routines_builtin.sis_grade_bridge, "apply_sis_grade_bridge", apply)

    result = routines_builtin._run_routine_sis_bridge_sync({})

    assert result == {
        "ok": True,
        "lines": [
            "✓ Invented Check: 1 copied, 0 excused, 1 missing zeroes, "
            "0 cleared, 1 matching, 0 held, 0 conflicting"
        ],
        "summary": "1 families: 1 copied, 1 missing zeroes, 0 cleared, 1 matching, 0 held, 0 conflicting",
        "counts": {
            "families": 1,
            "copied_scores": 1,
            "copied_excused": 0,
            "missing_zeroes": 1,
            "cleared_prior_values": 0,
            "already_matching": 1,
            "held": 0,
            "conflicting_final_values": 0,
            "attention_families": 0,
        },
    }
    assert calls == [
        ("preview", "course-1", "Invented Check", "routine"),
        ("apply", "operation-1", "batch-1", "digest-1"),
    ]
    assert "student" not in json.dumps(result).lower()


def test_builtin_bridge_sync_registration_is_default_off_write_routine():
    definition = routines._ROUTINE_DEFS["sis_bridge_sync"]

    assert definition == {
        "label": "Differentiated bridge grade sync",
        "writes": True,
        "default": {"enabled": False, "every_hours": 24, "params": {}},
    }
    assert routines._ROUTINE_RUNNERS["sis_bridge_sync"] is routines_builtin._run_routine_sis_bridge_sync


def test_builtin_bridge_sync_reports_blocked_family_and_continues(monkeypatch):
    monkeypatch.setattr(config, "active_courses", lambda: [{"id": "course-1"}])
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course_id: [
        {"family_title": "Blocked Family"},
        {"family_title": "Safe Family"},
    ])

    def preview(_course_id, family_title, *, write_origin):
        assert write_origin == "routine"
        if family_title == "Blocked Family":
            return {"ok": False, "error": "registered_bridge_drift"}
        return {
            "ok": True, "operation_id": "operation-2",
            "batch_id": "batch-2", "review_digest": "digest-2",
        }

    monkeypatch.setattr(routines_builtin.sis_grade_bridge, "preview_sis_grade_bridge", preview)
    monkeypatch.setattr(
        routines_builtin.sis_grade_bridge,
        "apply_sis_grade_bridge",
        lambda *_args: {"ok": True, "counts": {"already_matching": 1}},
    )

    result = routines_builtin._run_routine_sis_bridge_sync({})

    assert result["ok"] is False
    assert len(result["lines"]) == 2
    assert result["lines"][0] == "✗ Blocked Family: registered_bridge_drift"
    assert result["lines"][1].startswith("✓ Safe Family:")
