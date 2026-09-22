"""Landing a staged draft in Canvas from the connected agent.

The pair is deliberately thin: it resolves one staged label, hands the same
prepare request the push tab hands the adapter, and applies through the same
Operation Ledger executor. What these tests hold down is the boundary around
that -- which drafts it can reach, which options each kind accepts, and what
crosses back out to the client.

The retained push tab is a control-console path; these tests protect equivalence
at the runtime/write boundary, not a requirement that the browser remain the
primary authoring surface.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from api import content_push, runtime_paths
from api.mcp_server import server, tools
from api.operation_ledger import models
from api.platform_services import canvas_client, config, workspace


@pytest.fixture(autouse=True)
def _workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _current_course(monkeypatch):
    monkeypatch.setattr(config, "active_courses", lambda: [
        {"id": "course-x", "name": "Invented Course", "active": True},
    ])


def _stage(kind: str, name: str, body: str = "<ENVELOPE>draft</ENVELOPE>") -> str:
    """Drop a marker-gated draft in the per-kind Inbox, as an assistant would."""
    folder = runtime_paths.inbox_folder(kind)
    path = os.path.join(str(folder), f"{name}.txt")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(body)
    with open(path + ".done", "w", encoding="utf-8") as handle:
        handle.write(str(len(body.encode("utf-8"))))
    return path


class RecordingAdapter:
    """Stands in for one ledger adapter and records what it was handed."""

    def __init__(self, kind="content.page"):
        self.kind = kind
        self.requests = []

    def build_payload(self, request):
        self.requests.append(dict(request))
        return {"title": "Invented Page", "source_path": request.get("path")}

    def source_digest(self, payload):
        return models.sha256_dict(payload)

    def verify_targets(self, payload, targets):
        return [{
            "course_id": targets[0]["course_id"],
            "target_key": "target-key",
            "idempotency_key": "idempotency-key",
        }]

    def capture_baseline(self, payload, target):
        return {"existing_page": None}

    def freeze_review(self, payload, target, baseline):
        return {
            "course_name": "Invented Course",
            "page_title": payload["title"],
            "baseline_has_existing_page": False,
            "dependencies": [{"type": "printable", "path": r"C:\private\packet.pdf"}],
        }


class DifferentiatedAdapter(RecordingAdapter):
    kind = "content.quiz"

    def build_payload(self, request):
        self.requests.append(dict(request))
        return request

    def source_digest(self, payload):
        return "source-digest"

    def verify_targets(self, payload, targets):
        return [{"course_id": targets[0]["course_id"], "target_key": "target",
                 "idempotency_key": "idempotency"}]

    def capture_baseline(self, payload, target):
        return {"group_snapshot": {"tiers": [
            {"student_count": 2}, {"student_count": 3}
        ]}}

    def freeze_review(self, payload, target, baseline):
        return {"mode": "differentiated", "variants": [
            {"group_name": row["group_name"], "title": "Same Quiz"}
            for row in payload["variants"]
        ]}


@pytest.fixture
def _adapter(monkeypatch, tmp_path):
    """One recording adapter behind a ledger writing to a temp private root."""
    from api.operation_ledger import paths

    monkeypatch.setattr(paths, "private_root", lambda: tmp_path / "private")
    adapter = RecordingAdapter()
    monkeypatch.setattr(content_push.registry, "get_adapter", lambda _kind: adapter)
    return adapter


# --- delegation ----------------------------------------------------------------

def test_tools_delegate_to_the_shared_use_case(monkeypatch):
    seen = {}

    def preview(course_id, kind, label, **options):
        seen["preview"] = (course_id, kind, label, options)
        return {"ok": True}

    monkeypatch.setattr(content_push, "preview_content_push", preview)
    monkeypatch.setattr(content_push, "apply_content_push",
                        lambda *coordinates: {"ok": True, "coordinates": coordinates})

    result = tools.preview_content_push(
        "course-x", "page", "welcome.txt", published=True, module_name="Unit 3")

    assert seen["preview"][:3] == ("course-x", "page", "welcome.txt")
    assert seen["preview"][3]["published"] is True
    assert seen["preview"][3]["module_name"] == "Unit 3"
    assert result["next"] == tools._NEXT_STEPS["preview_content_push"]

    applied = tools.apply_content_push("op", "batch", "digest")
    assert applied["coordinates"] == ("op", "batch", "digest")
    # A refusal carries no post-call procedure: there is nothing to apply.
    monkeypatch.setattr(content_push, "preview_content_push",
                        lambda *a, **k: {"ok": False, "error": "no"})
    assert "next" not in tools.preview_content_push("course-x", "page", "gone")


def test_differentiated_preview_validates_exact_staged_labels_and_groups(_workspace, monkeypatch):
    _stage("quiz", "one")
    _stage("quiz", "two")
    adapter = DifferentiatedAdapter()
    monkeypatch.setattr(content_push.registry, "get_adapter", lambda _kind: adapter)
    for variants, expected in [
        ([{"label": "one", "group_name": "Blue"}], "at least two"),
        ([{"label": "one", "group_name": "Blue", "extra": 1}, {"label": "two", "group_name": "Gold"}], "exactly"),
        ([{"label": "one", "group_name": "Blue"}, {"label": "ONE.TXT", "group_name": "Gold"}], "unique"),
        ([{"label": "one", "group_name": "Blue"}, {"label": "two", "group_name": " blue "}], "unique"),
        ([{"label": r"C:\secret\one.txt", "group_name": "Blue"}, {"label": "two", "group_name": "Gold"}], "path"),
    ]:
        result = content_push.preview_differentiated_quiz_push("course-x", variants)
        assert result["ok"] is False
        assert expected in result["error"]
    assert adapter.requests == []


def test_differentiated_preview_refuses_baseline_error_before_persisting(_workspace, monkeypatch):
    _stage("quiz", "one")
    _stage("quiz", "two")
    adapter = DifferentiatedAdapter()
    adapter.capture_baseline = lambda payload, target: {"canvas_error": "private transport error"}
    monkeypatch.setattr(content_push.registry, "get_adapter", lambda _kind: adapter)
    result = content_push.preview_differentiated_quiz_push(
        "course-x", [{"label": "one", "group_name": "Blue"},
                      {"label": "two", "group_name": "Gold"}])
    assert result["ok"] is False
    assert result["blocking"] is True
    assert "private transport error" not in json.dumps(result)


# --- what the pair can reach ---------------------------------------------------

def test_preview_freezes_one_staged_draft_for_one_course(_adapter):
    _stage("page", "welcome")

    result = content_push.preview_content_push("course-x", "page", "welcome.txt")

    assert result["ok"] is True
    assert result["kind"] == "page"
    assert result["operation_id"] and result["batch_id"] and result["review_digest"]
    assert result["preview"]["page_title"] == "Invented Page"
    assert _adapter.requests == [{
        "path": os.path.abspath(str(runtime_paths.inbox_folder("page") / "welcome.txt")),
        "published": False,
    }]


def test_preview_never_returns_a_local_path(_adapter):
    """The label goes in, the label comes back; the Inbox path stays home.

    The adapter above returns a printable dependency path that no option on
    this boundary can set, standing in for a future adapter field.
    """
    _stage("page", "welcome")

    result = content_push.preview_content_push("course-x", "page", "welcome")
    serialized = server._compact(result)

    assert result["preview"]["dependencies"] == [{"type": "printable"}]
    assert "packet.pdf" not in serialized
    assert "To Review" not in serialized


def test_a_label_resolves_loosely(_adapter):
    _stage("page", "Welcome Letter")

    assert content_push.preview_content_push(
        "course-x", "page", "welcome letter")["ok"] is True
    assert content_push.preview_content_push(
        "course-x", "page", "Welcome Letter.txt")["ok"] is True


def test_a_label_matching_two_drafts_is_refused_rather_than_guessed(
    _adapter, monkeypatch,
):
    """Two drafts differing only by case cannot coexist on Windows, so this
    stubs the listing rather than the filesystem: the guard has to hold on a
    case-sensitive machine too."""
    monkeypatch.setattr(content_push.deps, "list_inbox_files", lambda _kind: [
        {"label": "Welcome Letter.txt", "path": r"C:\a\Welcome Letter.txt"},
        {"label": "welcome letter.txt", "path": r"C:\a\welcome letter.txt"},
    ])

    result = content_push.preview_content_push("course-x", "page", "welcome letter")

    assert result["ok"] is False
    assert "more than one" in result["error"]
    assert _adapter.requests == []


def test_an_unknown_label_names_what_is_actually_staged(_adapter):
    _stage("page", "welcome")

    result = content_push.preview_content_push("course-x", "page", "farewell")

    assert result["ok"] is False
    assert "welcome.txt" in result["error"]
    assert _adapter.requests == []


def test_an_unstaged_kind_points_back_at_staging(_adapter):
    result = content_push.preview_content_push("course-x", "quiz", "unit-3")

    assert result["ok"] is False
    assert "get_authoring_contract" in result["error"]


def test_a_draft_the_marker_does_not_cover_is_not_reachable(_adapter):
    """Half-synced drafts are invisible to the push, as they are to the push tab."""
    _stage("page", "welcome", body="original body")
    path = str(runtime_paths.inbox_folder("page") / "welcome.txt")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("a much longer body than the marker records")

    result = content_push.preview_content_push("course-x", "page", "welcome")

    assert result["ok"] is False
    assert "no page draft is staged" in result["error"]


def test_only_a_current_course_can_be_pushed_to(_adapter):
    _stage("page", "welcome")

    result = content_push.preview_content_push("course-y", "page", "welcome")

    assert result == {"ok": False, "error": "course is not in Current courses"}
    assert _adapter.requests == []


def test_an_unknown_kind_is_refused_before_anything_is_read(_adapter):
    result = content_push.preview_content_push("course-x", "module", "welcome")

    assert result["ok"] is False
    assert "unknown kind 'module'" in result["error"]
    assert _adapter.requests == []


# --- delivery options ----------------------------------------------------------

def test_an_option_a_kind_cannot_carry_is_refused_not_dropped(_adapter):
    _stage("page", "welcome")

    result = content_push.preview_content_push(
        "course-x", "page", "welcome", due_at="2026-09-11T23:59:00Z")

    assert result["ok"] is False
    assert "a page push does not take due_at" in result["error"]
    assert "module_name" in result["error"]
    assert _adapter.requests == []


def test_assignment_options_reach_the_adapter_verbatim(_adapter):
    _stage("assignment", "essay-1")

    content_push.preview_content_push(
        "course-x", "assignment", "essay-1", published=True,
        module_name="Unit 3", assignment_group_name="Essays",
        due_at="2026-09-11T23:59:00Z", post_to_sis=True)

    assert _adapter.requests == [{
        "path": os.path.abspath(
            str(runtime_paths.inbox_folder("assignment") / "essay-1.txt")),
        "published": True,
        "post_to_sis": True,
        "module_name": "Unit 3",
        "assignment_group_name": "Essays",
        "due_at": "2026-09-11T23:59:00Z",
    }]


def test_a_quiz_carries_its_options_as_push_settings(_adapter):
    _stage("quiz", "unit-3-check")

    content_push.preview_content_push(
        "course-x", "quiz", "unit-3-check", published=True,
        due_at="2026-09-11T23:59:00Z")

    request = _adapter.requests[0]
    assert request["mode"] == "whole"
    assert request["settings"] == {
        "published": True, "due_at": "2026-09-11T23:59:00Z",
    }


# --- apply ---------------------------------------------------------------------

def test_apply_lands_only_a_content_operation(monkeypatch):
    monkeypatch.setattr(content_push.operations, "get_operation", lambda _id: {
        "operation_id": "op-1", "kind": "gradebook.sis_bridge",
    })

    result = content_push.apply_content_push("op-1", "batch-1", "digest-1")

    assert result == {"ok": False, "error": "content push operation was not found"}


def test_apply_requires_all_three_coordinates():
    assert content_push.apply_content_push("op-1", "", "digest")["ok"] is False
    assert content_push.apply_content_push("", "batch", "digest")["ok"] is False
    assert content_push.apply_content_push("op-1", "batch", "")["ok"] is False


def test_apply_reports_what_landed_without_ledger_internals(monkeypatch):
    monkeypatch.setattr(content_push.operations, "get_operation", lambda _id: {
        "operation_id": "op-1", "kind": "content.page",
    })
    monkeypatch.setattr(content_push.executor, "apply_operation", lambda *_a: {
        "ok": True,
        "operation_id": "op-1",
        "status": "applied",
        "target_results": [{
            "target_key": "target-key",
            "state": "applied",
            "returned_object_id": "9001",
            "returned_object_url": "https://canvas.invalid/courses/1/pages/welcome",
            "error_code": None,
            "private_diagnostic": "KeyError",
            "steps": [{"step_key": "create_page", "state": "applied"}],
        }],
    })

    result = content_push.apply_content_push("op-1", "batch-1", "digest-1")

    assert result == {
        "ok": True,
        "kind": "page",
        "operation_id": "op-1",
        "status": "applied",
        "targets": [{
            "state": "applied",
            "url": "https://canvas.invalid/courses/1/pages/welcome",
        }],
    }
    assert "KeyError" not in server._compact(result)


def test_assignment_tier_result_projection_is_family_safe_and_actionable():
    operation = {
        "kind": "content.assignment",
        "normalized_payload": {
            "tiers": [
                {"label": "Support", "tag": "Red", "title": "Practice - Red"},
                {"label": "Core", "tag": "Blue", "title": "Practice - Blue"},
            ],
        },
        "targets": [{"steps": []}],
    }
    result = {
        "ok": True,
        "operation_id": "op-1",
        "status": "applied",
        "target_results": [{
            "state": "applied",
            "steps": [
                {"step_key": "create_tier_assignment:0", "state": "applied",
                 "returned_object_id": "101", "returned_object_url": "https://canvas.invalid/a/101"},
                {"step_key": "create_tier_assignment:1", "state": "applied",
                 "returned_object_id": "102", "returned_object_url": "https://canvas.invalid/a/102"},
            ],
        }],
    }

    projected = content_push._result_projection(operation, result)
    target = projected["targets"][0]
    assert target["created"] == [
        {"tier": "Support", "public_tag": "Red",
         "assignment_id": "101", "title": "Practice - Red",
         "url": "https://canvas.invalid/a/101"},
        {"tier": "Core", "public_tag": "Blue",
         "assignment_id": "102", "title": "Practice - Blue",
         "url": "https://canvas.invalid/a/102"},
    ]
    assert target["family_link"] == {"state": "needs_repair"}
    assert target["module"] == {"module_id": None, "module_name": None}

    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    assert not keys(target) & {"student_ids", "member_ids"}


def test_apply_surfaces_the_unfinished_step_when_a_push_needs_attention(monkeypatch):
    monkeypatch.setattr(content_push.operations, "get_operation", lambda _id: {
        "operation_id": "op-1", "kind": "content.assignment",
    })
    monkeypatch.setattr(content_push.executor, "apply_operation", lambda *_a: {
        "ok": False,
        "operation_id": "op-1",
        "status": "partial",
        "target_results": [{
            "target_key": "target-key",
            "state": "sent_unknown",
            "error_code": "adapter_exception_after_send",
            "steps": [
                {"step_key": "create_assignment", "state": "applied"},
                {"step_key": "attach_module", "state": "sent_unknown",
                 "error_code": "timeout"},
            ],
        }],
    })

    result = content_push.apply_content_push("op-1", "batch-1", "digest-1")

    assert result["ok"] is False
    assert result["kind"] == "assignment"
    assert result["status"] == "partial"
    assert result["targets"] == [{
        "state": "sent_unknown",
        "error_code": "adapter_exception_after_send",
        "unfinished_steps": [{
            "step": "attach_module", "state": "sent_unknown",
            "error_code": "timeout",
        }],
    }]


def test_apply_surfaces_failed_quiz_item_metadata(monkeypatch):
    monkeypatch.setattr(content_push.operations, "get_operation", lambda _id: {
        "operation_id": "op-1", "kind": "content.quiz",
    })
    monkeypatch.setattr(content_push.executor, "apply_operation", lambda *_a: {
        "ok": False,
        "operation_id": "op-1",
        "status": "failed",
        "target_results": [{
            "target_key": "target-key",
            "state": "failed",
            "error_code": "item_rejected",
            "failed_items": [{
                "id": "q09_weak_argument",
                "source_type": "MC",
                "item_index": 9,
                "field": "choices",
                "canvas_status": 422,
                "reason": "Canvas rejected the choices field for this quiz item.",
            }],
        }],
    })

    result = content_push.apply_content_push("op-1", "batch-1", "digest-1")

    assert result["targets"][0]["failed_items"][0]["id"] == "q09_weak_argument"
    assert result["targets"][0]["failed_items"][0]["item_index"] == 9


def test_a_drift_refusal_from_the_executor_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(content_push.operations, "get_operation", lambda _id: {
        "operation_id": "op-1", "kind": "content.page",
    })

    def refuse(*_args):
        raise ValueError("review batch or digest does not match stored review")

    monkeypatch.setattr(content_push.executor, "apply_operation", refuse)

    result = content_push.apply_content_push("op-1", "batch-1", "digest-1")

    assert result["ok"] is False
    assert "does not match stored review" in result["error"]


# --- against the real adapters -------------------------------------------------

@pytest.fixture
def _ledger(monkeypatch, tmp_path):
    """A real ledger over a temp private root, with Canvas reads stubbed empty."""
    from api.operation_ledger import paths
    from api.platform_services import canvas_client

    monkeypatch.setattr(paths, "private_root", lambda: tmp_path / "private")
    monkeypatch.setattr(canvas_client, "canvas_get", lambda *_a, **_k: ([], None))
    return tmp_path


def test_a_real_pageforge_draft_freezes_through_the_real_adapter(_ledger):
    _stage("page", "welcome", body=(
        '<PAGEFORGE_JSON>{"version":"1.0-json","type":"PAGE",'
        '"title":"Welcome to Unit 3","body":"<p>Read this first.</p>"}'
        '</PAGEFORGE_JSON>'
    ))

    result = content_push.preview_content_push(
        "course-x", "page", "welcome", published=True, module_name="Unit 3")

    assert result["ok"] is True
    assert result["preview"] == {
        "course_name": "Invented Course",
        "page_title": "Welcome to Unit 3",
        "published": True,
        "module_name": "Unit 3",
        "baseline_has_existing_page": False,
        "baseline_page_url": None,
    }


def test_mcp_differentiated_quiz_requires_dated_module_family(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        content_push, "preview_differentiated_quiz_push",
        lambda course_id, variants, **options: (
            seen.update({"course_id": course_id, "variants": variants, "options": options})
            or {"ok": True, "preview": {"bridge": {"title": "Shared Quiz"}}}
        ),
    )
    result = tools.preview_differentiated_quiz_push(
        "course-x",
        [{"label": "variant-a", "group_name": "Blue"},
         {"label": "variant-b", "group_name": "Gold"}],
        due_at="2026-09-14T15:00:00-05:00",
        module_name="Week 1",
    )
    assert result["ok"] is True
    assert seen["options"]["due_at"] == "2026-09-14T15:00:00-05:00"
    assert seen["options"]["module_name"] == "Week 1"


def test_a_real_assignmentforge_draft_carries_its_schedule(_ledger):
    _stage("assignment", "essay-1", body=(
        '<ASSIGNMENTFORGE_JSON>{"version":"1.0-json","type":"ASSIGNMENT",'
        '"title":"Essay 1","description":"<p>Write the thing.</p>","points":100}'
        '</ASSIGNMENTFORGE_JSON>'
    ))

    result = content_push.preview_content_push(
        "course-x", "assignment", "essay-1",
        due_at="2026-09-11T23:59:00Z", post_to_sis=True)

    assert result["ok"] is True
    assert result["preview"]["assignment_name"] == "Essay 1"
    assert result["preview"]["points"] == 100
    assert result["preview"]["due_at"] == "2026-09-11T23:59:00Z"
    assert result["preview"]["post_to_sis"] is True
    assert result["preview"]["published"] is False


def test_a_real_tiered_assignmentforge_preview_allows_teacher_owned_dates(
    _ledger, monkeypatch,
):
    _stage("assignment", "tiered-essay", body=(
        '<ASSIGNMENTFORGE_JSON>{"version":"1.0-json","type":"ASSIGNMENT",'
        '"title":"Tiered Essay","description":"<p>Write the thing.</p>",'
        '"points":100,"tiers":[{"label":"Support"},{"label":"Core"}]}'
        '</ASSIGNMENTFORGE_JSON>'
    ))
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Red", "Core": "Blue",
    })
    monkeypatch.setattr(config, "get_canvas_base", lambda: "https://canvas.invalid")
    monkeypatch.setattr(canvas_client, "canvas_get", lambda path, **_kwargs: (
        ({"id": "501"}, None) if path.endswith("/modules/501") else (None, None)
    ))
    monkeypatch.setattr(canvas_client, "canvas_get_all", lambda *_args, **_kwargs: ([], None))

    result = content_push.preview_content_push(
        "course-x", "assignment", "tiered-essay", module_id="501",
    )

    assert result["ok"] is True
    assert result["preview"]["tiered"] is True
    assert result["preview"]["due_at"] is None
    assert result["preview"]["bridge"]["due_at"] is None


def test_a_malformed_draft_is_refused_with_the_validator_problem(_ledger):
    _stage("page", "broken", body="no envelope here")

    result = content_push.preview_content_push("course-x", "page", "broken")

    assert result["ok"] is False
    assert "PAGEFORGE_JSON" in result["error"]


def test_the_whole_chain_answers_one_text_block_over_the_protocol(_ledger):
    _stage("page", "welcome", body=(
        '<PAGEFORGE_JSON>{"version":"1.0-json","type":"PAGE",'
        '"title":"Welcome","body":"<p>Hi.</p>"}</PAGEFORGE_JSON>'
    ))

    content = asyncio.run(server.mcp.call_tool("preview_content_push", {
        "course_id": "course-x", "kind": "page", "label": "welcome",
    }))

    assert len(content) == 1
    assert content[0].type == "text"
    payload = json.loads(content[0].text)
    assert payload["ok"] is True
    assert payload["next"] == tools._NEXT_STEPS["preview_content_push"]
    assert payload["preview"]["page_title"] == "Welcome"


def test_the_frozen_operation_is_visible_to_the_teacher_and_applies_once(_ledger):
    """The chat push is not a side channel: it lands in the same ledger the
    push tab's Operations list reads, and the same digest gate guards apply."""
    from api.operation_ledger import operations

    _stage("page", "welcome", body=(
        '<PAGEFORGE_JSON>{"version":"1.0-json","type":"PAGE",'
        '"title":"Welcome","body":"<p>Hi.</p>"}</PAGEFORGE_JSON>'
    ))
    preview = content_push.preview_content_push("course-x", "page", "welcome")

    listed = operations.list_operations_pii_minimized()
    assert [op["operation_id"] for op in listed] == [preview["operation_id"]]
    assert listed[0]["kind"] == "content.page"
    assert listed[0]["status"] == "reviewed"

    wrong_digest = content_push.apply_content_push(
        preview["operation_id"], preview["batch_id"], "not-the-digest")
    assert wrong_digest["ok"] is False
    assert "does not match" in wrong_digest["error"]


