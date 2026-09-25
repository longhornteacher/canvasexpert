"""Mirror-first laws for differentiated-family grade bridge operations."""

import copy

import pytest

from api import course_catalog, sis_grade_bridge
from api.mirror import read_service
from api.operation_ledger import operations, paths, receipts
from api.operation_ledger.adapters import differentiated_bridge
from api.operation_ledger.adapters.sis_grade_bridge import (
    _grade_request,
    _resolve_bridge_target,
)
from api.platform_services import canvas_client, config


class FakeCanvas:
    def __init__(self):
        self.assignments = {
            "source-a": {
                "id": "source-a", "course_id": "course-1", "name": "Synthetic Family - Red",
                "description": "a", "points_possible": 10, "assignment_group_id": "77",
                "due_at": "2026-10-01T15:00:00-05:00", "grading_type": "points",
                "submission_types": ["online_text_entry"], "published": True,
                "only_visible_to_overrides": True, "omit_from_final_grade": True,
                "post_to_sis": False,
            },
            "source-b": {
                "id": "source-b", "course_id": "course-1", "name": "Synthetic Family - Blue",
                "description": "b", "points_possible": 10, "assignment_group_id": "77",
                "due_at": "2026-10-02T15:00:00-05:00", "grading_type": "points",
                "submission_types": ["online_text_entry"], "published": True,
                "only_visible_to_overrides": True, "omit_from_final_grade": True,
                "post_to_sis": False,
            },
            "bridge": {
                "id": "bridge", "course_id": "course-1", "name": "Synthetic Family - Bridge",
                "description": differentiated_bridge.bridge_description(),
                "points_possible": 10, "assignment_group_id": "77",
                "due_at": "2026-10-01T23:59:00-05:00", "grading_type": "points",
                "submission_types": ["none"], "published": True,
                "only_visible_to_overrides": False, "omit_from_final_grade": False,
                "post_to_sis": True, "html_url": "https://canvas.invalid/a/bridge",
            },
        }
        self.submissions = {
            "source-a": [
                {"user_id": "student-1", "workflow_state": "graded", "score": 8,
                 "posted_at": "2026-09-01T12:00:00Z"},
                {"user_id": "student-2", "workflow_state": "pending_review", "score": 5,
                 "submitted_at": "2026-09-01T11:00:00Z"},
            ],
            "source-b": [
                {"user_id": "student-3", "workflow_state": "graded", "excused": True,
                 "posted_at": "2026-09-01T12:00:00Z"},
            ],
        }
        self.bridge_submissions = {}
        self.modules = {"501": {"id": "501", "name": "Week 1", "position": 1}}
        self.module_items = {
            ("501", "item-a"): {"id": "item-a", "type": "Assignment", "content_id": "source-a", "title": "Synthetic Family - Red", "position": 1},
            ("501", "item-b"): {"id": "item-b", "type": "Assignment", "content_id": "source-b", "title": "Synthetic Family - Blue", "position": 2},
            ("501", "item-bridge"): {"id": "item-bridge", "type": "Assignment", "content_id": "bridge", "title": "Synthetic Family - Bridge", "position": 3},
        }
        self.send_calls = []

    def get(self, path, params=None, timeout=20):
        if "/modules/" in path and "/items/" in path:
            module_id, item_id = path.split("/modules/")[1].split("/items/")
            return copy.deepcopy(self.module_items.get((module_id, item_id))), None
        if "/submissions/" in path:
            user_id = path.rsplit("/", 1)[-1]
            return copy.deepcopy(self.bridge_submissions.get(user_id)), None
        if "/assignments/" in path:
            return copy.deepcopy(self.assignments.get(path.rsplit("/", 1)[-1])), None
        return None, "missing"

    def get_all_complete(self, path, params=None, timeout=30):
        if path.endswith("/submissions"):
            assignment_id = path.split("/assignments/")[1].split("/")[0]
            if assignment_id == "bridge":
                return [
                    {"user_id": user_id, **copy.deepcopy(submission)}
                    for user_id, submission in self.bridge_submissions.items()
                ], None, True
            return copy.deepcopy(self.submissions.get(assignment_id, [])), None, True
        if "/modules/" in path and path.endswith("/items"):
            module_id = path.split("/modules/")[1].split("/items")[0]
            return [
                copy.deepcopy(row)
                for (item_module, _), row in self.module_items.items()
                if item_module == module_id
            ], None, True
        if path.endswith("/modules"):
            return [copy.deepcopy(row) for row in self.modules.values()], None, True
        return [], None, True

    def send(self, method, path, request, timeout=30):
        self.send_calls.append((method, path, copy.deepcopy(request)))
        if method == "POST" and path.endswith("/assignments"):
            assignment = {
                "id": "created-bridge", "course_id": "course-1",
                "html_url": "https://canvas.invalid/a/created-bridge",
                **copy.deepcopy(request["assignment"]),
            }
            self.assignments["created-bridge"] = assignment
            return copy.deepcopy(assignment), None
        user_id = path.rsplit("/", 1)[-1]
        submission = request["submission"]
        if submission.get("excuse"):
            self.bridge_submissions[user_id] = {"excused": True, "score": None}
        else:
            self.bridge_submissions[user_id] = {
                "excused": False,
                "score": None if submission.get("posted_grade") == "" else float(submission["posted_grade"]),
            }
        return copy.deepcopy(self.bridge_submissions[user_id]), None


