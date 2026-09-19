from __future__ import annotations

import json

from api.mcp_server import tools
from api.powergrader import session_store


def test_refresh_mirror_returns_operation_and_revision(monkeypatch, _set_active_courses):
    _set_active_courses(["course-1"])
    monkeypatch.setattr(tools, "_enqueue_sync", lambda *_args, **_kwargs: "op-1")
    monkeypatch.setattr(tools, "_wait_for_plan", lambda *_args, **_kwargs: {
        "plan_id": "op-1", "operation_id": "op-1", "state": "succeeded",
        "jobs": [{"mirror_revision": 7, "snapshot_id": "course-1:7"}],
    })

    result = tools.refresh_mirror("course-1")

    assert result["status"] == "synced"
    assert result["operation_id"] == "op-1"
    assert result["mirror_revision"] == 7
    assert result["snapshot_id"] == "course-1:7"


def test_workspace_reset_requires_unchanged_preview_and_returns_receipt(monkeypatch):
    report = {
        "mode": "dry_run", "status": "planned",
        "counts": {"items": 1, "files": 2, "directories": 1},
        "paths": ["C:/workspace/For AI"], "items": [], "refused": [],
    }
    applied = {**report, "mode": "apply", "status": "applied"}
    calls = []

    def reset(*, apply=False):
        calls.append(apply)
        return applied if apply else report

    monkeypatch.setattr(tools.workspace, "reset_workspace", reset)
    preview = tools.preview_workspace_reset()
    result = tools.apply_workspace_reset(preview["preview_digest"])

    assert result["ok"] is True
    assert result["receipt_id"].startswith("workspace-reset-")
    assert calls == [False, False, True]


def test_scoring_packet_invalidates_session_when_mirror_revision_changes(
    monkeypatch, tmp_path,
):
    session = {
        "session_id": "session-1", "session_kind": "scoring_assignment",
        "course_id": "course-1", "assignment_id": "assignment-1",
        "status": "ready", "mirror_revision": 3,
        "privacy_artifacts": {"safe_bundle": str(tmp_path / "missing.json")},
    }
    monkeypatch.setattr(session_store, "load_session", lambda _sid: session)
    monkeypatch.setattr(session_store, "is_current_session", lambda _sid: True)
    monkeypatch.setattr(session_store, "save_session", lambda _value: None)
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "course-1"}])
    monkeypatch.setattr(tools.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(
        tools.read_service, "private_submissions",
        lambda *_args, **_kwargs: {
            "state": "current", "mirror_revision": 4,
            "snapshot_id": "course-1:4",
        },
    )

    result = tools.get_scoring_packet("session-1")

    assert result["code"] == "session_stale"
    assert session["status"] == "superseded"


def test_scoring_packet_reports_missing_bundle_before_packet_read(monkeypatch, tmp_path):
    session = {
        "session_id": "session-1", "session_kind": "scoring_assignment",
        "course_id": "course-1", "assignment_id": "assignment-1",
        "status": "ready",
        "privacy_artifacts": {"safe_bundle": str(tmp_path / "missing.json")},
    }
    monkeypatch.setattr(session_store, "load_session", lambda _sid: session)
    monkeypatch.setattr(session_store, "is_current_session", lambda _sid: True)
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "course-1"}])

    result = tools.get_scoring_packet("session-1")

    assert result == {
        "ok": False,
        "code": "packet_missing",
        "error": "The SAFE scoring packet is missing or invalid. Prepare this exact assignment again.",
    }


def test_bridge_reconciliation_tool_is_student_free(monkeypatch):
    monkeypatch.setattr(
        tools.sis_grade_bridge, "reconcile_sis_grade_bridges",
        lambda course_id: {"ok": True, "course_id": course_id,
                           "matrix": [{"family_title": "Checkpoint", "status": "missing"}]},
    )

    result = tools.reconcile_sis_grade_bridges("course-1")

    assert result["matrix"][0]["status"] == "missing"
    assert "student" not in json.dumps(result).casefold()
