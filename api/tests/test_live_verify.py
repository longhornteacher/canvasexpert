"""Tests for api/live_verify.py (AC1).

verify_live is the only Live Canvas read an agent makes after a push. It
never writes -- no catalog, mirror, or pending-writes mutation -- and its
identity read costs exactly one Canvas call; module_ids costs one more only
when Canvas doesn't already report it on the object.
"""
import copy

import pytest

from api import live_verify
from api.platform_services import canvas_client, config


@pytest.fixture(autouse=True)
def _current_course(monkeypatch):
    monkeypatch.setattr(config, "active_courses", lambda: [{"id": "42", "name": "Course"}])


class FakeCanvas:
    """Records every Canvas call so tests can assert exact call counts and
    prove no write method is ever invoked."""

    def __init__(self):
        self.assignments = {}
        self.pages = {}
        self.modules = []
        self.get_calls = []
        self.get_all_calls = []

    def get(self, path, params=None, timeout=20):
        self.get_calls.append(path)
        if "/assignments/" in path:
            return copy.deepcopy(self.assignments.get(path.rsplit("/", 1)[-1])), None
        if "/pages/" in path:
            return copy.deepcopy(self.pages.get(path.rsplit("/", 1)[-1])), None
        return None, None

    def get_all(self, path, params=None, timeout=30):
        self.get_all_calls.append((path, dict(params or {})))
        if path.endswith("/assignments"):
            term = str((params or {}).get("search_term") or "").casefold()
            return [copy.deepcopy(row) for row in self.assignments.values()
                    if str(row.get("name") or "").casefold() == term], None
        if path.endswith("/pages"):
            term = str((params or {}).get("search_term") or "").casefold()
            return [copy.deepcopy(row) for row in self.pages.values()
                    if str(row.get("title") or "").casefold() == term], None
        if path.endswith("/modules"):
            return copy.deepcopy(self.modules), None
        return [], None

    def send_should_never_be_called(self, *args, **kwargs):
        raise AssertionError("verify_live must never write to Canvas")


def _install(monkeypatch, fake):
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake.get_all)
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send_should_never_be_called)


# --- Law: exactly one Canvas call, and it never writes ----------------------

def test_verify_live_by_id_makes_exactly_one_canvas_call_and_writes_nothing(monkeypatch):
    fake = FakeCanvas()
    fake.assignments["101"] = {
        "id": "101", "name": "Practice - Red", "published": True,
        "points_possible": 10.0, "module_ids": ["501"],
        "assignment_group_id": "77", "omit_from_final_grade": True,
        "post_to_sis": False, "due_at": "2026-09-20T23:59:00Z",
        "html_url": "https://canvas.invalid/a/101",
    }
    _install(monkeypatch, fake)

    result = live_verify.verify_live("42", "assignment", id="101")

    assert result["ok"] is True
    assert result["found"] is True
    assert result["id"] == "101"
    assert result["module_ids"] == ["501"]
    assert result["published"] is True
    assert result["assignment_group_id"] == "77"
    assert "checked_at" in result
    assert len(fake.get_calls) + len(fake.get_all_calls) == 1


def test_verify_live_by_title_makes_exactly_one_canvas_list_call(monkeypatch):
    fake = FakeCanvas()
    fake.assignments["101"] = {
        "id": "101", "name": "Practice - Red", "published": False,
        "points_possible": 10.0, "module_ids": [],
        "assignment_group_id": "77", "omit_from_final_grade": True,
        "post_to_sis": False, "due_at": None,
        "html_url": "https://canvas.invalid/a/101",
    }
    _install(monkeypatch, fake)

    result = live_verify.verify_live("42", "assignment", title="Practice - Red")

    assert result["ok"] is True
    assert result["found"] is True
    assert result["id"] == "101"
    assert len(fake.get_calls) + len(fake.get_all_calls) == 1


def test_verify_live_page_by_id(monkeypatch):
    fake = FakeCanvas()
    fake.pages["welcome"] = {
        "url": "welcome", "title": "Welcome", "published": True,
        "module_ids": ["501"],
    }
    _install(monkeypatch, fake)

    result = live_verify.verify_live("42", "page", id="welcome")

    assert result == {
        "ok": True, "found": True, "url": "welcome", "title": "Welcome",
        "published": True, "module_ids": ["501"], "checked_at": result["checked_at"],
    }
    assert len(fake.get_calls) + len(fake.get_all_calls) == 1


# --- Not found / ambiguous ---------------------------------------------------

