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


def test_stage_to_plan_to_apply_for_a_policy_course_posts_the_mark_and_late_fields(
    monkeypatch, tmp_path, _set_active_courses, grading_policy_files,
):
    """EXAMPLE: a grading-policy course posts the effort-credit mark and the
    teacher-confirmed late fields in the same request, with the gradebook
    line and late sentence appended to the comment (criteria 3 and 4)."""
    session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    session["students"][0].update({
        "cached_due_date": "2026-09-25T23:59:00-05:00",   # Friday, due
        "canvas_late": True,
        "seconds_late": 100000,                            # canvas_late_days = 2
        "submission_baseline": {"attempt": 1, "submitted_at": "2026-09-28T08:00:00-05:00"},  # Monday
    })

    from api.platform_services import config
    from api.powergrader import scoring_apply
    grading_policy_files.policy(floor_percent=30, missing_percent=20,
                                sweep_after_school_days=15)
    monkeypatch.setattr(config, "get_extra_time", lambda course_id: [])
    sent = []
    monkeypatch.setattr(scoring_apply, "default_transports", lambda: (
        lambda method, path, payload, timeout=30: (sent.append((method, path, payload)) or ({"id": 1}, None))
    ))

    result_with_grading = _result(6)
    result_with_grading[0]["late_days"] = 1
    digest = _digest(bundle)
    first = tools.stage_scoring_results("session-1", result_with_grading, digest,
                                        exemplars=EXEMPLARS)
    assert first["status"] == "needs_teacher_input"
    assert [q["id"] for q in first["questions"]] == ["late_days"]
    # Pseudonymized, with a per-row count -- criterion 6.
    assert first["questions"][0]["rows"] == [
        {"student": PSEUDONYM, "canvas_days": 2, "late_days": 1}
    ]
    assert REAL_ID not in _blob(first) and REAL_NAME not in _blob(first)

    staged = tools.stage_scoring_results(
        "session-1", result_with_grading, digest, exemplars=EXEMPLARS,
        review_digest=first["review_digest"], answers={"late_days": "post_late_days"})
    assert staged["status"] == "staged"

    applied = tools.apply_staged_scoring_results("session-1", staged["stage_digest"])
    assert applied["counts"]["finalized"] == 1
    assert len(sent) == 1
    method, path, payload = sent[0]
    assert method == "PUT" and path.endswith(f"/submissions/{REAL_ID}")
    assert payload["submission"]["posted_grade"] == "7"
    assert payload["submission"]["late_policy_status"] == "late"
    assert payload["submission"]["seconds_late_override"] == 86400
    assert payload["comment"]["text_comment"].endswith(
        "Entered in the gradebook: 7/10. Canvas applies the late penalty to that."
    )
    assert REAL_ID not in _blob(applied) and REAL_NAME not in _blob(applied)


def test_stage_scoring_results_refuses_when_grading_policy_file_is_invalid(
    monkeypatch, tmp_path, _set_active_courses, grading_policy_files,
):
    """Criterion 3 (staging side): an invalid Grading Policy.txt refuses
    staging with grading_policy_file_invalid and load_policy's own readable
    message, before anything is frozen."""
    _session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    grading_policy_files.raw_policy(
        "floor_percent: 10\nmissing_percent: 20\nsweep_after_school_days: 15\n")

    result = tools.stage_scoring_results(
        "session-1", _result(8), _digest(bundle), exemplars=EXEMPLARS)

    assert result == {"ok": False, "code": "grading_policy_file_invalid",
                      "error": ("Grading Policy.txt's floor_percent must be at or "
                               "above missing_percent, and both must be between "
                               "0 and 100.")}


REAL_ID_A, REAL_NAME_A, PSEUDONYM_A = REAL_ID, REAL_NAME, PSEUDONYM
REAL_ID_B, REAL_NAME_B, PSEUDONYM_B = "900456", "Beatrix Potter", "Eevee"


def _wire_two_students(monkeypatch, tmp_path, _set_active_courses):
    """A two-student variant of ``_wire`` for the earlier-candidate law below."""
    _set_active_courses(["course-1"])
    from api.powergrader import session_store
    bundle = {"contract_version": "1.0", "students": [
        {"pseudonym": PSEUDONYM_A, "responses": [
            {"item_id": "item-1", "prompt": "Explain.",
             "response": "A sufficiently long synthetic answer.", "possible": 10}]},
        {"pseudonym": PSEUDONYM_B, "responses": [
            {"item_id": "item-1", "prompt": "Explain.",
             "response": "Another sufficiently long synthetic answer.", "possible": 10}]},
    ]}
    bundle_path = tmp_path / "safe-bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    vault = Vault(str(tmp_path / "vault.json"))
    vault.get_or_assign(REAL_ID_A, real_name=REAL_NAME_A)
    vault.set_pseudonym(REAL_ID_A, PSEUDONYM_A)
    vault.get_or_assign(REAL_ID_B, real_name=REAL_NAME_B)
    vault.set_pseudonym(REAL_ID_B, PSEUDONYM_B)
    vault.save()
    monkeypatch.setattr(tools, "_vault_factory", lambda: vault)
    session = {"session_id": "session-1", "session_kind": "scoring_assignment",
        "course_id": "course-1", "assignment_id": "assignment-1", "assignment_name": "Essay",
        "created": "2026-01-01T08:00:00", "status": "ready",
        "scoring_basis": {"source": "canvas_rubric", "label": "Canvas rubric"},
        "privacy_artifacts": {"safe_bundle": str(bundle_path)},
        "assignment": {"points_possible": 10},
        "students": [{"user_id": REAL_ID_A, "new_quiz_items": []},
                     {"user_id": REAL_ID_B, "new_quiz_items": []}]}
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


