"""Feedback-contract file, packet, and preparation boundary laws."""
from __future__ import annotations

import json

import pytest

from api import feedback_contract
from api.mcp_server import tools
from api.platform_services import config, workspace
from api.powergrader import scoring_packet, scoring_preparation


def _assignment(*, description="Write.", rubric=None):
    return {
        "id": "a1", "name": "Synthetic Essay", "description": description,
        "points_possible": 10, "is_quiz_lti_assignment": False,
        "quiz_kind": "", "rubric": rubric or [],
    }


def _submission():
    return {
        "user_id": "synthetic-user-1", "id": "submission-1", "attempt": 1,
        "workflow_state": "submitted", "submission_type": "online_text_entry",
        "body": "A synthetic submitted response.",
        "submitted_at": "2026-09-18T10:00:00Z",
        "user": {"name": "Synthetic Learner"},
    }


def _wire_prepare(monkeypatch, tmp_path, *, assignment=None, freshness=None,
                  contracts=None):
    assignment = assignment or _assignment()
    freshness = freshness or {
        "state": "current", "last_success_at": "2026-09-21T12:00:00Z",
        "age_minutes": 0, "requires_teacher_confirmation": False,
    }
    monkeypatch.setattr(scoring_preparation.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(scoring_preparation.config, "course_display_name", lambda _id: "Synthetic Course")
    monkeypatch.setattr(scoring_preparation.config, "get_roster_student_settings", lambda _id: {})
    monkeypatch.setattr(scoring_preparation.config, "roster_tier_by_id", lambda _id: {})
    monkeypatch.setattr(scoring_preparation.config, "get_monitored_students", lambda: {})
    monkeypatch.setattr(scoring_preparation.config, "get_extra_time", lambda _id: [])
    monkeypatch.setattr(
        scoring_preparation.assignment_refresh,
        "prepare_assignment_from_mirror",
        lambda _course, _assignment: (
            [_submission()], assignment,
            {"status": "mirror", "manifest_path": None, "mirror_revision": 1,
             "snapshot_id": "c1:1", "freshness": freshness},
        ),
    )
    bundle_path = tmp_path / "safe-bundle.json"
    bundle_path.write_text(json.dumps({
        "contract_version": "1.0",
        "students": [{"pseudonym": "Synthetic Learner", "responses": [{
            "item_id": "item-1", "prompt": "Explain.",
            "response": "A synthetic response.", "possible": 10,
        }]}],
    }), encoding="utf-8")
    monkeypatch.setattr(
        scoring_preparation.scoring_artifacts,
        "build_scoring_artifacts",
        lambda **_kwargs: {
            "ok": True, "privacy_steps": [],
            "privacy_artifacts": {"safe_bundle": str(bundle_path)},
            "ai_by_uid": {}, "ai_item_by_uid": {}, "copilot_packet": None,
        },
    )
    monkeypatch.setattr(
        scoring_preparation.session_builder,
        "build_students",
        lambda **_kwargs: [{"user_id": "synthetic-user-1", "status": "pending"}],
    )
    monkeypatch.setattr(
        scoring_preparation.assignmentforge,
        "for_assignment",
        lambda *_args: {},
    )
    contract_rows = contracts or []
    monkeypatch.setattr(scoring_preparation.config, "list_feedback_contracts", lambda: contract_rows)
    monkeypatch.setattr(
        scoring_preparation.config,
        "get_feedback_contract",
        lambda wanted: next((row for row in contract_rows if row.get("id") == wanted), None),
    )
    saved = {}

    def activate(session):
        saved[session["session_id"]] = session
        return []

    return saved, activate


def test_contract_seed_is_marker_gated_after_teacher_deletion(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))

    first = config.list_feedback_contracts()
    assert [item["id"] for item in first] == ["basic"]
    seeded_path = tmp_path / "Library" / "Feedback Contracts" / "Glows & Grows (Basic).md"
    assert seeded_path.is_file()
    seeded_path.unlink()

    assert config.list_feedback_contracts() == []


