"""Tests for the core AssignmentForge operation adapter.

Patterns follow test_quick_assignment_operation.py: private-root redirection,
mock Canvas API, and adapter lifecycle verification.
"""
import json
import os

import pytest

from api.operation_ledger import (
    batches, executor, models, operations, paths, registry,
)
from api.operation_ledger.adapters.assignment import (
    AssignmentAdapter,
    _normalize,
)


def _root(tmp_path, monkeypatch):
    root = tmp_path / "local-private"
    monkeypatch.setattr(paths, "private_root", lambda: root)
    return root


def _fake_courses():
    return [
        {"id": 101, "name": "Algebra 1"},
        {"id": 202, "name": "Biology"},
    ]


SAMPLE_AF_JSON = """<ASSIGNMENTFORGE_JSON>
{
  "version": "2.0-json",
  "type": "ASSIGNMENT",
  "title": "Found Poetry",
  "overview": "<p>Create a poem.</p>",
  "directions": [{"html": "<p>Choose your words.</p>", "response": "none"}],
  "points": 100,
  "submission": {
    "types": ["online_text_entry", "online_upload"],
    "allowed_extensions": ["pdf", "docx"]
  }
}
</ASSIGNMENTFORGE_JSON>"""


def _fakecanvas_get(path, params=None, timeout=20):
    if "assignments/24680" in path:
        return {"id": 24680, "name": "Found Poetry",
                "html_url": "https://c/42/assignments/24680"}, None
    if "assignments" in path and "search_term" in (params or {}):
        search = (params or {}).get("search_term", "").lower()
        if "existing" in search:
            return [{"id": 999, "name": "Existing Quiz",
                     "html_url": "https://c/42/assignments/999",
                     "points_possible": 100, "published": False}], None
        return [], None
    if "assignment_groups" in path:
        return [{"id": 55, "name": "Homework"}], None
    return None, None


def _fake_canvas_send(method, path, payload, timeout=30):
    return {
        "id": 24680,
        "name": payload.get("assignment", {}).get("name", "Untitled"),
        "html_url": "https://canvas.invalid/courses/42/assignments/24680",
    }, None


def _fakecanvas_get_all(path, params=None, timeout=30):
    if "assignment_groups" in path:
        return [{"id": 55, "name": "Homework"}], None
    return [], None


# ── Protocol conformance ─────────────────────────────────────────────────

def test_adapter_is_registered():
    adapter = registry.get_adapter("content.assignment")
    assert adapter is not None
    assert adapter.kind == "content.assignment"


def test_payload_build_from_file(tmp_path, monkeypatch):
    af_file = tmp_path / "poetry.assignmentforge.json"
    af_file.write_text(SAMPLE_AF_JSON, encoding="utf-8")
    adapter = AssignmentAdapter()
    payload = adapter.build_payload({"path": str(af_file)})
    assert payload["name"] == "Found Poetry"
    assert payload["points"] == 100.0
    assert payload["submission_types"] == ["online_text_entry", "online_upload"]
    assert payload["allowed_extensions"] == ["pdf", "docx"]
    assert payload["published"] is False
    assert payload["post_to_sis"] is False


def test_hub_payload_renders_one_whole_class_assignment_and_support_pages(monkeypatch):
    from api.operation_ledger.adapters import assignment as assignment_module
    from api.platform_services import config
    authored = {"version": "2.0-json", "type": "ASSIGNMENT", "title": "Hub",
                "points": 10, "submission": {"types": ["online_text_entry"]},
                "overview": "<p>Shared directions.</p>",
                "directions": [{"html": "<p>Write.</p>", "response": "long"}],
                "differentiation": "hub",
                "tiers": [{"label": "Support", "supports": {"word_bank": ["claim"]}}]}
    monkeypatch.setattr(assignment_module.af, "parse_file", lambda _path: (authored, []))
    monkeypatch.setattr(assignment_module, "_generate_printable", lambda *a, **k: {"available": False})
    monkeypatch.setattr(config, "get_tier_tags", lambda: {"Support": "Silver"})
    payload = AssignmentAdapter().build_payload({"path": "synthetic.json"})
    assert payload["hub"] is True
    assert "Shared directions" in payload["description"]
    assert "{{ce-tier-page:0}}" in payload["description"]
    assert payload["tiers"][0]["title"] == "Hub - Silver"
    assert "claim" in payload["tiers"][0]["description"]
    assert "Write." not in payload["tiers"][0]["description"]


