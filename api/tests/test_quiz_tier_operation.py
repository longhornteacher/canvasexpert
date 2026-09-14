"""Contract tests for differentiated QuizForge family delivery."""

import copy
import json

import pytest

from api.operation_ledger import models
from api.operation_ledger.adapters import quiz as quiz_module
from api.operation_ledger.adapters.quiz import QuizAdapter
from api.platform_services import canvas_client, config


def _plan(tier="Support", title="Reading Check", points=20):
    return {
        "version": 1,
        "title": title,
        "metadata": {"variant": tier},
        "quiz_payload": {"quiz": {
            "title": title, "points_possible": points,
            "grading_type": "points", "quiz_settings": {},
        }},
        "items": [{
            "index": 1, "source_item_id": "q1", "source_type": "MC",
            "payload": {"item": {"entry_type": "Item", "points_possible": points}},
        }],
        "assignment_settings": {},
        "module": {},
    }


def _plans(monkeypatch, first=None, second=None):
    rows = {"a.txt": copy.deepcopy(first or _plan()),
            "b.txt": copy.deepcopy(second or _plan("Extend"))}
    monkeypatch.setattr(quiz_module, "run_json_object", lambda args, extra_env=None: copy.deepcopy(rows[args[1]]))
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Red", "Core": "Blue", "Accelerate": "Silver", "Extend": "Gold",
    })
    monkeypatch.setattr(config, "get_canvas_base", lambda: "https://canvas.invalid")


def _request(**settings):
    base = {"due_at": "2026-09-14T10:00:00-05:00", "module_name": "Week 1",
            "assignment_group_name": "Assessments"}
    base.update(settings)
    return {"mode": "differentiated", "variants": [
        {"path": "a.txt", "group_name": "Blue"},
        {"path": "b.txt", "group_name": "Gold"},
    ], "settings": base}


@pytest.mark.parametrize(
    ("first", "second", "change", "message"),
    [
        (_plan(), _plan("Extend", title="Different"), {}, "same exact"),
        (_plan(title="Reading Check - Red"), _plan("Extend", title="Reading Check - Red"), {}, "unsuffixed"),
        (_plan(points=20), _plan("Extend", points=25), {}, "equal total points"),
        (_plan(), _plan("Unknown"), {}, "canonical tier"),
        (_plan(), _plan("Extend"), {"due_at": ""}, "due_at"),
        (_plan(), _plan("Extend"), {"due_at": "2026-09-14T10:00:00"}, "UTC offset"),
        (_plan(), _plan("Extend"), {"module_name": ""}, "module_name"),
    ],
)
def test_prepare_laws_block_invalid_family(monkeypatch, first, second, change, message):
    _plans(monkeypatch, first, second)
    with pytest.raises(ValueError, match=message):
        QuizAdapter().build_payload(_request(**change))


def test_prepare_normalizes_server_owned_titles_and_shapes(monkeypatch):
    _plans(monkeypatch)
    payload = QuizAdapter().build_payload(_request())
    assert payload["base_title"] == "Reading Check"
    assert payload["bridge_due_at"] == "2026-09-14T23:59:00-05:00"
    assert [variant["plan"]["title"] for variant in payload["variants"]] == [
        "Reading Check - Red", "Reading Check - Gold",
    ]
    assert [variant["tier"] for variant in payload["variants"]] == ["Support", "Extend"]
    for variant in payload["variants"]:
        settings = variant["plan"]["assignment_settings"]
        assert settings["published"] is True
        assert settings["only_visible_to_overrides"] is True
        assert settings["omit_from_final_grade"] is True
        assert settings["post_to_sis"] is False
        assert variant["plan"]["module"] == {}


@pytest.mark.parametrize(
    "tags",
    [
        {"Support": "", "Core": "Blue", "Accelerate": "Silver", "Extend": "Gold"},
        {"Support": "Red", "Core": "Blue", "Accelerate": "Silver", "Extend": " red "},
    ],
)
def test_prepare_requires_unique_public_tags_for_used_quiz_tiers(monkeypatch, tags):
    _plans(monkeypatch)
    monkeypatch.setattr(config, "get_tier_tags", lambda: tags)
    with pytest.raises(ValueError, match="Settings|unique"):
        QuizAdapter().build_payload(_request())