def test_verify_live_reports_not_found_without_error(monkeypatch):
    fake = FakeCanvas()
    _install(monkeypatch, fake)

    result = live_verify.verify_live("42", "assignment", id="999")

    assert result == {"ok": True, "found": False, "checked_at": result["checked_at"]}


def test_verify_live_by_title_reports_ambiguous_ids(monkeypatch):
    fake = FakeCanvas()
    fake.assignments["101"] = {"id": "101", "name": "Practice - Red", "module_ids": []}
    fake.assignments["102"] = {"id": "102", "name": "Practice - Red", "module_ids": []}
    _install(monkeypatch, fake)

    result = live_verify.verify_live("42", "assignment", title="Practice - Red")

    assert result["ok"] is True
    assert result["found"] is False
    assert result["ambiguous"] is True
    assert result["ids"] == ["101", "102"]


# --- module_ids fallback (up to one extra call) ------------------------------

def test_verify_live_falls_back_to_one_module_scan_when_not_on_the_object(monkeypatch):
    fake = FakeCanvas()
    fake.assignments["101"] = {
        "id": "101", "name": "Practice - Red", "published": True,
        "points_possible": 10.0, "assignment_group_id": "77",
        "omit_from_final_grade": True, "post_to_sis": False, "due_at": None,
        "html_url": "https://canvas.invalid/a/101",
        # no "module_ids" key -- Canvas assignments don't carry it natively.
    }
    fake.modules = [
        {"id": "501", "items": [{"content_id": "101", "type": "Assignment"}]},
        {"id": "502", "items": [{"content_id": "999", "type": "Assignment"}]},
    ]
    _install(monkeypatch, fake)

    result = live_verify.verify_live("42", "assignment", id="101")

    assert result["found"] is True
    assert result["module_ids"] == ["501"]
    assert len(fake.get_calls) + len(fake.get_all_calls) == 2


# --- Student-free projection and refusals ------------------------------------

def test_verify_live_returns_only_the_projected_fields_never_the_raw_response(monkeypatch):
    fake = FakeCanvas()
    fake.assignments["101"] = {
        "id": "101", "name": "Practice - Red", "published": True,
        "points_possible": 10.0, "module_ids": [], "assignment_group_id": "77",
        "omit_from_final_grade": True, "post_to_sis": False, "due_at": None,
        "html_url": "https://canvas.invalid/a/101",
        "description": "a raw Canvas field verify_live must never leak",
        "submissions_download_url": "https://canvas.invalid/secret",
    }
    _install(monkeypatch, fake)

    result = live_verify.verify_live("42", "assignment", id="101")

    assert set(result) == {
        "ok", "found", "id", "title", "published", "points_possible",
        "module_ids", "assignment_group_id", "omit_from_final_grade",
        "post_to_sis", "due_at", "url", "checked_at",
    }


def test_verify_live_refuses_an_unsupported_kind(monkeypatch):
    fake = FakeCanvas()
    _install(monkeypatch, fake)
    result = live_verify.verify_live("42", "module", id="1")
    assert result["ok"] is False
    assert not fake.get_calls and not fake.get_all_calls


def test_verify_live_refuses_a_course_outside_current_courses(monkeypatch):
    fake = FakeCanvas()
    _install(monkeypatch, fake)
    result = live_verify.verify_live("999", "assignment", id="1")
    assert result["ok"] is False
    assert "Current courses" in result["error"]
    assert not fake.get_calls and not fake.get_all_calls


def test_verify_live_refuses_with_neither_id_nor_title(monkeypatch):
    fake = FakeCanvas()
    _install(monkeypatch, fake)
    result = live_verify.verify_live("42", "assignment")
    assert result["ok"] is False
    assert not fake.get_calls and not fake.get_all_calls


def test_verify_live_quiz_kind_reads_the_underlying_assignment(monkeypatch):
    """AC1: kind=quiz returns the assignment shape for the quiz's assignment,
    addressed by the quiz's own id."""
    fake = FakeCanvas()
    fake.assignments["777"] = {
        "id": "777", "name": "Unit 3 Check", "published": True,
        "points_possible": 20.0, "module_ids": ["501"],
        "assignment_group_id": "77", "omit_from_final_grade": False,
        "post_to_sis": True, "due_at": "2026-09-20T23:59:00Z",
        "html_url": "https://canvas.invalid/a/777",
    }
    _install(monkeypatch, fake)

    result = live_verify.verify_live("42", "quiz", id="777")

    assert result["found"] is True
    assert result["id"] == "777"
    assert result["post_to_sis"] is True