def test_list_feedback_contracts_is_teacher_only_and_omits_file_body(monkeypatch):
    monkeypatch.setattr(tools.config, "active_courses", lambda: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(tools.config, "list_feedback_contracts", lambda: [{
        "id": "ecr", "name": "ECR", "applies_to": "Evidence", "summary": "Use evidence.",
        "body": "Synthetic teacher text with canvas_id=not-a-student-id.",
    }])

    result = tools.list_feedback_contracts()

    assert result == {"ok": True, "contracts": [{
        "id": "ecr", "name": "ECR", "applies_to": "Evidence",
        "summary": "Use evidence.", "projected_tokens": 13,
    }]}
    assert "canvas_id" not in json.dumps(result)


def test_selected_contract_body_is_verbatim_in_page_zero(monkeypatch, tmp_path):
    body = "# Synthetic ECR\n\nUse evidence before explanation.\n"
    contract = {"id": "ecr", "name": "Synthetic ECR", "path": str(tmp_path / "ecr.md"),
                "body": body}
    saved, activate = _wire_prepare(monkeypatch, tmp_path, contracts=[contract])

    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", feedback_contract_id="ecr",
        activate_session=activate,
    )

    session = saved[result["scoring_session_id"]]
    page = scoring_packet.build_packet(
        session, json.loads((tmp_path / "safe-bundle.json").read_text(encoding="utf-8")),
        include_context=True,
    )
    assert session["feedback_contract_text"] == body
    assert body in page["contract"]


def test_conversational_guidance_becomes_contract_and_keeps_provenance(monkeypatch, tmp_path):
    guidance = "Use a concise claim-evidence-reasoning response."
    saved, activate = _wire_prepare(monkeypatch, tmp_path)

    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", scoring_guidance=guidance,
        activate_session=activate,
    )

    session = saved[result["scoring_session_id"]]
    assert session["feedback_contract_source"] == "conversation"
    assert session["feedback_contract_text"] == guidance
    assert session["scoring_guidance_provenance"] == "teacher_authored"


def test_teacher_body_cannot_remove_product_transport_rules():
    body = "Only write two sentences and quote the pseudonym."
    rendered = feedback_contract.build_contract_text(teacher_contract=body)

    assert body in rendered
    assert "Copy pseudonym and item_id exactly" in rendered
    assert "Never quote a pseudonym back" in rendered
    assert "Do not identify students" in rendered
    assert "Return only valid JSON" in rendered
    assert "feedback` must be non-empty" in rendered


def test_oversized_contract_is_a_typed_non_truncating_refusal():
    with pytest.raises(scoring_packet.ContractTooLarge) as raised:
        scoring_packet.build_packet(
            {"session_id": "synthetic", "feedback_contract_text": "x" * 100001,
             "feedback_contract_filename": "oversized.md"},
            {"students": []}, include_context=True,
        )

    error = raised.value
    assert error.code == "scoring_contract_too_large"
    assert error.contract_file == "oversized.md"
    assert error.projected_tokens > scoring_packet._TOKEN_BUDGET


def test_inherited_guidance_stays_non_authoritative_across_freshness_retry(monkeypatch, tmp_path):
    old = {"state": "current", "last_success_at": "2026-09-20T12:00:00Z",
           "age_minutes": 31, "requires_teacher_confirmation": True}
    fresh = {"state": "current", "last_success_at": "2026-09-21T12:00:00Z",
             "age_minutes": 0, "requires_teacher_confirmation": False}
    calls = iter([old, fresh])
    assignment = _assignment(description="", rubric=[])
    saved, activate = _wire_prepare(monkeypatch, tmp_path, assignment=assignment,
                                    freshness=old)
    monkeypatch.setattr(
        scoring_preparation.assignment_refresh,
        "prepare_assignment_from_mirror",
        lambda _course, _assignment: (
            [_submission()], assignment,
            {"status": "mirror", "manifest_path": None, "mirror_revision": 1,
             "snapshot_id": "c1:1", "freshness": next(calls)},
        ),
    )

    first = scoring_preparation.prepare_scoring_session(
        "c1", "a1", "Inherited criteria", scoring_guidance_provenance="inherited",
        activate_session=activate,
    )
    second = scoring_preparation.prepare_scoring_session(
        "c1", "a1", use_existing_mirror=True, activate_session=activate,
    )

    assert first["code"] == "mirror_freshness_confirmation_required"
    assert second["code"] == "needs_scoring_norms"
    assert not saved


def test_layered_teacher_guidance_is_marked_on_assignment_basis(monkeypatch, tmp_path):
    saved, activate = _wire_prepare(monkeypatch, tmp_path)

    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", "Use concrete evidence in every comment.",
        activate_session=activate,
    )

    assert saved[result["scoring_session_id"]]["scoring_basis"] == {
        "source": "assignment_content", "label": "Assignment content", "layered": True,
    }


def test_removed_feedback_pattern_api_is_not_imported():
    assert not hasattr(config, "FEEDBACK_PATTERNS_DEFAULT")
    assert not hasattr(config, "list_feedback_patterns")
    assert not hasattr(config, "get_feedback_pattern")
    assert not hasattr(config, "set_feedback_patterns")