def test_assignment_canvas_text_contains_no_em_dashes(tmp_path, monkeypatch):
    af_file = tmp_path / "student-facing.assignmentforge.txt"
    af_file.write_text(
        """<ASSIGNMENTFORGE_JSON>
{"version":"2.0-json","type":"ASSIGNMENT",
 "title":"Argument \u2014 draft","points":10,
 "overview":"<p>Read \u2014 respond.</p>",
 "directions":[{"html":"<p>Use \u2014 evidence.</p>","response":"none"}],
 "differentiation":"bridge","tiers":[{"label":"Support"},
          {"label":"Core","overview":"<p>Explain \u2014 evidence.</p>"}]}
</ASSIGNMENTFORGE_JSON>""",
        encoding="utf-8",
    )
    monkeypatch.setattr("api.platform_services.config.get_tier_tags", lambda: {
        "Support": "Red", "Core": "Blue",
    })
    monkeypatch.setattr("api.platform_services.config.get_canvas_base",
                        lambda: "https://canvas.invalid")

    payload = AssignmentAdapter().build_payload({
        "path": str(af_file),
        "tier_targets": [
            {"tier": "Support", "group_name": "Blue"},
            {"tier": "Core", "group_name": "Red"},
        ],
    })

    assert payload["name"] == "Argument - draft"
    assert "\u2014" not in payload["description"]
    assert all("\u2014" not in tier["description"] for tier in payload["tiers"])

    from api.operation_ledger.adapters.assignment_tiered import _assignment_data
    canvas_body = _assignment_data(
        payload, "Argument \u2014 Support", payload["tiers"][0]["description"],
        "course-1", lambda *_args: None,
    )
    assert "\u2014" not in repr(canvas_body)


def test_payload_build_with_overrides(tmp_path, monkeypatch):
    af_file = tmp_path / "poetry.assignmentforge.json"
    af_file.write_text(SAMPLE_AF_JSON, encoding="utf-8")
    adapter = AssignmentAdapter()
    payload = adapter.build_payload({
        "path": str(af_file),
        "published": True,
        "post_to_sis": True,
        "due_at": "2026-08-01T23:59:00Z",
        "assignment_group_name": "Homework",
    })
    assert payload["published"] is True
    assert payload["post_to_sis"] is True
    assert payload["due_at"] == "2026-08-01T23:59:00Z"
    assert payload["assignment_group_name"] == "Homework"


def test_payload_build_raises_on_missing_path():
    adapter = AssignmentAdapter()
    with pytest.raises(ValueError, match="path is required"):
        adapter.build_payload({})


def test_payload_build_accepts_tiers(tmp_path, monkeypatch):
    af_file = tmp_path / "tiered.assignmentforge.json"
    af_file.write_text(
        """<ASSIGNMENTFORGE_JSON>
{"version":"2.0-json","type":"ASSIGNMENT","title":"Tiered","points":10,"overview":"<p>Hi</p>","directions":[{"html":"<p>Work.</p>","response":"none"}],"differentiation":"bridge","tiers":[{"label":"Support"},{"label":"Core"}]}
</ASSIGNMENTFORGE_JSON>""",
        encoding="utf-8",
    )
    monkeypatch.setattr("api.platform_services.config.get_tier_tags", lambda: {
        "Support": "Red", "Core": "Blue",
    })
    monkeypatch.setattr("api.platform_services.config.get_canvas_base",
                        lambda: "https://canvas.invalid")
    adapter = AssignmentAdapter()
    payload = adapter.build_payload({
        "path": str(af_file),
        "due_at": "2026-09-14T10:00:00-05:00",
        "module_name": "Week 1",
        "tier_targets": [
            {"tier": "Support", "group_name": "Blue"},
            {"tier": "Core", "group_name": "Gold"},
        ],
    })
    assert [(row["label"], row["tag"], row["title"]) for row in payload["tiers"]] == [
        ("Support", "Red", "Tiered - Red"),
        ("Core", "Blue", "Tiered - Blue"),
    ]
    assert all("<p>Hi</p>" in row["description"] for row in payload["tiers"])


