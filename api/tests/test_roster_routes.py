"""Route-level tests for Roster Console API (V3: Canvas groups are source of truth)."""
import json
from fastapi.testclient import TestClient
from contextlib import contextmanager
import pytest

from api.webui.server import app
from api.platform_services import config
from api import feedback_vault
from api.feedback_vault import Vault
import api.webui.routes.roster as roster_routes
from api.mirror import store as mirror_store
from api.platform_services import workspace

client = TestClient(app)

_WORDS = feedback_vault._REGISTRY_WORDS


class FakeVault:
    def __init__(self):
        self.rows = {
            "101": {
                "canvas_id": "101",
                "real_name": "Ada Lovelace",
                "sis_id": "SIS-SECRET",
                "nicknames": ["Addie"],
                "pseudonym": _WORDS[0],
                "first_seen": "",
            }
        }
        self.saved = False

    @contextmanager
    def transaction(self):
        yield self

    def entries(self):
        return list(self.rows.values())

    def set_nicknames(self, canvas_id, nicknames):
        row = self.rows.setdefault(str(canvas_id), {"canvas_id": str(canvas_id)})
        row["nicknames"] = nicknames

    def set_pseudonym(self, canvas_id, value):
        row = self.rows.setdefault(str(canvas_id), {"canvas_id": str(canvas_id)})
        row["pseudonym"] = value

    def regenerate_pseudonym(self, canvas_id):
        self.set_pseudonym(canvas_id, _WORDS[1])

    def save(self):
        self.saved = True


@pytest.fixture(autouse=True)
def isolated_roster(monkeypatch):
    stores = {
        "vault": FakeVault(),
        "extra_time": {},
        "monitored": {},
        "settings": {},
        "score_matrices": {},
        "relationships": {},
        "baselines": {},
    }

    def fake_get_extra_time(course_id):
        return list(stores["extra_time"].get(str(course_id), []))

    def fake_set_extra_time(course_id, students):
        stores["extra_time"][str(course_id)] = students

    def fake_get_monitored_students():
        return dict(stores["monitored"])

    def fake_set_monitored_student(user_id, name, note=""):
        stores["monitored"][str(user_id)] = {"name": name, "note": note}

    def fake_remove_monitored_student(user_id):
        stores["monitored"].pop(str(user_id), None)

    def fake_get_roster_student_settings(course_id):
        return stores["settings"].get(str(course_id), {})

    def fake_update_roster_student_settings(course_id, user_id, patch):
        course = stores["settings"].setdefault(str(course_id), {})
        row = course.setdefault(str(user_id), {})
        for key, value in patch.items():
            if value is None:
                row.pop(key, None)
            else:
                row[key] = value

    def fake_get_roster_score_matrix(course_id):
        return stores["score_matrices"].get(str(course_id), {})

    def fake_set_roster_score_matrix(course_id, matrix):
        stores["score_matrices"][str(course_id)] = matrix

    def fake_get_roster_relationships(course_id):
        return stores["relationships"].get(str(course_id), {})

    def fake_set_roster_relationships(course_id, relationships):
        stores["relationships"][str(course_id)] = relationships

    def fake_get_roster_baseline(course_id):
        return stores["baselines"].get(str(course_id), config.ROSTER_BASELINE_DEFAULT)

    def fake_set_roster_baseline(course_id, baseline):
        stores["baselines"][str(course_id)] = baseline

    def fake_get_roster_group_scheme(course_id):
        return stores.get("group_schemes", {}).get(str(course_id), {})

    def fake_set_roster_group_scheme(course_id, scheme):
        stores.setdefault("group_schemes", {})[str(course_id)] = scheme

    def fake_get_selected_group_category_id(course_id):
        return fake_get_roster_group_scheme(course_id).get("selected_group_category_id")

    def fake_set_selected_group_category_id(course_id, cat_id):
        scheme = fake_get_roster_group_scheme(course_id)
        scheme["selected_group_category_id"] = cat_id
        fake_set_roster_group_scheme(course_id, scheme)

    def fake_get_group_label(course_id, group_id):
        scheme = fake_get_roster_group_scheme(course_id)
        return scheme.get("group_labels", {}).get(str(group_id))

    monkeypatch.setattr(roster_routes, "_vault", lambda: stores["vault"])
    monkeypatch.setattr(roster_routes, "_upsert_roster", lambda vault, users: None)
    monkeypatch.setattr(roster_routes, "_fetch_students", lambda course_id: ([], "No token saved."))
    monkeypatch.setattr(roster_routes, "_fetch_sections", lambda course_id: {})
    monkeypatch.setattr(roster_routes, "load_group_categories", lambda course_id: ([], None, ""))
    monkeypatch.setattr(roster_routes.config, "get_extra_time", fake_get_extra_time)
    monkeypatch.setattr(roster_routes.config, "set_extra_time", fake_set_extra_time)
    monkeypatch.setattr(roster_routes.config, "get_monitored_students", fake_get_monitored_students)
    monkeypatch.setattr(roster_routes.config, "set_monitored_student", fake_set_monitored_student)
    monkeypatch.setattr(roster_routes.config, "remove_monitored_student", fake_remove_monitored_student)
    monkeypatch.setattr(roster_routes.config, "get_roster_student_settings", fake_get_roster_student_settings)
    monkeypatch.setattr(roster_routes.config, "update_roster_student_settings", fake_update_roster_student_settings)
    monkeypatch.setattr(roster_routes.config, "get_roster_score_matrix", fake_get_roster_score_matrix)
    monkeypatch.setattr(roster_routes.config, "set_roster_score_matrix", fake_set_roster_score_matrix)
    monkeypatch.setattr(roster_routes.config, "get_roster_relationships", fake_get_roster_relationships)
    monkeypatch.setattr(roster_routes.config, "set_roster_relationships", fake_set_roster_relationships)
    monkeypatch.setattr(roster_routes.config, "get_roster_baseline", fake_get_roster_baseline)
    monkeypatch.setattr(roster_routes.config, "set_roster_baseline", fake_set_roster_baseline)
    monkeypatch.setattr(roster_routes.config, "active_protected_names", lambda: set())
    monkeypatch.setattr(roster_routes.config, "get_roster_group_scheme", fake_get_roster_group_scheme)
    monkeypatch.setattr(roster_routes.config, "set_roster_group_scheme", fake_set_roster_group_scheme)
    monkeypatch.setattr(roster_routes.config, "get_selected_group_category_id", fake_get_selected_group_category_id)
    monkeypatch.setattr(roster_routes.config, "set_selected_group_category_id", fake_set_selected_group_category_id)
    monkeypatch.setattr(roster_routes.config, "get_group_label", fake_get_group_label)
    monkeypatch.setattr(roster_routes.config, "compute_group_display",
                        lambda label, name: f"{label} / {name}" if label and label != name else name)
    return stores


def test_roster_get_requires_course_id():
    resp = client.get("/api/roster")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "course_id" in data.get("error", "").lower()