def test_unknown_unsuffixed_bridge_title_collision_is_drift(monkeypatch):
    adapter = QuizAdapter()
    baseline = {
        "group_snapshot": {"tiers": []},
        "existing_by_title": {"Reading Check": [{"id": "unknown"}]},
    }
    monkeypatch.setattr(adapter, "capture_baseline", lambda _payload, _target: copy.deepcopy(baseline))
    assert adapter.check_drift(
        {"mode": "differentiated"}, {"course_id": "42", "steps": []}, baseline
    ) is True


class Context:
    def __init__(self):
        self.steps = []

    def before_send(self, key, digest):
        step = next((copy.deepcopy(row) for row in self.steps if row["step_key"] == key), models.new_step(key))
        step.update({"state": "claimed", "payload_digest": digest,
                     "outbound_started_at": "2026-09-14T12:00:00+00:00"})
        self._put(step)
        return copy.deepcopy(step)

    def checkpoint_step(self, step, returned_object_id=None, returned_object_url=None):
        step = copy.deepcopy(step)
        if returned_object_id is not None:
            step["returned_object_id"] = returned_object_id
        if returned_object_url is not None:
            step["returned_object_url"] = returned_object_url
        self._put(step)
        return step

    def _put(self, step):
        self.steps = [row for row in self.steps if row["step_key"] != step["step_key"]]
        self.steps.append(copy.deepcopy(step))


class FakeCanvas:
    def __init__(self):
        self.quizzes = {}
        self.assignments = {}
        self.overrides = {}
        self.items = {}
        self.module_items = {}
        self.sends = []
        self.next_id = 200

    def send(self, method, path, body, timeout=30):
        self.sends.append((method, path, copy.deepcopy(body)))
        self.next_id += 1
        if method == "POST" and path.endswith("/quizzes"):
            quiz_id = str(self.next_id)
            quiz = {"id": quiz_id, **copy.deepcopy(body["quiz"])}
            self.quizzes[quiz_id] = quiz
            self.assignments[quiz_id] = {
                "id": quiz_id, "course_id": "42", "name": quiz["title"],
                "points_possible": quiz["points_possible"], "grading_type": "points",
                "assignment_group_id": "77", "due_at": None, "published": False,
                "only_visible_to_overrides": False, "omit_from_final_grade": False,
                "post_to_sis": False, "html_url": f"https://canvas.invalid/a/{quiz_id}",
            }
            self.overrides[quiz_id] = []
            self.items[quiz_id] = {}
            return copy.deepcopy(quiz), None
        if method == "POST" and path.endswith("/overrides"):
            quiz_id = path.split("/assignments/")[1].split("/")[0]
            row = {"id": str(self.next_id), **copy.deepcopy(body["assignment_override"])}
            self.overrides[quiz_id].append(row)
            return copy.deepcopy(row), None
        if method == "POST" and path.endswith("/items") and "/modules/" not in path:
            quiz_id = path.split("/quizzes/")[1].split("/")[0]
            row = {"id": str(self.next_id), **copy.deepcopy(body["item"])}
            self.items[quiz_id][row["id"]] = row
            return copy.deepcopy(row), None
        if method == "POST" and path.endswith("/assignments"):
            assignment_id = str(self.next_id)
            row = {"id": assignment_id, "course_id": "42", "assignment_group_id": "77",
                   "html_url": f"https://canvas.invalid/a/{assignment_id}",
                   **copy.deepcopy(body["assignment"])}
            self.assignments[assignment_id] = row
            self.overrides[assignment_id] = []
            return copy.deepcopy(row), None
        if method == "POST" and path.endswith("/items") and "/modules/" in path:
            module_id = path.split("/modules/")[1].split("/")[0]
            row = {"id": str(self.next_id), **copy.deepcopy(body["module_item"])}
            self.module_items[(module_id, row["id"])] = row
            return copy.deepcopy(row), None
        if method == "PUT" and "/assignments/" in path:
            assignment_id = path.rsplit("/", 1)[-1]
            patch = copy.deepcopy(body["assignment"])
            if patch.get("assignment_group_name"):
                patch["assignment_group_id"] = "77"
            self.assignments[assignment_id].update(patch)
            return copy.deepcopy(self.assignments[assignment_id]), None
        raise AssertionError((method, path, body))

    def get(self, path, params=None, timeout=20):
        if "/api/quiz/" in path and "/items/" in path:
            quiz_id, item_id = path.split("/quizzes/")[1].split("/items/")
            return copy.deepcopy(self.items.get(quiz_id, {}).get(item_id)), None
        if "/api/quiz/" in path:
            return copy.deepcopy(self.quizzes.get(path.rsplit("/", 1)[-1])), None
        if "/modules/" in path and "/items/" in path:
            module_id, item_id = path.split("/modules/")[1].split("/items/")
            return copy.deepcopy(self.module_items.get((module_id, item_id))), None
        if "/assignments/" in path and "/overrides/" in path:
            assignment_id, override_id = path.split("/assignments/")[1].split("/overrides/")
            row = next((row for row in self.overrides.get(assignment_id, []) if str(row["id"]) == override_id), None)
            return copy.deepcopy(row), None
        if "/assignments/" in path:
            return copy.deepcopy(self.assignments.get(path.rsplit("/", 1)[-1])), None
        return [], None

    def get_all(self, path, params=None, timeout=30):
        if path.endswith("/modules"):
            return [{"id": "501", "name": "Week 1"}], None
        if path.endswith("/overrides"):
            assignment_id = path.split("/assignments/")[1].split("/")[0]
            return copy.deepcopy(self.overrides.get(assignment_id, [])), None
        if path.endswith("/assignments"):
            search = str((params or {}).get("search_term") or "").casefold()
            return [copy.deepcopy(row) for row in self.assignments.values() if row["name"].casefold() == search], None
        return [], None