@pytest.fixture
def bridge_harness(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "private_root", lambda: tmp_path / "private")
    monkeypatch.setattr(config, "active_courses", lambda: [{"id": "course-1", "name": "Synthetic Course", "active": True}])
    monkeypatch.setattr(config, "saved_courses", lambda: [{"id": "course-1", "name": "Synthetic Course", "active": True}])
    monkeypatch.setattr(config, "get_canvas_base", lambda: "https://canvas.invalid")
    fake = FakeCanvas()
    registration = {
        "family_title": "Synthetic Family",
        "source_assignment_ids": ["source-a", "source-b"],
        "source_titles": ["Synthetic Family - Red", "Synthetic Family - Blue"],
        "bridge_assignment_id": "bridge",
        "bridge_state_digest": differentiated_bridge.structural_digest(
            differentiated_bridge.assignment_shape(fake.assignments["bridge"], [])
        ),
    }

    def read_local_catalog(_course_id, **_kwargs):
        return {"catalog": {
            "updated_at": "2026-09-21T12:00:00Z",
            "assignments": {"state": "current", "records": copy.deepcopy(fake.assignments)},
            "modules": {"state": "current", "records": [copy.deepcopy(row) for row in fake.modules.values()]},
        }}

    def read_local_submissions(_course_id, **_kwargs):
        records = []
        for assignment_id, rows in fake.submissions.items():
            for row in rows:
                records.append({"assignment_id": assignment_id, **copy.deepcopy(row)})
        for user_id, row in fake.bridge_submissions.items():
            records.append({"assignment_id": "bridge", "user_id": user_id, **copy.deepcopy(row)})
        return {"source": "mirror", "state": "current", "records": records}

    monkeypatch.setattr(course_catalog, "read_catalog", read_local_catalog)
    monkeypatch.setattr(read_service, "private_submissions", read_local_submissions)
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda course, title: copy.deepcopy(registration) if (course, title) == ("course-1", "Synthetic Family") else None)
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [copy.deepcopy(registration)])
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "canvas_get_all_complete", fake.get_all_complete)
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    monkeypatch.setattr("api.webui.mirror_service.notify_course_changed", lambda _course: None)
    return fake


def _preview():
    return sis_grade_bridge.preview_sis_grade_bridge("course-1", "Synthetic Family")


def _apply(preview):
    return sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )


def test_preview_is_mirror_only_and_does_not_call_canvas(bridge_harness, monkeypatch):
    def fail_live(*_args, **_kwargs):
        raise AssertionError("preview reached CanvasLive")

    monkeypatch.setattr(canvas_client, "canvas_get", fail_live)
    monkeypatch.setattr(canvas_client, "canvas_get_all_complete", fail_live)
    monkeypatch.setattr(canvas_client, "_canvas_send", fail_live)

    preview = _preview()

    assert preview["ok"] is True
    assert preview["preview"]["common_due_date"] is None