def test_roster_get_handles_missing_token():
    resp = client.get("/api/roster?course_id=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "error" in data
    assert data.get("ok") is False
    assert "error" in data


def test_roster_get_merges_sources_without_sis(monkeypatch, isolated_roster):
    isolated_roster["extra_time"]["1"] = [{"id": "101", "name": "Ada Lovelace", "days": 2}]
    isolated_roster["monitored"]["101"] = {"name": "Ada Lovelace", "note": "Private note"}
    users = [{
        "id": 101,
        "name": "Ada Lovelace",
        "sortable_name": "Lovelace, Ada",
        "short_name": "Ada",
        "sis_user_id": "SIS-SECRET",
        "enrollments": [{"course_section_id": 44}],
    }]
    groups = [{
        "category_id": "7",
        "category_name": "Reading tiers",
        "groups": [{"id": "8", "name": "Blue", "student_ids": [101]}],
    }]
    monkeypatch.setattr(roster_routes, "_fetch_students", lambda course_id: (users, None))
    monkeypatch.setattr(roster_routes, "_fetch_sections", lambda course_id: {"44": "Period 1"})
    monkeypatch.setattr(roster_routes, "load_group_categories", lambda course_id: (groups, None, ""))
    monkeypatch.setattr(roster_routes.mirror_store, "read_groups", lambda course_id: None)
    monkeypatch.setattr(roster_routes.mirror_store, "write_groups", lambda course_id, categories: None)

    resp = client.get("/api/roster?course_id=1")
    data = resp.json()

    assert data["ok"] is True
    # V3: groups and group_label_scheme instead of tier_scheme
    assert "groups" in data
    assert "group_label_scheme" in data
    row = data["students"][0]
    assert row["id"] == "101"
    assert row["nicknames"] == ["Addie"]
    assert row["pseudonym"] == _WORDS[0]
    assert "pseudo_first" not in row
    assert "pseudo_last" not in row
    assert row["extra_time"] == {"enabled": True, "days": 2}
    assert row["monitored"] == {"enabled": True, "note": "Private note"}
    assert data["score_matrix"] == {"columns": [], "values_by_section": {}}
    # V3: canvas_group instead of tier_id
    assert "canvas_group" in row
    assert row["canvas_groups"][0]["group_name"] == "Blue"
    assert "sis_id" not in row
    assert "Groups" not in str(data.get("groups", ""))


def test_roster_score_matrix_patch_round_trips_through_roster_get(monkeypatch, isolated_roster):
    users = [{
        "id": 101,
        "name": "Test Student",
        "sortable_name": "Student, Test",
        "short_name": "Test",
        "enrollments": [{"course_section_id": "section-a"}],
    }]
    columns = [
        {"id": "score-writing", "label": "Writing"},
        {"id": "score-reading", "label": "Reading"},
    ]
    monkeypatch.setattr(roster_routes, "_fetch_students", lambda course_id: (users, None))
    monkeypatch.setattr(roster_routes, "_fetch_sections", lambda course_id: {"section-a": "Section A"})
    monkeypatch.setattr(roster_routes.mirror_store, "read_groups", lambda course_id: None)
    monkeypatch.setattr(roster_routes.mirror_store, "write_groups", lambda course_id, categories: None)

    created = client.post("/api/roster/score-matrix", data={
        "course_id": "course-a",
        "patch": json.dumps({"columns": columns}),
    }).json()
    saved = client.post("/api/roster/score-matrix", data={
        "course_id": "course-a",
        "patch": json.dumps({
            "section_id": "section-a",
            "values": {"101": {"score-writing": 0, "score-reading": 12.5}},
        }),
    }).json()
    reloaded = client.get("/api/roster?course_id=course-a").json()["score_matrix"]

    assert created["ok"] is True
    assert saved["ok"] is True
    assert reloaded == {
        "columns": columns,
        "values_by_section": {
            "section-a": {"101": {"score-writing": 0, "score-reading": 12.5}},
        },
    }

    cleared = client.post("/api/roster/score-matrix", data={
        "course_id": "course-a",
        "patch": json.dumps({
            "section_id": "section-a",
            "values": {"101": {"score-writing": None}},
        }),
    }).json()

    assert cleared["ok"] is True
    assert cleared["score_matrix"]["values_by_section"]["section-a"]["101"] == {
        "score-reading": 12.5,
    }
    assert "course-b" not in isolated_roster["score_matrices"]


@pytest.mark.parametrize("patch", [
    {"columns": [{"id": "bad id", "label": "Writing"}]},
    {"columns": [
        {"id": "score-a", "label": "Writing"},
        {"id": "score-b", "label": " writing "},
    ]},
    {"section_id": "section-a", "values": {"student-a": {"unknown": 1}}},
    {"section_id": "section-a", "values": {"student-a": {"score-a": True}}},
    {"section_id": "section-a", "values": None},
    {"section_id": "invalid id", "values": {"student-a": {"score-a": None}}},
])
def test_roster_score_matrix_rejects_invalid_patch_atomically(isolated_roster, patch):
    original = {
        "columns": [{"id": "score-a", "label": "Writing"}],
        "values_by_section": {"section-a": {"student-a": {"score-a": 3}}},
    }
    isolated_roster["score_matrices"]["course-a"] = original

    response = client.post("/api/roster/score-matrix", data={
        "course_id": "course-a", "patch": json.dumps(patch),
    }).json()

    assert response["ok"] is False
    assert isolated_roster["score_matrices"]["course-a"] == original


def test_roster_relationships_replace_one_section_and_round_trip(monkeypatch, isolated_roster):
    users = [{
        "id": "student-a", "name": "Test Student A", "sortable_name": "A, Test",
        "short_name": "A", "enrollments": [{"course_section_id": "section-a"}],
    }, {
        "id": "student-b", "name": "Test Student B", "sortable_name": "B, Test",
        "short_name": "B", "enrollments": [{"course_section_id": "section-a"}],
    }]
    monkeypatch.setattr(roster_routes, "_fetch_students", lambda course_id: (users, None))
    monkeypatch.setattr(roster_routes, "_fetch_sections", lambda course_id: {"section-a": "Section A"})
    monkeypatch.setattr(roster_routes.mirror_store, "read_groups", lambda course_id: None)
    monkeypatch.setattr(roster_routes.mirror_store, "write_groups", lambda course_id, categories: None)

    response = client.post("/api/roster/relationships", data={
        "course_id": "course-a", "section_id": "section-a",
        "relationships": json.dumps([{
            "student_a": "student-b", "student_b": "student-a",
            "type": "preferred_pair", "reason": "private local reason",
        }]),
    }).json()

    assert response == {"ok": True, "relationships": {"by_section": {"section-a": [{
        "student_a": "student-a", "student_b": "student-b",
        "type": "preferred_pair", "reason": "private local reason",
    }]}}}
    assert client.get("/api/roster?course_id=course-a").json()["relationships"] == response["relationships"]


@pytest.mark.parametrize("items", [
    [{"student_a": "student-a", "student_b": "student-a", "type": "keep_apart", "reason": ""}],
    [{"student_a": "student-a", "student_b": "student-b", "type": "unknown", "reason": ""}],
    [{"student_a": "student-a", "student_b": "student-b", "type": "keep_apart", "reason": 3}],
    [{"student_a": "student-a", "student_b": "student-b", "type": "keep_apart", "reason": ""},
     {"student_a": "student-b", "student_b": "student-a", "type": "preferred_pair", "reason": ""}],
])
def test_roster_relationships_reject_invalid_replace_atomically(isolated_roster, items):
    original = {"by_section": {"section-a": [{
        "student_a": "student-a", "student_b": "student-b",
        "type": "keep_apart", "reason": "saved",
    }]}}
    isolated_roster["relationships"]["course-a"] = original

    response = client.post("/api/roster/relationships", data={
        "course_id": "course-a", "section_id": "section-a", "relationships": json.dumps(items),
    }).json()

    assert response["ok"] is False
    assert isolated_roster["relationships"]["course-a"] == original


def test_roster_get_uses_current_mirror_before_live_students_and_sections(
    monkeypatch, isolated_roster,
):
    roster_document = {
        "state": "current",
        "students": {
            "101": {
                "id": "101",
                "name": "Ada Lovelace",
                "sortable_name": "Lovelace, Ada",
                "short_name": "Ada",
                "sis_user_id": "SIS-SECRET",
                "enrollments": [{"course_section_id": "44"}],
            }
        },
        "sections": {"44": "Period 1"},
    }
    group_calls = []

    monkeypatch.setattr(roster_routes.mirror_store, "read_roster",
                        lambda course_id: roster_document)
    monkeypatch.setattr(roster_routes, "_fetch_students",
                        lambda course_id: pytest.fail("students must use the mirror"))
    monkeypatch.setattr(roster_routes, "_fetch_sections",
                        lambda course_id: pytest.fail("sections must use the mirror"))
    monkeypatch.setattr(
        roster_routes,
        "load_group_categories",
        lambda course_id: (group_calls.append(course_id) or [], None, ""),
    )
    monkeypatch.setattr(roster_routes.mirror_store, "read_groups", lambda course_id: None)
    monkeypatch.setattr(roster_routes.mirror_store, "write_groups", lambda course_id, categories: None)

    data = client.get("/api/roster?course_id=1").json()

    assert data["ok"] is True
    assert data["students"][0]["id"] == "101"
    assert data["students"][0]["sections"] == [{"id": "44", "name": "Period 1"}]
    assert "sis_id" not in data["students"][0]
    assert group_calls == ["1"]


def _current_roster_document():
    return {
        "state": "current",
        "students": {
            "101": {
                "id": "101", "name": "Ada Lovelace", "sortable_name": "Lovelace, Ada",
                "short_name": "Ada", "sis_user_id": "", "enrollments": [],
            }
        },
        "sections": {},
    }


def _groups_snapshot(state="current"):
    return {
        "state": state,
        "last_success_at": mirror_store.now_iso(),
        "categories": [{
            "category_id": "7", "category_name": "Reading groups",
            "groups": [{"id": "8", "name": "Blue", "memberships": [{"id": "9", "user_id": "101"}]}],
        }],
    }


def test_roster_get_uses_fresh_private_groups_without_live_loader(monkeypatch, isolated_roster):
    monkeypatch.setattr(roster_routes.mirror_store, "read_roster",
                        lambda course_id: _current_roster_document())
    monkeypatch.setattr(roster_routes.mirror_store, "read_groups",
                        lambda course_id: _groups_snapshot())
    monkeypatch.setattr(roster_routes, "load_group_categories",
                        lambda course_id: pytest.fail("fresh group snapshot must avoid the live loader"))

    data = client.get("/api/roster?course_id=1").json()

    assert data["ok"] is True
    assert data["groups"][0]["groups"][0]["student_ids"] == ["101"]
    assert data["students"][0]["canvas_groups"][0]["group_name"] == "Blue"


@pytest.mark.parametrize("snapshot", [None, _groups_snapshot("stale")])
def test_roster_get_falls_back_live_for_missing_or_stale_groups(monkeypatch, isolated_roster, snapshot):
    live_categories = [{
        "category_id": "7", "category_name": "Live groups",
        "groups": [{"id": "8", "name": "Blue", "memberships": [{"id": "9", "user_id": "101"}]}],
    }]
    writes = []
    monkeypatch.setattr(roster_routes.mirror_store, "read_roster",
                        lambda course_id: _current_roster_document())
    monkeypatch.setattr(roster_routes.mirror_store, "read_groups", lambda course_id: snapshot)
    monkeypatch.setattr(roster_routes, "load_group_categories",
                        lambda course_id: (live_categories, None, ""))
    monkeypatch.setattr(roster_routes.mirror_store, "write_groups",
                        lambda course_id, categories: writes.append((course_id, categories)))

    data = client.get("/api/roster?course_id=1").json()

    assert data["ok"] is True
    assert data["groups"][0]["category_name"] == "Live groups"
    assert writes == [("1", live_categories)]


def test_roster_live_group_failure_does_not_replace_snapshot(monkeypatch, isolated_roster):
    monkeypatch.setattr(roster_routes.mirror_store, "read_roster",
                        lambda course_id: _current_roster_document())
    monkeypatch.setattr(roster_routes.mirror_store, "read_groups",
                        lambda course_id: _groups_snapshot("stale"))
    monkeypatch.setattr(roster_routes, "load_group_categories",
                        lambda course_id: ([], "forbidden", ""))
    monkeypatch.setattr(roster_routes.mirror_store, "write_groups",
                        lambda *args: pytest.fail("failed live read must preserve last-good snapshot"))

    data = client.get("/api/roster?course_id=1").json()

    assert data["ok"] is True
    assert data["groups"] == []


def test_private_group_snapshot_round_trip_has_only_allowlisted_fields(tmp_path):
    mirror_store.write_groups("1", [{
        "category_id": 7, "category_name": "Reading groups", "ignored": "not persisted",
        "groups": [{
            "id": 8, "name": "Blue", "other": "not persisted",
            "memberships": [{"id": 9, "user_id": 101, "user_name": "not persisted"}],
        }],
    }], root=tmp_path, attempted_at="2026-07-18T00:00:00Z")

    with open(mirror_store.groups_path("1", root=tmp_path), encoding="utf-8") as handle:
        document = json.load(handle)

    assert set(document) == {
        "schema_version", "course_id", "state", "last_success_at", "last_attempt_at", "error_code", "categories",
    }
    membership_id = document["categories"][0]["groups"][0]["memberships"][0]["user_id"]
    assert document["categories"] == [{
        "category_id": "7", "category_name": "Reading groups",
        "groups": [{"id": "8", "name": "Blue", "memberships": [{"id": "9", "user_id": membership_id}]}],
    }]
    assert membership_id != "101" and membership_id.isalpha()
    stale = mirror_store.invalidate_groups("1", root=tmp_path, attempted_at="2026-07-18T01:00:00Z")
    assert stale["state"] == "stale"
    assert stale["categories"] == document["categories"]
    assert mirror_store.groups_are_current(stale, max_age_hours=24) is False


def test_private_group_snapshot_rejects_ambiguous_duplicate_ids(tmp_path):
    duplicate_category = [
        {"category_id": "7", "category_name": "One", "groups": []},
        {"category_id": "7", "category_name": "Two", "groups": []},
    ]
    duplicate_group = [{
        "category_id": "7", "category_name": "One",
        "groups": [
            {"id": "8", "name": "Blue", "memberships": []},
            {"id": "8", "name": "Green", "memberships": []},
        ],
    }]
    duplicate_membership_id = [{
        "category_id": "7", "category_name": "One",
        "groups": [{
            "id": "8", "name": "Blue",
            "memberships": [{"id": "9", "user_id": "101"}, {"id": "9", "user_id": "102"}],
        }],
    }]
    duplicate_user_id = [{
        "category_id": "7", "category_name": "One",
        "groups": [{
            "id": "8", "name": "Blue",
            "memberships": [{"id": "9", "user_id": "101"}, {"id": "10", "user_id": "101"}],
        }],
    }]

    for categories in (duplicate_category, duplicate_group, duplicate_membership_id, duplicate_user_id):
        with pytest.raises(ValueError):
            mirror_store.write_groups("1", categories, root=tmp_path)


@pytest.mark.parametrize(
    ("case", "roster_document"),
    [
        ("missing", None),
        # read_roster returns None for a corrupt or invalid on-disk document.
        ("corrupt", None),
        ("stale", {"state": "stale", "students": {}, "sections": {}}),
    ],
)
def test_roster_get_falls_back_live_when_mirror_is_not_current(
    monkeypatch, isolated_roster, case, roster_document,
):
    users = [{
        "id": 101,
        "name": "Live Ada",
        "sortable_name": "Ada, Live",
        "short_name": "Ada",
        "enrollments": [{"course_section_id": 44}],
    }]
    calls = []

    monkeypatch.setattr(roster_routes.mirror_store, "read_roster",
                        lambda course_id: roster_document)
    monkeypatch.setattr(roster_routes, "_fetch_students",
                        lambda course_id: (calls.append("students") or (users, None)))
    monkeypatch.setattr(roster_routes, "_fetch_sections",
                        lambda course_id: (calls.append("sections") or {"44": "Live section"}))
    monkeypatch.setattr(roster_routes, "load_group_categories", lambda course_id: ([], None, ""))

    data = client.get("/api/roster?course_id=1").json()

    assert data["ok"] is True, case
    assert data["students"][0]["display_name"] == "Live Ada"
    assert calls == ["students", "sections"]


def test_roster_get_handles_student_without_canvas_group(monkeypatch, isolated_roster):
    isolated_roster["group_schemes"] = {
        "1": {"selected_group_category_id": "7", "group_labels": {}}
    }
    users = [{
        "id": 101,
        "name": "Ada Lovelace",
        "sortable_name": "Lovelace, Ada",
        "short_name": "Ada",
        "enrollments": [{"course_section_id": 44}],
    }]
    groups = [{
        "category_id": "7",
        "category_name": "Reading tiers",
        "groups": [{"id": "8", "name": "Blue", "student_ids": []}],
    }]
    monkeypatch.setattr(roster_routes, "_fetch_students", lambda course_id: (users, None))
    monkeypatch.setattr(roster_routes, "_fetch_sections", lambda course_id: {"44": "Period 1"})
    monkeypatch.setattr(roster_routes, "load_group_categories", lambda course_id: (groups, None, ""))

    resp = client.get("/api/roster?course_id=1")
    data = resp.json()

    assert resp.status_code == 200
    assert data["ok"] is True
    assert data["students"][0]["canvas_group"] is None
    assert data["counts"]["group_unset"] == 1
    assert "group_unset" in data["students"][0]["warnings"]


def test_create_group_set_with_groups(monkeypatch, isolated_roster):
    calls = []
    invalidations = []

    def fake_canvas_send(method, path, payload):
        calls.append((method, path, payload))
        if path == "/api/v1/courses/1/group_categories":
            return {"id": 7, "name": payload["name"]}, None
        if path == "/api/v1/group_categories/7/groups":
            return {"id": len(calls), "name": payload["name"]}, None
        return None, "unexpected call"

    def fake_reconcile(course_id, category_id, category_name=None):
        invalidations.append((course_id, category_id, category_name))
        # Also land a marker in the shared ``calls`` sequence so the ordering
        # assertion below proves reconciliation fires only after every seed
        # group already exists in Canvas — not right after category
        # creation (see 1.0beta-05a's create_group_set fix).
        calls.append(("reconcile", category_id, category_name))

    monkeypatch.setattr(roster_routes, "_canvas_send", fake_canvas_send)
    monkeypatch.setattr(roster_routes, "_reconcile_group_category", fake_reconcile)

    resp = client.post("/api/roster/group-set", data={
        "course_id": "1",
        "name": "Reading groups",
        "group_names": '["Blue", "Green"]',
    })
    data = resp.json()

    assert data["ok"] is True
    assert data["group_category"]["id"] == 7
    assert [g["name"] for g in data["created_groups"]] == ["Blue", "Green"]
    assert isolated_roster["group_schemes"]["1"]["selected_group_category_id"] == "7"
    assert calls == [
        ("POST", "/api/v1/courses/1/group_categories", {"name": "Reading groups"}),
        ("POST", "/api/v1/group_categories/7/groups", {"name": "Blue"}),
        ("POST", "/api/v1/group_categories/7/groups", {"name": "Green"}),
        ("reconcile", "7", "Reading groups"),
    ]
    assert invalidations == [("1", "7", "Reading groups")]


def test_create_group_set_reconciliation_shows_seeded_groups_not_zero(
    monkeypatch, tmp_path, isolated_roster,
):
    """1.0beta-05a fix: reconciling right after category creation (before the
    seed-groups loop) would merge in a category with zero groups that never
    self-corrects. Reconciliation must fire only after every seed group
    already exists in Canvas, so the merged snapshot shows them immediately."""
    _mount_groups_workspace(monkeypatch, tmp_path)
    # A previous groups document must already exist for the merge to run at
    # all (targeted reconciliation only ever merges into a previously
    # written document); seed one unrelated category to also prove it stays
    # untouched.
    mirror_store.write_groups("1", [{
        "category_id": "99", "category_name": "Other set",
        "groups": [{"id": "100", "name": "Other", "memberships": []}],
    }])

    sequence = []

    def fake_canvas_send(method, path, payload):
        sequence.append(("canvas_send", path))
        if path == "/api/v1/courses/1/group_categories":
            return {"id": 7, "name": payload["name"]}, None
        if path == "/api/v1/group_categories/7/groups":
            return {"id": 10 + len(sequence), "name": payload["name"]}, None
        return None, "unexpected call"

    def fake_fetch(course_id, category_id):
        sequence.append(("fetch", category_id))
        # By the time reconciliation fetches this category, Canvas must
        # already have both seed groups — proving the call fired after the
        # seed loop, not right after category creation.
        return [
            {"id": "12", "name": "Blue", "student_ids": [], "memberships": []},
            {"id": "13", "name": "Green", "student_ids": [], "memberships": []},
        ], None

    monkeypatch.setattr(roster_routes, "_canvas_send", fake_canvas_send)
    monkeypatch.setattr(roster_routes, "fetch_group_category_groups", fake_fetch)

    resp = client.post("/api/roster/group-set", data={
        "course_id": "1",
        "name": "Reading groups",
        "group_names": '["Blue", "Green"]',
    })
    data = resp.json()

    assert data["ok"] is True
    assert sequence == [
        ("canvas_send", "/api/v1/courses/1/group_categories"),
        ("canvas_send", "/api/v1/group_categories/7/groups"),
        ("canvas_send", "/api/v1/group_categories/7/groups"),
        ("fetch", "7"),
    ]

    document = mirror_store.read_groups("1")
    assert document["state"] == "current"
    new_category = next(c for c in document["categories"] if c["category_id"] == "7")
    assert new_category["category_name"] == "Reading groups"
    assert [g["name"] for g in new_category["groups"]] == ["Blue", "Green"]
    other = next(c for c in document["categories"] if c["category_id"] == "99")
    assert [g["name"] for g in other["groups"]] == ["Other"]


def test_create_group_set_partial_failure_reconciles_created_groups(
    monkeypatch, tmp_path, isolated_roster,
):
    """A seed group failing partway through must still reconcile whatever
    groups were actually created before the failure, not lose them."""
    _mount_groups_workspace(monkeypatch, tmp_path)
    mirror_store.write_groups("1", [{
        "category_id": "99", "category_name": "Other set",
        "groups": [{"id": "100", "name": "Other", "memberships": []}],
    }])

    def fake_canvas_send(method, path, payload):
        if path == "/api/v1/courses/1/group_categories":
            return {"id": 7, "name": payload["name"]}, None
        if path == "/api/v1/group_categories/7/groups":
            if payload["name"] == "Green":
                return None, "Canvas rejected 'Green'"
            return {"id": 12, "name": payload["name"]}, None
        return None, "unexpected call"

    def fake_fetch(course_id, category_id):
        assert category_id == "7"
        return [{"id": "12", "name": "Blue", "student_ids": [], "memberships": []}], None

    monkeypatch.setattr(roster_routes, "_canvas_send", fake_canvas_send)
    monkeypatch.setattr(roster_routes, "fetch_group_category_groups", fake_fetch)

    resp = client.post("/api/roster/group-set", data={
        "course_id": "1",
        "name": "Reading groups",
        "group_names": '["Blue", "Green"]',
    })
    data = resp.json()

    assert data["ok"] is False
    assert "Green" in data["error"]

    document = mirror_store.read_groups("1")
    new_category = next(c for c in document["categories"] if c["category_id"] == "7")
    assert [g["name"] for g in new_category["groups"]] == ["Blue"]


def test_create_group_set_first_seed_failure_still_reconciles_new_empty_category(
    monkeypatch, tmp_path, isolated_roster,
):
    """Unlike ``create_groups`` (an already-existing category, where nothing
    changed if the first new group fails), ``create_group_set``'s category
    POST already succeeded unconditionally before the seed loop starts. Even
    if the very first seed group name fails — so ``created_groups`` is still
    empty — that brand-new category is real Canvas state and must still be
    reconciled into the mirror (present, zero groups), not left absent."""
    _mount_groups_workspace(monkeypatch, tmp_path)
    mirror_store.write_groups("1", [{
        "category_id": "99", "category_name": "Other set",
        "groups": [{"id": "100", "name": "Other", "memberships": []}],
    }])

    def fake_canvas_send(method, path, payload):
        if path == "/api/v1/courses/1/group_categories":
            return {"id": 7, "name": payload["name"]}, None
        if path == "/api/v1/group_categories/7/groups":
            return None, "Canvas rejected 'Blue'"
        return None, "unexpected call"

    def fake_fetch(course_id, category_id):
        assert category_id == "7"
        return [], None

    monkeypatch.setattr(roster_routes, "_canvas_send", fake_canvas_send)
    monkeypatch.setattr(roster_routes, "fetch_group_category_groups", fake_fetch)

    resp = client.post("/api/roster/group-set", data={
        "course_id": "1",
        "name": "Reading groups",
        "group_names": '["Blue", "Green"]',
    })
    data = resp.json()

    assert data["ok"] is False
    assert data["created_groups"] == []

    document = mirror_store.read_groups("1")
    new_category = next((c for c in document["categories"] if c["category_id"] == "7"), None)
    assert new_category is not None, "the new category must not be silently absent"
    assert new_category["category_name"] == "Reading groups"
    assert new_category["groups"] == []
    other = next(c for c in document["categories"] if c["category_id"] == "99")
    assert [g["name"] for g in other["groups"]] == ["Other"]


def test_create_groups_rejects_existing_name(monkeypatch):
    groups = [{
        "category_id": "7",
        "category_name": "Reading tiers",
        "groups": [{"id": "8", "name": "Blue", "student_ids": []}],
    }]
    monkeypatch.setattr(roster_routes, "load_group_categories", lambda course_id: (groups, None, ""))

    resp = client.post("/api/roster/groups", data={
        "course_id": "1",
        "category_id": "7",
        "group_names": '["Blue"]',
    })
    data = resp.json()

    assert data["ok"] is False
    assert "already exists" in data["error"]


def test_roster_student_requires_ids():
    resp = client.post("/api/roster/student", data={
        "course_id": "", "user_id": "", "patch": "{}"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "course_id" in data.get("error", "").lower()


def test_roster_student_validates_patch_json():
    resp = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101", "patch": "not-json"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "json" in data.get("error", "").lower()


def test_roster_student_requires_patch_object():
    resp = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101", "patch": '["not-object"]'
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "object" in data.get("error", "").lower()


def test_roster_student_rejects_unknown_keys():
    resp = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101", "patch": '{"unknown_key": true}'
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "unknown" in data.get("error", "").lower()


def test_roster_student_validates_nicknames_type():
    resp = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101", "patch": '{"nicknames": "not-a-list"}'
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "list" in data.get("error", "").lower()


def test_roster_student_validates_pseudonym_shape():
    resp = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101",
        "patch": '{"pseudonym": {"bad": "shape"}}'
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "string" in data.get("error", "").lower()


def _real_vault_pair(monkeypatch, tmp_path):
    """Two students in a real (not fake-doubled) Vault, wired onto the route,
    for the pseudonym patch Contract test below -- the deeper registry and
    collision checks only exist on the real `Vault`, not `FakeVault`."""
    vault = Vault(str(tmp_path / "vault.json"))
    with vault.transaction():
        first = vault.get_or_assign("101", "Ada Lovelace", "SIS-101")
        second = vault.get_or_assign("102", "Alan Turing", "SIS-102")
    monkeypatch.setattr(roster_routes, "_vault", lambda: vault)
    monkeypatch.setattr(roster_routes, "_fetch_students", lambda course_id: ([], "No token saved."))
    monkeypatch.setattr(roster_routes, "_fetch_sections", lambda course_id: {})
    monkeypatch.setattr(roster_routes, "load_group_categories", lambda course_id: ([], None, ""))
    return vault, first, second


@pytest.mark.parametrize("patch_value,accepted", [
    ("VALID", True),
    ("", False),
    ("   ", False),
    ("Two Words", False),
    (123, False),
    ("Notarealregistryword", False),
    ("COLLIDING", False),
])
def test_roster_student_pseudonym_patch_boundary(monkeypatch, tmp_path, patch_value, accepted):
    """Contract: the Roster route's pseudonym patch accepts exactly one
    available registry word and refuses blank, multiword, non-string,
    out-of-registry, and colliding values without a partial write."""
    vault, first, second = _real_vault_pair(monkeypatch, tmp_path)
    if patch_value == "VALID":
        patch_value = next(w for w in _WORDS if w not in (first, second))
    elif patch_value == "COLLIDING":
        patch_value = second  # already held by canvas_id 102

    resp = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101",
        "patch": json.dumps({"pseudonym": patch_value}),
    })
    data = resp.json()
    reloaded = Vault(str(tmp_path / "vault.json"))

    if accepted:
        assert data["ok"] is True
        assert reloaded.get_or_assign("101") == patch_value
    else:
        assert data["ok"] is False
        assert reloaded.get_or_assign("101") == first, "a rejected value must not mutate the vault"


def test_roster_student_regeneration_returns_new_unused_pseudonym(monkeypatch, tmp_path):
    vault, first, second = _real_vault_pair(monkeypatch, tmp_path)

    resp = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101",
        "patch": json.dumps({"regenerate_pseudonym": True}),
    })

    data = resp.json()
    reloaded = Vault(str(tmp_path / "vault.json"))
    assert data["ok"] is True
    assert data["pseudonym"] == reloaded.get_or_assign("101")
    assert data["pseudonym"] not in {first, second}