# --- preview_assignment_update / apply_assignment_update: id-addressed, no draft ---

_LIVE_ASSIGNMENT_FOR_UPDATE = {
    "id": 24680,
    "name": "Found Poetry",
    "html_url": "https://c/course-x/assignments/24680",
    "updated_at": "2026-09-01T12:00:00Z",
    "published": False,
    "due_at": None,
    "unlock_at": None,
    "lock_at": None,
}


@pytest.fixture
def _assignment_update_ledger(monkeypatch, tmp_path):
    """A real ledger and the real AssignmentUpdateAdapter over a fake Canvas
    transport that knows about exactly one existing assignment."""
    from api.operation_ledger import paths

    monkeypatch.setattr(paths, "private_root", lambda: tmp_path / "private")
    monkeypatch.setattr(
        canvas_client, "canvas_get",
        lambda path, params=None, timeout=20: (
            (dict(_LIVE_ASSIGNMENT_FOR_UPDATE), None)
            if "assignments/24680" in path else (None, "HTTP 404: Not Found")
        ),
    )
    sent = []

    def fake_send(method, path, payload, timeout=30):
        sent.append((method, path, payload))
        return {"id": 24680, "html_url": _LIVE_ASSIGNMENT_FOR_UPDATE["html_url"]}, None

    monkeypatch.setattr(canvas_client, "_canvas_send", fake_send)
    return sent


