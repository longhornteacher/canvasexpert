import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import course_catalog
from api.platform_services import workspace
from api.webui.routes import course_catalog as course_catalog_routes
from api.webui.server import app


STAMP_1 = "2026-07-14T12:00:00+00:00"
STAMP_2 = "2026-07-14T13:00:00+00:00"


def _assignment(assignment_id="101", **changes):
    row = {
        "id": assignment_id,
        "name": "Fictional Reflection",
        "description": '<p>Explain <a href="https://example.invalid/private">your reasoning</a>. Visit https://example.invalid/visible</p><script>secret()</script>',
        "points_possible": 10,
        "due_at": "2026-07-18T05:00:00Z",
        "unlock_at": None,
        "lock_at": None,
        "created_at": "2026-07-01T05:00:00Z",
        "updated_at": "2026-07-14T05:00:00Z",
        "published": True,
        "submission_types": ["online_text_entry"],
        "assignment_group_id": 44,
        "rubric": [{
            "id": "criterion-1",
            "description": "Reasoning",
            "long_description": "<b>Use evidence.</b>",
            "points": 10,
            "ratings": [{"id": "rating-1", "description": "Complete", "long_description": "", "points": 10, "url": "drop"}],
            "url": "drop",
        }],
        "rubric_settings": {"id": 9, "title": "Reasoning rubric", "points_possible": 10, "free_form_criterion_comments": True},
        "html_url": "https://example.invalid/drop",
        "submission": {"user_id": "must-not-persist"},
    }
    row.update(changes)
    return row


def _module(module_id="10", *, items=None, include_items=True, position=1):
    row = {"id": module_id, "name": f"Module {module_id}", "position": position, "url": "https://example.invalid/drop"}
    if include_items:
        row["items"] = items if items is not None else [{
            "id": f"item-{module_id}",
            "type": "Assignment",
            "title": "Fictional Reflection",
            "position": 1,
            "content_id": "101",
            "url": "https://example.invalid/drop",
        }]
    return row


def _assignment_group(group_id="44", **changes):
    row = {"id": group_id, "name": "Projects", "position": 1, "group_weight": 25}
    row.update(changes)
    return row


def _page(page_id="500", **changes):
    # Shaped like Canvas's real Wiki Pages API: keyed by `page_id` and `url`,
    # not `id` like every other resource this module normalizes.
    row = {
        "page_id": page_id,
        "url": "fictional-page",
        "title": "Fictional Page",
        "body": '<p>Read <a href="https://example.invalid/private">this</a>. Visit https://example.invalid/visible</p><script>secret()</script>',
        "published": True,
        "front_page": False,
        "updated_at": "2026-07-14T05:00:00Z",
        "created_at": "2026-07-01T05:00:00Z",
        "hide_from_students": False,
        "editing_roles": "teachers",
        "html_url": "https://example.invalid/drop",
    }
    row.update(changes)
    return row


def _canvas_success(path, params):
    if path.endswith("/assignments"):
        return [_assignment()], None
    if path.endswith("/modules"):
        return [_module()], None
    if path.endswith("/pages"):
        return [], None
    raise AssertionError(f"unexpected Canvas call: {path}")


def _canvas_success_complete(path, params):
    if path.endswith("/assignments"):
        return [_assignment()], None, True
    if path.endswith("/modules"):
        return [_module()], None, True
    if path.endswith("/assignment_groups"):
        return [_assignment_group()], None, True
    if path.endswith("/pages"):
        assert params == {"include[]": "body", "per_page": 100}
        return [], None, True
    raise AssertionError(f"unexpected Canvas call: {path}")


def _with_assignment_groups(callback):
    def wrapped(path, params):
        if path.endswith("/assignment_groups"):
            return [_assignment_group()], None, True
        if path.endswith("/pages"):
            return [], None, True
        return callback(path, params)
    return wrapped