def test_roster_student_saves_and_clears_classroom_profile(monkeypatch, isolated_roster):
    users = [{"id": 101, "name": "Test Student", "sortable_name": "Student, Test",
              "short_name": "Test", "enrollments": []}]
    monkeypatch.setattr(roster_routes, "_fetch_students", lambda course_id: (users, None))
    monkeypatch.setattr(roster_routes, "_fetch_sections", lambda course_id: {})
    profile = {"birthday": "09-08", "celebrations": [{
        "id": "celebration-1", "label": "Helpful teammate",
        "start": "2026-09-08", "end": "2026-09-12",
    }]}
    saved = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101",
        "patch": json.dumps({"classroom_profile": profile}),
    }).json()
    row = client.get("/api/roster?course_id=1").json()["students"][0]

    assert saved["ok"] is True
    assert row["classroom_profile"] == profile
    assert isolated_roster["settings"]["1"]["101"]["classroom_profile"] == profile

    cleared = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101",
        "patch": json.dumps({"classroom_profile": None}),
    }).json()
    assert cleared["ok"] is True
    assert "classroom_profile" not in isolated_roster["settings"]["1"]["101"]


@pytest.mark.parametrize("profile", [
    {"birthday": "09-08", "celebrations": [{"id": "x", "label": "<bad>", "start": "2026-09-08", "end": "2026-09-08"}]},
    {"birthday": "", "celebrations": [{"id": "x", "label": "One", "start": "2026-09-09", "end": "2026-09-08"}]},
    {"birthday": "", "celebrations": [{"id": "x", "label": "One", "start": "2026-09-08", "end": "2026-09-08", "unexpected": True}]},
])
def test_roster_student_rejects_invalid_classroom_profile_without_write(isolated_roster, profile):
    original = {"tier": "Support"}
    isolated_roster["settings"]["1"] = {"101": original.copy()}
    response = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101",
        "patch": json.dumps({"classroom_profile": profile}),
    }).json()
    assert response["ok"] is False
    assert isolated_roster["settings"]["1"]["101"] == original


