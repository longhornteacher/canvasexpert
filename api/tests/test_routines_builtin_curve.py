"""Focused tests for the built-in curve routine's shared grade-adjustment path."""
from __future__ import annotations

from datetime import datetime, timedelta

from api.webui.routes import routines_builtin


COURSE_A = "5001"
COURSE_B = "5002"


def _recent_due() -> str:
    return (datetime.now().date() - timedelta(days=5)).isoformat() + "T09:00:00Z"


def _wire(monkeypatch):
    monkeypatch.setattr(routines_builtin.config, "active_courses",
                        lambda: [{"id": COURSE_A, "nickname": "Course A"},
                                 {"id": COURSE_B, "nickname": "Course B"}])
    monkeypatch.setattr(routines_builtin.grade_adjustment,
                        "active_adjustment_assignments", lambda: set())
    due = _recent_due()

    def fake_get_all(path, params=None, timeout=None):
        if path.endswith("/assignments"):
            aid = 111 if f"/courses/{COURSE_A}/" in path else 222
            return [{"id": aid, "name": "Quiz", "due_at": due,
                     "points_possible": 10, "published": True}], None
        if path.endswith("/submissions"):
            score = 5 if f"/courses/{COURSE_A}/" in path else 9
            return [{"user_id": str(i), "workflow_state": "graded", "score": score}
                    for i in range(3)], None
        return [], None

    monkeypatch.setattr(routines_builtin, "canvas_get_all", fake_get_all)
    previews = []
    applies = []

    def fake_preview(course_id, assignment_id, adjustment):
        previews.append((course_id, assignment_id, adjustment))
        return {"ok": True, "operation_id": "op-1", "batch_id": "batch-1",
                "review_digest": "digest-1",
                "preview": {"summary": {"changed": 3}}}

    def fake_apply(operation_id, batch_id, review_digest):
        applies.append((operation_id, batch_id, review_digest))
        return {"ok": True, "status": "applied",
                "counts": {"adjusted": 3}}

    monkeypatch.setattr(routines_builtin.grade_adjustment,
                        "preview_grade_adjustment", fake_preview)
    monkeypatch.setattr(routines_builtin.grade_adjustment,
                        "apply_grade_adjustment", fake_apply)
    notified = []
    monkeypatch.setattr(routines_builtin.mirror_service, "notify_course_changed",
                        lambda course_id, **kw: notified.append(str(course_id)))
    return previews, applies, notified


def test_apply_mode_uses_reviewed_adjustment_and_reconciles_once(monkeypatch):
    previews, applies, notified = _wire(monkeypatch)

    result = routines_builtin._run_routine_curve({"mode": "apply", "floor": 80})

    assert result["ok"] is True
    assert previews[0][0:2] == (COURSE_A, "111")
    assert previews[0][2] == {
        "kind": "rule", "model": "target_average",
        "settings": {"target_avg_pct": 80, "do_no_harm": True},
    }
    assert applies == [("op-1", "batch-1", "digest-1")]
    assert notified == [COURSE_A]


def test_flag_mode_never_previews_or_reconciles(monkeypatch):
    previews, applies, notified = _wire(monkeypatch)

    result = routines_builtin._run_routine_curve({"mode": "flag", "floor": 80})

    assert result["ok"] is True
    assert previews == []
    assert applies == []
    assert notified == []


def test_already_adjusted_assignment_is_skipped(monkeypatch):
    previews, applies, _notified = _wire(monkeypatch)
    monkeypatch.setattr(routines_builtin.grade_adjustment,
                        "active_adjustment_assignments",
                        lambda: {(COURSE_A, "111")})

    result = routines_builtin._run_routine_curve({"mode": "apply", "floor": 80})

    assert result["ok"] is True
    assert previews == []
    assert applies == []
