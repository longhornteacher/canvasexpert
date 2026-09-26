"""MCP contracts for local staging followed by explicit Canvas apply."""
from __future__ import annotations

import json

import pytest

from api.feedback_vault import Vault
from api.mcp_server import tools
from api.powergrader import scoring_packet


REAL_ID = "900123"
REAL_NAME = "Ada Lovelace"
PSEUDONYM = "Pikachu"


def _wire(monkeypatch, tmp_path, _set_active_courses):
    _set_active_courses(["course-1"])
    from api.powergrader import session_store
    bundle = {"contract_version": "1.0", "students": [{"pseudonym": PSEUDONYM,
        "responses": [{"item_id": "item-1", "prompt": "Explain.",
                        "response": "A sufficiently long synthetic answer.", "possible": 10}]}]}
    bundle_path = tmp_path / "safe-bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    vault = Vault(str(tmp_path / "vault.json"))
    vault.get_or_assign(REAL_ID, real_name=REAL_NAME)
    vault.set_pseudonym(REAL_ID, PSEUDONYM)
    vault.save()
    monkeypatch.setattr(tools, "_vault_factory", lambda: vault)
    session = {"session_id": "session-1", "session_kind": "scoring_assignment",
        "course_id": "course-1", "assignment_id": "assignment-1", "assignment_name": "Essay",
        "created": "2026-01-01T08:00:00", "status": "ready",
        "scoring_basis": {"source": "canvas_rubric", "label": "Canvas rubric"},
        "privacy_artifacts": {"safe_bundle": str(bundle_path)},
        "assignment": {"points_possible": 10}, "students": [{"user_id": REAL_ID, "new_quiz_items": []}]}
    sessions = {"session-1": session}
    monkeypatch.setattr(session_store, "load_session", lambda sid: sessions.get(sid))
    monkeypatch.setattr(session_store, "save_session", lambda updated: sessions.__setitem__(updated["session_id"], updated))
    monkeypatch.setattr(session_store, "session_lock", lambda _sid: __import__("contextlib").nullcontext())
    monkeypatch.setattr(session_store, "scope_lock", lambda _course, _assignment: __import__("contextlib").nullcontext())
    monkeypatch.setattr(session_store, "list_session_summaries", lambda: [
        {"session_id": value["session_id"], "session_kind": value.get("session_kind", ""),
         "course_id": value.get("course_id", ""), "assignment_id": value.get("assignment_id", ""),
         "created": value.get("created", ""), "status": value.get("status", ""),
         "assignment_name": value.get("assignment_name", ""), "total": len(value.get("students") or [])}
        for value in sessions.values() if value])
    return session, bundle, sessions


def _result(score=8):
    return [{"pseudonym": PSEUDONYM, "item_id": "item-1", "score": score,
             "explanation": "Clear reasoning throughout the response.",
             "glows": ["Strong topic sentence.", "Concrete supporting detail."],
             "grows": ["Add a closing sentence."],
             "fixes": ["Add one more supporting detail.", "Write a closing sentence."]}]


EXEMPLARS = {"item-1": "A short model answer a student could hand copy."}


def _digest(bundle):
    return scoring_packet.packet_digest("session-1", bundle,
        course_id="course-1", assignment_id="assignment-1")


def _stage_then_apply(session_id, results, packet_digest, *, review_digest="", answers=None,
                      exemplars=EXEMPLARS):
    staged = tools.stage_scoring_results(session_id, results, packet_digest,
                                         review_digest=review_digest, answers=answers,
                                         exemplars=exemplars)
    if staged.get("status") != "staged":
        return staged
    return tools.apply_staged_scoring_results(session_id, staged["stage_digest"])


def _blob(payload):
    return json.dumps(payload, default=str)


