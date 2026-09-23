"""Contract tests for differentiated AssignmentForge family delivery."""

import copy
import json

import pytest

from api.operation_ledger import batches, executor, models, operations, paths
from api.operation_ledger.adapters import differentiated_bridge
from api.operation_ledger.adapters.assignment import AssignmentAdapter
from api.operation_ledger.adapters.assignment_groups import GroupResolutionError, resolve_assignment_groups
from api.operation_ledger.adapters.module_placement import attach_assignment_type_module_item
from api.platform_services import canvas_client, config


TIERS = [
    {"label": "Support", "group": "Blue", "description": "support body"},
    {"label": "Extend", "group": "Gold", "description": "extend body"},
]


def _get_all(path, params=None, timeout=30):
    if "group_categories" in path:
        return [{"id": 10, "name": " Blue "}, {"id": 20, "name": "GOLD"}], None
    if "/groups/10/memberships" in path:
        return [{"user_id": 9001}, {"user_id": 9002}], None
    if "/groups/20/memberships" in path:
        return [{"user_id": 9003}], None
    if "/enrollments" in path:
        return [{"user_id": 9001}, {"user_id": 9002}, {"user_id": 9003}], None
    return [], None


def _resolved():
    return resolve_assignment_groups("42", TIERS, canvas_get_all=_get_all, selected_category_id="7")


def test_group_resolver_safe_snapshot_and_source_order():
    result = _resolved()
    assert [row["label"] for row in result["safe"]["tiers"]] == ["Support", "Extend"]
    assert [row["student_count"] for row in result["safe"]["tiers"]] == [2, 1]
    assert result["student_ids_by_group"] == {"10": ["9001", "9002"], "20": ["9003"]}
    assert all(value not in json.dumps(result["safe"]) for value in ("9001", "9002", "9003"))


def test_exact_module_id_is_write_authority_even_when_display_name_differs(monkeypatch):
    fake = FakeCanvas()
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    context = Context()
    steps = []
    result = attach_assignment_type_module_item(
        course_id="42", content_id="9001", title="Practice",
        module_id="501", module_name="Renamed display", steps=steps,
        context=context, attach_step_key="attach", returned_object_id="assignment-1",
        deterministic_failure_state="blocked",
    )
    assert result["state"] == "applied", result
    assert not any(path.endswith("/modules") for _method, path, _body in fake.sends)
    assert any(path.endswith("/modules/501/items") for _method, path, _body in fake.sends)


def test_public_module_selection_options_refuse_mixed_or_implicit_create(monkeypatch):
    from api import content_push
    assert "module_id and create_module" in content_push._collect_options(
        "assignment", {"module_id": "501", "create_module": True}
    )[1]
    assert "non-empty module_name" in content_push._collect_options(
        "assignment", {"create_module": True, "module_name": ""}
    )[1]

    fake = FakeCanvas()
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    result = attach_assignment_type_module_item(
        course_id="42", content_id="9002", title="New Practice",
        module_name="Explicit New", create_module=True, steps=[], context=Context(),
        attach_step_key="attach", returned_object_id="assignment-2",
        deterministic_failure_state="blocked",
    )
    assert result["state"] == "applied"
    assert any(path.endswith("/modules") for _method, path, _body in fake.sends)