def test_preview_then_apply_a_date_change_sends_exactly_one_put(_assignment_update_ledger):
    """Example: the one happy path -- preview a date change against an
    existing assignment, apply it, and land exactly one Canvas PUT carrying
    only the field that changed."""
    sent = _assignment_update_ledger

    preview = content_push.preview_assignment_update(
        "course-x", "24680", due_at="2026-09-11T23:59:00Z")

    assert preview["ok"] is True
    assert preview["preview"]["assignment_name"] == "Found Poetry"
    assert preview["preview"]["changes"] == [
        {"field": "due_at", "from": None, "to": "2026-09-11T23:59:00Z"},
    ]
    assert sent == []  # no Canvas write yet

    applied = content_push.apply_assignment_update(
        preview["operation_id"], preview["batch_id"], preview["review_digest"])

    assert applied["ok"] is True
    assert applied["kind"] == "assignment_update"
    assert len(sent) == 1
    method, path, request = sent[0]
    assert method == "PUT"
    assert "assignments/24680" in path
    assert request == {"assignment": {"due_at": "2026-09-11T23:59:00Z"}}


def test_apply_assignment_update_is_refused_by_apply_content_push(_assignment_update_ledger):
    """The two apply kinds are siblings, not aliases: apply_content_push's
    staged-content kind gate must refuse an assignment-update operation."""
    preview = content_push.preview_assignment_update(
        "course-x", "24680", published=True)

    result = content_push.apply_content_push(
        preview["operation_id"], preview["batch_id"], preview["review_digest"])

    assert result == {"ok": False, "error": "content push operation was not found"}