def test_stage_is_local_and_apply_is_the_only_canvas_lane(monkeypatch, tmp_path, _set_active_courses):
    session, bundle, sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    from api.powergrader import scoring_apply
    writes = []
    monkeypatch.setattr(scoring_apply, "build_plan", lambda *_a, **_kw: {
        "ok": True, "candidate_ids": [REAL_ID], "questions": [],
        "digest": "frozen-review", "notes": []})
    monkeypatch.setattr(scoring_apply, "apply_plan", lambda **_kw: (
        writes.append(True) or ({"ok": True, "pushed": [REAL_ID],
        "results": [{"user_id": REAL_ID, "status": "pushed"}]}, 200)))
    staged = tools.stage_scoring_results("session-1", _result(), _digest(bundle),
                                         exemplars=EXEMPLARS)
    assert staged["status"] == "staged"
    assert writes == []
    assert sessions["session-1"]["status"] == "staged"
    result = tools.apply_staged_scoring_results("session-1", staged["stage_digest"])
    assert writes == [True]
    assert result["counts"]["finalized"] == 1
    assert REAL_ID not in _blob(result) and REAL_NAME not in _blob(result)


def test_staging_injects_private_assignmentforge_correction(monkeypatch, tmp_path, _set_active_courses):
    session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    session["assignmentforge_corrections"] = {"item-1": {"shared": {
        "answer": "Use walk.", "why": "Present tense."}, "by_tier": None}}
    from api.powergrader import scoring_apply
    monkeypatch.setattr(scoring_apply, "build_plan", lambda *_a, **_kw: {
        "ok": True, "candidate_ids": [REAL_ID], "questions": [],
        "digest": "frozen-review", "notes": []})
    monkeypatch.setattr(scoring_apply, "apply_plan", lambda **_kw: (
        {"ok": True, "pushed": [REAL_ID], "results": [{"user_id": REAL_ID, "status": "pushed"}]}, 200))
    result = _stage_then_apply("session-1", _result(), _digest(bundle))
    assert result["counts"]["finalized"] == 1
    feedback = session["students"][0]["ai_feedback"]
    assert "Extra credit Part 2: Hand copy this exemplar" in feedback
    assert "Use walk." in feedback and "Why: Present tense." in feedback


def test_questions_block_stage_then_matching_digest_allows_apply(monkeypatch, tmp_path, _set_active_courses):
    _session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    from api.powergrader import scoring_apply
    writes = []
    question = {"id": "score_above_possible", "kind": "score_above_possible",
                "detail": "Score exceeds the available points.", "user_ids": [REAL_ID],
                "options": ["post_anyway", "skip_those"]}
    monkeypatch.setattr(scoring_apply, "build_plan", lambda *_a, **_kw: {
        "ok": True, "candidate_ids": [REAL_ID], "questions": [question],
        "digest": "review-1", "notes": []})
    monkeypatch.setattr(scoring_apply, "apply_plan", lambda **_kw: (
        writes.append(True) or ({"ok": True, "pushed": [REAL_ID],
        "results": [{"user_id": REAL_ID, "status": "pushed"}]}, 200)))
    digest = _digest(bundle)
    first = _stage_then_apply("session-1", _result(12), digest)
    assert first["status"] == "needs_teacher_input"
    assert writes == []
    result = _stage_then_apply("session-1", _result(12), digest,
                               review_digest="review-1", answers={"score_above_possible": "post_anyway"})
    assert writes == [True]
    assert result["counts"]["finalized"] == 1


def test_stale_stage_digest_and_malformed_results_fail_closed(monkeypatch, tmp_path, _set_active_courses):
    _session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    from api.powergrader import scoring_apply
    monkeypatch.setattr(scoring_apply, "build_plan", lambda *_a, **_kw: {
        "ok": True, "candidate_ids": [REAL_ID], "questions": [],
        "digest": "frozen-review", "notes": []})
    digest = _digest(bundle)
    staged = tools.stage_scoring_results("session-1", _result(), digest, exemplars=EXEMPLARS)
    assert tools.apply_staged_scoring_results("session-1", "wrong")["code"] == "stage_changed"
    assert tools.stage_scoring_results("session-1", [{"pseudonym": PSEUDONYM}], digest)["code"] == "invalid_results"
    assert staged["stage_digest"] != "wrong"