def test_stale_local_mirror_refuses_without_live_fallback(bridge_harness, monkeypatch):
    """AC2 (don't cry wolf): a non-current local mirror is its own plain-text
    answer, not a bare 'mirror_read_failed' and never 'drift_detected'."""
    monkeypatch.setattr(course_catalog, "read_catalog", lambda *_args, **_kwargs: {
        "catalog": {"assignments": {"state": "stale", "records": {}}}
    })
    monkeypatch.setattr(canvas_client, "canvas_get", lambda *_args, **_kwargs: pytest.fail("live fallback"))
    monkeypatch.setattr(canvas_client, "canvas_get_all_complete", lambda *_args, **_kwargs: pytest.fail("live fallback"))

    result = _preview()

    assert result == {
        "ok": False,
        "code": "catalog_not_current",
        "blocking": True,
        "sections": {"assignments": "stale"},
        "error": "The local course catalog is not current.",
        "next": (
            "Ask the teacher whether to refresh this course's structure "
            "(refresh_course_structure). Do not refresh automatically."
        ),
    }


def test_apply_with_non_current_catalog_blocks_as_catalog_not_current_not_drift(
    bridge_harness, monkeypatch,
):
    """Law (AC2): apply-time check_drift blocks as catalog_not_current, not
    drift_detected, when the local catalog goes stale between preview and
    apply -- the evidence's second symptom."""
    preview = _preview()
    assert preview["ok"] is True

    monkeypatch.setattr(course_catalog, "read_catalog", lambda *_args, **_kwargs: {
        "catalog": {"assignments": {"state": "stale", "records": {}}}
    })

    result = _apply(preview)

    assert result["ok"] is False
    operation = operations.get_operation(preview["operation_id"])
    target = operation["targets"][0]
    assert target["error_code"] == "catalog_not_current"
    assert target["error_code"] != "drift_detected"
    assert target["next"] == (
        "Ask the teacher whether to refresh this course's structure "
        "(refresh_course_structure). Do not refresh automatically."
    )


def test_present_tier_score_copies_even_without_post_marker(bridge_harness):
    fake = bridge_harness
    fake.submissions["source-a"][0]["posted_at"] = None
    fake.submissions["source-a"][1]["score"] = None

    preview = _preview()
    assert preview["ok"] is True
    assert preview["preview"]["counts"]["raised_scores"] == 1
    assert preview["preview"]["counts"]["copied_excused"] == 1
    assert preview["preview"]["raises"] == 1
    assert preview["preview"]["already_canon"] == 0
    assert preview["preview"]["held"] == 0
    assert preview["preview"]["held_students"] == []

    result = _apply(preview)

    assert result["status"] == "applied"
    assert fake.bridge_submissions["student-1"]["score"] == 8
    assert fake.bridge_submissions["student-3"]["excused"] is True
    assert all(call[0] == "PUT" for call in fake.send_calls)


def test_matching_scores_from_multiple_tiers_still_reach_bridge(bridge_harness):
    fake = bridge_harness
    fake.submissions["source-b"].append({
        "user_id": "student-1", "workflow_state": "graded", "score": 8,
        "posted_at": None,
    })

    result = _apply(_preview())

    assert result["status"] == "applied"
    assert fake.bridge_submissions["student-1"]["score"] == 8


def test_approved_apply_pushes_grades_but_never_repairs_modules(bridge_harness):
    fake = bridge_harness
    fake.module_items.clear()
    fake.modules["502"] = {"id": "502", "name": "Unexpected", "position": 2}

    result = _apply(_preview())

    assert result["status"] == "applied"
    assert all("/modules/" not in call[1] for call in fake.send_calls)
    assert all(call[0] == "PUT" for call in fake.send_calls)


def test_mirror_score_drift_blocks_before_any_live_write(bridge_harness):
    fake = bridge_harness
    preview = _preview()
    fake.submissions["source-a"][0]["score"] = 7

    result = _apply(preview)

    assert result["status"] in {"blocked", "attention"}
    assert fake.send_calls == []


def test_due_dates_are_not_a_preview_invariant(bridge_harness):
    fake = bridge_harness
    fake.assignments["source-a"]["due_at"] = "2026-11-01T15:00:00-05:00"
    fake.assignments["source-b"]["due_at"] = "2026-12-01T15:00:00-05:00"

    result = _preview()

    assert result["ok"] is True
    assert result["preview"]["common_due_date"] is None
    assert result["preview"]["warnings"] == []


def test_missing_bridge_is_refused_from_local_snapshot(bridge_harness):
    fake = bridge_harness
    fake.assignments.pop("bridge")

    result = _preview()

    assert result["ok"] is False
    assert result["error"] == "family_link_bridge_missing_or_renamed"
    assert result["blocking"] is True