@pytest.mark.parametrize("case", ["missing", "ambiguous", "empty", "overlap", "coverage", "read"])
def test_group_resolution_law_fails_closed(case):
    if case == "missing":
        with pytest.raises(GroupResolutionError, match="Select"):
            resolve_assignment_groups("42", TIERS, canvas_get_all=_get_all, selected_category_id="")
        return

    def getter(path, params=None, timeout=30):
        data, error = _get_all(path, params, timeout)
        if case == "ambiguous" and "group_categories" in path:
            data.append({"id": 11, "name": "blue"})
        if case == "empty" and "/groups/10/memberships" in path:
            data = []
        if case == "overlap" and "/groups/20/memberships" in path:
            data = [{"user_id": 9002}, {"user_id": 9003}]
        if case == "coverage" and "/enrollments" in path:
            data.append({"user_id": 9004})
        if case == "read" and "group_categories" in path:
            return None, "HTTP 500"
        return data, error

    with pytest.raises(GroupResolutionError):
        resolve_assignment_groups("42", TIERS, canvas_get_all=getter, selected_category_id="7")


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
        self.assignments = {}
        self.overrides = {}
        self.modules = {"501": {"id": "501", "name": "Week 1"}}
        self.module_items = {}
        self.sends = []
        self.reads = []
        self.next_id = 100

    def send(self, method, path, body, timeout=30):
        self.sends.append((method, path, copy.deepcopy(body)))
        self.next_id += 1
        if method == "POST" and path.endswith("/modules"):
            module_id = str(self.next_id)
            self.modules[module_id] = {"id": module_id, **copy.deepcopy(body["module"])}
            return copy.deepcopy(self.modules[module_id]), None
        if method == "POST" and path.endswith("/assignments"):
            assignment_id = str(self.next_id)
            row = {"id": assignment_id, "course_id": "42", "assignment_group_id": "77",
                   "html_url": f"https://canvas.invalid/a/{assignment_id}",
                   **copy.deepcopy(body["assignment"])}
            self.assignments[assignment_id] = row
            self.overrides[assignment_id] = []
            return copy.deepcopy(row), None
        if method == "POST" and path.endswith("/overrides"):
            assignment_id = path.split("/assignments/")[1].split("/")[0]
            override = {"id": str(self.next_id), **copy.deepcopy(body["assignment_override"])}
            self.overrides[assignment_id].append(override)
            return copy.deepcopy(override), None
        if method == "POST" and path.endswith("/items"):
            module_id = path.split("/modules/")[1].split("/")[0]
            item = {"id": str(self.next_id), **copy.deepcopy(body["module_item"])}
            self.module_items[(module_id, item["id"])] = item
            return copy.deepcopy(item), None
        if method == "PUT" and "/assignments/" in path:
            assignment_id = path.rsplit("/", 1)[-1]
            self.assignments[assignment_id].update(copy.deepcopy(body["assignment"]))
            return copy.deepcopy(self.assignments[assignment_id]), None
        raise AssertionError((method, path, body))

    def get(self, path, params=None, timeout=20):
        self.reads.append(path)
        if "/modules/" in path and "/items/" in path:
            module_id, item_id = path.split("/modules/")[1].split("/items/")
            return copy.deepcopy(self.module_items.get((module_id, item_id))), None
        if path.endswith("/modules/501"):
            return copy.deepcopy(self.modules.get("501")), None
        if "/assignments/" in path and "/overrides/" in path:
            assignment_id, override_id = path.split("/assignments/")[1].split("/overrides/")
            row = next((row for row in self.overrides.get(assignment_id, []) if str(row["id"]) == override_id), None)
            return copy.deepcopy(row), None
        if "/assignments/" in path:
            return copy.deepcopy(self.assignments.get(path.rsplit("/", 1)[-1])), None
        return [], None

    def get_all(self, path, params=None, timeout=30):
        self.reads.append(path)
        if path.endswith("/assignment_groups"):
            return [{"id": 77, "name": "Coursework"}], None
        if path.endswith("/modules"):
            return list(copy.deepcopy(self.modules).values()), None
        if "/modules/" in path and path.endswith("/items"):
            module_id = path.split("/modules/")[1].split("/")[0]
            return [copy.deepcopy(item) for (current_module_id, _item_id), item in self.module_items.items()
                    if current_module_id == module_id], None
        if path.endswith("/overrides"):
            assignment_id = path.split("/assignments/")[1].split("/")[0]
            return copy.deepcopy(self.overrides.get(assignment_id, [])), None
        if path.endswith("/assignments"):
            search = str((params or {}).get("search_term") or "").casefold()
            return [copy.deepcopy(row) for row in self.assignments.values() if row["name"].casefold() == search], None
        return [], None


def _authoring_data():
    return {"title": "Practice", "description": "base", "points": 10, "tiers": copy.deepcopy(TIERS)}