def test_superseded_stage_refuses_before_canvas_apply(monkeypatch, tmp_path, _set_active_courses):
    _session, bundle, sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    from api.powergrader import scoring_apply
    sessions["session-2"] = {**sessions["session-1"], "session_id": "session-2",
                              "created": "2026-02-01T08:00:00"}
    monkeypatch.setattr(scoring_apply, "build_plan", lambda *_a, **_kw: pytest.fail("planning must not run"))
    result = tools.stage_scoring_results("session-1", _result(), _digest(bundle))
    assert result["code"] == "session_superseded"


def test_transport_unknown_is_projected_without_grade_facts(monkeypatch, tmp_path, _set_active_courses):
    _session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    from api.powergrader import scoring_apply
    monkeypatch.setattr(scoring_apply, "build_plan", lambda *_a, **_kw: {
        "ok": True, "candidate_ids": [REAL_ID], "questions": [],
        "digest": "frozen-review", "notes": []})
    monkeypatch.setattr(scoring_apply, "apply_plan", lambda **_kw: ({
        "ok": False, "code": "write_transport_unknown", "results": [
            {"user_id": REAL_ID, "status": "transport_unknown", "code": "write_transport_unknown"}],
        "posted_rows": [], "remaining_rows": [REAL_ID]}, 200))
    result = _stage_then_apply("session-1", _result(), _digest(bundle))
    assert result["code"] == "write_transport_unknown"
    assert REAL_ID not in _blob(result) and REAL_NAME not in _blob(result)
    assert "grade" not in _blob(result)


def test_stage_scoring_results_example_renders_structured_fields_onto_the_session(
    monkeypatch, tmp_path, _set_active_courses,
):
    """EXAMPLE: structured results in, one rendered plain-text layout out."""
    session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)

    staged = tools.stage_scoring_results(
        "session-1", _result(8), _digest(bundle), exemplars=EXEMPLARS,
        disclosure="Drafted by AI, reviewed by your teacher.",
    )

    assert staged["status"] == "staged"
    feedback = session["students"][0]["ai_feedback"]
    assert feedback == (
        "Score: 8/10\n\n"
        "Clear reasoning throughout the response.\n\n"
        "Glows\n- Strong topic sentence.\n- Concrete supporting detail.\n\n"
        "Grows\n- Add a closing sentence.\n\n"
        f"{'-' * 40}\n"
        "Extra credit Part 1: Fix these in a handwritten second draft\n"
        "1. Add one more supporting detail.\n2. Write a closing sentence.\n\n"
        "Extra credit Part 2: Hand copy this exemplar\n"
        "A short model answer a student could hand copy.\n\n"
        "Drafted by AI, reviewed by your teacher."
    )
    assert session["students"][0]["ai_score"] == 8

    # The posted comment text is exactly the rendered layout: no AI label, no
    # persona name, no disclosure beyond the one explicitly staged above. The
    # plan digest already covers these bytes (scoring_apply.py's
    # _projected_payload mirrors what approve_rows copies before posting).
    from api.powergrader import scoring_apply
    payload = scoring_apply._projected_payload(session["students"][0])
    assert payload["comment"]["text_comment"] == feedback


def test_stage_scoring_results_missing_exemplar_refuses_with_item_ids_only(
    monkeypatch, tmp_path, _set_active_courses,
):
    """EXAMPLE: a below-full-marks item with no exemplar and no correction
    fails closed. No response content or identity is returned."""
    _session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)

    result = tools.stage_scoring_results("session-1", _result(8), _digest(bundle))

    assert result["ok"] is False
    assert result["code"] == "missing_exemplars"
    assert result["item_ids"] == ["item-1"]
    assert REAL_ID not in _blob(result) and REAL_NAME not in _blob(result)
    assert "Clear reasoning" not in _blob(result)
