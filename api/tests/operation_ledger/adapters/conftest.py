"""Shared fixtures for adapter tests that need a stateful fake Canvas.

``classic_canvas`` answers just enough of the Classic Quiz REST surface, the Pages
date-details surface, and the module surface for a whole push to run through the real
Operation Ledger. It records every write, so a test asserts the calls Canvas received
rather than how the adapter arrived at them. ``classic_push`` prepares and applies one
staged QuizForge file the way ``content_push`` does (preview freezes, apply executes).
"""
import copy
import json

import pytest

from api import qf_pusher
from api.operation_ledger import batches, executor, models, operations, paths
from api.operation_ledger.adapters import quiz as quiz_adapter_module
from api.operation_ledger.adapters import QuizAdapter
from api.platform_services import canvas_client, config

COURSE = "101"
BASE = "https://canvas.invalid"


class FakeClassicCanvas:
    """In-memory Canvas: classic quizzes, their assignments, questions, pages, modules, tags."""

    def __init__(self):
        self.quizzes, self.assignments, self.questions = {}, {}, {}
        self.pages, self.dates = {}, {}
        self.modules = {"10": {"id": 10, "name": "Unit 1"}}
        self.module_items = {"10": []}
        self.assignment_groups = [{"id": 7, "name": "Quizzes"}]
        self.tags = {"11": {"id": "11", "group_category_id": "5", "name": "Red",
                            "non_collaborative": True}}
        self.sends = []
        self.lose_response = None      # (method, path suffix, nth) -> write lands, reply is lost
        self.reject = None             # (method, path suffix) -> definitive HTTP 422
        self._counts = {}

    # -- reads ---------------------------------------------------------------

    def get(self, path, params=None, timeout=20):
        parts = path.split("/")
        if path.endswith("/assignments"):
            term = str((params or {}).get("search_term") or "")
            return [copy.deepcopy(row) for row in self.assignments.values()
                    if term.casefold() in row["name"].casefold()], None
        if "/assignments/" in path:
            row = self.assignments.get(parts[-1])
            return (copy.deepcopy(row), None) if row else (None, "HTTP 404: not found")
        if "/quizzes/" in path and "/questions/" in path:
            quiz_id, question_id = parts[-3], parts[-1]
            found = next((q for q in self.questions.get(quiz_id, []) if str(q["id"]) == question_id), None)
            return (copy.deepcopy(found), None) if found else (None, "HTTP 404: not found")
        if "/quizzes/" in path:
            quiz = self.quizzes.get(parts[-1])   # a deleted quiz still answers 200
            return (copy.deepcopy(quiz), None) if quiz else (None, "HTTP 404: not found")
        if "/date_details" in path:
            return copy.deepcopy(self.dates.get(path.split("/pages/")[1].split("/")[0])), None
        if "/pages/" in path:
            return copy.deepcopy(self.pages.get(parts[-1])), None
        if path.startswith("/api/v1/groups/"):
            return copy.deepcopy(self.tags.get(parts[-1])), None
        if "/modules/" in path and "/items/" in path:
            item = next((i for i in self.module_items.get(parts[-3], []) if str(i["id"]) == parts[-1]), None)
            return (copy.deepcopy(item), None) if item else (None, "HTTP 404: not found")
        if "/modules/" in path:
            row = self.modules.get(parts[-1])
            return (copy.deepcopy(row), None) if row else (None, "HTTP 404: not found")
        raise AssertionError(f"unexpected read {path}")

    def get_all(self, path, params=None, timeout=30):
        if path.endswith("/questions"):
            return copy.deepcopy(self.questions.get(path.split("/quizzes/")[1].split("/")[0], [])), None
        if path.endswith("/quizzes"):
            term = str((params or {}).get("search_term") or "")
            return [{"id": q["id"], "title": q["title"], "assignment_id": q["assignment_id"]}
                    for q in self.quizzes.values()
                    if q["assignment_id"] in self.assignments and term.casefold() in q["title"].casefold()], None
        if path.endswith("/assignment_groups"):
            return copy.deepcopy(self.assignment_groups), None
        if path.endswith("/pages"):
            return copy.deepcopy(list(self.pages.values())), None
        if path.endswith("/modules"):
            return copy.deepcopy(list(self.modules.values())), None
        if "/modules/" in path and path.endswith("/items"):
            return copy.deepcopy(self.module_items.get(path.split("/modules/")[1].split("/")[0], [])), None
        raise AssertionError(f"unexpected list read {path}")

    def get_all_complete(self, path, params=None, timeout=30):
        if path.endswith("/group_categories"):
            return [{"id": "5"}], None, True
        if path.endswith("/group_categories/5/groups"):
            return [{"id": "11", "name": "Red"}], None, True
        raise AssertionError(f"unexpected complete read {path}")

    # -- writes --------------------------------------------------------------

    def send(self, method, path, body, timeout=30):
        self.sends.append((method, path, copy.deepcopy(body)))
        if self.reject and method == self.reject[0] and path.endswith(self.reject[1]):
            return None, "HTTP 422: rejected by the fake"
        response = self._apply(method, path, body)
        if self.lose_response and method == self.lose_response[0] and path.endswith(self.lose_response[1]):
            key = (method, path)
            self._counts[key] = self._counts.get(key, 0) + 1
            if self._counts[key] == self.lose_response[2]:
                return None, "timeout: read timed out"
        return response, None

    def _apply(self, method, path, body):
        wiki = body.get("wiki_page", {})
        if method == "POST" and path.endswith("/quizzes"):
            quiz_id, assignment_id = str(9000 + len(self.quizzes) + 1), str(500 + len(self.quizzes) + 1)
            quiz = {**body["quiz"], "id": quiz_id, "assignment_id": assignment_id,
                    "points_possible": None, "workflow_state": "unpublished"}
            self.quizzes[quiz_id] = quiz
            self.questions[quiz_id] = []
            self.assignments[assignment_id] = {
                "id": assignment_id, "name": quiz["title"], "description": quiz.get("description"),
                "published": False, "post_to_sis": False, "assignment_group_id": 1,
                "html_url": f"{BASE}/courses/{COURSE}/assignments/{assignment_id}",
                "new_quizzes": False,
            }
            return copy.deepcopy(quiz)
        if method == "POST" and path.endswith("/questions"):
            quiz_id = path.split("/quizzes/")[1].split("/")[0]
            question = {**body["question"], "id": 7000 + sum(len(v) for v in self.questions.values()) + 1}
            self.questions[quiz_id].append(question)
            return copy.deepcopy(question)
        if method == "PUT" and "/quizzes/" in path:
            quiz = self.quizzes[path.rsplit("/", 1)[-1]]
            quiz.update(body["quiz"])
            quiz["points_possible"] = sum(q["points_possible"] for q in self.questions[quiz["id"]])
            self.assignments[quiz["assignment_id"]]["published"] = bool(quiz.get("published"))
            return copy.deepcopy(quiz)
        if method == "DELETE" and "/quizzes/" in path:
            quiz = self.quizzes[path.rsplit("/", 1)[-1]]
            quiz["workflow_state"] = None
            self.assignments.pop(quiz["assignment_id"], None)
            return {}
        if method == "PUT" and "/assignments/" in path:
            self.assignments[path.rsplit("/", 1)[-1]].update(body["assignment"])
            return {}
        if method == "POST" and path.endswith("/items"):
            module_id = path.split("/modules/")[1].split("/")[0]
            item = {"id": 3000 + len(self.module_items[module_id]) + 1, **body["module_item"]}
            self.module_items[module_id].append(item)
            return copy.deepcopy(item)
        if method == "POST" and path.endswith("/pages"):
            number = len(self.pages) + 1
            page_id, slug = str(100 + number), f"support-page-{number}"
            page = {"id": page_id, "page_id": page_id, "url": slug,
                    "html_url": f"{BASE}/courses/{COURSE}/pages/{slug}",
                    "title": wiki["title"], "body": wiki["body"], "published": False}
            self.pages[page_id] = page
            self.dates[page_id] = {"visible_to_everyone": True, "overrides": []}
            return copy.deepcopy(page)
        if method == "PUT" and path.endswith("/date_details"):
            page_id = path.split("/pages/")[1].split("/")[0]
            self.dates[page_id] = {"visible_to_everyone": not body.get("only_visible_to_overrides", False),
                                   "overrides": copy.deepcopy(body.get("assignment_overrides", []))}
            return {}
        if method == "PUT" and "/pages/" in path:
            page_id = path.rsplit("/", 1)[-1]
            self.pages[page_id]["published"] = wiki.get("published", False)
            return copy.deepcopy(self.pages[page_id])
        raise AssertionError((method, path, body))

    def writes(self, method, suffix=""):
        return [(m, p, b) for m, p, b in self.sends if m == method and p.endswith(suffix)]