def test_tier_colors_are_resolved_during_prepare_and_frozen_in_payload(tmp_path, monkeypatch):
    from api.platform_services import config

    af_file = tmp_path / "colored.assignmentforge.json"
    af_file.write_text(
        """<ASSIGNMENTFORGE_JSON>
{"version":"2.0-json","type":"ASSIGNMENT","title":"Colored","points":10,
 "overview":"<p>Hi</p>","directions":[{"html":"<p>Work.</p>","response":"none"}],
 "supports":{"sentence_frames":["I can ___"]},
 "differentiation":"bridge","tiers":[{"label":"Support"},{"label":"Core"},{"label":"Accelerate"}]}
</ASSIGNMENTFORGE_JSON>""",
        encoding="utf-8",
    )
    colors = {"Support": "purple", "Core": "red", "Accelerate": "blue", "untiered": "teal"}
    monkeypatch.setattr(config, "get_tier_colors", lambda: dict(colors))
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Silver", "Core": "Red", "Accelerate": "Blue",
    })
    request = {"path": str(af_file)}
    payload = AssignmentAdapter().build_payload(request)
    support = payload["tiers"][0]["description"]
    accelerate = payload["tiers"][2]["description"]
    assert "#63428f" in support
    assert "Go further" in accelerate
    digest = AssignmentAdapter().source_digest(payload)

    colors["Support"] = "orange"
    assert "#63428f" in payload["tiers"][0]["description"]
    assert digest == AssignmentAdapter().source_digest(payload)
    updated = AssignmentAdapter().build_payload(request)
    assert "#a44a12" in updated["tiers"][0]["description"]
    assert AssignmentAdapter().source_digest(updated) != digest


def test_payload_keeps_supports_and_corrections_private_to_the_operation(tmp_path, monkeypatch):
    af_file = tmp_path / "tiered.assignmentforge.txt"
    af_file.write_text(
        """<ASSIGNMENTFORGE_JSON>
{"version":"2.0-json","type":"ASSIGNMENT","title":"Tiered","points":10,"overview":"<p>Hi</p>",
 "directions":[{"html":"<p>Work.</p>","response":"none"}],
 "differentiation":"bridge","tiers":[{"label":"Support"},{"label":"Core"},{"label":"Accelerate"}],
 "supports":{"sentence_frames":["The author reveals ___."],"word_bank":["reveals"]},
 "corrections":{"item-1":{"shared":{"answer":"Use walk.","why":"Present tense."},"by_tier":null}}}
</ASSIGNMENTFORGE_JSON>""",
        encoding="utf-8",
    )
    monkeypatch.setattr("api.platform_services.config.get_tier_tags", lambda: {
        "Support": "Silver", "Core": "Red", "Accelerate": "Blue",
    })
    monkeypatch.setattr("api.platform_services.config.get_canvas_base",
                        lambda: "https://canvas.invalid")

    payload = AssignmentAdapter().build_payload({
        "path": str(af_file),
        "tier_targets": [
            {"tier": "Support", "group_name": "Silver"},
            {"tier": "Core", "group_name": "Red"},
            {"tier": "Accelerate", "group_name": "Blue"},
        ],
    })

    assert "supports" not in payload
    assert payload["corrections"]["item-1"]["shared"]["answer"] == "Use walk."
    assert all("The author reveals ___." in row["description"] for row in payload["tiers"])
    assert all("<details" in row["description"] for row in payload["tiers"])

    from api.operation_ledger.adapters.assignment_tiered import _assignment_data
    canvas_body = _assignment_data(
        payload, payload["tiers"][0]["title"], payload["tiers"][0]["description"],
        "course-1", lambda *_args: None,
    )
    assert "supports" not in canvas_body
    assert "corrections" not in canvas_body


def test_payload_build_raises_on_placeholders(tmp_path, monkeypatch):
    af_file = tmp_path / "placeholder.assignmentforge.json"
    af_file.write_text(
        """<ASSIGNMENTFORGE_JSON>
{"version":"2.0-json","type":"ASSIGNMENT","title":"With Placeholder","points":10,"overview":"<p>See {{file:Rubric.pdf}}</p>","directions":[{"html":"<p>Work.</p>","response":"none"}]}
</ASSIGNMENTFORGE_JSON>""",
        encoding="utf-8",
    )
    adapter = AssignmentAdapter()
    with pytest.raises(ValueError, match="canvas_file attachment"):
        adapter.build_payload({"path": str(af_file)})