def _build(monkeypatch, **request_overrides):
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.af.parse_file", lambda _path: (_authoring_data(), []))
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.af.tier_payloads", lambda data: copy.deepcopy(data["tiers"]))
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Red", "Core": "Blue", "Accelerate": "Silver", "Extend": "Gold",
    })
    monkeypatch.setattr(config, "get_canvas_base", lambda: "https://canvas.invalid")
    request = {"path": "synthetic.txt", "due_at": "2026-09-14T15:30:00-05:00",
               "module_name": "Week 1", "assignment_group_name": "Coursework",
               "tier_targets": [
                   {"tier": "Support", "group_name": "Blue"},
                   {"tier": "Extend", "group_name": "Gold"},
               ]}
    request.update(request_overrides)
    return AssignmentAdapter().build_payload(request)


def test_prepare_ignores_deprecated_tier_targets(monkeypatch):
    payload = _build(monkeypatch, tier_targets=None)
    assert "tier_targets" not in payload
    assert all("group_name" not in tier for tier in payload["tiers"])


def test_unrestricted_family_can_omit_group_category_and_due_without_a_bypass_flag(monkeypatch):
    monkeypatch.setattr(canvas_client, "canvas_get_all", lambda *_args, **_kwargs: ([], None))
    monkeypatch.setattr(canvas_client, "canvas_get", lambda *_args, **_kwargs: ({"id": "501"}, None))
    payload = _build(
        monkeypatch,
        tier_targets=None,
        assignment_group_name="",
        due_at="",
        module_id="501",
    )
    baseline = AssignmentAdapter().capture_baseline(payload, {"course_id": "42", "steps": []})
    assert baseline["existing_assignments"] == []
    assert payload.get("assignment_group_id") is None
    assert payload["due_at"] is None
    assert payload["bridge_due_at"] is None


def test_prepare_preserves_ordinary_dates_for_each_tier(monkeypatch):
    payload = _build(
        monkeypatch,
        due_at="2026-09-14T15:30:00-05:00",
        unlock_at="2026-09-01T08:00:00-05:00",
        lock_at="2026-09-30T23:59:00-05:00",
    )
    assert payload["due_at"] == "2026-09-14T15:30:00-05:00"
    assert payload["unlock_at"] == "2026-09-01T08:00:00-05:00"
    assert payload["lock_at"] == "2026-09-30T23:59:00-05:00"


def test_prepare_requires_unique_public_tags(monkeypatch):
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.af.parse_file", lambda _path: (_authoring_data(), []))
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.af.tier_payloads", lambda data: copy.deepcopy(data["tiers"]))
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Red", "Core": "Blue", "Accelerate": "Silver", "Extend": " red ",
    })
    with pytest.raises(ValueError, match="unique"):
        AssignmentAdapter().build_payload({
            "path": "synthetic.txt", "due_at": "2026-09-14T15:30:00-05:00",
            "module_name": "Week 1", "tier_targets": [
                {"tier": "Support", "group_name": "Blue"},
                {"tier": "Extend", "group_name": "Gold"},
            ],
        })


def test_assignmentforge_tier_tag_collision_is_a_stable_envelope_refusal(monkeypatch):
    """Contract (AC5): a tier-tag collision inside one AssignmentForge
    envelope refuses with a stable ``tier_tag_collision`` code, not a bare
    message. Support/Core/Extend -> Silver/Red/Blue (all distinct) still
    passes, matching QuizForge's identical shared rule."""
    colliding_data = {
        "title": "Practice", "description": "base", "points": 10,
        "tiers": [
            {"label": "Support", "group": "Blue", "description": "support body"},
            {"label": "Accelerate", "group": "Gold", "description": "accelerate body"},
        ],
    }
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.af.parse_file", lambda _path: (colliding_data, []))
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.af.tier_payloads", lambda data: copy.deepcopy(data["tiers"]))
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Silver", "Core": "Red", "Accelerate": "Silver", "Extend": "Blue",
    })
    with pytest.raises(differentiated_bridge.TierTagCollisionError) as excinfo:
        AssignmentAdapter().build_payload({
            "path": "synthetic.txt", "due_at": "2026-09-14T15:30:00-05:00",
            "module_name": "Week 1", "tier_targets": [
                {"tier": "Support", "group_name": "Blue"},
                {"tier": "Accelerate", "group_name": "Gold"},
            ],
        })
    assert excinfo.value.labels == ["Support", "Accelerate"]
    assert excinfo.value.tag == "Silver"