def test_roster_get_warns_with_affected_student_when_profile_is_corrupt(monkeypatch, isolated_roster):
    users = [{"id": 101, "name": "Test Student", "sortable_name": "Student, Test",
              "short_name": "Test", "enrollments": []}]
    monkeypatch.setattr(roster_routes, "_fetch_students", lambda course_id: (users, None))
    monkeypatch.setattr(roster_routes, "_fetch_sections", lambda course_id: {})
    isolated_roster["settings"]["1"] = {"101": {"classroom_profile": {"bad": True}}}
    data = client.get("/api/roster?course_id=1").json()
    assert "classroom_profile_invalid" in data["students"][0]["warnings"]
    assert "Test Student" in data["note"]
    assert data["students"][0]["classroom_profile"] == config.empty_classroom_profile()


def test_roster_student_rejects_obsolete_tier_id(isolated_roster):
    """V3: tier_id is obsolete and should be rejected with clear error."""
    resp = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101", "patch": '{"tier_id": "support"}'
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "obsolete" in data.get("error", "").lower()
    assert "canvas_group" in data.get("error", "").lower()


def test_roster_student_rejects_obsolete_tier(isolated_roster):
    """V3: tier is obsolete and should be rejected with clear error."""
    resp = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101", "patch": '{"tier": "Support"}'
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "obsolete" in data.get("error", "").lower()