def test_source_digest_is_deterministic():
    adapter = AssignmentAdapter()
    payload = {"name": "Test", "description": "<p>Body</p>", "points": 100,
               "submission_types": ["online_text_entry"], "published": False,
               "post_to_sis": False}
    d1 = adapter.source_digest(payload)
    d2 = adapter.source_digest(payload)
    assert d1 == d2
    assert d1 != adapter.source_digest({**payload, "description": "<p>Changed</p>"})
    tiered = {**payload, "tiers": [{"description": "<p>Silver body</p>"}]}
    assert adapter.source_digest(tiered) != adapter.source_digest({
        **tiered, "tiers": [{"description": "<p>Revised Silver body</p>"}],
    })


# ── Target verification ─────────────────────────────────────────────────

def test_verify_targets_valid(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.config.active_courses",
        _fake_courses,
    )
    adapter = AssignmentAdapter()
    payload = {"name": "Test", "description": "<p>Body</p>", "points": 100,
               "submission_types": ["online_text_entry"], "published": False,
               "post_to_sis": False}
    targets = adapter.verify_targets(payload, [{"course_id": "101"}, {"course_id": "202"}])
    assert len(targets) == 2
    assert targets[0]["course_id"] == "101"
    assert targets[1]["course_id"] == "202"


def test_verify_targets_rejects_unknown_course(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.config.active_courses",
        _fake_courses,
    )
    adapter = AssignmentAdapter()
    payload = {"name": "Test", "description": "<p>Body</p>", "points": 100,
               "submission_types": ["online_text_entry"], "published": False,
               "post_to_sis": False}
    with pytest.raises(ValueError, match="not in active courses"):
        adapter.verify_targets(payload, [{"course_id": "999"}])


# ── Baseline / drift ────────────────────────────────────────────────────

def test_capture_baseline_no_existing(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.canvas_client.canvas_get",
        _fakecanvas_get,
    )
    adapter = AssignmentAdapter()
    payload = {"name": "New Assignment", "description": "<p>Body</p>", "points": 100,
               "submission_types": ["online_text_entry"], "published": False,
               "post_to_sis": False}
    baseline = adapter.capture_baseline(payload, {"course_id": "42"})
    assert baseline["existing_assignment"] is None


def test_capture_baseline_finds_existing(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.canvas_client.canvas_get",
        _fakecanvas_get,
    )
    adapter = AssignmentAdapter()
    payload = {"name": "Existing Quiz", "description": "<p>Body</p>", "points": 100,
               "submission_types": ["online_text_entry"], "published": False,
               "post_to_sis": False}
    baseline = adapter.capture_baseline(payload, {"course_id": "42"})
    assert baseline["existing_assignment"] is not None
    assert baseline["existing_assignment"]["id"] == "999"


def test_drift_detected_when_existing_found():
    adapter = AssignmentAdapter()
    assert adapter.check_drift(
        {"name": "Existing Quiz"},
        {"course_id": "42"},
        {"existing_assignment": {"id": "999", "name": "Existing Quiz"}},
    ) is True


def test_no_drift_when_no_existing():
    adapter = AssignmentAdapter()
    assert adapter.check_drift(
        {"name": "New Quiz"},
        {"course_id": "42"},
        {"existing_assignment": None},
    ) is False


# ── Freeze review ───────────────────────────────────────────────────────

def test_freeze_review(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.config.active_courses",
        _fake_courses,
    )
    adapter = AssignmentAdapter()
    payload = {"name": "Found Poetry", "description": "<h2>Found Poetry</h2><p>Create a poem.</p>",
               "points": 100, "submission_types": ["online_text_entry"],
               "published": True, "post_to_sis": False}
    review = adapter.freeze_review(payload, {"course_id": "101"}, {"existing_assignment": None})
    assert review["course_name"] == "Algebra 1"
    assert review["assignment_name"] == "Found Poetry"
    assert review["points"] == 100
    assert review["baseline_has_existing"] is False


# ── Execute ──────────────────────────────────────────────────────────────