@pytest.fixture
def classic_canvas(monkeypatch):
    fake = FakeClassicCanvas()
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake.get_all)
    monkeypatch.setattr(canvas_client, "canvas_get_all_complete", fake.get_all_complete)
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    monkeypatch.setattr(config, "get_canvas_base", lambda: BASE)
    monkeypatch.setattr(config, "active_courses", lambda: [{"id": COURSE, "name": "Invented Course", "active": True}])
    monkeypatch.setattr(config, "get_tier_tags",
                        lambda: {"Support": "Silver", "Core": "Red", "Accelerate": "Blue"})
    return fake


@pytest.fixture
def classic_push(classic_canvas, tmp_path, monkeypatch):
    """``push(envelope, settings, apply=True)`` -> (adapter, operation_id, result_or_batch).

    The planner runs in-process (same function, JSON round trip) so the whole chain from a
    synthetic QuizForge file to Canvas writes is exercised without a subprocess.
    """
    monkeypatch.setattr(paths, "private_root", lambda: tmp_path / "local-private")

    def planner(args, extra_env=None, **_kwargs):
        settings = json.loads((extra_env or {}).get("QF_PUSH_SETTINGS") or "{}")
        return json.loads(json.dumps(qf_pusher.build_push_plan(args[1], settings)))

    monkeypatch.setattr(quiz_adapter_module, "run_json_object", planner)

    def push(envelope: dict, settings: dict | None = None, *, apply: bool = True):
        path = tmp_path / "classic-quiz.txt"
        path.write_text("<QUIZFORGE_JSON>\n" + json.dumps(envelope) + "\n</QUIZFORGE_JSON>\n",
                        encoding="utf-8")
        adapter = QuizAdapter()
        payload = adapter.build_payload({"mode": "whole", "path": str(path),
                                         "settings": settings or {}, "course_id": COURSE})
        target = adapter.verify_targets(payload, [{"course_id": COURSE}])[0]
        baseline = adapter.capture_baseline(payload, target)
        record = models.new_target(target_key=target["target_key"],
                                   idempotency_key=target["idempotency_key"],
                                   course_id=COURSE, baseline=baseline)
        operation_id = models.new_operation_id()
        operations.create_operation(models.new_operation(
            operation_id=operation_id, kind="content.quiz",
            source_ref={"type": "staged_inbox", "value": "quiz"},
            source_digest=adapter.source_digest(payload),
            normalized_payload=payload, targets=[record]))
        frozen = adapter.freeze_review(payload, record, baseline)
        batch = batches.freeze_batch([operation_id], {operation_id: [frozen]})
        operations.set_operation_review(operation_id, batch)
        if not apply:
            return adapter, operation_id, {"batch": batch, "review": frozen}
        return adapter, operation_id, executor.apply_operation(
            operation_id, batch["batch_id"], batch["review_digest"])

    return push