def test_assignment_normalization_is_strict_url_free_and_rejects_unknown_fields(tmp_path):
    normalized = course_catalog.normalize_assignment(_assignment())

    assert set(normalized) == course_catalog.ASSIGNMENT_KEYS
    assert normalized["description_text"] == "Explain your reasoning . Visit [link]"
    assert "https://" not in json.dumps(normalized)
    assert "user_id" not in json.dumps(normalized)
    assert normalized["rubric"][0]["ratings"][0] == {
        "id": "rating-1", "description": "Complete", "long_description": "", "points": 10,
    }

    result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_1,
    )
    document = result["catalog"]
    document["assignments"]["records"]["101"]["html_url"] = "unapproved"
    with pytest.raises(ValueError, match="assignment schema"):
        course_catalog.validate_catalog(document)
    document["assignments"]["records"]["wrong"] = document["assignments"]["records"].pop("101")
    document["assignments"]["records"]["wrong"].pop("html_url")
    with pytest.raises(ValueError, match="stable id"):
        course_catalog.validate_catalog(document)


def test_refresh_succeeds_for_both_scopes_and_persists_projection(tmp_path):
    result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_1,
    )

    document = result["catalog"]
    assert document["assignments"]["state"] == "current"
    assert document["modules"]["state"] == "current"
    assert set(document["assignments"]["records"]) == {"101"}
    projection = course_catalog.public_projection(result, course_id="course-1")
    assert projection["available"] is True
    assert projection["modules"][0]["assignment_ids"] == ["101"]
    stored = course_catalog.read_catalog("course-1")
    assert stored["source"] == "canonical"
    assert stored["catalog"] == document


def test_v3_assignment_groups_are_sorted_strict_and_public(tmp_path):
    groups = [
        _assignment_group("20", name="  Later  ", position=2, group_weight=75, html_url="drop"),
        _assignment_group("10", name=" Earlier ", position=1, group_weight=25, rules={"drop": True}),
    ]

    def complete(path, params):
        if path.endswith("/assignment_groups"):
            assert params == {"per_page": 100}
            return groups, None, True
        return _canvas_success_complete(path, params)

    result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=complete, attempted_at=STAMP_1,
    )
    document = result["catalog"]
    assert document["version"] == 3
    assert document["assignment_groups"] == {
        "state": "current", "last_success_at": STAMP_1, "last_attempt_at": STAMP_1,
        "error_code": "", "records": [
            {"id": "10", "name": "Earlier", "position": 1, "group_weight": 25},
            {"id": "20", "name": "Later", "position": 2, "group_weight": 75},
        ],
    }
    projection = course_catalog.public_projection(result, course_id="course-1")
    assert projection["scopes"]["assignment_groups"]["state"] == "current"
    assert projection["assignment_groups"] == document["assignment_groups"]["records"]
    assert "html_url" not in json.dumps(document)


def test_page_normalization_uses_canvas_page_id_not_id():
    normalized = course_catalog.normalize_page(_page())

    assert set(normalized) == course_catalog.PAGE_KEYS
    assert normalized["id"] == "500"
    assert normalized["body_text"] == "Read this. Visit [link]"
    assert "https://" not in json.dumps(normalized)
    assert "editing_roles" not in json.dumps(normalized)

    with pytest.raises(ValueError, match="stable id"):
        course_catalog.normalize_page({k: v for k, v in _page().items() if k != "page_id"})


def test_real_canvas_page_rows_reach_the_catalog_as_current_not_incomplete(tmp_path):
    pages = [_page("500", title="A Syllabus", front_page=True), _page("501", title="B Unit One", url="unit-1")]

    def complete(path, params):
        if path.endswith("/pages"):
            return pages, None, True
        return _canvas_success_complete(path, params)

    result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=complete, attempted_at=STAMP_1,
    )
    document = result["catalog"]
    assert document["pages"]["state"] == "current"
    assert document["pages"]["error_code"] == ""
    assert [page["id"] for page in document["pages"]["records"]] == ["500", "501"]
    assert document["pages"]["records"][0]["front_page"] is True


@pytest.mark.parametrize(
    ("rows", "state", "records"),
    [
        ([], "current", []),
        ([_assignment_group("20"), {"name": "missing"}], "incomplete", [{"id": "44", "name": "Projects", "position": 1, "group_weight": 25}]),
    ],
)
def test_assignment_group_membership_requires_complete_valid_collection(tmp_path, rows, state, records):
    first = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete, attempted_at=STAMP_1,
    )["catalog"]

    def complete(path, params):
        if path.endswith("/assignment_groups"):
            return rows, None, True
        return _canvas_success_complete(path, params)

    result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=complete, attempted_at=STAMP_2,
    )["catalog"]["assignment_groups"]
    assert result["state"] == state
    assert result["records"] == records
    if state == "incomplete":
        assert result["last_success_at"] == STAMP_1
        assert result["error_code"] == "invalid_assignment_group_record"
        assert result["records"] == first["assignment_groups"]["records"]