def test_operation_records_only_local_read_source(bridge_harness):
    preview = _preview()
    operation = operations.get_operation(preview["operation_id"])

    assert operation["normalized_payload"]["read_source"] == "mirror"
    assert operation["normalized_payload"]["bridge_only"] is True
    assert operation["normalized_payload"]["due_at"] is None
    assert receipts.list_receipts() == []


def test_reconcile_preview_apply_links_a_two_theme_family(tmp_path, monkeypatch):
    """Example (happy path): reconcile -> preview -> apply for a synthetic,
    not-yet-linked Two Theme-shaped family (T4.1).

    Family-link storage is stubbed in-memory (never the real machine/workspace
    config) so this test cannot read or write outside the sandbox.
    """
    monkeypatch.setattr(paths, "private_root", lambda: tmp_path / "private")
    monkeypatch.setattr(config, "active_courses", lambda: [{"id": "course-1", "name": "Synthetic Course", "active": True}])
    monkeypatch.setattr(config, "saved_courses", lambda: [{"id": "course-1", "name": "Synthetic Course", "active": True}])
    monkeypatch.setattr(config, "get_canvas_base", lambda: "https://canvas.invalid")
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Silver", "Core": "Gold", "Accelerate": "",
    })
    saved_bridges: dict[str, dict] = {}
    monkeypatch.setattr(
        config, "get_sis_grade_bridge",
        lambda _course, title: copy.deepcopy(saved_bridges.get(title)),
    )
    monkeypatch.setattr(
        config, "list_sis_grade_bridges",
        lambda _course: [copy.deepcopy(row) for row in saved_bridges.values()],
    )

    def fake_save(_course, registration):
        saved_bridges[registration["family_title"]] = copy.deepcopy(registration)
        return copy.deepcopy(registration)

    monkeypatch.setattr(config, "save_sis_grade_bridge", fake_save)
    fake = FakeCanvas()
    fake.assignments["source-a"]["name"] = "Two Theme SCRs - Silver"
    fake.assignments["source-b"]["name"] = "Two Theme SCRs - Gold"
    fake.assignments["bridge"]["name"] = "Two Theme SCRs - Bridge"

    def read_local_catalog(_course_id, **_kwargs):
        return {"catalog": {
            "updated_at": "2026-09-21T12:00:00Z",
            "assignments": {"state": "current", "records": copy.deepcopy(fake.assignments)},
            "modules": {"state": "current", "records": [copy.deepcopy(row) for row in fake.modules.values()]},
        }}

    def read_local_submissions(_course_id, **_kwargs):
        records = []
        for assignment_id, rows in fake.submissions.items():
            for row in rows:
                records.append({"assignment_id": assignment_id, **copy.deepcopy(row)})
        return {"source": "mirror", "state": "current", "records": records}

    monkeypatch.setattr(course_catalog, "read_catalog", read_local_catalog)
    monkeypatch.setattr(read_service, "private_submissions", read_local_submissions)
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "canvas_get_all_complete", fake.get_all_complete)
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    monkeypatch.setattr("api.webui.mirror_service.notify_course_changed", lambda _course: None)

    matrix = sis_grade_bridge.reconcile_sis_grade_bridges("course-1")
    row = next(item for item in matrix["matrix"] if item["family_title"] == "Two Theme SCRs")
    assert row["status"] == "missing"
    assert row["bridge_assignment_id"] == "bridge"
    assert row["source_assignment_ids"] == ["source-a", "source-b"]

    preview = sis_grade_bridge.preview_sis_grade_bridge_reconciliation(
        "course-1", "Two Theme SCRs"
    )
    assert preview["ok"] is True

    result = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )
    assert result["ok"] is True

    linked = config.get_sis_grade_bridge("course-1", "Two Theme SCRs")
    assert linked["bridge_assignment_id"] == "bridge"

    followup = sis_grade_bridge.preview_sis_grade_bridge("course-1", "Two Theme SCRs")
    assert followup["ok"] is True