def test_execute_creates_assignment(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    sent = []

    def capture_send(method, path, payload, timeout=30):
        sent.append(payload)
        return _fake_canvas_send(method, path, payload, timeout)

    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment_whole.canvas_client._canvas_send",
        capture_send,
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.canvas_client.canvas_get",
        _fakecanvas_get,
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.config.active_courses",
        _fake_courses,
    )

    adapter = AssignmentAdapter()
    payload = {"name": "Found \u2014 Poetry", "description": "<h2>Found Poetry</h2><p>Create - a poem.</p>",
               "points": 100, "submission_types": ["online_text_entry"],
               "published": True, "post_to_sis": False}

    op = models.new_operation(
        operation_id="op-af-execute",
        kind="content.assignment",
        source_ref=None,
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload,
        targets=[
            models.new_target(
                target_key=adapter.target_key(payload, "42"),
                idempotency_key=adapter.idempotency_key(payload, "42"),
                course_id="42",
            )
        ],
    )
    operations.create_operation(op)

    target = op["targets"][0]
    baseline = adapter.capture_baseline(payload, target)
    assert baseline["existing_assignment"] is None

    from api.operation_ledger.executor import ExecutionContext
    from api.operation_ledger import claims

    claim = claims.acquire_claim(
        target_key=target["target_key"],
        operation_id=op["operation_id"],
        payload_digest=models.sha256_dict(payload),
    )

    context = ExecutionContext(
        operation_id=op["operation_id"],
        target_key=target["target_key"],
        claim=claim,
    )

    result = adapter.execute(payload, target, baseline, claim, context)
    assert result["state"] == "applied"
    assert result["returned_object_id"] == "24680"
    assert "assignments/24680" in (result.get("returned_object_url") or "")
    assignment_body = sent[0]["assignment"]
    assert assignment_body["name"] == "Found - Poetry"
    assert "\u2014" not in assignment_body["description"]


def test_execute_handles_canvas_error(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)

    def fail_send(method, path, payload, timeout=30):
        return None, "HTTP 400: bad request"

    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.canvas_client._canvas_send",
        fail_send,
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.canvas_client.canvas_get",
        _fakecanvas_get,
    )

    adapter = AssignmentAdapter()
    payload = {"name": "Fail Assignment", "description": "<p>Body</p>", "points": 100,
               "submission_types": ["online_text_entry"], "published": False,
               "post_to_sis": False}

    op = models.new_operation(
        operation_id="op-af-fail",
        kind="content.assignment",
        source_ref=None,
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload,
        targets=[
            models.new_target(
                target_key=adapter.target_key(payload, "42"),
                idempotency_key=adapter.idempotency_key(payload, "42"),
                course_id="42",
            )
        ],
    )
    operations.create_operation(op)

    target = op["targets"][0]
    baseline = adapter.capture_baseline(payload, target)

    from api.operation_ledger.executor import ExecutionContext
    from api.operation_ledger import claims

    claim = claims.acquire_claim(
        target_key=target["target_key"],
        operation_id=op["operation_id"],
        payload_digest=models.sha256_dict(payload),
    )

    context = ExecutionContext(
        operation_id=op["operation_id"],
        target_key=target["target_key"],
        claim=claim,
    )

    result = adapter.execute(payload, target, baseline, claim, context)
    assert result["state"] == "failed"
    assert result["error_code"] == "canvas_rejected"


# ── Reconcile ────────────────────────────────────────────────────────────

def test_reconcile_finds_assignment(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.canvas_client.canvas_get",
        _fakecanvas_get,
    )
    adapter = AssignmentAdapter()
    result = adapter.reconcile(
        {"name": "Found Poetry"},
        {"course_id": "42", "returned_object_id": "24680"},
        {},
    )
    assert result["state"] == "applied"
    assert result["returned_object_id"] == "24680"


# ── Retry / reversal ────────────────────────────────────────────────────

def test_retry_selector_picks_unresolved():
    adapter = AssignmentAdapter()
    operation = {
        "targets": [
            {"target_key": "tk-1", "state": "applied"},
            {"target_key": "tk-2", "state": "failed"},
            {"target_key": "tk-3", "state": "sent_unknown"},
            {"target_key": "tk-4", "state": "skipped"},
        ]
    }
    selected = adapter.retry_selector(operation)
    keys = {t["target_key"] for t in selected}
    assert "tk-1" not in keys
    assert "tk-2" in keys
    assert "tk-3" in keys
    assert "tk-4" not in keys