def test_read_catalog_uses_v3_previous_and_rejects_older_documents(tmp_path):
    document = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete, attempted_at=STAMP_1,
    )["catalog"]
    directory = Path(workspace.course_catalog_dir("course-1"))
    course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete, attempted_at=STAMP_2,
    )
    (directory / "catalog.v3.json").write_text("{broken", encoding="utf-8")
    recovered = course_catalog.read_catalog("course-1")
    assert recovered["source"] == "previous"
    assert recovered["catalog"] == document

    (directory / "catalog.v3.previous.json").unlink()
    old_document = dict(document)
    old_document["version"] = 2
    old_path = directory / "catalog.legacy.json"
    old_path.write_text(json.dumps(old_document), encoding="utf-8")
    fallback = course_catalog.read_catalog("course-1")
    assert fallback["source"] == "none"
    assert fallback["catalog"] is None
    assert old_path.read_text(encoding="utf-8") == json.dumps(old_document)
    projection = course_catalog.public_projection(fallback, course_id="course-1")
    assert projection["scopes"]["assignment_groups"]["state"] == "unavailable"
    assert projection["assignment_groups"] == []


def test_scope_failure_preserves_last_good_records_while_other_scope_updates(tmp_path):
    course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_1,
    )

    def assignment_failure(path, params):
        if path.endswith("/assignments"):
            return None, "HTTP 503: do not expose this"
        if path.endswith("/modules"):
            return [_module("20", position=2)], None
        raise AssertionError(path)

    def assignment_failure_complete(path, params):
        if path.endswith("/assignments"):
            return None, "HTTP 503: do not expose this", True
        if path.endswith("/modules"):
            return [_module("20", position=2)], None, True
        raise AssertionError(path)

    result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=assignment_failure,
        canvas_get_all_complete=_with_assignment_groups(assignment_failure_complete),
        attempted_at=STAMP_2,
    )

    assert result["catalog"]["assignments"]["state"] == "stale"
    assert set(result["catalog"]["assignments"]["records"]) == {"101"}
    assert result["catalog"]["assignments"]["last_success_at"] == STAMP_1
    assert result["catalog"]["assignments"]["error_code"] == "canvas_unavailable"
    assert result["catalog"]["modules"]["state"] == "current"
    assert result["catalog"]["modules"]["records"][0]["id"] == "20"
    assert "503" not in json.dumps(result)


@pytest.mark.parametrize(
    "rows",
    [
        [_assignment("202"), {"name": "Missing assignment ID"}],
        [_assignment("202"), _assignment(202)],
    ],
    ids=["missing-id", "duplicate-normalized-id"],
)
def test_invalid_assignment_membership_retains_last_good_records_and_allows_modules_update(tmp_path, rows):
    first = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_1,
    )["catalog"]

    def invalid_assignments_complete(path, params):
        if path.endswith("/assignments"):
            return rows, None, True
        if path.endswith("/modules"):
            return [_module("20", position=2)], None, True
        raise AssertionError(path)

    result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_with_assignment_groups(invalid_assignments_complete),
        attempted_at=STAMP_2,
    )["catalog"]

    assert result["assignments"] == {
        "state": "incomplete", "last_success_at": STAMP_1, "last_attempt_at": STAMP_2,
        "error_code": "invalid_assignment_record", "records": first["assignments"]["records"],
    }
    assert result["modules"]["state"] == "current"
    assert result["modules"]["records"][0]["id"] == "20"


@pytest.mark.parametrize(
    "rows",
    [
        [_module("20", include_items=False), {"name": "Missing module ID"}],
        [_module("20", include_items=False), _module(20, include_items=False)],
    ],
    ids=["missing-id", "duplicate-normalized-id"],
)
def test_invalid_module_membership_retains_last_good_records_without_item_fallback(tmp_path, rows):
    first = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_1,
    )["catalog"]
    item_calls = []

    def no_item_fallback(path, params):
        item_calls.append(path)
        raise AssertionError(f"unexpected module-item fallback: {path}")

    def invalid_modules_complete(path, params):
        if path.endswith("/assignments"):
            return [_assignment("202")], None, True
        if path.endswith("/modules"):
            return rows, None, True
        raise AssertionError(path)

    result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=no_item_fallback,
        canvas_get_all_complete=_with_assignment_groups(invalid_modules_complete),
        attempted_at=STAMP_2,
    )["catalog"]

    assert result["assignments"]["state"] == "current"
    assert set(result["assignments"]["records"]) == {"202"}
    assert result["modules"] == {
        "state": "incomplete", "last_success_at": STAMP_1, "last_attempt_at": STAMP_2,
        "error_code": "invalid_module_record", "records": first["modules"]["records"],
    }
    assert item_calls == []