def test_two_link_only_repairs_previewed_together_both_apply(tmp_path, monkeypatch):
    """Example (AC3): two link-only SIS bridge repairs, previewed together
    and then applied one after the other, both reach 'applied'. This is the
    exact evidence scenario (brief 'don't cry wolf'): the first repair's
    apply must not invalidate the local catalog (AC1), so the second
    repair's own drift check never sees a stale/not-current catalog and
    never falsely reports drift_detected or catalog_not_current (AC2)."""
    monkeypatch.setattr(paths, "private_root", lambda: tmp_path / "private")
    monkeypatch.setattr(config, "active_courses", lambda: [{"id": "course-1", "name": "Synthetic Course", "active": True}])
    monkeypatch.setattr(config, "saved_courses", lambda: [{"id": "course-1", "name": "Synthetic Course", "active": True}])
    monkeypatch.setattr(config, "get_canvas_base", lambda: "https://canvas.invalid")
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Silver", "Core": "Gold", "Accelerate": "",
    })
    saved_bridges: dict[str, dict] = {}
    monkeypatch.setattr(
        config, "get_sis_grade_bridge",
        lambda _course, title: copy.deepcopy(saved_bridges.get(title)),
    )
    monkeypatch.setattr(
        config, "list_sis_grade_bridges",
        lambda _course: [copy.deepcopy(row) for row in saved_bridges.values()],
    )

    def fake_save(_course, registration):
        saved_bridges[registration["family_title"]] = copy.deepcopy(registration)
        return copy.deepcopy(registration)

    monkeypatch.setattr(config, "save_sis_grade_bridge", fake_save)

    fake = FakeCanvas()
    # Family A reuses the harness's default source-a/source-b/bridge ids;
    # family B is a second, fully independent, already-safe bridge family.
    fake.assignments["source-c"] = {
        **fake.assignments["source-a"], "id": "source-c", "name": "Family B - Silver",
    }
    fake.assignments["source-d"] = {
        **fake.assignments["source-b"], "id": "source-d", "name": "Family B - Gold",
    }
    fake.assignments["bridge-b"] = {
        **fake.assignments["bridge"], "id": "bridge-b", "name": "Family B - Bridge",
    }
    fake.assignments["source-a"]["name"] = "Family A - Silver"
    fake.assignments["source-b"]["name"] = "Family A - Gold"
    fake.assignments["bridge"]["name"] = "Family A - Bridge"

    def read_local_catalog(_course_id, **_kwargs):
        return {"catalog": {
            "updated_at": "2026-09-21T12:00:00Z",
            "assignments": {"state": "current", "records": copy.deepcopy(fake.assignments)},
            "modules": {"state": "current", "records": [copy.deepcopy(row) for row in fake.modules.values()]},
        }}

    def read_local_submissions(_course_id, **_kwargs):
        return {"source": "mirror", "state": "current", "records": []}

    monkeypatch.setattr(course_catalog, "read_catalog", read_local_catalog)
    monkeypatch.setattr(read_service, "private_submissions", read_local_submissions)
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "canvas_get_all_complete", fake.get_all_complete)
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    monkeypatch.setattr("api.webui.mirror_service.notify_course_changed", lambda _course: None)
    invalidate_calls = []
    monkeypatch.setattr(
        course_catalog, "invalidate_scope",
        lambda *args, **kwargs: invalidate_calls.append(args) or None,
    )

    preview_a = sis_grade_bridge.preview_sis_grade_bridge_reconciliation(
        "course-1", "Family A"
    )
    preview_b = sis_grade_bridge.preview_sis_grade_bridge_reconciliation(
        "course-1", "Family B"
    )
    assert preview_a["ok"] is True
    assert preview_b["ok"] is True

    result_a = sis_grade_bridge.apply_sis_grade_bridge(
        preview_a["operation_id"], preview_a["batch_id"], preview_a["review_digest"]
    )
    result_b = sis_grade_bridge.apply_sis_grade_bridge(
        preview_b["operation_id"], preview_b["batch_id"], preview_b["review_digest"]
    )

    assert result_a["ok"] is True
    assert result_a["status"] == "applied"
    assert result_b["ok"] is True
    assert result_b["status"] == "applied"
    # AC1: a link-only repair invalidates no catalog scope.
    assert invalidate_calls == []


def _final_source(score=None, excused=False):
    return {"score": score, "excused": excused}


def _bridge_state(score=None, excused=False):
    return {"score": score, "excused": excused}