def test_roster_student_accepts_canvas_group(monkeypatch, isolated_roster):
    """V3: canvas_group writes the selected Canvas group membership."""
    calls = []
    monkeypatch.setattr(roster_routes, "load_group_categories",
                        lambda course_id: ([{
                            "category_id": "7",
                            "category_name": "Differentiation",
                            "groups": [{"id": "8", "name": "Blue", "student_ids": [], "memberships": []}],
                        }], None, ""))
    monkeypatch.setattr(roster_routes, "_update_student_canvas_group",
                        lambda *args: calls.append(args) or (True, None))
    resp = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101",
        "patch": '{"canvas_group": {"category_id": "7", "group_id": "8"}}'
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert calls
    assert calls[0][:4] == ("1", "101", "7", "8")


def test_roster_group_membership_write_invalidates_only_after_success(monkeypatch, isolated_roster):
    invalidations = []
    monkeypatch.setattr(roster_routes, "load_group_categories", lambda course_id: ([{
        "category_id": "7", "category_name": "Differentiation",
        "groups": [{"id": "8", "name": "Blue", "student_ids": [], "memberships": []}],
    }], None, ""))
    monkeypatch.setattr(roster_routes, "_reconcile_group_category",
                        lambda course_id, category_id, category_name=None:
                            invalidations.append((course_id, category_id)))
    monkeypatch.setattr(roster_routes, "_update_student_canvas_group",
                        lambda *args: (False, "Canvas denied"))

    failed = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101",
        "patch": '{"canvas_group": {"category_id": "7", "group_id": "8"}}',
    }).json()

    assert failed["ok"] is False
    assert invalidations == []

    monkeypatch.setattr(roster_routes, "_update_student_canvas_group", lambda *args: (True, None))
    succeeded = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101",
        "patch": '{"canvas_group": {"category_id": "7", "group_id": "8"}}',
    }).json()

    assert succeeded["ok"] is True
    assert invalidations == [("1", "7")]