def test_differentiated_assignment_family_is_unrestricted_and_student_free(monkeypatch):
    payload = _build(
        monkeypatch,
        unlock_at="2026-09-01T08:00:00-05:00",
        lock_at="2026-09-30T23:59:00-05:00",
        module_id="501",
    )
    fake = FakeCanvas()
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake.get_all)
    baseline = AssignmentAdapter().capture_baseline(payload, {"course_id": "42", "steps": []})
    assert baseline["existing_assignments"] == []
    context = Context()
    result = AssignmentAdapter().execute(payload, {"course_id": "42", "steps": []}, baseline, {}, context)
    assert result["state"] == "applied", result
    sources = [row for row in fake.assignments.values() if row["name"] not in {"Practice", "Practice - Bridge"}]
    assert [row["name"] for row in sources] == ["Practice - Red", "Practice - Gold"]
    assert all(row["due_at"] == "2026-09-14T15:30:00-05:00" for row in sources)
    assert all(row["unlock_at"] == "2026-09-01T08:00:00-05:00" for row in sources)
    assert all(row["lock_at"] == "2026-09-30T23:59:00-05:00" for row in sources)
    assert all(row["published"] and not row["only_visible_to_overrides"] for row in sources)
    assert all(row["omit_from_final_grade"] and not row["post_to_sis"] for row in sources)
    assert all(len(fake.overrides[row["id"]]) == 0 for row in sources)
    assert not any("override" in step["step_key"] or "restrict" in step["step_key"] for step in context.steps)
    assert {str(row["content_id"]) for row in fake.module_items.values()} == {row["id"] for row in sources}
    assert all(value not in json.dumps(context.steps) for value in ("9001", "9002", "9003"))

    sends_before = len(fake.sends)
    retry = AssignmentAdapter().execute(payload, {"course_id": "42", "steps": copy.deepcopy(context.steps)}, baseline, {}, Context())
    assert retry["state"] == "applied"
    assert len(fake.sends) == sends_before


def test_created_tier_id_survives_create_verification_mismatch_and_resumes(monkeypatch):
    payload = _build(monkeypatch, module_id="501")
    fake = FakeCanvas()
    original_get = fake.get
    mismatch_once = {"value": True}

    def get_with_transient_canvas_normalization(path, params=None, timeout=20):
        row, error = original_get(path, params, timeout)
        if (
            mismatch_once["value"]
            and path.endswith("/assignments/101")
            and isinstance(row, dict)
        ):
            mismatch_once["value"] = False
            row["description"] = "Canvas returned a different description"
        return row, error

    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    monkeypatch.setattr(canvas_client, "canvas_get", get_with_transient_canvas_normalization)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake.get_all)
    baseline = AssignmentAdapter().capture_baseline(payload, {"course_id": "42", "steps": []})
    context = Context()

    first = AssignmentAdapter().execute(
        payload, {"course_id": "42", "steps": []}, baseline, {}, context
    )

    assert first["state"] == "sent_unknown"
    assert first["error_code"] == "assignment_create_unverified"
    create_step = next(row for row in first["steps"] if row["step_key"] == "create_tier_assignment:0")
    assert create_step["returned_object_id"] == "101"
    assert not AssignmentAdapter().check_drift(
        payload, {"course_id": "42", "steps": copy.deepcopy(context.steps)}, baseline
    )
    assert len([
        body for _method, path, body in fake.sends
        if path.endswith("/assignments")
        and body.get("assignment", {}).get("name") == "Practice - Red"
    ]) == 1
    assert not any(path.endswith("/assignments/101") and method == "PUT" for method, path, _body in fake.sends)

    retry = AssignmentAdapter().execute(
        payload,
        {"course_id": "42", "steps": copy.deepcopy(context.steps)},
        baseline,
        {},
        context,
    )

    assert retry["state"] == "applied", retry
    assert len([
        body for _method, path, body in fake.sends
        if path.endswith("/assignments")
        and body.get("assignment", {}).get("name") == "Practice - Red"
    ]) == 1
    assert any(path.endswith("/assignments/101") and method == "PUT" for method, path, _body in fake.sends)