def test_preview_assignment_update_refuses_with_no_field_and_no_canvas_call(
    _assignment_update_ledger,
):
    sent = _assignment_update_ledger

    result = content_push.preview_assignment_update("course-x", "24680")

    assert result["ok"] is False
    assert "at least one of" in result["error"]
    assert sent == []


# --- the staging contract still says where drafts go ---------------------------

def test_the_staging_appendix_offers_the_push_without_replacing_the_push_tab():
    contract = tools.get_authoring_contract("page")["contract"]

    assert "Canvas Expert push tab" in contract
    assert "preview_content_push" in contract
    assert "apply_content_push" in contract

# --- staging from the assistant itself -----------------------------------------

def test_stage_content_writes_a_draft_the_marker_gate_accepts(_workspace):
    """The whole point: what stage_content writes must be immediately listable,
    which means the .done marker has to match the file's real byte size."""
    from api.webui import deps

    result = content_push.stage_content(
        "page", "Photosynthesis intro", "<PAGEFORGE_JSON>{}</PAGEFORGE_JSON>")

    assert result["ok"] is True
    assert result["label"] == "Photosynthesis intro.txt"
    listed = deps.list_inbox_files("page")
    assert [entry["label"] for entry in listed] == ["Photosynthesis intro.txt"]


def test_stage_content_marker_matches_bytes_not_character_count(_workspace):
    """Two ways this goes wrong, and the body catches both: a text-mode write
    turns each newline into two bytes on Windows, and a marker taken from the
    generated string's length counts characters rather than bytes. The accented
    characters make those two counts differ, so this can tell them apart
    instead of passing by coincidence on plain ASCII."""
    body = "première ligne\ndeuxième ligne\ntroisième ligne\n"
    assert len(body) != len(body.encode("utf-8"))

    content_push.stage_content("page", "multiline", body)

    folder = runtime_paths.inbox_folder("page")
    draft = os.path.join(str(folder), "multiline.txt")
    marker = int(open(draft + ".done", encoding="utf-8").read().strip())
    assert marker == os.path.getsize(draft) == len(body.encode("utf-8"))