def test_roster_student_rejects_canvas_group_outside_category(monkeypatch, isolated_roster):
    monkeypatch.setattr(roster_routes, "load_group_categories",
                        lambda course_id: ([{
                            "category_id": "7",
                            "category_name": "Differentiation",
                            "groups": [{"id": "8", "name": "Blue", "student_ids": [], "memberships": []}],
                        }], None, ""))
    monkeypatch.setattr(roster_routes, "_update_student_canvas_group",
                        lambda *args: pytest.fail("_update_student_canvas_group should not be called"))
    resp = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101",
        "patch": '{"canvas_group": {"category_id": "7", "group_id": "999"}}'
    })
    data = resp.json()
    assert data.get("ok") is False
    assert "invalid group_id" in data.get("error", "").lower()


def test_roster_student_validates_extra_time_days():
    resp = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101",
        "patch": '{"extra_time": {"enabled": true, "days": "bad"}}'
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "integer" in data.get("error", "").lower()


def test_roster_student_legacy_planned_group_is_cleaned(monkeypatch, isolated_roster):
    """V3: planned_group is obsolete and should be rejected with clear error."""
    resp = client.post("/api/roster/student", data={
        "course_id": "1", "user_id": "101",
        "patch": '{"planned_group": {"category_id": "7", "group_id": "8"}}'
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "obsolete" in data.get("error", "").lower()


def test_roster_bulk_requires_params():
    resp = client.post("/api/roster/bulk", data={
        "course_id": "", "user_ids": "[]", "action": ""
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "course_id" in data.get("error", "").lower()


def test_roster_bulk_validates_user_ids_json():
    resp = client.post("/api/roster/bulk", data={
        "course_id": "1", "user_ids": "bad-json", "action": "set_extra_time"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "json" in data.get("error", "").lower()


def test_roster_bulk_requires_non_empty_list():
    resp = client.post("/api/roster/bulk", data={
        "course_id": "1", "user_ids": "[]", "action": "set_extra_time"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "non-empty" in data.get("error", "").lower()


def test_roster_bulk_rejects_unknown_action():
    resp = client.post("/api/roster/bulk", data={
        "course_id": "1", "user_ids": '["101"]', "action": "fly_to_moon"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "unknown" in data.get("error", "").lower()


def test_roster_bulk_rejects_obsolete_set_tier(isolated_roster):
    """V3: set_tier is obsolete."""
    resp = client.post("/api/roster/bulk", data={
        "course_id": "1", "user_ids": '["101"]',
        "action": "set_tier", "value": '{"tier_id": "support"}'
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "obsolete" in data.get("error", "").lower()


def test_roster_bulk_set_canvas_group(monkeypatch, isolated_roster):
    """V3: set_canvas_group writes Canvas membership for every selected user."""
    calls = []
    invalidations = []
    monkeypatch.setattr(roster_routes, "load_group_categories",
                        lambda course_id: ([{
                            "category_id": "7",
                            "category_name": "Differentiation",
                            "groups": [{"id": "8", "name": "Blue", "student_ids": [], "memberships": []}],
                        }], None, ""))
    monkeypatch.setattr(roster_routes, "_update_student_canvas_group",
                        lambda *args: calls.append(args) or (True, None))
    monkeypatch.setattr(roster_routes, "_reconcile_group_category",
                        lambda course_id, category_id, category_name=None:
                            invalidations.append((course_id, category_id)))
    resp = client.post("/api/roster/bulk", data={
        "course_id": "1", "user_ids": '["101", "102"]',
        "action": "set_canvas_group", "value": '{"category_id": "7", "group_id": "8"}'
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert data.get("updated") == 2
    assert [c[:4] for c in calls] == [
        ("1", "101", "7", "8"),
        ("1", "102", "7", "8"),
    ]
    assert invalidations == [("1", "7")]


def test_roster_bulk_rejects_canvas_group_outside_category(monkeypatch, isolated_roster):
    monkeypatch.setattr(roster_routes, "load_group_categories",
                        lambda course_id: ([{
                            "category_id": "7",
                            "category_name": "Differentiation",
                            "groups": [{"id": "8", "name": "Blue", "student_ids": [], "memberships": []}],
                        }], None, ""))
    monkeypatch.setattr(roster_routes, "_update_student_canvas_group",
                        lambda *args: pytest.fail("_update_student_canvas_group should not be called"))
    resp = client.post("/api/roster/bulk", data={
        "course_id": "1", "user_ids": '["101"]',
        "action": "set_canvas_group", "value": '{"category_id": "7", "group_id": "999"}'
    })
    data = resp.json()
    assert data.get("ok") is False
    assert "invalid group_id" in data.get("error", "").lower()


def test_roster_bulk_clear_canvas_group(monkeypatch, isolated_roster):
    """V3: clear_canvas_group should be accepted."""
    calls = []
    # Mock load_group_categories to return a valid category
    monkeypatch.setattr(roster_routes, "load_group_categories",
                        lambda course_id: ([{"category_id": "7", "category_name": "Test", "groups": []}], None, ""))
    monkeypatch.setattr(roster_routes, "_update_student_canvas_group",
                        lambda *args: calls.append(args) or (True, None))
    resp = client.post("/api/roster/bulk", data={
        "course_id": "1", "user_ids": '["101"]',
        "action": "clear_canvas_group", "value": '{"category_id": "7"}'
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert data.get("updated") == 1
    assert calls[0][:4] == ("1", "101", "7", None)


def test_roster_bulk_set_extra_time_uses_name_map(isolated_roster):
    resp = client.post("/api/roster/bulk", data={
        "course_id": "1",
        "user_ids": '["101"]',
        "action": "set_extra_time",
        "value": '{"days": 2, "names": {"101": "Ada Lovelace"}}',
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert isolated_roster["extra_time"]["1"] == [
        {"id": "101", "name": "Ada Lovelace", "days": 2}
    ]


def test_roster_bulk_clear_extra_time_ok(isolated_roster):
    isolated_roster["extra_time"]["1"] = [{"id": "101", "name": "Ada", "days": 2}]
    resp = client.post("/api/roster/bulk", data={
        "course_id": "1", "user_ids": '["101"]',
        "action": "clear_extra_time", "value": "{}"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert isolated_roster["extra_time"]["1"] == []


def test_roster_bulk_clear_tier_is_obsolete(isolated_roster):
    """V3: clear_tier is obsolete and should be rejected."""
    resp = client.post("/api/roster/bulk", data={
        "course_id": "1", "user_ids": '["101"]',
        "action": "clear_tier", "value": "{}"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "obsolete" in data.get("error", "").lower()


def test_roster_bulk_clear_planned_group_is_obsolete(isolated_roster):
    """V3: clear_planned_group is obsolete and should be rejected."""
    resp = client.post("/api/roster/bulk", data={
        "course_id": "1", "user_ids": '["101"]',
        "action": "clear_planned_group", "value": "{}"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is False
    assert "obsolete" in data.get("error", "").lower()


def test_roster_bulk_monitor_uses_name_map(isolated_roster):
    resp = client.post("/api/roster/bulk", data={
        "course_id": "1", "user_ids": '["101"]',
        "action": "set_monitored", "value": '{"names": {"101": "Ada Lovelace"}}'
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert isolated_roster["monitored"]["101"]["name"] == "Ada Lovelace"
    assert isolated_roster["monitored"]["101"]["note"] == ""


def test_roster_bulk_clear_monitor_ok(isolated_roster):
    isolated_roster["monitored"]["101"] = {"name": "Ada Lovelace", "note": ""}
    resp = client.post("/api/roster/bulk", data={
        "course_id": "1", "user_ids": '["101"]',
        "action": "clear_monitored", "value": "{}"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert "101" not in isolated_roster["monitored"]


# --------------------------------------------------------------------------
# _reconcile_group_category — targeted post-write group reconciliation
# --------------------------------------------------------------------------

def _mount_groups_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))


def test_reconcile_group_category_merges_only_affected_category(
    monkeypatch, tmp_path, isolated_roster,
):
    _mount_groups_workspace(monkeypatch, tmp_path)
    mirror_store.write_groups("1", [
        {"category_id": "7", "category_name": "Reading groups",
         "groups": [{"id": "8", "name": "Blue",
                     "memberships": [{"id": "9", "user_id": "101"}]}]},
        {"category_id": "20", "category_name": "Math groups",
         "groups": [{"id": "21", "name": "Advanced",
                     "memberships": [{"id": "22", "user_id": "102"}]}]},
    ])
    other_before = mirror_store.read_groups("1")["categories"][1]

    requested_category_ids = []

    def fake_fetch(course_id, category_id):
        requested_category_ids.append(category_id)
        return [{
            "id": "8", "name": "Blue", "student_ids": ["101", "103"],
            "memberships": [{"id": "9", "user_id": "101"}, {"id": "31", "user_id": "103"}],
        }], None

    monkeypatch.setattr(roster_routes, "fetch_group_category_groups", fake_fetch)

    roster_routes._reconcile_group_category("1", "7", "Reading groups")

    # Zero Canvas calls naming any other existing category's id.
    assert requested_category_ids == ["7"]

    document = mirror_store.read_groups("1")
    assert document["state"] == "current"
    other_after = next(c for c in document["categories"] if c["category_id"] == "20")
    assert other_after == other_before
    changed = next(c for c in document["categories"] if c["category_id"] == "7")
    assert changed["groups"][0]["memberships"] == [
        {"id": "9", "user_id": "101"}, {"id": "31", "user_id": "103"},
    ]


def test_reconcile_group_category_falls_back_when_fetch_fails(
    monkeypatch, tmp_path, isolated_roster,
):
    _mount_groups_workspace(monkeypatch, tmp_path)
    mirror_store.write_groups("1", [{
        "category_id": "7", "category_name": "Reading groups",
        "groups": [{"id": "8", "name": "Blue", "memberships": []}],
    }])
    monkeypatch.setattr(roster_routes, "fetch_group_category_groups",
                        lambda course_id, category_id: (None, "Canvas returned 403"))
    merge_calls = []
    monkeypatch.setattr(roster_routes.mirror_store, "merge_group_category",
                        lambda *a, **k: merge_calls.append((a, k)))

    roster_routes._reconcile_group_category("1", "7", "Reading groups")

    assert merge_calls == []
    document = mirror_store.read_groups("1")
    assert document["state"] == "stale"
    assert document["error_code"] == "invalidated"


def test_reconcile_group_category_falls_back_when_merge_write_fails(
    monkeypatch, tmp_path, isolated_roster,
):
    _mount_groups_workspace(monkeypatch, tmp_path)
    mirror_store.write_groups("1", [{
        "category_id": "7", "category_name": "Reading groups",
        "groups": [{"id": "8", "name": "Blue", "memberships": []}],
    }])
    monkeypatch.setattr(
        roster_routes, "fetch_group_category_groups",
        lambda course_id, category_id: ([{"id": "8", "name": "Blue", "memberships": []}], None),
    )

    def raising_merge(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(roster_routes.mirror_store, "merge_group_category", raising_merge)

    roster_routes._reconcile_group_category("1", "7", "Reading groups")

    document = mirror_store.read_groups("1")
    assert document["state"] == "stale"
    assert document["error_code"] == "invalidated"


def test_reconcile_group_category_skips_merge_without_previous_document(
    monkeypatch, tmp_path, isolated_roster,
):
    _mount_groups_workspace(monkeypatch, tmp_path)
    # No previous groups document exists for this course.
    fetch_calls = []
    monkeypatch.setattr(
        roster_routes, "fetch_group_category_groups",
        lambda course_id, category_id: (
            fetch_calls.append(category_id)
            or ([{"id": "8", "name": "Blue", "memberships": []}], None)
        ),
    )

    roster_routes._reconcile_group_category("1", "7", "Reading groups")

    assert fetch_calls == ["7"]
    # merge_group_category no-ops (no previous document); falling back to
    # invalidate_groups is itself a no-op here too — unchanged from today.
    assert mirror_store.read_groups("1") is None


def test_roster_get_note_reports_profile_and_group_problems_together(monkeypatch, isolated_roster):
    """Two unrelated problems must both surface.

    The note was an if/elif chain, so a corrupt classroom profile silently hid
    a groups failure and the teacher only ever saw whichever came first.
    """
    users = [{"id": 101, "name": "Test Student", "sortable_name": "Student, Test",
              "short_name": "Test", "enrollments": []}]
    monkeypatch.setattr(roster_routes, "_fetch_students", lambda course_id: (users, None))
    monkeypatch.setattr(roster_routes, "_fetch_sections", lambda course_id: {})
    monkeypatch.setattr(roster_routes, "_group_categories_for_roster",
                        lambda course_id: ([], "mirror unavailable", ""))
    isolated_roster["settings"]["1"] = {"101": {"classroom_profile": {"bad": True}}}

    data = client.get("/api/roster?course_id=1").json()

    assert "Test Student" in data["note"]
    assert "Groups: mirror unavailable" in data["note"]


# --------------------------------------------------------------------------
# Roster-change baseline: added / departed / changed-section warnings,
# acknowledge, and the one-click section-change migration.
# --------------------------------------------------------------------------


def test_roster_get_has_no_roster_changes_before_any_acknowledgment(monkeypatch, isolated_roster):
    """A course that has never been acknowledged has nothing to diff against,
    so no student is flagged and the card reports itself as not tracking yet."""
    users = [{"id": 101, "name": "Ada Lovelace", "sortable_name": "Lovelace, Ada",
              "short_name": "Ada", "enrollments": [{"course_section_id": "sec-a"}]}]
    monkeypatch.setattr(roster_routes, "_fetch_students", lambda course_id: (users, None))
    monkeypatch.setattr(roster_routes, "_fetch_sections", lambda course_id: {"sec-a": "Period 1"})

    data = client.get("/api/roster?course_id=1").json()

    assert data["roster_changes"] == {
        "baseline_set": False, "added_count": 0, "changed_section_count": 0, "departed": [],
    }
    assert data["students"][0]["roster_change"] is None
    assert "student_added" not in data["students"][0]["warnings"]


def test_roster_changes_acknowledge_requires_course_id():
    resp = client.post("/api/roster/changes/acknowledge", data={"course_id": ""})
    assert resp.json() == {"ok": False, "error": "course_id required."}


def test_acknowledge_then_new_and_moved_students_are_flagged(monkeypatch, isolated_roster):
    baseline_users = [
        {"id": 101, "name": "Ada Lovelace", "sortable_name": "Lovelace, Ada",
         "short_name": "Ada", "enrollments": [{"course_section_id": "sec-a"}]},
    ]
    monkeypatch.setattr(roster_routes, "_fetch_students", lambda course_id: (baseline_users, None))
    monkeypatch.setattr(roster_routes, "_fetch_sections", lambda course_id: {"sec-a": "Period 1", "sec-b": "Period 2"})

    ack = client.post("/api/roster/changes/acknowledge", data={"course_id": "1"}).json()
    assert ack["ok"] is True
    assert ack["baseline"]["students"] == {"101": ["sec-a"]}
    assert ack["baseline"]["acknowledged_at"]

    later_users = [
        {"id": 101, "name": "Ada Lovelace", "sortable_name": "Lovelace, Ada",
         "short_name": "Ada", "enrollments": [{"course_section_id": "sec-b"}]},
        {"id": 102, "name": "New Kid", "sortable_name": "Kid, New",
         "short_name": "New", "enrollments": [{"course_section_id": "sec-b"}]},
    ]
    monkeypatch.setattr(roster_routes, "_fetch_students", lambda course_id: (later_users, None))

    data = client.get("/api/roster?course_id=1").json()
    rows = {row["id"]: row for row in data["students"]}

    assert data["roster_changes"]["baseline_set"] is True
    assert data["roster_changes"]["added_count"] == 1
    assert data["roster_changes"]["changed_section_count"] == 1
    assert rows["102"]["roster_change"] == {"is_new": True}
    assert "student_added" in rows["102"]["warnings"]
    changed = rows["101"]["roster_change"]["changed_section"]
    assert changed["old_section_ids"] == ["sec-a"]
    assert changed["new_section_ids"] == ["sec-b"]
    assert changed["old_section_names"] == ["Period 1"]
    assert changed["new_section_names"] == ["Period 2"]
    assert changed["clean_swap"] is True
    assert changed["can_migrate"] is True
    assert "student_changed_section" in rows["101"]["warnings"]


def test_roster_get_reports_departed_student_with_every_kind_of_held_data(monkeypatch, isolated_roster):
    isolated_roster["vault"].rows["202"] = {
        "canvas_id": "202", "real_name": "Riley Departed", "sis_id": "",
        "nicknames": [], "pseudonym": _WORDS[3], "first_seen": "",
    }
    isolated_roster["baselines"]["1"] = {
        "acknowledged_at": "2026-08-01T00:00:00",
        "students": {"101": ["sec-a"], "202": ["sec-a"]},
    }
    isolated_roster["extra_time"]["1"] = [{"id": "202", "name": "Riley Departed", "days": 3}]
    isolated_roster["monitored"]["202"] = {"name": "Riley Departed", "note": "watch"}
    isolated_roster["settings"]["1"] = {"202": {"classroom_profile": {
        "birthday": "03-14", "celebrations": [],
    }}}
    isolated_roster["score_matrices"]["1"] = {
        "columns": [{"id": "score-writing", "label": "Writing"}],
        "values_by_section": {"sec-a": {"202": {"score-writing": 9}}},
    }
    isolated_roster["relationships"]["1"] = {"by_section": {"sec-a": [{
        "student_a": "101", "student_b": "202", "type": "keep_apart", "reason": "",
    }]}}
    users = [{"id": 101, "name": "Ada Lovelace", "sortable_name": "Lovelace, Ada",
              "short_name": "Ada", "enrollments": [{"course_section_id": "sec-a"}]}]
    monkeypatch.setattr(roster_routes, "_fetch_students", lambda course_id: (users, None))
    monkeypatch.setattr(roster_routes, "_fetch_sections", lambda course_id: {"sec-a": "Period 1"})

    data = client.get("/api/roster?course_id=1").json()

    assert data["roster_changes"]["departed"] == [{
        "student_id": "202",
        "display_name": "Riley Departed",
        "settings": True,
        "extra_time_days": 3,
        "monitored": True,
        "score_values": [{"section_id": "sec-a", "section_name": "Period 1", "columns": ["score-writing"]}],
        "relationship_pairs": [{"section_id": "sec-a", "section_name": "Period 1",
                                "partner_id": "101", "partner_name": "Ada Lovelace", "type": "keep_apart"}],
    }]


def test_migrate_section_rejects_when_no_clean_swap_exists(monkeypatch, isolated_roster):
    monkeypatch.setattr(roster_routes, "_fetch_students", lambda course_id: ([], None))
    monkeypatch.setattr(roster_routes, "_fetch_sections", lambda course_id: {})

    resp = client.post("/api/roster/changes/migrate-section",
                       data={"course_id": "1", "user_id": "999"})
    data = resp.json()

    assert data["ok"] is False
    assert "unambiguous" in data["error"]


def test_migrate_section_moves_score_values_and_pairs_then_updates_only_that_baseline(
    monkeypatch, isolated_roster,
):
    isolated_roster["baselines"]["chg"] = {
        "acknowledged_at": "2026-08-01T00:00:00",
        "students": {"101": ["sec-old"], "102": ["sec-old"], "103": ["sec-old"]},
    }
    isolated_roster["score_matrices"]["chg"] = {
        "columns": [{"id": "score-writing", "label": "Writing"}],
        "values_by_section": {"sec-old": {"101": {"score-writing": 5}}},
    }
    isolated_roster["relationships"]["chg"] = {"by_section": {"sec-old": [
        {"student_a": "101", "student_b": "102", "type": "keep_apart", "reason": "r1"},
        {"student_a": "101", "student_b": "103", "type": "preferred_pair", "reason": "r2"},
    ]}}
    # 101 and 102 both moved from sec-old to sec-new; 103 stayed in sec-old.
    users = [
        {"id": 101, "name": "Mover One", "sortable_name": "One, Mover",
         "short_name": "One", "enrollments": [{"course_section_id": "sec-new"}]},
        {"id": 102, "name": "Mover Two", "sortable_name": "Two, Mover",
         "short_name": "Two", "enrollments": [{"course_section_id": "sec-new"}]},
        {"id": 103, "name": "Stays Three", "sortable_name": "Three, Stays",
         "short_name": "Three", "enrollments": [{"course_section_id": "sec-old"}]},
    ]
    monkeypatch.setattr(roster_routes, "_fetch_students", lambda course_id: (users, None))
    monkeypatch.setattr(roster_routes, "_fetch_sections",
                        lambda course_id: {"sec-old": "Old Period", "sec-new": "New Period"})

    resp = client.post("/api/roster/changes/migrate-section",
                       data={"course_id": "chg", "user_id": "101"})
    data = resp.json()

    assert data["ok"] is True
    migration = data["migration"]
    assert migration["old_section_id"] == "sec-old"
    assert migration["new_section_id"] == "sec-new"
    assert migration["moved_score_columns"] == ["score-writing"]
    assert migration["moved_relationship_pairs"] == [
        {"student_a": "101", "student_b": "102", "type": "keep_apart"},
    ]
    assert migration["broken_relationship_pairs"] == [
        {"student_a": "101", "student_b": "103", "type": "preferred_pair", "reason": "r2", "partner_id": "103"},
    ]
    # Score values moved wholesale (old section had nothing left to keep).
    matrix = isolated_roster["score_matrices"]["chg"]
    assert "sec-old" not in matrix["values_by_section"]
    assert matrix["values_by_section"]["sec-new"] == {"101": {"score-writing": 5}}

    # The co-migrating pair moved; the pair with the student who stayed
    # remains in the old section, visible and unresolved rather than dropped.
    relationships = isolated_roster["relationships"]["chg"]
    assert relationships["by_section"]["sec-old"] == [
        {"student_a": "101", "student_b": "103", "type": "preferred_pair", "reason": "r2"},
    ]
    assert relationships["by_section"]["sec-new"] == [
        {"student_a": "101", "student_b": "102", "type": "keep_apart", "reason": "r1"},
    ]

    # Only student 101's own baseline entry advanced; 102 and 103 are
    # untouched so their own still-open changes are not silently cleared.
    baseline = isolated_roster["baselines"]["chg"]
    assert baseline["students"]["101"] == ["sec-new"]
    assert baseline["students"]["102"] == ["sec-old"]
    assert baseline["students"]["103"] == ["sec-old"]

    # Re-running migrate for 101 is a harmless no-op: nothing is left to
    # move, and the relationship that already moved is not reported again.
    again = client.post("/api/roster/changes/migrate-section",
                        data={"course_id": "chg", "user_id": "101"}).json()
    assert again["ok"] is False

    # Migrating the co-migrating partner afterwards must not re-report or
    # duplicate the pair that 101's migration already moved.
    second = client.post("/api/roster/changes/migrate-section",
                         data={"course_id": "chg", "user_id": "102"}).json()
    assert second["ok"] is True
    assert second["migration"]["moved_relationship_pairs"] == []
    assert second["migration"]["broken_relationship_pairs"] == []
    assert isolated_roster["relationships"]["chg"]["by_section"]["sec-new"] == [
        {"student_a": "101", "student_b": "102", "type": "keep_apart", "reason": "r1"},
    ]