def test_resume_operation_on_a_recorded_tier_zero_id_continues_without_a_second_create(
    monkeypatch, tmp_path,
):
    """Correction 1: resume_operation on a tier-0 sent_unknown operation with
    a recorded id continues from that id -- through the real Operation
    Ledger (executor.apply_operation, then retry_operation, exactly what the
    resume_operation MCP tool calls), not a bare adapter re-call."""
    monkeypatch.setattr(paths, "private_root", lambda: tmp_path / "private")
    payload = _build(monkeypatch, module_id="501")
    fake = FakeCanvas()
    original_get = fake.get
    mismatch_once = {"value": True}

    def get_with_transient_canvas_normalization(path, params=None, timeout=20):
        row, error = original_get(path, params, timeout)
        if (
            mismatch_once["value"]
            and path.endswith("/assignments/101")
            and isinstance(row, dict)
        ):
            mismatch_once["value"] = False
            row["description"] = "Canvas returned a different description"
        return row, error

    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    monkeypatch.setattr(canvas_client, "canvas_get", get_with_transient_canvas_normalization)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake.get_all)

    adapter = AssignmentAdapter()
    target = models.new_target(target_key="tk-1", idempotency_key="ik-1", course_id="42")
    baseline = adapter.capture_baseline(payload, target)
    target["baseline"] = baseline
    operation = models.new_operation(
        operation_id="op-tier-resume", kind="content.assignment",
        source_ref={"type": "test", "value": "tier-resume"},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=[target],
    )
    operations.create_operation(operation)
    batch = batches.freeze_batch(["op-tier-resume"], {"op-tier-resume": [{}]})
    operations.set_operation_review("op-tier-resume", batch)

    first = executor.apply_operation("op-tier-resume", batch["batch_id"], batch["review_digest"])
    assert first["status"] == "attention"
    tier0_creates = [
        body for _method, path, body in fake.sends
        if path.endswith("/assignments")
        and body.get("assignment", {}).get("name") == "Practice - Red"
    ]
    assert len(tier0_creates) == 1

    resumed = executor.retry_operation("op-tier-resume")

    assert resumed["status"] == "applied", resumed
    tier0_creates_after_resume = [
        body for _method, path, body in fake.sends
        if path.endswith("/assignments")
        and body.get("assignment", {}).get("name") == "Practice - Red"
    ]
    assert len(tier0_creates_after_resume) == 1, "resume must not create tier 0 a second time"


def test_family_tail_completes_after_teacher_edits_and_saves_live_titles(monkeypatch):
    """Law (Correction 3): after CE's own verified create, a teacher's
    Canvas Live edits to an unrestricted source's title, due date, and
    overrides are authoritative. Resume completes the tail and the saved
    family link records each source's current live title, not the
    originally pushed one."""
    payload = _build(monkeypatch, module_id="501")
    fake = FakeCanvas()
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake.get_all)
    baseline = AssignmentAdapter().capture_baseline(payload, {"course_id": "42", "steps": []})
    context = Context()

    first = AssignmentAdapter().execute(
        payload, {"course_id": "42", "steps": []}, baseline, {}, context)
    assert first["state"] == "applied", first

    sources = [row for row in fake.assignments.values()
               if row["name"] not in {"Practice", "Practice - Bridge"}]
    assert len(sources) == 2

    # The teacher renames each source, clears its due date, and adds a
    # per-class override in Canvas Live -- none of this is CE's business
    # any more.
    for row in sources:
        row["name"] = f"ECR Prep 3: {row['name']}"
        row["due_at"] = None
        fake.overrides[row["id"]].append(
            {"id": f"ov-{row['id']}", "due_at": "2026-10-01T23:59:00Z"})

    sends_before = len(fake.sends)
    retry = AssignmentAdapter().execute(
        payload, {"course_id": "42", "steps": copy.deepcopy(context.steps)},
        baseline, {}, context,
    )
    assert retry["state"] == "applied", retry
    assert not any(
        method == "POST" and path.endswith("/assignments")
        for method, path, _body in fake.sends[sends_before:]
    ), "resume must not recreate a renamed source"

    saved = config.get_sis_grade_bridge("42", payload["base_title"])
    assert sorted(saved["source_titles"]) == sorted(row["name"] for row in sources)