def test_stage_content_refuses_to_overwrite_a_staged_label(_workspace):
    content_push.stage_content("page", "same-name", "first")

    again = content_push.stage_content("page", "same-name", "second")

    assert again["ok"] is False
    assert "already staged" in again["error"]
    folder = runtime_paths.inbox_folder("page")
    assert open(os.path.join(str(folder), "same-name.txt"), encoding="utf-8").read() == "first"


@pytest.mark.parametrize("label", [
    "../escape", "sub/dir", "sub\\dir", "C:/tmp/x", "", "   ",
    ".hidden", "trailing.", "CON", "nul", 'quote"mark', "star*",
])
def test_stage_content_refuses_a_label_that_is_not_a_plain_file_name(_workspace, label):
    """A label from an assistant becomes a filename in the teacher's synced
    workspace, so anything that could land outside this Inbox is refused
    rather than rewritten into a different file than the one it named."""
    result = content_push.stage_content("page", label, "body")

    assert result["ok"] is False
    folder = runtime_paths.inbox_folder("page")
    assert os.listdir(str(folder)) == []


def test_stage_content_refuses_an_unknown_kind_and_empty_content(_workspace):
    assert content_push.stage_content("discussion", "x", "body")["ok"] is False
    assert content_push.stage_content("page", "x", "   ")["ok"] is False
    assert os.listdir(str(runtime_paths.inbox_folder("page"))) == []