def _result_for(pseudonym, score=10, **extra):
    return {"pseudonym": pseudonym, "item_id": "item-1", "score": score,
            "explanation": "Clear reasoning throughout the response.",
            "glows": ["Strong topic sentence.", "Concrete supporting detail."],
            "grows": ["Add a closing sentence."],
            "fixes": ["Add one more supporting detail.", "Write a closing sentence."],
            **extra}


def test_a_students_earlier_stamp_and_question_survive_staging_only_b_again(
    monkeypatch, tmp_path, _set_active_courses, grading_policy_files,
):
    """Senior correction: the grading-stamp loop and the no-policy pop in
    ``_stage_scoring_results_locked`` must both restrict to this call's
    staged rows (``by_uid``), not every candidate. A student staged earlier
    (A, insincere) and left out of a later call that stages only B must keep
    its earlier stamp -- in both the candidate used for the plan digest and
    the persisted session -- and the plan must still carry A's insincere
    question, all the way through a clean apply (no stage_changed)."""
    _session, bundle, sessions = _wire_two_students(monkeypatch, tmp_path, _set_active_courses)
    from api.platform_services import config
    from api.powergrader import scoring_apply
    grading_policy_files.policy(floor_percent=30, missing_percent=20,
                                sweep_after_school_days=15)
    monkeypatch.setattr(config, "get_extra_time", lambda course_id: [])
    sent = []
    monkeypatch.setattr(scoring_apply, "default_transports", lambda: (
        lambda method, path, payload, timeout=30: (sent.append((method, path, payload)) or ({"id": 1}, None))
    ))

    digest = _digest(bundle)
    both_results = [_result_for(PSEUDONYM_A, 10, insincere=True), _result_for(PSEUDONYM_B, 10)]
    first = tools.stage_scoring_results("session-1", both_results, digest)
    assert first["status"] == "needs_teacher_input"
    assert [q["id"] for q in first["questions"]] == ["insincere_attempt"]

    staged = tools.stage_scoring_results(
        "session-1", both_results, digest,
        review_digest=first["review_digest"], answers={"insincere_attempt": "confirm_insincere"})
    assert staged["status"] == "staged"

    a_grading_after_first_stage = dict(
        next(s for s in sessions["session-1"]["students"] if s["user_id"] == REAL_ID_A)["grading"])
    assert a_grading_after_first_stage["insincere"] is True

    # Stage only B this time -- A is left out of the results entirely.
    b_only = [_result_for(PSEUDONYM_B, 9)]
    second = tools.stage_scoring_results("session-1", b_only, digest, exemplars=EXEMPLARS)
    assert second["status"] == "needs_teacher_input"
    # A is still a candidate (staged, unposted) with its earlier insincere
    # mark, so the plan still asks about it -- the bug this corrects would
    # have silently reset A's stamp and dropped this question.
    assert [q["id"] for q in second["questions"]] == ["insincere_attempt"]
    assert second["questions"][0]["students"] == [PSEUDONYM_A]

    second_staged = tools.stage_scoring_results(
        "session-1", b_only, digest, exemplars=EXEMPLARS,
        review_digest=second["review_digest"], answers={"insincere_attempt": "confirm_insincere"})
    assert second_staged["status"] == "staged"

    a_student = next(s for s in sessions["session-1"]["students"] if s["user_id"] == REAL_ID_A)
    assert a_student["grading"] == a_grading_after_first_stage
    assert REAL_ID_A not in _blob(second) and REAL_NAME_A not in _blob(second)

    # No digest disagreement between what staging froze and what apply
    # recomputes from the persisted session -- the concrete failure mode the
    # bug produced (stage_changed).
    applied = tools.apply_staged_scoring_results("session-1", second_staged["stage_digest"])
    assert applied.get("code") != "stage_changed"
    assert applied["counts"]["finalized"] == 2
    posted_grades = {path.rstrip("/").split("/")[-1]: payload["submission"]["posted_grade"]
                     for _method, path, payload in sent}
    assert posted_grades[REAL_ID_A] == "10"   # insincere: posts unchanged
    assert posted_grades[REAL_ID_B] == "9"    # mark(9, 10, 30, False) == 9