@pytest.mark.parametrize(
    ("rows", "expected_state", "expected_records"),
    [
        ([_assignment("202"), {"name": "Missing assignment ID"}], "incomplete", {"202"}),
        ([{"name": "Missing assignment ID"}], "unavailable", set()),
    ],
    ids=["valid-subset", "all-invalid"],
)
def test_first_invalid_assignment_membership_exposes_only_valid_subset(tmp_path, rows, expected_state, expected_records):
    def assignments_complete(path, params):
        if path.endswith("/assignments"):
            return rows, None, True
        if path.endswith("/modules"):
            return [], None, True
        raise AssertionError(path)

    result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_with_assignment_groups(assignments_complete),
        attempted_at=STAMP_1,
    )["catalog"]["assignments"]

    assert result["state"] == expected_state
    assert result["last_success_at"] == ""
    assert result["last_attempt_at"] == STAMP_1
    assert result["error_code"] == "invalid_assignment_record"
    assert set(result["records"]) == expected_records


@pytest.mark.parametrize(
    ("rows", "expected_state", "expected_ids"),
    [
        ([_module("20", include_items=False), {"name": "Missing module ID"}], "incomplete", ["20"]),
        ([{"name": "Missing module ID"}], "unavailable", []),
    ],
    ids=["valid-subset", "all-invalid"],
)
def test_first_invalid_module_membership_exposes_only_valid_subset_without_item_fallback(
    tmp_path, rows, expected_state, expected_ids,
):
    item_calls = []

    def no_item_fallback(path, params):
        item_calls.append(path)
        raise AssertionError(f"unexpected module-item fallback: {path}")

    def modules_complete(path, params):
        if path.endswith("/assignments"):
            return [], None, True
        if path.endswith("/modules"):
            return rows, None, True
        raise AssertionError(path)

    result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=no_item_fallback,
        canvas_get_all_complete=_with_assignment_groups(modules_complete),
        attempted_at=STAMP_1,
    )["catalog"]["modules"]

    assert result["state"] == expected_state
    assert result["last_success_at"] == ""
    assert result["last_attempt_at"] == STAMP_1
    assert result["error_code"] == "invalid_module_record"
    assert [record["id"] for record in result["records"]] == expected_ids
    assert item_calls == []


def test_first_sync_partial_result_keeps_successful_scope(tmp_path):
    def module_failure(path, params):
        if path.endswith("/assignments"):
            return [_assignment()], None
        if path.endswith("/modules"):
            return None, "HTTP 403 private detail"
        raise AssertionError(path)

    def module_failure_complete(path, params):
        if path.endswith("/assignments"):
            return [_assignment()], None, True
        if path.endswith("/modules"):
            return None, "HTTP 403 private detail", True
        raise AssertionError(path)

    result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=module_failure,
        canvas_get_all_complete=_with_assignment_groups(module_failure_complete),
        attempted_at=STAMP_1,
    )

    assert result["catalog"]["assignments"]["state"] == "current"
    assert result["catalog"]["modules"] == {
        "state": "unavailable", "last_success_at": "", "last_attempt_at": STAMP_1,
        "error_code": "forbidden", "records": [],
    }
    assert course_catalog.public_projection(result, course_id="course-1")["available"] is True