# --- push_content_live: stage, freeze, apply, in one call ----------------------

def test_push_content_live_stages_then_lands_in_one_call(_adapter, monkeypatch):
    landed = {}

    def _fake_apply(op, batch, digest):
        landed["coords"] = (op, batch, digest)
        return {"ok": True, "status": "applied", "operation_id": op,
                "target_results": [{"state": "applied",
                                    "returned_object_url": "about:blank"}]}

    monkeypatch.setattr(content_push.executor, "apply_operation", _fake_apply)

    result = content_push.push_content_live(
        "course-x", "page", "Cell cycle", "<PAGEFORGE_JSON>{}</PAGEFORGE_JSON>",
        published=True)

    assert result["ok"] is True
    assert result["staged_label"] == "Cell cycle.txt"
    # The draft is still on disk afterwards: staging is the artifact of record,
    # not a step the live push skips.
    from api.webui import deps
    assert [e["label"] for e in deps.list_inbox_files("page")] == ["Cell cycle.txt"]
    # And it went through a real freeze, not straight to the adapter.
    assert landed["coords"][0] and landed["coords"][2]


def test_push_content_live_still_freezes_a_review_before_applying(_adapter, monkeypatch):
    """The freeze is internal here, not skipped. Apply must receive the exact
    coordinates of a persisted review, which is what the drift check hangs on."""
    from api.operation_ledger import operations

    monkeypatch.setattr(
        content_push.executor, "apply_operation",
        lambda op, batch, digest: {"ok": True, "status": "applied",
                                   "operation_id": op, "target_results": []})

    result = content_push.push_content_live(
        "course-x", "page", "frozen", "<PAGEFORGE_JSON>{}</PAGEFORGE_JSON>")

    assert result["ok"] is True
    listed = operations.list_operations_pii_minimized()
    assert len(listed) == 1 and listed[0]["status"] in ("reviewed", "applied")