def test_differentiated_quiz_family_example_creates_only_bridge_module_item(monkeypatch):
    _plans(monkeypatch)
    payload = QuizAdapter().build_payload(_request())
    resolved = {"safe": {"tiers": [
        {"index": 0, "label": "variant_0", "group_name": "Blue", "group_id": "10", "student_count": 1, "membership_digest": "a"},
        {"index": 1, "label": "variant_1", "group_name": "Gold", "group_id": "20", "student_count": 1, "membership_digest": "b"},
    ]}, "student_ids_by_group": {"10": ["9001"], "20": ["9002"]}}
    monkeypatch.setattr(quiz_module, "resolve_assignment_groups", lambda *args, **kwargs: resolved)
    monkeypatch.setattr(config, "get_extra_time", lambda _course: [])
    fake = FakeCanvas()
    registrations = {}
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake.get_all)
    monkeypatch.setattr(config, "save_sis_grade_bridge", lambda course, row: registrations.__setitem__((course, row["family_title"]), copy.deepcopy(row)) or copy.deepcopy(row))
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda course, title: copy.deepcopy(registrations.get((course, title))))

    context = Context()
    result = QuizAdapter().execute(payload, {"course_id": "42", "steps": []}, {"group_snapshot": resolved["safe"]}, {}, context)

    assert result["state"] == "applied"
    sources = [row for row in fake.assignments.values() if row["name"] != "Reading Check"]
    assert [row["name"] for row in sources] == ["Reading Check - Red", "Reading Check - Gold"]
    assert all(row["published"] and row["only_visible_to_overrides"] and row["omit_from_final_grade"] and not row["post_to_sis"] for row in sources)
    bridge = next(row for row in fake.assignments.values() if row["name"] == "Reading Check")
    assert [str(row["content_id"]) for row in fake.module_items.values()] == [bridge["id"]]
    assert registrations[("42", "Reading Check")]["source_assignment_ids"] == [row["id"] for row in sources]
    assert all(value not in json.dumps(context.steps) for value in ("9001", "9002"))
    assert not any(path.endswith("/post_grades") for _method, path, _body in fake.sends)

    sent_count = len(fake.sends)
    retry = QuizAdapter().execute(
        payload,
        {"course_id": "42", "steps": copy.deepcopy(context.steps)},
        {"group_snapshot": resolved["safe"]},
        {},
        context,
    )
    assert retry["state"] == "applied"
    assert len(fake.sends) == sent_count


def test_whole_quiz_plan_has_no_bridge(monkeypatch):
    monkeypatch.setattr(quiz_module, "run_json_object", lambda *args, **kwargs: _plan("Core", "Whole"))
    payload = QuizAdapter().build_payload({"mode": "whole", "path": "whole.txt", "settings": {}})
    assert payload["mode"] == "whole"
    assert "bridge_due_at" not in payload