def _matching_source_assignment(assignment_id: str, *, name: str = "Practice - Red") -> dict:
    """A live Canvas assignment matching exactly what tier 0 (Support ->
    Red) would have created, for AC3's ambiguous-create-lookup tests."""
    return {
        "id": assignment_id, "name": name, "course_id": "42",
        "assignment_group_id": "77", "html_url": f"https://canvas.invalid/a/{assignment_id}",
        "published": False, "description": "support body",
        "submission_types": ["online_text_entry"], "grading_type": "points",
        "only_visible_to_overrides": False, "omit_from_final_grade": True,
        "post_to_sis": False, "points_possible": 10.0,
    }


def _crashed_create_step(*, retry_attempted: bool = False) -> dict:
    """A tier-0 create step whose Canvas POST was sent, but whose response
    was never recorded (the exact Issue #11-shaped crash AC3 resolves)."""
    step = {
        "step_key": "create_tier_assignment:0", "state": "claimed",
        "attempt_id": None, "returned_object_id": None, "error_code": None,
        "private_diagnostic": None, "outbound_started_at": "2026-09-14T12:00:00+00:00",
        "updated_at": "2026-09-14T12:00:00+00:00",
    }
    if retry_attempted:
        step["retry_attempted"] = True
    return step


def test_ambiguous_create_with_no_recorded_id_adopts_the_single_exact_match(monkeypatch):
    """AC3: exactly one exact-title match -> adopt its id, re-read it, and
    continue the family instead of sending a second create."""
    payload = _build(monkeypatch, module_id="501")
    fake = FakeCanvas()
    fake.assignments["101"] = _matching_source_assignment("101")
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake.get_all)
    baseline = AssignmentAdapter().capture_baseline(payload, {"course_id": "42", "steps": []})
    context = Context()

    result = AssignmentAdapter().execute(
        payload, {"course_id": "42", "steps": [_crashed_create_step()]}, baseline, {}, context,
    )

    create_step = next(row for row in result["steps"] if row["step_key"] == "create_tier_assignment:0")
    assert create_step["returned_object_id"] == "101"
    assert create_step["state"] in ("applied", "skipped")
    assert not any(
        method == "POST" and path.endswith("/assignments")
        and body.get("assignment", {}).get("name") == "Practice - Red"
        for method, path, body in fake.sends
    )


def test_ambiguous_create_with_no_recorded_id_and_no_match_retries_the_create_once(monkeypatch):
    """AC3: no exact-title match -> retry the create exactly once."""
    payload = _build(monkeypatch, module_id="501")
    fake = FakeCanvas()
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake.get_all)
    baseline = AssignmentAdapter().capture_baseline(payload, {"course_id": "42", "steps": []})
    context = Context()

    result = AssignmentAdapter().execute(
        payload, {"course_id": "42", "steps": [_crashed_create_step()]}, baseline, {}, context,
    )

    assert result["state"] == "applied", result
    creates = [
        body for _method, path, body in fake.sends
        if path.endswith("/assignments")
        and body.get("assignment", {}).get("name") == "Practice - Red"
    ]
    assert len(creates) == 1


def test_ambiguous_create_with_no_recorded_id_and_two_matches_stops_as_duplicate_suspected(monkeypatch):
    """AC3: more than one exact-title match -> stop with duplicate_suspected,
    never guess which one this attempt created."""
    payload = _build(monkeypatch, module_id="501")
    fake = FakeCanvas()
    fake.assignments["101"] = _matching_source_assignment("101")
    fake.assignments["103"] = _matching_source_assignment("103")
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake.get_all)
    baseline = AssignmentAdapter().capture_baseline(payload, {"course_id": "42", "steps": []})
    context = Context()

    result = AssignmentAdapter().execute(
        payload, {"course_id": "42", "steps": [_crashed_create_step()]}, baseline, {}, context,
    )

    assert result["state"] == "blocked"
    assert result["error_code"] == "duplicate_suspected"
    assert not any(method == "POST" and path.endswith("/assignments") for method, path, _body in fake.sends)