def test_push_content_live_leaves_the_draft_staged_when_the_push_fails(_adapter):
    """A push that cannot complete must not also swallow the evidence: the
    teacher should be able to read what the assistant actually authored."""
    result = content_push.push_content_live(
        "not-a-current-course", "page", "orphan", "<PAGEFORGE_JSON>{}</PAGEFORGE_JSON>")

    assert result["ok"] is False
    assert result["staged_label"] == "orphan.txt"
    assert "staged" in result["note"]
    from api.webui import deps
    assert [e["label"] for e in deps.list_inbox_files("page")] == ["orphan.txt"]


def test_push_content_live_refuses_a_bad_label_before_touching_canvas(_adapter):
    result = content_push.push_content_live(
        "course-x", "page", "../escape", "<PAGEFORGE_JSON>{}</PAGEFORGE_JSON>")

    assert result["ok"] is False
    assert _adapter.requests == []


def test_push_content_live_refuses_an_option_the_kind_cannot_carry(_adapter):
    """Same per-kind option discipline as the preview path: a page has no
    assignment group, and saying so beats silently dropping it."""
    result = content_push.push_content_live(
        "course-x", "page", "grouped", "<PAGEFORGE_JSON>{}</PAGEFORGE_JSON>",
        assignment_group_name="Essays")

    assert result["ok"] is False
    assert "assignment_group_name" in result["error"]