# ── Full pipeline ────────────────────────────────────────────────────────

def test_pipeline_with_mocks(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.config.active_courses",
        _fake_courses,
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.canvas_client.canvas_get",
        _fakecanvas_get,
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.canvas_client._canvas_send",
        _fake_canvas_send,
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.canvas_client.canvas_get_all",
        _fakecanvas_get_all,
    )

    af_file = tmp_path / "poetry.assignmentforge.json"
    af_file.write_text(SAMPLE_AF_JSON, encoding="utf-8")

    adapter = registry.get_adapter("content.assignment")
    assert adapter is not None

    # 1. Build payload from file
    payload = adapter.build_payload({
        "path": str(af_file),
        "published": True,
    })
    assert payload["name"] == "Found Poetry"
    assert payload["points"] == 100.0

    # 2. Verify targets
    targets = adapter.verify_targets(payload, [
        {"course_id": "101"},
        {"course_id": "202"},
    ])
    assert len(targets) == 2

    # 3. Capture baselines
    bl1 = adapter.capture_baseline(payload, targets[0])
    assert bl1["existing_assignment"] is None

    # 4. Freeze review
    r1 = adapter.freeze_review(payload, targets[0], bl1)
    assert r1["course_name"] == "Algebra 1"
    assert r1["assignment_name"] == "Found Poetry"

    # 5. No drift
    assert adapter.check_drift(payload, targets[0], bl1) is False


# ── Module attachment ────────────────────────────────────────────────────

def test_payload_build_with_module(tmp_path, monkeypatch):
    af_file = tmp_path / "poetry.assignmentforge.json"
    af_file.write_text(SAMPLE_AF_JSON, encoding="utf-8")
    adapter = AssignmentAdapter()
    payload = adapter.build_payload({
        "path": str(af_file),
        "module_name": "Unit 1",
    })
    assert payload["module_name"] == "Unit 1"


def test_freeze_review_shows_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.config.active_courses",
        _fake_courses,
    )
    af_file = tmp_path / "poetry.assignmentforge.json"
    af_file.write_text(SAMPLE_AF_JSON, encoding="utf-8")
    adapter = AssignmentAdapter()
    payload = adapter.build_payload({
        "path": str(af_file),
        "module_name": "Unit 1",
    })
    review = adapter.freeze_review(payload, {"course_id": "101"}, {"existing_assignment": None})
    deps = review.get("dependencies", [])
    assert len(deps) == 1
    assert deps[0]["type"] == "module"
    assert deps[0]["name"] == "Unit 1"


def test_execute_with_module(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.canvas_client._canvas_send",
        _fake_canvas_send,
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.canvas_client.canvas_get",
        _fakecanvas_get,
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.canvas_client.canvas_get_all",
        lambda path, params=None, timeout=30: (
            ([{"id": 77, "name": "Unit 1"}], None)
            if "modules" in path else ([], None)
        ),
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.config.active_courses",
        _fake_courses,
    )

    adapter = AssignmentAdapter()
    payload = {"name": "Found Poetry", "description": "<h2>Found Poetry</h2><p>Create a poem.</p>",
               "points": 100, "submission_types": ["online_text_entry"],
               "published": True, "post_to_sis": False,
               "module_name": "Unit 1"}

    op = models.new_operation(
        operation_id="op-af-module",
        kind="content.assignment",
        source_ref=None,
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload,
        targets=[
            models.new_target(
                target_key=adapter.target_key(payload, "42"),
                idempotency_key=adapter.idempotency_key(payload, "42"),
                course_id="42",
            )
        ],
    )
    operations.create_operation(op)

    target = op["targets"][0]
    baseline = adapter.capture_baseline(payload, target)

    from api.operation_ledger.executor import ExecutionContext
    from api.operation_ledger import claims

    claim = claims.acquire_claim(
        target_key=target["target_key"],
        operation_id=op["operation_id"],
        payload_digest=models.sha256_dict(payload),
    )
    context = ExecutionContext(
        operation_id=op["operation_id"],
        target_key=target["target_key"],
        claim=claim,
    )

    result = adapter.execute(payload, target, baseline, claim, context)
    assert result["state"] == "applied"
    assert result["returned_object_id"] == "24680"