@pytest.mark.parametrize(
    ("assignments", "modules", "available"),
    [
        ([], [_module()], True),
        ([_assignment()], [], True),
        ([], [], True),
    ],
)
def test_proven_empty_scope_replaces_last_good_records(tmp_path, assignments, modules, available):
    first = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_1,
    )["catalog"]

    def proven_empty_complete(path, params):
        if path.endswith("/assignments"):
            return assignments, None, True
        if path.endswith("/modules"):
            return modules, None, True
        raise AssertionError(path)

    result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_with_assignment_groups(proven_empty_complete),
        attempted_at=STAMP_2,
    )
    document = result["catalog"]
    assert document["assignments"]["state"] == "current"
    assert document["modules"]["state"] == "current"
    assert all(
        document[scope][key] == STAMP_2
        for scope in ("assignments", "modules")
        for key in ("last_success_at", "last_attempt_at")
    )
    assert all(document[scope]["error_code"] == "" for scope in ("assignments", "modules"))
    assert document["assignments"]["records"] == ({} if not assignments else {"101": course_catalog.normalize_assignment(_assignment())})
    assert document["modules"]["records"] == ([] if not modules else [
        {"id": "10", "name": "Module 10", "position": 1, "items": [
            {"id": "item-10", "type": "Assignment", "title": "Fictional Reflection", "position": 1, "content_id": "101"},
        ]},
    ])
    directory = Path(workspace.course_catalog_dir("course-1"))
    assert json.loads((directory / "catalog.v3.previous.json").read_text(encoding="utf-8")) == first
    assert course_catalog.public_projection(result, course_id="course-1")["available"] is available


@pytest.mark.parametrize(
    ("rows", "error", "complete", "error_code"),
    [
        ([], None, False, "pagination_incomplete"),
        (None, "pagination_incomplete", False, "pagination_incomplete"),
        (None, "invalid_response", False, "invalid_response"),
        (None, "HTTP 503: private detail", True, "canvas_unavailable"),
    ],
)
def test_unproven_receipt_after_successful_empty_scope_is_stale(tmp_path, rows, error, complete, error_code):
    def empty_assignments_complete(path, params):
        if path.endswith("/assignments"):
            return [], None, True
        if path.endswith("/modules"):
            return [_module()], None, True
        raise AssertionError(path)

    course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_with_assignment_groups(empty_assignments_complete),
        attempted_at=STAMP_1,
    )

    def failed_assignments_complete(path, params):
        if path.endswith("/assignments"):
            return rows, error, complete
        if path.endswith("/modules"):
            return [_module()], None, True
        raise AssertionError(path)

    result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_with_assignment_groups(failed_assignments_complete),
        attempted_at=STAMP_2,
    )
    assignments_scope = result["catalog"]["assignments"]
    assert assignments_scope == {
        "state": "stale", "last_success_at": STAMP_1, "last_attempt_at": STAMP_2,
        "error_code": error_code, "records": {},
    }


@pytest.mark.parametrize(
    ("rows", "complete", "error_code"),
    [
        ([], False, "pagination_incomplete"),
        ({"unexpected": "root"}, True, "invalid_response"),
    ],
)
def test_unproven_receipt_before_successful_scope_is_unavailable(tmp_path, rows, complete, error_code):
    def unproven_assignments_complete(path, params):
        if path.endswith("/assignments"):
            return rows, None, complete
        if path.endswith("/modules"):
            return [_module()], None, True
        raise AssertionError(path)

    result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_with_assignment_groups(unproven_assignments_complete),
        attempted_at=STAMP_1,
    )
    assert result["catalog"]["assignments"] == {
        "state": "unavailable", "last_success_at": "", "last_attempt_at": STAMP_1,
        "error_code": error_code, "records": {},
    }


def test_inline_empty_items_are_complete_but_omitted_items_use_bounded_fallback(tmp_path):
    calls = []
    top_level_calls = []
    active = 0
    peak = 0
    guard = threading.Lock()

    def canvas_get_all(path, params):
        nonlocal active, peak
        calls.append((path, dict(params)))
        if path.endswith("/assignments"):
            return [_assignment()], None
        if path.endswith("/modules"):
            return [_module("empty", items=[])] + [
                _module(str(index), include_items=False, position=index + 1) for index in range(5)
            ], None
        if path.endswith("/items"):
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.01)
            module_id = path.split("/modules/")[1].split("/")[0]
            with guard:
                active -= 1
            return [{"id": f"item-{module_id}", "type": "Assignment", "title": "Work", "position": 1, "content_id": "101"}], None
        raise AssertionError(path)

    def canvas_get_all_complete(path, params):
        top_level_calls.append((path, dict(params)))
        if path.endswith("/assignments"):
            return [_assignment()], None, True
        if path.endswith("/modules"):
            return [_module("empty", items=[])] + [
                _module(str(index), include_items=False, position=index + 1) for index in range(5)
            ], None, True
        raise AssertionError(path)

    result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=canvas_get_all,
        canvas_get_all_complete=_with_assignment_groups(canvas_get_all_complete),
        attempted_at=STAMP_1,
    )

    assert result["catalog"]["modules"]["state"] == "current"
    assert peak <= course_catalog.MODULE_ITEM_CONCURRENCY
    item_calls = [path for path, _ in calls if path.endswith("/items")]
    assert len(item_calls) == 5
    assert not any("/modules/empty/items" in path for path in item_calls)
    assert not any("/assignments/" in path for path, _ in calls)
    module_params = next(params for path, params in top_level_calls if path.endswith("/modules"))
    assert module_params == {"per_page": 100, "include[]": "items"}