def test_ambiguous_create_retry_marker_is_idempotent_under_resume(monkeypatch):
    """AC3 law: a step already marked retry_attempted never retries the
    create a second time, so a repeated resume cannot pile up duplicates."""
    payload = _build(monkeypatch, module_id="501")
    fake = FakeCanvas()
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake.get_all)
    baseline = AssignmentAdapter().capture_baseline(payload, {"course_id": "42", "steps": []})
    context = Context()

    result = AssignmentAdapter().execute(
        payload,
        {"course_id": "42", "steps": [_crashed_create_step(retry_attempted=True)]},
        baseline, {}, context,
    )

    assert result["state"] == "failed"
    create_step = next(row for row in result["steps"] if row["step_key"] == "create_tier_assignment:0")
    assert create_step["error_code"] == "assignment_create_retry_failed"
    assert not fake.sends


def test_source_shape_matching_tolerates_canvas_html_normalization():
    from api.operation_ledger.adapters.assignment_tiered import _source_shape_matches

    expected = {
        "name": "Practice - Red",
        "description": "ECR & prep",
        "submission_types": ["on_paper"],
        "grading_type": "points",
        "only_visible_to_overrides": False,
        "omit_from_final_grade": True,
        "post_to_sis": False,
        "points_possible": 100.0,
        "published": False,
    }
    actual = {**expected, "description": "  ECR &amp;   prep  ", "submission_types": ["on_paper"]}

    assert _source_shape_matches(actual, expected, published=False)


def test_source_shape_matching_tolerates_a_sanitized_scaffolding_panel(monkeypatch):
    """Correction 1: the composed base + scaffolding-panel description (a
    real tag, attributes, and entities -- not just plain text) still
    verifies after a Canvas-shaped sanitization round trip, but a changed
    visible sentence inside it still does not."""
    from api.operation_ledger.adapters.assignment_tiered import _source_shape_matches

    expected = {
        "name": "Practice - Silver",
        "description": (
            '<p>Read the passage and respond.</p>'
            '<div class="scaffold" style="border:1px solid #000;" data-tier="Accelerate">'
            '<p>Extension &mdash; cite two sources &middot; use a &quot;so what&quot; closer.</p>'
            '</div>'
        ),
        "submission_types": ["online_text_entry"],
        "grading_type": "points",
        "only_visible_to_overrides": False,
        "omit_from_final_grade": True,
        "post_to_sis": False,
        "points_possible": 10.0,
        "published": False,
    }
    sanitized = {
        **expected,
        "description": (
            '<p>Read the passage and respond.</p>'
            '<div data-tier="Accelerate"   style="border:1px solid #000;"    class="scaffold">'
            '  <p>Extension &#8212; cite two sources &#183; use a &#34;so what&#34; closer.</p>  '
            '</div>'
        ),
    }
    assert _source_shape_matches(sanitized, expected, published=False)

    changed = {**sanitized, "description": sanitized["description"].replace(
        "cite two sources", "cite three sources")}
    assert not _source_shape_matches(changed, expected, published=False)

    # CE sends normalize_student_text(description): Canvas holds " - " where
    # the draft had an em dash, and that must still verify (Issue #11).
    dash_normalized = {**sanitized, "description": sanitized["description"].replace(
        " &#8212; ", " - ")}
    assert _source_shape_matches(dash_normalized, expected, published=False)

    # Canvas's sanitizer may add structure and drop attributes; only visible
    # text is compared (Issue #11 live run).
    restructured = {**expected, "description": (
        '<p>Read the passage and respond.</p><div>'
        '<p>Extension - cite two sources · use a "so what" closer.</p></div>')}
    assert _source_shape_matches(restructured, expected, published=False)


def test_whole_class_payload_has_no_bridge(monkeypatch):
    data = {"title": "Whole", "description": "body", "points": 10}
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.af.parse_file", lambda _path: (data, []))
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.af.tier_payloads", lambda _data: [])
    payload = AssignmentAdapter().build_payload({"path": "whole.txt"})
    assert "tiers" not in payload
    assert "bridge_due_at" not in payload