@pytest.mark.parametrize(
    ("final_sources", "bridge", "expected_entry", "expected_held"),
    [
        pytest.param(
            [_final_source(90)], _bridge_state(95), None, False,
            id="bridge_95_source_90_no_write",
        ),
        pytest.param(
            [_final_source(95)], _bridge_state(90),
            {"action": "score", "excused": False, "score": 95}, False,
            id="source_95_bridge_90_raises_to_95",
        ),
        pytest.param(
            [_final_source(80), _final_source(92)], _bridge_state(None),
            {"action": "score", "excused": False, "score": 92}, False,
            id="two_sources_bridge_blank_takes_highest",
        ),
        pytest.param(
            [], _bridge_state(70), None, False,
            id="source_blank_bridge_70_no_entry",
        ),
        pytest.param(
            [], _bridge_state(None), None, False,
            id="all_sources_blank_no_entry",
        ),
        pytest.param(
            [_final_source(excused=True)], _bridge_state(None),
            {"action": "excuse", "excused": True, "score": None}, False,
            id="source_excused_bridge_blank_excuses",
        ),
        pytest.param(
            [_final_source(excused=True)], _bridge_state(80), None, True,
            id="source_excused_bridge_scored_is_held",
        ),
        pytest.param(
            [_final_source(excused=True), _final_source(70)], _bridge_state(None),
            None, True,
            id="one_excused_one_scored_is_held",
        ),
        pytest.param(
            [_final_source(85)], _bridge_state(None),
            {"action": "score", "excused": False, "score": 85}, False,
            id="late_penalized_source_writes_the_final_85",
        ),
    ],
)
def test_resolve_bridge_target_law(final_sources, bridge, expected_entry, expected_held):
    """Law (contract section 6 score reconciliation rule, AC1-AC4): highest
    score is canon, a bridge score is never lowered, a blank source is left
    alone, and any mix of excused/scored across the sources and the bridge
    is held, never guessed."""
    entry, held = _resolve_bridge_target(final_sources, bridge)
    assert entry == expected_entry
    assert held is expected_held


def test_score_entry_never_carries_late_policy_status(monkeypatch):
    """AC5: a score entry writes posted_grade only -- the Canvas-reported
    final score already has any late penalty baked in, so no
    late_policy_status is ever copied to the bridge."""
    entry, held = _resolve_bridge_target([_final_source(85)], _bridge_state(None))
    assert held is False
    request = _grade_request(entry)
    assert request == {"posted_grade": "85", "excuse": False}
    assert "late_policy_status" not in request


def test_preview_summary_shape_is_pseudonym_only_contract(bridge_harness, monkeypatch, tmp_path):
    """Contract (AC6): the preview summary carries raises, already_canon,
    held, and held_students -- pseudonyms only, resolved through the
    existing identity/pseudonym service, never Canvas user ids or names."""
    from api import feedback_vault, identity_vault_service
    monkeypatch.setattr(
        identity_vault_service, "open_vault",
        lambda root=None: feedback_vault.Vault(str(tmp_path / "identity-vault.json")),
    )
    fake = bridge_harness
    # student-2's source is excused while the bridge is already scored --
    # a genuine mixed excused/scored state that must be held (AC4).
    fake.submissions["source-a"][1]["excused"] = True
    fake.submissions["source-a"][1]["score"] = None
    fake.bridge_submissions["student-2"] = {"score": 50, "excused": False}

    result = _preview()

    assert result["ok"] is True
    preview = result["preview"]
    assert set(preview) >= {"raises", "already_canon", "held", "held_students"}
    assert preview["raises"] == 1
    assert preview["already_canon"] == 0
    assert preview["held"] == 1
    assert isinstance(preview["held_students"], list)
    assert len(preview["held_students"]) == 1
    pseudonym = preview["held_students"][0]
    assert isinstance(pseudonym, str) and pseudonym
    # Never a Canvas user id or a name -- a real vault pseudonym only.
    assert pseudonym not in {"student-1", "student-2", "student-3"}


def test_no_generated_grade_write_ever_targets_a_tier_source(bridge_harness):
    """Law (AC2): every grade-write request path names the bridge
    assignment id; none ever names a tier source id."""
    fake = bridge_harness
    fake.submissions["source-a"][0]["score"] = 99  # forces a raise

    result = _apply(_preview())

    assert result["status"] == "applied"
    put_calls = [call for call in fake.send_calls if call[0] == "PUT"]
    assert put_calls, "expected at least one grade write"
    for _method, path, _request in put_calls:
        assert "/assignments/bridge/" in path
        assert "/assignments/source-a/" not in path
        assert "/assignments/source-b/" not in path