def test_partial_module_fallback_reuses_prior_items_and_marks_incomplete(tmp_path):
    course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_1,
    )

    def failed_items(path, params):
        if path.endswith("/assignments"):
            return [_assignment()], None
        if path.endswith("/modules"):
            return [_module("10", include_items=False)], None
        if path.endswith("/modules/10/items"):
            return None, "timeout with private host"
        raise AssertionError(path)

    def modules_without_inline_items_complete(path, params):
        if path.endswith("/assignments"):
            return [_assignment()], None, True
        if path.endswith("/modules"):
            return [_module("10", include_items=False)], None, True
        raise AssertionError(path)

    result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=failed_items,
        canvas_get_all_complete=_with_assignment_groups(modules_without_inline_items_complete),
        attempted_at=STAMP_2,
    )
    modules = result["catalog"]["modules"]
    assert modules["state"] == "incomplete"
    assert modules["error_code"] == "module_items_unavailable"
    assert modules["records"][0]["items"][0]["content_id"] == "101"


def test_atomic_update_preserves_previous_and_leaves_no_temp_files(tmp_path):
    first = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_1,
    )["catalog"]

    def changed(path, params):
        if path.endswith("/assignments"):
            return [_assignment(name="Updated Reflection")], None
        if path.endswith("/modules"):
            return [_module()], None
        raise AssertionError(path)

    def changed_complete(path, params):
        if path.endswith("/assignments"):
            return [_assignment(name="Updated Reflection")], None, True
        if path.endswith("/modules"):
            return [_module()], None, True
        raise AssertionError(path)

    second = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=changed,
        canvas_get_all_complete=_with_assignment_groups(changed_complete),
        attempted_at=STAMP_2,
    )["catalog"]
    directory = Path(workspace.course_catalog_dir("course-1"))
    assert json.loads((directory / "catalog.v3.previous.json").read_text(encoding="utf-8")) == first
    assert json.loads((directory / "catalog.v3.json").read_text(encoding="utf-8")) == second
    assert not list(directory.glob("*.tmp"))


def test_corrupt_canonical_falls_back_to_previous_and_quarantines_bad_file(tmp_path):
    course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_1,
    )
    course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_2,
    )
    directory = Path(workspace.course_catalog_dir("course-1"))
    (directory / "catalog.v3.json").write_text("{broken", encoding="utf-8")

    result = course_catalog.read_catalog("course-1")

    assert result["source"] == "previous"
    assert result["catalog"]["updated_at"] == STAMP_1
    assert "using_previous_catalog" in result["warnings"]
    assert not (directory / "catalog.v3.json").exists()
    assert len(list((directory / "quarantine").glob("catalog.v3.json.*.corrupt"))) == 1


def test_corrupt_previous_without_canonical_is_unavailable(tmp_path):
    directory = Path(workspace.course_catalog_dir("course-1"))
    directory.mkdir(parents=True)
    (directory / "catalog.legacy.previous.json").write_text("not-json", encoding="utf-8")

    result = course_catalog.read_catalog("course-1")

    assert result == {"catalog": None, "source": "none", "warnings": []}
    assert (directory / "catalog.legacy.previous.json").exists()
    assert len(list((directory / "quarantine").glob("catalog.legacy.previous.json.*.corrupt"))) == 0


def test_onedrive_conflict_warns_but_is_never_modified_or_deleted(tmp_path):
    course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_1,
    )
    directory = Path(workspace.course_catalog_dir("course-1"))
    conflict = directory / "catalog.v3-LAPTOP.json"
    conflict.write_text('{"leave": "untouched"}', encoding="utf-8")

    read_result = course_catalog.read_catalog("course-1")
    refresh_result = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_2,
    )

    assert "competing_catalog_files" in read_result["warnings"]
    assert "competing_catalog_files" in refresh_result["warnings"]
    assert conflict.read_text(encoding="utf-8") == '{"leave": "untouched"}'


