"""Course Catalog laws for post-push confirmation records."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from api import course_catalog


def test_pending_writes_are_separate_and_expired_entries_are_reported(tmp_path):
    created_at = "2026-01-02T03:04:05Z"

    course_catalog.record_pending_write(
        "course-1", "assignment", "assignment-1", "Fictional assignment",
        "op-1", created_at=created_at, root=str(tmp_path),
    )
    course_catalog.record_pending_write(
        "course-1", "assignment", "assignment-1", "Fictional assignment",
        "op-1", created_at=created_at, root=str(tmp_path),
    )

    assert course_catalog.read_catalog("course-1", root=str(tmp_path))["catalog"] is None
    pending_path = (tmp_path / "_System" / "Canvas Catalog" / "course-1"
                    / course_catalog.PENDING_WRITES_FILENAME)
    document = json.loads(pending_path.read_text(encoding="utf-8"))
    assert document["records"] == [{
        "id": "assignment-1",
        "kind": "assignment",
        "title": "Fictional assignment",
        "created_by_op": "op-1",
        "created_at": created_at,
        "confirmed": False,
    }]

    assert course_catalog.pending_unconfirmed(
        "course-1", now=datetime(2026, 1, 3, 2, 0, tzinfo=timezone.utc),
        root=str(tmp_path),
    ) == []
    expired = course_catalog.pending_unconfirmed(
        "course-1", now=datetime(2026, 1, 3, 4, 0, tzinfo=timezone.utc),
        root=str(tmp_path),
    )
    assert expired[0]["id"] == "assignment-1"
    assert expired[0]["state"] == "pending_unconfirmed"


def test_full_refresh_drops_only_pending_objects_seen_by_canvas(tmp_path):
    course_id = "course-2"
    created_at = "2026-01-02T03:04:05Z"
    for kind, object_id in (
        ("assignment", "assignment-1"),
        ("page", "page-one"),
        ("assignment", "assignment-missing"),
    ):
        course_catalog.record_pending_write(
            course_id, kind, object_id, f"Fictional {kind}", "op-2",
            created_at=created_at, root=str(tmp_path),
        )

    def get_all(_path, _params):
        raise AssertionError("an empty module collection must not fetch module items")

    assignment_rows = [{"id": "assignment-1", "name": "Fictional assignment"}]
    page_rows = [{"page_id": "page-one", "title": "Fictional page",
                  "body": "", "published": False}]

    def get_all_complete(path, _params):
        if path.endswith("/assignment_groups"):
            return [], None, True
        if path.endswith("/modules"):
            return [], None, True
        if path.endswith("/pages"):
            return list(page_rows), None, True
        raise AssertionError(f"unexpected catalog endpoint: {path}")

    receipt = lambda: (list(assignment_rows), None, True)
    refreshed = course_catalog.refresh_catalog(
        course_id, "Fictional Course", canvas_get_all=get_all,
        canvas_get_all_complete=get_all_complete,
        assignment_receipt=receipt(),
        root=str(tmp_path), attempted_at="2026-01-03T04:00:00Z",
    )

    assert refreshed["catalog"]["assignments"]["records"].keys() == {"assignment-1"}
    assert refreshed["result"] == "complete"
    assert refreshed["sections"]["assignments"]["added_ids"] == ["assignment-1"]
    assert refreshed["sections"]["pages"]["added_ids"] == ["page-one"]
    pending = course_catalog._read_pending_writes(course_id, root=str(tmp_path))["records"]
    assert [(row["kind"], row["id"]) for row in pending] == [
        ("assignment", "assignment-missing"),
    ]

    assignment_rows.clear()
    page_rows.clear()
    deleted = course_catalog.refresh_catalog(
        course_id, "Fictional Course", canvas_get_all=get_all,
        canvas_get_all_complete=get_all_complete,
        assignment_receipt=receipt(), root=str(tmp_path),
        attempted_at="2026-01-03T05:00:00Z",
    )
    assert deleted["sections"]["assignments"]["deleted_ids"] == ["assignment-1"]
    assert deleted["sections"]["pages"]["deleted_ids"] == ["page-one"]
    assert deleted["catalog"]["assignments"]["records"] == {}


def test_quiz_confirmation_requires_a_current_canvas_module_item(tmp_path):
    course_id = "course-3"
    course_catalog.record_pending_write(
        course_id, "quiz", "quiz-1", "Fictional quiz", "op-3",
        created_at="2026-01-02T03:04:05Z", root=str(tmp_path),
    )
    document = {
        "assignments": {"state": "current", "records": {}},
        "modules": {"state": "stale", "records":[{
            "items": [{"type": "Quiz", "content_id": "quiz-1"}],
        }]},
        "pages": {"state": "current", "records": []},
    }

    course_catalog._confirm_pending_writes(course_id, document, root=str(tmp_path))
    assert len(course_catalog._read_pending_writes(course_id, root=str(tmp_path))["records"]) == 1

    document["modules"]["state"] = "current"
    course_catalog._confirm_pending_writes(course_id, document, root=str(tmp_path))
    assert course_catalog._read_pending_writes(course_id, root=str(tmp_path))["records"] == []
