"""Focused mirror behavior for the existing ``GET /api/groups`` route."""

from __future__ import annotations

import json

from api.mirror import store
from api.platform_services import workspace
from api.webui.routes import courses


COURSE = "555001"
CATEGORIES = [{
    "category_id": "cat-1",
    "category_name": "Teams",
    "groups": [{
        "id": "group-1",
        "name": "Team 1",
        "memberships": [{"id": "membership-1", "user_id": "900101"}],
    }],
}]


def _mount(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))


def _body(response):
    return json.loads(response.body)








# --- fetch_group_category_groups (targeted single-category Canvas fetch) -----------

class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_group_category_groups_returns_normalized_shape(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    monkeypatch.setattr(courses, "canvas_headers",
                        lambda: ({"Authorization": "Bearer x"}, "https://canvas.example.invalid"))
    requested = []

    def fake_get(url, headers=None, params=None, timeout=20):
        requested.append(url)
        if url == "https://canvas.example.invalid/api/v1/group_categories/cat-1/groups":
            return _FakeResponse(200, [{"id": 5, "name": "Team A"}])
        if url == "https://canvas.example.invalid/api/v1/groups/5/memberships":
            return _FakeResponse(200, [{"id": 6, "user_id": 900101}])
        raise AssertionError(f"unexpected Canvas call: {url}")

    monkeypatch.setattr(courses.requests, "get", fake_get)

    groups_out, err = courses.fetch_group_category_groups("555001", "cat-1")

    assert err is None
    assert groups_out == [{
        "id": "5", "name": "Team A",
        "student_ids": [900101],
        "memberships": [{"id": 6, "user_id": 900101}],
    }]
    # Only this one category's groups + its own groups' memberships are ever
    # requested — never any other category's id.
    assert requested == [
        "https://canvas.example.invalid/api/v1/group_categories/cat-1/groups",
        "https://canvas.example.invalid/api/v1/groups/5/memberships",
    ]


def test_fetch_group_category_groups_returns_error_on_non_200(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    monkeypatch.setattr(courses, "canvas_headers",
                        lambda: ({"Authorization": "Bearer x"}, "https://canvas.example.invalid"))
    monkeypatch.setattr(courses.requests, "get",
                        lambda *a, **k: _FakeResponse(403, None))

    groups_out, err = courses.fetch_group_category_groups("555001", "cat-1")

    assert groups_out is None
    assert "403" in err


def test_fetch_group_category_groups_requires_token(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    monkeypatch.setattr(courses, "canvas_headers", lambda: (None, None))

    groups_out, err = courses.fetch_group_category_groups("555001", "cat-1")

    assert groups_out is None
    assert err == "No token saved."


def test_load_group_categories_uses_shared_fetch_helper_for_each_category(monkeypatch, tmp_path):
    """The refactor shares one Canvas-shape-normalization path; the
    per-category degrade-to-empty-on-failure behavior is unchanged."""
    _mount(monkeypatch, tmp_path)
    monkeypatch.setattr(courses, "canvas_headers",
                        lambda: ({"Authorization": "Bearer x"}, "https://canvas.example.invalid"))

    def fake_get(url, headers=None, params=None, timeout=20):
        if url == "https://canvas.example.invalid/api/v1/courses/555001/group_categories":
            return _FakeResponse(200, [{"id": 1, "name": "Teams"}, {"id": 2, "name": "Broken"}])
        if url == "https://canvas.example.invalid/api/v1/group_categories/1/groups":
            return _FakeResponse(200, [{"id": 5, "name": "Team A"}])
        if url == "https://canvas.example.invalid/api/v1/groups/5/memberships":
            return _FakeResponse(200, [{"id": 6, "user_id": 900101}])
        if url == "https://canvas.example.invalid/api/v1/group_categories/2/groups":
            return _FakeResponse(403, None)
        raise AssertionError(f"unexpected Canvas call: {url}")

    monkeypatch.setattr(courses.requests, "get", fake_get)

    result, err, message = courses.load_group_categories("555001")

    assert err is None
    assert result == [
        {"category_id": "1", "category_name": "Teams", "groups": [{
            "id": "5", "name": "Team A", "student_ids": [900101],
            "memberships": [{"id": 6, "user_id": 900101}],
        }]},
        {"category_id": "2", "category_name": "Broken", "groups": []},
    ]