def test_routes_gate_current_courses_and_get_is_disk_only(monkeypatch):
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    calls = []
    refresh_calls = []
    mirror_calls = []
    assignment_calls = []
    receipt = ([{"id": "101"}], None, True)
    monkeypatch.setattr(course_catalog_routes.config, "active_courses", lambda: [{"id": "course-1", "name": "Fictional Course"}])

    def fake_read(course_id):
        calls.append(course_id)
        return {"catalog": None, "source": "none", "warnings": []}

    monkeypatch.setattr(course_catalog_routes.course_catalog, "read_catalog", fake_read)
    monkeypatch.setattr(
        course_catalog_routes, "canvas_get_all",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("GET contacted Canvas")),
    )
    def complete_assignments(path, params):
        assignment_calls.append((path, params))
        return receipt

    monkeypatch.setattr(course_catalog_routes, "canvas_get_all_complete", complete_assignments)

    def fake_refresh(course_id, course_name, *, canvas_get_all, canvas_get_all_complete,
                     assignment_receipt):
        refresh_calls.append((course_id, course_name, canvas_get_all, canvas_get_all_complete,
                              assignment_receipt))
        return {"catalog": None, "source": "none", "warnings": []}

    monkeypatch.setattr(course_catalog_routes.course_catalog, "refresh_catalog", fake_refresh)
    monkeypatch.setattr(
        course_catalog_routes.mirror_sync,
        "apply_assignment_collection_receipt",
        lambda course_id, received_receipt: mirror_calls.append((course_id, received_receipt)) or {"ok": True},
    )

    allowed = client.get("/api/course-catalog", params={"course_id": "course-1"}).json()
    refreshed = client.post("/api/course-catalog/refresh", data={"course_id": "course-1"}).json()
    blocked_get = client.get("/api/course-catalog", params={"course_id": "previous-course"}).json()
    blocked_post = client.post("/api/course-catalog/refresh", data={"course_id": "previous-course"}).json()

    assert allowed["ok"] is True and allowed["available"] is False
    assert refreshed["ok"] is True and refreshed["available"] is False
    assert calls == ["course-1"]
    assert len(refresh_calls) == 1
    assert refresh_calls[0][0:2] == ("course-1", "Fictional Course")
    assert refresh_calls[0][2] is course_catalog_routes.canvas_get_all
    assert refresh_calls[0][3] is course_catalog_routes.canvas_get_all_complete
    assert refresh_calls[0][4] is receipt
    assert assignment_calls == [("/api/v1/courses/course-1/assignments", {"per_page": 100})]
    assert mirror_calls == [("course-1", receipt)]
    assert blocked_get == {"ok": False, "error": "Select a saved current course first."}
    assert blocked_post == blocked_get


# --- refresh_catalog_assignments_only (1.0beta 02c: heartbeat/full/delta coordination) -----

def test_refresh_catalog_assignments_only_updates_assignments_leaves_modules_groups_unchanged(tmp_path):
    first = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_1,
    )["catalog"]

    receipt = ([_assignment("202")], None, True)
    result = course_catalog.refresh_catalog_assignments_only(
        "course-1", assignment_receipt=receipt, attempted_at=STAMP_2,
    )["catalog"]

    assert result["assignments"]["state"] == "current"
    assert set(result["assignments"]["records"]) == {"202"}
    assert result["assignments"]["last_success_at"] == STAMP_2
    assert result["modules"] == first["modules"]
    assert result["assignment_groups"] == first["assignment_groups"]
    # course_name omitted -> falls back to the previous catalog's stored name.
    assert result["course_name"] == "Fictional Course"
    stored = course_catalog.read_catalog("course-1")
    assert stored["catalog"] == result


def test_refresh_catalog_assignments_only_course_name_explicit_overrides_previous(tmp_path):
    course_catalog.refresh_catalog(
        "course-1", "Old Name", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_1,
    )
    receipt = ([_assignment("202")], None, True)

    result = course_catalog.refresh_catalog_assignments_only(
        "course-1", assignment_receipt=receipt, course_name="New Name",
        attempted_at=STAMP_2,
    )["catalog"]

    assert result["course_name"] == "New Name"