def test_push_content_live_carries_no_scheduling_options():
    """Dates are deliberately preview-only: the live push is for content a
    teacher wants landed now, and dated work is exactly the case that wants a
    look first. Pinned so a later convenience parameter is a decision rather
    than a drift."""
    import inspect

    live = set(inspect.signature(content_push.push_content_live).parameters)
    preview = set(inspect.signature(content_push.preview_content_push).parameters)

    assert not live & {"due_at", "unlock_at", "lock_at"}
    assert {"due_at", "unlock_at", "lock_at"} <= preview
    # Everything else the live push takes still matches the preview path, so
    # the two do not quietly drift apart on the options they share.
    assert (live - {"content"}) <= preview


def test_live_push_tools_delegate_to_the_shared_use_case(monkeypatch):
    seen = {}
    monkeypatch.setattr(content_push, "stage_content",
                        lambda *a, **k: seen.setdefault("stage", (a, k)) or {"ok": True})
    monkeypatch.setattr(content_push, "push_content_live",
                        lambda *a, **k: seen.setdefault("live", (a, k)) or {"ok": True})

    tools.stage_content("page", "l", "body")
    tools.push_content_live("course-x", "quiz", "l", "body", published=True)

    assert seen["stage"][0] == ("page", "l", "body")
    assert seen["live"][0] == ("course-x", "quiz", "l", "body")
    assert seen["live"][1]["published"] is True