def test_refresh_catalog_assignments_only_with_no_previous_catalog_stubs_modules_and_groups(tmp_path):
    receipt = ([_assignment("202")], None, True)

    result = course_catalog.refresh_catalog_assignments_only(
        "course-1", assignment_receipt=receipt, attempted_at=STAMP_1,
    )["catalog"]

    assert result["assignments"]["state"] == "current"
    assert set(result["assignments"]["records"]) == {"202"}
    unavailable_stub = {
        "state": "unavailable", "last_success_at": "", "last_attempt_at": STAMP_1,
        "error_code": "", "records": [],
    }
    assert result["modules"] == unavailable_stub
    assert result["assignment_groups"] == unavailable_stub
    # course_name omitted and no previous catalog -> falls back to course_id.
    assert result["course_name"] == "course-1"
    course_catalog.validate_catalog(result)


def test_refresh_catalog_assignments_only_bad_receipt_keeps_last_good_and_never_corrupts_catalog(tmp_path):
    first = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_1,
    )["catalog"]

    bad_receipt = (None, "HTTP 503: do not expose this", True)
    result = course_catalog.refresh_catalog_assignments_only(
        "course-1", assignment_receipt=bad_receipt, attempted_at=STAMP_2,
    )["catalog"]

    assert result["assignments"]["state"] == "stale"
    assert result["assignments"]["records"] == first["assignments"]["records"]
    assert result["assignments"]["last_success_at"] == STAMP_1
    assert result["assignments"]["error_code"] == "canvas_unavailable"
    assert "503" not in json.dumps(result)
    assert result["modules"] == first["modules"]
    assert result["assignment_groups"] == first["assignment_groups"]


def test_refresh_catalog_assignments_only_bad_receipt_with_no_previous_catalog_is_unavailable(tmp_path):
    bad_receipt = (None, "HTTP 503: do not expose this", True)

    result = course_catalog.refresh_catalog_assignments_only(
        "course-1", assignment_receipt=bad_receipt, attempted_at=STAMP_1,
    )["catalog"]

    assert result["assignments"]["state"] == "unavailable"
    assert result["assignments"]["records"] == {}
    assert "503" not in json.dumps(result)
    course_catalog.validate_catalog(result)


# ── invalidate_scope ─────────────────────────────────────────────────────

def test_invalidate_scope_marks_exactly_one_scope_stale_and_leaves_others_intact(tmp_path):
    first = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_1,
    )["catalog"]

    result = course_catalog.invalidate_scope(
        "course-1", "assignments", attempted_at=STAMP_2,
    )

    assert result["assignments"]["state"] == "stale"
    assert result["assignments"]["error_code"] == "invalidated"
    assert result["assignments"]["last_attempt_at"] == STAMP_2
    assert result["assignments"]["last_success_at"] == first["assignments"]["last_success_at"]
    assert result["assignments"]["records"] == first["assignments"]["records"]
    # Other scopes and every other record stay byte-identical.
    assert result["modules"] == first["modules"]
    assert result["assignment_groups"] == first["assignment_groups"]
    stored = course_catalog.read_catalog("course-1")
    assert stored["catalog"] == result


def test_invalidate_scope_no_ops_without_a_catalog_document(tmp_path):
    result = course_catalog.invalidate_scope(
        "course-1", "modules", attempted_at=STAMP_1,
    )

    assert result is None
    assert course_catalog.read_catalog("course-1")["catalog"] is None


def test_invalidate_scope_rejects_unknown_scope_key(tmp_path):
    course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_1,
    )

    with pytest.raises(ValueError):
        course_catalog.invalidate_scope("course-1", "bogus_scope")


def test_invalidate_scope_is_atomic_and_preserves_previous(tmp_path):
    first = course_catalog.refresh_catalog(
        "course-1", "Fictional Course", canvas_get_all=_canvas_success,
        canvas_get_all_complete=_canvas_success_complete,
        attempted_at=STAMP_1,
    )["catalog"]

    second = course_catalog.invalidate_scope(
        "course-1", "modules", attempted_at=STAMP_2,
    )

    directory = Path(workspace.course_catalog_dir("course-1"))
    assert json.loads((directory / "catalog.v3.previous.json").read_text(encoding="utf-8")) == first
    assert json.loads((directory / "catalog.v3.json").read_text(encoding="utf-8")) == second
    assert not list(directory.glob("*.tmp"))
