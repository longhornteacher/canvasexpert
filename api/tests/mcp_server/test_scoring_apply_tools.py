"""MCP contracts for local staging followed by explicit Canvas apply."""
from __future__ import annotations

import json

import pytest

from api.feedback_vault import Vault
from api.mcp_server import tools
from api.powergrader import scoring_packet, session_store


REAL_ID = "900123"
REAL_NAME = "Ada Lovelace"
PSEUDONYM = "Pikachu"


@pytest.fixture(autouse=True)
def _score_evidence_workspace(tmp_path, monkeypatch):
    from api.platform_services import workspace

    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))


def _wire(monkeypatch, tmp_path, _set_active_courses):
    _set_active_courses(["course-1"])
    from api.powergrader import session_store
    bundle = {"contract_version": "2.0", "students": [{"pseudonym": PSEUDONYM,
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
             "feedback": "Clear reasoning throughout the response."}]


def _digest(bundle):
    session = session_store.load_session("session-1") or {}
    return scoring_packet.packet_digest("session-1", bundle,
        course_id="course-1", assignment_id="assignment-1",
        baseline_provenance=session.get("students"))


def _stage_then_apply(session_id, results, packet_digest, *, review_digest="", answers=None,
                      grade_mode=None):
    staged = tools.stage_scoring_results(session_id, results, packet_digest,
                                         review_digest=review_digest, answers=answers,
                                         grade_mode=grade_mode)
    if staged.get("status") != "staged":
        return staged
    return tools.apply_staged_scoring_results(session_id, staged["stage_digest"])


def _blob(payload):
    return json.dumps(payload, default=str)


def _baseline_rows(session, params):
    by_user = {
        str(student["user_id"]): dict(student.get("submission_baseline") or {})
        for student in session.get("students") or []
    }
    return [
        {
            "user_id": int(user_id),
            "entered_score": by_user.get(str(user_id), {}).get("entered_score"),
            "score": by_user.get(str(user_id), {}).get(
                "score", by_user.get(str(user_id), {}).get("canvas_score")),
            "workflow_state": by_user.get(str(user_id), {}).get("workflow_state", ""),
            "graded_at": by_user.get(str(user_id), {}).get("graded_at"),
        }
        for user_id in params["student_ids[]"]
    ]


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
    staged = tools.stage_scoring_results("session-1", _result(), _digest(bundle))
    assert staged["status"] == "staged"
    assert writes == []
    assert sessions["session-1"]["status"] == "staged"
    result = tools.apply_staged_scoring_results("session-1", staged["stage_digest"])
    assert writes == [True]
    assert result["counts"]["finalized"] == 1
    assert REAL_ID not in _blob(result) and REAL_NAME not in _blob(result)


def test_apply_projects_live_grade_changes_to_pseudonyms(monkeypatch, tmp_path, _set_active_courses):
    session, bundle, sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    from api.powergrader import scoring_apply
    monkeypatch.setattr(scoring_apply, "build_plan", lambda *_a, **_kw: {
        "ok": True, "candidate_ids": [REAL_ID], "questions": [],
        "digest": "frozen-review", "notes": []})
    monkeypatch.setattr(scoring_apply, "apply_plan", lambda **_kw: ({
        "ok": False,
        "status": "needs_teacher_input",
        "code": "canvas_grade_changed",
        "error": "Canvas grades changed since this Scoring Session was prepared. Nothing was sent.",
        "changed_rows": [{
            "user_id": REAL_ID,
            "expected": {"score": 4.0, "entered_score": 4.0, "workflow_state": "graded", "graded_at": None},
            "live": {"score": 8.0, "entered_score": 8.0, "workflow_state": "graded", "graded_at": None},
        }],
        "next_steps": ["Re-stage without the changed rows.",
                       "Refresh the Scoring Session, then re-stage the results."],
    }, 200))

    staged = tools.stage_scoring_results("session-1", _result(), _digest(bundle))
    result = tools.apply_staged_scoring_results("session-1", staged["stage_digest"])

    assert result["status"] == "needs_teacher_input"
    assert result["code"] == "canvas_grade_changed"
    assert result["changed_rows"][0]["pseudonym"] == PSEUDONYM
    assert REAL_ID not in _blob(result) and REAL_NAME not in _blob(result)
    assert sessions["session-1"]["status"] == "needs_teacher_input"


def test_stage_apply_corrects_posted_days_only_and_surfaces_corrected_count(
        monkeypatch, tmp_path, _set_active_courses):
    session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    from api.powergrader import scoring_apply
    from api.powergrader import session_actions
    import hashlib

    student = session["students"][0]
    student.update({
        "posted": True, "status": "posted", "canvas_late": True,
        "submission_baseline": {"attempt": 1, "submitted_at": "2026-01-01T10:00:00Z",
                                "first_attempt_at": "2026-01-01T10:00:00Z",
                                "latest_attempt_at": "2026-01-01T10:00:00Z",
                                "attempts_complete": True, "attempts_known": True},
        "cached_due_date": "2026-01-01T09:00:00Z",
        "grading": {"floor_percent": None, "points_possible": 10, "late_days": 2,
                    "suggested_late_days": 2},
        "ai_score": 8, "ai_feedback": "Clear reasoning throughout the response.",
        "last_posted": {"event_id": "verified-before-correction", "entered_score": 8,
                        "late_days": 2, "attempt": 1,
                        "feedback_digest": hashlib.sha256(
                            b"Clear reasoning throughout the response.").hexdigest()},
    })
    monkeypatch.setattr(session_actions, "hydrate_last_posted",
                        lambda _session, row, _events=None: row.get("last_posted"))
    digest = _digest(bundle)
    result = _result(8)
    result[0]["late_days"] = 0
    staged = tools.stage_scoring_results("session-1", result, digest)
    assert staged.get("status") == "staged", staged
    assert session["students"][0]["correction_pending"] is True
    assert session["students"][0]["last_posted"]["event_id"] == "verified-before-correction"

    monkeypatch.setattr(scoring_apply, "apply_plan", lambda **_kw: (
        {"ok": True, "pushed": [REAL_ID], "results": [{"user_id": REAL_ID,
         "status": "pushed", "corrected": True}]}, 200))
    applied = tools.apply_staged_scoring_results("session-1", staged["stage_digest"])
    assert applied["counts"]["corrected"] == 1
    assert applied["results"] == [{"pseudonym": PSEUDONYM, "status": "finalized",
                                    "corrected": True}]


def test_incomplete_history_requests_days_only_for_affected_late_row(
        monkeypatch, tmp_path, _set_active_courses):
    session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    session["students"][0].update({"canvas_late": True,
                                   "cached_due_date": "2026-01-01T00:00:00Z",
                                   "submission_baseline": {"attempt": 2,
                                       "attempts_complete": False, "attempts_known": True,
                                       "latest_attempt_at": "2026-01-02T00:00:00Z"}})
    response = tools.stage_scoring_results("session-1", _result(), _digest(bundle))
    assert response["status"] == "needs_teacher_input"
    assert response["questions"][0]["kind"] == "late_days_unknown"
    assert response["questions"][0]["pseudonyms"] == [PSEUDONYM]
    assert response["rows"] == [{"pseudonym": PSEUDONYM,
        "late": {"decision": "unknown", "days": None, "basis": "unknown",
                 "first_attempt_at": None, "latest_attempt_at": "2026-01-01"},
        "warnings": [{"code": "late_days_unknown",
                      "text": "Enter late_days for this row; do not infer it from the latest attempt."}]}]


def test_posted_comment_only_row_without_verified_score_receipt_is_bounded(
        monkeypatch, tmp_path, _set_active_courses):
    session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    session["students"][0].update({"posted": True, "status": "posted",
                                   "ai_score": None, "ai_feedback": "New comment."})
    result = tools.stage_scoring_results("session-1", _result(), _digest(bundle),
                                         grade_mode="feedback_only")
    assert result["ok"] is False and result["code"] == "no_valid_results"
    assert "no verified numeric-score receipt" in result["error"]
    assert 'mode="feedback_revision"' in result["error"]


@pytest.mark.parametrize(("due", "first", "latest", "canvas_late", "posted_attempt",
                          "expected_days", "expected_fields"), [
    ("2026-09-25T23:59:00Z", "2026-09-25T10:00:00Z", "2026-09-28T10:00:00Z",
     True, None, 0, {"late_policy_status": "none"}),
    ("2026-09-23T23:59:00Z", "2026-09-25T10:00:00Z", "2026-10-01T10:00:00Z",
     True, 1, 2, {"late_policy_status": "late", "seconds_late_override": 172800}),
])
def test_staging_uses_first_meaningful_attempt_and_defaults_to_computed_days(
        monkeypatch, tmp_path, _set_active_courses, due, first, latest,
        canvas_late, posted_attempt,
        expected_days, expected_fields):
    session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    student = session["students"][0]
    student.update({"canvas_late": canvas_late, "posted_attempt": posted_attempt,
                    "cached_due_date": due,
        "submission_baseline": {"attempt": 2, "submitted_at": latest,
            "attempt_count": 2, "attempts_complete": True, "attempts_known": True,
            "first_attempt_at": first, "latest_attempt_at": latest,
            "latest_attempt": 2}})
    staged = tools.stage_scoring_results("session-1", _result(), _digest(bundle))
    assert staged["status"] == "staged"
    assert not any(question["kind"] == "late_days" for question in staged.get("questions", []))
    preview = tools.get_scoring_preview("session-1")
    assert preview["rows"][0]["late"]["days"] == expected_days
    assert preview["rows"][0]["late"]["first_attempt_at"] == first[:10]
    assert preview["rows"][0]["late"]["latest_attempt_at"] == latest[:10]
    from api.powergrader import scoring_apply
    plan = scoring_apply.build_plan(session)
    payload = scoring_apply._projected_payload(student, session)
    assert {key: payload["submission"][key] for key in expected_fields} == expected_fields


def test_curve_stage_apply_ledger_and_ledger_driven_revert(monkeypatch, tmp_path, _set_active_courses):
    """EXAMPLE: one frozen 53→67 post is verified, recorded, replay-safe and revertible."""
    session, bundle, sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    from api import score_curves, score_ledger, grade_adjustment
    from api.operation_ledger.adapters import grade_adjustment as adjustment_adapter
    from api.platform_services import canvas_client, config
    from api.powergrader import scoring_apply

    session["assignment"]["points_possible"] = 100
    bundle["students"][0]["responses"][0]["possible"] = 100
    (tmp_path / "safe-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    session["students"][0]["submission_baseline"] = {
        "attempt": 1, "submitted_at": "2026-01-01T00:00:00Z",
        "entered_score": None, "canvas_score": None,
    }
    rule = score_curves.create_rule("course-1", {"model": "gap_close", "fraction": .30},
                                    "assignment-1", root=tmp_path)
    writes, reads = [], []
    def send(method, path, payload):
        writes.append((path, payload))
        return {"ok": True}, None
    def read(path, params):
        reads.append((path, params))
        if not writes:
            return _baseline_rows(session, params), None
        return ([{"user_id": int(REAL_ID), "entered_score": 67, "score": 67,
                  "points_deducted": None, "late_policy_status": None}], None)
    monkeypatch.setattr(scoring_apply, "default_transports", lambda: send)
    monkeypatch.setattr(scoring_apply, "default_read_transport", lambda: read)
    staged = tools.stage_scoring_results("session-1", _result(53), _digest(bundle))
    assert staged.get("status") == "staged", staged
    assert staged["score_rows"][0]["raw"] == 53
    assert staged["score_rows"][0]["entered"] == 67
    assert staged["score_rows"][0]["rule_id"] == rule["rule_id"]
    applied = tools.apply_staged_scoring_results("session-1", staged["stage_digest"])
    assert applied["ok"] is True and applied["counts"]["finalized"] == 1
    result_row = applied["results"][0]
    assert result_row["late"]["entered_score"] == 67
    assert result_row["late"]["decision"] == "not_late"
    assert "points_deducted" not in _blob(result_row)
    assert "raw_score" not in result_row["late"]
    assert len(writes) == 1 and len(reads) == 2
    assert writes[0][1]["submission"]["posted_grade"] == "67"
    comment = writes[0][1]["comment"]["text_comment"]
    assert "Raw 53 -> Entered 67." in comment

    replay = tools.apply_staged_scoring_results("session-1", staged["stage_digest"])
    assert replay["counts"] == applied["counts"]
    assert replay["results"] == applied["results"]
    assert len(writes) == 1 and len(reads) == 2
    ledger = tools.get_score_ledger("course-1", "assignment-1")
    assert ledger.get("coverage") == "recorded_only" and ledger.get("first_recorded_at"), ledger
    staged_event = next(event for event in ledger["events"] if event["source"] == "ce_stage")
    sent_event = next(event for event in ledger["events"]
                      if event["source"] == "ce_apply" and event["action"] == "verified")
    assert staged_event["feedback"] == sent_event["feedback"] == comment
    assert sent_event["raw_score"] == 53 and sent_event["entered_score"] == 67
    assert sent_event["feedback_sha256"]

    # Feed the same exact current baseline into the existing reviewed adjustment lane.
    class FakeVault:
        def transaction(self):
            from contextlib import nullcontext
            return nullcontext(self)
        def get_or_assign(self, value): return PSEUDONYM
    monkeypatch.setattr(config, "active_courses", lambda: [{"id": "course-1", "name": "Synthetic"}])
    monkeypatch.setattr(grade_adjustment, "_vault", FakeVault)
    live = {"assignment": {"id": "assignment-1", "grading_type": "points",
                            "points_possible": 100, "name": "Essay"},
            "score": 67, "entered_score": 67, "attempt": 1, "excused": False}
    def mirror_baseline(payload, target):
        return {"course_id": "course-1", "assignment_id": "assignment-1",
            "assignment": dict(live["assignment"]),
            "entries": [{"user_id": REAL_ID, "eligible": True, "before": live["entered_score"],
                "canvas_score": live["score"], "attempt": live["attempt"],
                "before_excused": False, "missing": False}],
            "roster": [{"id": REAL_ID}], "synced_at": "2026-01-01T00:00:00Z",
            "freshness": {"state": "current", "within_policy": True}}
    monkeypatch.setattr(adjustment_adapter, "_mirror_baseline", mirror_baseline)
    def canvas_get(path):
        if path.endswith("/assignments/assignment-1"):
            return dict(live["assignment"]), None
        return dict(live), None
    def canvas_put(method, path, payload):
        live["entered_score"] = float(payload["submission"]["posted_grade"])
        live["score"] = live["entered_score"]
        return dict(live), None
    monkeypatch.setattr(canvas_client, "canvas_get", canvas_get)
    monkeypatch.setattr(canvas_client, "_canvas_send", canvas_put)
    monkeypatch.setattr("api.mirror.service.notify_course_changed", lambda _course: None)
    preview = grade_adjustment.preview_grade_adjustment("course-1", "assignment-1",
        {"kind": "revert_rule", "rule_id": rule["rule_id"]})
    assert preview["ok"] is True
    assert preview["preview"]["changed"] == [{"pseudonym": PSEUDONYM, "before": 67, "after": 53}]
    reverted = grade_adjustment.apply_grade_adjustment(preview["operation_id"],
        preview["batch_id"], preview["review_digest"])
    assert reverted["ok"] is True
    assert live["entered_score"] == 53
    assert any(event["source"] == "ce_curve" and event["action"] == "revert"
               for event in score_ledger.list_events("course-1", "assignment-1", root=tmp_path))


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
    staged = tools.stage_scoring_results("session-1", _result(), digest)
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
    # grade_mode is safe mode metadata, not a grade fact.
    assert result["grade_mode"] == "post_score"
    assert "grade" not in _blob({k: v for k, v in result.items() if k != "grade_mode"})


def _fake_plan_and_canvas(monkeypatch, writes):
    from api.powergrader import scoring_apply
    baseline_by_user = {}

    def build_plan(session, *, pseudonyms=()):
        candidates = [student for student in session.get("students", [])
                     if student.get("ai_score") is not None or student.get("ai_feedback")]
        baseline_by_user.update({
            str(student["user_id"]): dict(student.get("submission_baseline") or {})
            for student in candidates
        })
        return {"ok": True,
                "candidate_ids": [str(student["user_id"]) for student in candidates],
                "questions": [], "digest": "frozen-review", "notes": [],
                "grade_mode": str(session.get("grade_mode") or "post_score")}

    monkeypatch.setattr(scoring_apply, "build_plan", build_plan)
    monkeypatch.setattr(scoring_apply, "default_transports", lambda: (
        lambda method, path, payload, timeout=30: (
            writes.append((method, path, payload)) or ({"id": 1}, None))
    ))
    def read(path, params):
        if not writes:
            return ([
                {"user_id": int(uid),
                 "entered_score": baseline_by_user.get(str(uid), {}).get("entered_score"),
                 "score": baseline_by_user.get(str(uid), {}).get(
                     "score", baseline_by_user.get(str(uid), {}).get("canvas_score")),
                 "workflow_state": baseline_by_user.get(str(uid), {}).get("workflow_state", ""),
                 "graded_at": baseline_by_user.get(str(uid), {}).get("graded_at")}
                for uid in params["student_ids[]"]
            ], None)
        rows = []
        for uid in params["student_ids[]"]:
            payload = next(item[2] for item in writes if item[1].endswith("/" + str(uid)))
            entered = float(payload["submission"]["posted_grade"])
            rows.append({"user_id": int(uid), "entered_score": entered, "score": entered,
                         "points_deducted": None,
                         "late_policy_status": payload["submission"].get("late_policy_status")})
        return rows, None
    monkeypatch.setattr(scoring_apply, "default_read_transport", lambda: read)


def test_authored_feedback_reaches_canvas_unchanged_and_score_only_omits_comment(
    monkeypatch, tmp_path, _set_active_courses,
):
    """EXAMPLE: arbitrary authored feedback is exact; numeric score-only sends no comment."""
    session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    writes = []
    _fake_plan_and_canvas(monkeypatch, writes)
    feedback = ("# Teacher's heading & notes\n\n**Keep this Markdown.**\n\n"
                "Draft score: teacher-selected line\nScore: another chosen heading.\n\n"
                "Revise the conclusion.\n\n- Ms. Teacher")
    authored = _result(8)
    authored[0]["feedback"] = feedback
    staged = tools.stage_scoring_results("session-1", authored, _digest(bundle))
    assert staged["status"] == "staged"
    assert session["students"][0]["ai_score"] == 8
    applied = tools.apply_staged_scoring_results("session-1", staged["stage_digest"])
    assert applied["counts"]["finalized"] == 1
    assert writes[0][2]["comment"]["text_comment"] == feedback

    (tmp_path / "score-only").mkdir()
    score_session, score_bundle, _score_sessions = _wire(
        monkeypatch, tmp_path / "score-only", _set_active_courses,
    )
    score_writes = []
    _fake_plan_and_canvas(monkeypatch, score_writes)
    from api.powergrader import scoring_apply
    original_build_plan = scoring_apply.build_plan
    monkeypatch.setattr(scoring_apply, "build_plan", lambda *args, **kwargs: {
        **original_build_plan(*args, **kwargs), "digest": "score-only-review"})
    score_only = _result(6)
    score_only[0]["feedback"] = ""
    score_stage = tools.stage_scoring_results(
        "session-1", score_only, _digest(score_bundle),
    )
    assert score_stage.get("status") == "staged", score_stage
    score_apply = tools.apply_staged_scoring_results("session-1", score_stage["stage_digest"])
    assert score_apply["counts"]["finalized"] == 1
    assert "comment" not in score_writes[0][2]


def test_feedback_only_prefix_and_mode_changes_preserve_authored_score_lines(
    monkeypatch, tmp_path, _set_active_courses,
):
    session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    writes = []
    _fake_plan_and_canvas(monkeypatch, writes)
    feedback = "Score: authored heading\nDraft score: authored note\n\nSignature"
    result = _result(8)
    result[0]["feedback"] = feedback

    post_score = tools.stage_scoring_results("session-1", result, _digest(bundle))
    assert session["students"][0]["ai_feedback"] == feedback
    feedback_only = tools.stage_scoring_results(
        "session-1", result, _digest(bundle), grade_mode="feedback_only",
    )
    assert feedback_only["status"] == "staged"
    assert session["students"][0]["ai_feedback"] == f"Draft score: 8/10\n\n{feedback}"
    post_again = tools.stage_scoring_results(
        "session-1", result, _digest(bundle), grade_mode="post_score",
    )
    assert post_again["status"] == "staged"
    assert session["students"][0]["ai_feedback"] == feedback
    assert post_score["stage_digest"] != feedback_only["stage_digest"]


def test_feedback_only_apply_posts_prefixed_comment_without_submission_fields(
    monkeypatch, tmp_path, _set_active_courses,
):
    (tmp_path / "feedback-only").mkdir()
    session, bundle, _sessions = _wire(
        monkeypatch, tmp_path / "feedback-only", _set_active_courses,
    )
    writes = []
    _fake_plan_and_canvas(monkeypatch, writes)
    feedback = "A chosen paragraph & heading."
    result = _result(8)
    result[0]["feedback"] = feedback
    staged = tools.stage_scoring_results(
        "session-1", result, _digest(bundle), grade_mode="feedback_only",
    )
    assert staged["status"] == "staged"
    applied = tools.apply_staged_scoring_results("session-1", staged["stage_digest"])
    assert applied["counts"]["finalized"] == 1
    assert writes[0][2] == {
        "comment": {"text_comment": f"Draft score: 8/10\n\n{feedback}"}
    }
    assert session["grade_mode"] == "feedback_only"


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
    session["late_policy"] = "ask"

    from api.platform_services import config
    from api.powergrader import scoring_apply
    grading_policy_files.policy(floor_percent=30)
    monkeypatch.setattr(config, "get_extra_time", lambda course_id: [])
    sent = []
    monkeypatch.setattr(scoring_apply, "default_transports", lambda: (
        lambda method, path, payload, timeout=30: (sent.append((method, path, payload)) or ({"id": 1}, None))
    ))
    def read(path, params):
        if not sent:
            return _baseline_rows(session, params), None
        rows = []
        for uid in params["student_ids[]"]:
            payload = next(item[2] for item in sent if item[1].endswith("/" + str(uid)))
            entered = float(payload["submission"]["posted_grade"])
            rows.append({"user_id": int(uid), "entered_score": entered, "score": entered,
                         "points_deducted": None,
                         "late_policy_status": payload["submission"].get("late_policy_status")})
        return rows, None
    monkeypatch.setattr(scoring_apply, "default_read_transport", lambda: read)
    def neutral_read(path, params):
        if not sent:
            return _baseline_rows(session, params), None
        rows = []
        for uid in params["student_ids[]"]:
            payload = next(item[2] for item in sent if item[1].endswith("/" + str(uid)))
            entered = float(payload["submission"]["posted_grade"])
            rows.append({"user_id": int(uid), "entered_score": entered, "score": entered,
                         "points_deducted": None,
                         "late_policy_status": payload["submission"].get("late_policy_status")})
        return rows, None
    monkeypatch.setattr(scoring_apply, "default_read_transport", lambda: neutral_read)

    result_with_grading = _result(6)
    result_with_grading[0]["late_days"] = 1
    digest = _digest(bundle)
    first = tools.stage_scoring_results("session-1", result_with_grading, digest)
    assert first["status"] == "needs_teacher_input"
    assert [q["id"] for q in first["questions"]] == ["late_days"]
    # Pseudonymized, with a per-row count -- criterion 6.
    assert first["questions"][0]["rows"] == [
        {"student": PSEUDONYM, "late_days": 1}
    ]
    assert REAL_ID not in _blob(first) and REAL_NAME not in _blob(first)

    staged = tools.stage_scoring_results(
        "session-1", result_with_grading, digest,
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
    assert payload["comment"]["text_comment"] == "Clear reasoning throughout the response."
    assert REAL_ID not in _blob(applied) and REAL_NAME not in _blob(applied)


def test_stage_scoring_results_refuses_when_grading_policy_file_is_invalid(
    monkeypatch, tmp_path, _set_active_courses, grading_policy_files,
):
    """Criterion 3 (staging side): an invalid Grading Policy.txt refuses
    staging with grading_policy_file_invalid and load_policy's own readable
    message, before anything is frozen."""
    _session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    grading_policy_files.raw_policy("floor_percent: 101\n")

    result = tools.stage_scoring_results(
        "session-1", _result(8), _digest(bundle))

    assert result == {"ok": False, "code": "grading_policy_file_invalid",
                      "error": "Grading Policy.txt's floor_percent must be between 0 and 100."}


REAL_ID_A, REAL_NAME_A, PSEUDONYM_A = REAL_ID, REAL_NAME, PSEUDONYM
REAL_ID_B, REAL_NAME_B, PSEUDONYM_B = "900456", "Beatrix Potter", "Eevee"


def _wire_two_students(monkeypatch, tmp_path, _set_active_courses):
    """A two-student variant of ``_wire`` for the earlier-candidate law below."""
    _set_active_courses(["course-1"])
    from api.powergrader import session_store
    bundle = {"contract_version": "2.0", "students": [
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
            "feedback": "Clear reasoning throughout the response.",
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
    session, bundle, sessions = _wire_two_students(monkeypatch, tmp_path, _set_active_courses)
    from api.platform_services import config
    from api.powergrader import scoring_apply
    grading_policy_files.policy(floor_percent=30)
    monkeypatch.setattr(config, "get_extra_time", lambda course_id: [])
    sent = []
    monkeypatch.setattr(scoring_apply, "default_transports", lambda: (
        lambda method, path, payload, timeout=30: (sent.append((method, path, payload)) or ({"id": 1}, None))
    ))
    def read(path, params):
        if not sent:
            return _baseline_rows(session, params), None
        rows = []
        for uid in params["student_ids[]"]:
            payload = next(item[2] for item in sent if item[1].endswith("/" + str(uid)))
            entered = float(payload["submission"]["posted_grade"])
            rows.append({"user_id": int(uid), "entered_score": entered, "score": entered,
                         "points_deducted": None,
                         "late_policy_status": payload["submission"].get("late_policy_status")})
        return rows, None
    monkeypatch.setattr(scoring_apply, "default_read_transport", lambda: read)

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
    second = tools.stage_scoring_results("session-1", b_only, digest)
    assert second["status"] == "needs_teacher_input"
    # A is still a candidate (staged, unposted) with its earlier insincere
    # mark, so the plan still asks about it -- the bug this corrects would
    # have silently reset A's stamp and dropped this question.
    assert [q["id"] for q in second["questions"]] == ["insincere_attempt"]
    assert second["questions"][0]["students"] == [PSEUDONYM_A]

    second_staged = tools.stage_scoring_results(
        "session-1", b_only, digest,
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


# ── Late decision, read-back, and self-explaining errors ────────────────────


def _late_policy_course(monkeypatch, tmp_path, _set_active_courses, grading_policy_files,
                        reader=None):
    """A policy-course session with one late row; returns (session, bundle, sent, reads)."""
    session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    session["students"][0].update({
        "cached_due_date": "2026-09-25T23:59:00-05:00",
        "canvas_late": True,
        "seconds_late": 100000,
        "submission_baseline": {"attempt": 1, "submitted_at": "2026-09-28T08:00:00-05:00"},
    })
    session["late_policy"] = "ask"
    from api.platform_services import config
    from api.powergrader import scoring_apply
    grading_policy_files.policy(floor_percent=30)
    monkeypatch.setattr(config, "get_extra_time", lambda course_id: [])
    sent, reads = [], []
    monkeypatch.setattr(scoring_apply, "default_transports", lambda: (
        lambda method, path, payload, timeout=30: (sent.append((method, path, payload)) or ({"id": 1}, None))
    ))

    def read(path, params):
        reads.append((path, params))
        if not sent:
            return _baseline_rows(session, params), None
        if reader:
            return reader(path, params)
        rows = []
        for uid in params["student_ids[]"]:
            payload = next(item[2] for item in sent if item[1].endswith("/" + str(uid)))
            entered = float(payload["submission"]["posted_grade"])
            rows.append({"user_id": int(uid), "entered_score": entered, "score": entered,
                         "points_deducted": None,
                         "late_policy_status": payload["submission"].get("late_policy_status")})
        return rows, None

    monkeypatch.setattr(scoring_apply, "default_read_transport", lambda: read)
    return session, bundle, sent, reads


def _late_results(score=6):
    results = _result(score)
    results[0]["late_days"] = 1
    return results


def test_waive_late_stage_shows_the_post_answer_decision_and_sends_the_waived_bytes(
    monkeypatch, tmp_path, _set_active_courses, grading_policy_files,
):
    """EXAMPLE: the question carries the generic legend and per-row late block;
    answering waive_late shows waived in the staged summary and posts status none."""
    _session, bundle, sent, _reads = _late_policy_course(
        monkeypatch, tmp_path, _set_active_courses, grading_policy_files)
    digest = _digest(bundle)

    first = tools.stage_scoring_results("session-1", _late_results(), digest)
    assert first["status"] == "needs_teacher_input"
    question = first["questions"][0]
    assert question["answer_with"] == ["post_late_days", "waive_late", "stop"]
    assert "first meaningful attempt" in question["legend"]
    assert first["rows"] == [{"pseudonym": PSEUDONYM,
                              "late": {"decision": "set", "late_days": 1,
                                       "basis": "teacher_set"}}]

    staged = tools.stage_scoring_results(
        "session-1", _late_results(), digest, 
        review_digest=first["review_digest"], answers={"late_days": "waive_late"})
    assert staged["status"] == "staged"
    assert staged["rows"] == [{"pseudonym": PSEUDONYM,
                                "late": {"decision": "waived", "late_days": 0,
                                         "basis": "teacher_set"}}]

    applied = tools.apply_staged_scoring_results("session-1", staged["stage_digest"])
    assert applied["counts"]["finalized"] == 1
    submission = sent[0][2]["submission"]
    assert submission["late_policy_status"] == "none"
    assert "seconds_late_override" not in submission
    assert "Canvas applies the late penalty" not in sent[0][2]["comment"]["text_comment"]


def test_apply_reports_a_waived_row_canvas_still_penalized(
    monkeypatch, tmp_path, _set_active_courses, grading_policy_files,
):
    """EXAMPLE: points_deducted 2 on a waived row -> late_not_honored, the code,
    a user_action, and the row's own late facts; the write stays recorded."""
    _session, bundle, _sent, reads = _late_policy_course(
        monkeypatch, tmp_path, _set_active_courses, grading_policy_files,
        reader=lambda _p, _q: ([{"user_id": int(REAL_ID), "score": 4, "entered_score": 6,
                                 "points_deducted": 2, "late_policy_status": "none"}], None))
    digest = _digest(bundle)
    first = tools.stage_scoring_results("session-1", _late_results(), digest)
    staged = tools.stage_scoring_results(
        "session-1", _late_results(), digest, 
        review_digest=first["review_digest"], answers={"late_days": "waive_late"})

    applied = tools.apply_staged_scoring_results("session-1", staged["stage_digest"])

    assert len(reads) == 2
    assert applied["ok"] is False
    assert applied["code"] == "score_mismatch"
    assert applied["counts"]["score_mismatch"] == 1 and applied["counts"]["finalized"] == 0
    row = applied["results"][0]
    assert row["status"] == "score_mismatch"
    assert row["late"]["decision"] == "waived"
    assert row["late"]["verification"] == "score_mismatch"
    assert row["late"]["entered_score"] == 6
    assert applied["posted_rows"] == [PSEUDONYM]
    assert REAL_ID not in _blob(applied) and REAL_NAME not in _blob(applied)


def test_apply_with_a_failed_read_reports_score_verification_unavailable(
    monkeypatch, tmp_path, _set_active_courses, grading_policy_files,
):
    _session, bundle, _sent, _reads = _late_policy_course(
        monkeypatch, tmp_path, _set_active_courses, grading_policy_files,
        reader=lambda _p, _q: (None, "HTTP 500: boom"))
    digest = _digest(bundle)
    first = tools.stage_scoring_results("session-1", _late_results(), digest)
    staged = tools.stage_scoring_results(
        "session-1", _late_results(), digest, 
        review_digest=first["review_digest"], answers={"late_days": "post_late_days"})

    applied = tools.apply_staged_scoring_results("session-1", staged["stage_digest"])

    assert applied["ok"] is False
    assert applied["counts"]["score_readback_unavailable"] == 1
    assert applied["code"] == "score_readback_unavailable"
    assert applied["results"][0]["late"]["readback"] == "unavailable"
    assert applied["warnings"] == ["score_readback_unavailable"]


def test_changing_late_policy_after_a_frozen_stage_refuses_stage_changed(
    monkeypatch, tmp_path, _set_active_courses, grading_policy_files,
):
    """LAW: a frozen stage cannot apply under a different late policy."""
    session, bundle, sent, _reads = _late_policy_course(
        monkeypatch, tmp_path, _set_active_courses, grading_policy_files)
    digest = _digest(bundle)
    first = tools.stage_scoring_results("session-1", _late_results(), digest)
    staged = tools.stage_scoring_results(
        "session-1", _late_results(), digest, 
        review_digest=first["review_digest"], answers={"late_days": "post_late_days"})
    assert staged["status"] == "staged"

    session["late_policy"] = "waive"
    refused = tools.apply_staged_scoring_results("session-1", staged["stage_digest"])

    assert refused["ok"] is False and refused["code"] == "stage_changed"
    assert sent == []


def test_invalid_results_names_unknown_and_missing_pseudonyms_and_fields(
    monkeypatch, tmp_path, _set_active_courses,
):
    """CONTRACT: an unknown pseudonym is explained, never a blank ``fields``."""
    _session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    results = _result(8)
    results[0]["pseudonym"] = "Mewtwo"

    refused = tools.stage_scoring_results("session-1", results, _digest(bundle))

    assert refused["ok"] is False and refused["code"] == "invalid_results"
    validation = refused["validation"]
    assert validation["fields"] == ["pseudonym"]
    assert validation["issues"] == []
    # A string that is nobody's pseudonym is counted, never echoed.
    assert validation["unknown_pseudonyms"] == []
    assert validation["unrecognized_count"] == 1
    assert validation["missing_pseudonyms"] == [PSEUDONYM]
    assert "Mewtwo" not in _blob(refused)


def test_invalid_results_echoes_only_unknown_pseudonyms_that_resolve_in_the_vault(
    monkeypatch, tmp_path, _set_active_courses,
):
    """CONTRACT: another student's real pseudonym is echoed so the agent can see
    the mix-up; sorted, with the missing packet pseudonym alongside."""
    _session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    vault = tools._vault_factory()
    vault.get_or_assign("900777", real_name="Zed Zeta")
    vault.set_pseudonym("900777", "Eevee")
    vault.save()
    results = _result(8)
    results[0]["pseudonym"] = "Eevee"

    refused = tools.stage_scoring_results("session-1", results, _digest(bundle))

    validation = refused["validation"]
    assert validation["unknown_pseudonyms"] == ["Eevee"]
    assert validation["missing_pseudonyms"] == [PSEUDONYM]
    assert validation["unrecognized_count"] == 0
    assert "Zed" not in _blob(refused)


def test_a_real_name_supplied_as_a_pseudonym_is_never_echoed(
    monkeypatch, tmp_path, _set_active_courses,
):
    """LAW: unknown pseudonyms are agent-supplied strings; one that is not a
    vault pseudonym (here a real name) is never echoed back."""
    _session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    results = _result(8)
    results[0]["pseudonym"] = REAL_NAME

    refused = tools.stage_scoring_results("session-1", results, _digest(bundle))

    assert refused["ok"] is False
    assert REAL_NAME not in _blob(refused)


# ── Scoring preview, agent commentary, posting warnings ─────────────────────

COMMENTARY = ("Possible plagiarism: CE found a long shared run of words with another "
              "response, and a reading-level check suggests this is AI-generated. "
              "That may be cheating, so worth a conversation.")


def _canvas_recorder(monkeypatch, session):
    """Record every Canvas send and echo it back to the posted-score check."""
    from api.powergrader import scoring_apply

    sent = []
    monkeypatch.setattr(scoring_apply, "default_transports", lambda: (
        lambda method, path, payload, timeout=30: (
            sent.append((method, path, payload)) or ({"id": 1}, None))
    ))
    def read(path, params):
        if not sent:
            return _baseline_rows(session, params), None
        if not sent:
            return _baseline_rows(session, params), None
        rows = []
        for uid in params["student_ids[]"]:
            payload = next(item[2] for item in sent if item[1].endswith("/" + str(uid)))
            entered = float(payload["submission"]["posted_grade"])
            rows.append({"user_id": int(uid), "entered_score": entered, "score": entered,
                         "points_deducted": None,
                         "late_policy_status": payload["submission"].get("late_policy_status")})
        return rows, None

    monkeypatch.setattr(scoring_apply, "default_read_transport", lambda: read)
    return sent


def _posting_read(monkeypatch, read):
    from api.powergrader import scoring_apply

    monkeypatch.setattr(scoring_apply, "default_assignment_read", lambda: read)


def test_stage_then_preview_returns_projected_rows_and_the_commentary_unchanged(
    monkeypatch, tmp_path, _set_active_courses,
):
    """EXAMPLE: integrity words in agent_commentary survive staging, are stored on
    the session student and come back in get_scoring_preview, and only there."""
    session, bundle, sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    session["students"][0]["submission_baseline"] = {
        "attempt": 1, "entered_score": 5, "canvas_score": 5}
    results = _result(8)
    results[0]["agent_commentary"] = COMMENTARY

    staged = tools.stage_scoring_results("session-1", results, _digest(bundle))

    assert staged["status"] == "staged"
    assert "get_scoring_preview" in staged["next"]
    summary = staged["preview_summary"]
    assert summary["posting"]["post_manually"] is True and summary["posting"]["checked_at"]
    assert summary["warnings"] == []
    assert summary["counts"] == {"ready": 1, "held": 0, "with_warnings": 1,
                                 "with_agent_commentary": 1}
    assert summary["attention"] == [PSEUDONYM]
    assert "plagiarism" not in _blob(staged)
    assert sessions["session-1"]["students"][0]["agent_commentary"] == COMMENTARY
    assert sessions["session-1"]["posting_policy"]["post_manually"] is True

    preview = tools.get_scoring_preview("session-1")

    assert preview["ok"] is True, preview
    assert preview["stage_digest"] == staged["stage_digest"]
    assert preview["grade_mode"] == "post_score"
    assert preview["held"] == [] and preview["total"] == 1 and "next_offset" not in preview
    assert preview["rows"] == [{
        "pseudonym": PSEUDONYM, "raw_score": 8.0, "entered": "8", "points_possible": 10.0,
        "late": None, "correction": None,
        "comment": "Clear reasoning throughout the response.",
        "agent_commentary": COMMENTARY,
        "warnings": [{"code": "replaces_canvas_score",
                      "text": "Replaces a score already in Canvas (5), as of session preparation."}],
        "attention": True,
    }]
    for word in ("plagiarism", "AI-generated", "cheating"):
        assert word in preview["rows"][0]["agent_commentary"]
    assert REAL_ID not in _blob(preview) and REAL_NAME not in _blob(preview)


@pytest.mark.parametrize("scenario", ["post_score", "feedback_only", "score_curve", "late_waived"])
def test_preview_comment_and_entered_are_exactly_what_canvas_receives(
    monkeypatch, tmp_path, _set_active_courses, grading_policy_files, scenario,
):
    """LAW: for every row the preview's comment is the projected text_comment,
    character for character, and entered is the projected posted_grade (null in
    feedback-only mode), checked against the bytes the apply actually sends."""
    kwargs, answers = {}, None
    results = _result(8)
    if scenario == "late_waived":
        session, bundle, sent, _reads = _late_policy_course(
            monkeypatch, tmp_path, _set_active_courses, grading_policy_files)
        results, answers = _late_results(6), {"late_days": "waive_late"}
    else:
        session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
        sent = _canvas_recorder(monkeypatch, session)
    if scenario == "feedback_only":
        kwargs["grade_mode"] = "feedback_only"
    if scenario == "score_curve":
        from api import score_curves
        session["assignment"]["points_possible"] = 100
        bundle["students"][0]["responses"][0]["possible"] = 100
        (tmp_path / "safe-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
        session["students"][0]["submission_baseline"] = {"attempt": 1, "entered_score": None}
        score_curves.create_rule("course-1", {"model": "gap_close", "fraction": .30},
                                 "assignment-1", root=tmp_path)
        results = _result(53)
    digest = _digest(bundle)
    staged = tools.stage_scoring_results("session-1", results, digest, **kwargs)
    if answers:
        assert staged["status"] == "needs_teacher_input"
        staged = tools.stage_scoring_results(
            "session-1", results, digest, review_digest=staged["review_digest"],
            answers=answers, **kwargs)
    assert staged["status"] == "staged", staged

    row = tools.get_scoring_preview("session-1")["rows"][0]
    applied = tools.apply_staged_scoring_results("session-1", staged["stage_digest"])

    assert applied["counts"]["finalized"] == 1, applied
    payload = sent[0][2]
    assert row["comment"] == (payload.get("comment") or {}).get("text_comment", "")
    assert row["entered"] == (payload.get("submission") or {}).get("posted_grade")
    if scenario == "feedback_only":
        assert row["entered"] is None and "submission" not in payload and row["late"] is None
        assert row["comment"].startswith("Draft score: 8/10")
    if scenario == "score_curve":
        assert row["entered"] == "67" and "Raw 53 -> Entered 67." in row["comment"]
        assert [w["code"] for w in row["warnings"]] == ["entered_differs_from_raw"]
    if scenario == "late_waived":
        assert row["late"]["decision"] == "waived"
        assert row["late"]["days"] == 0
        assert row["late"]["basis"] == "teacher_set"
        assert [w["code"] for w in row["warnings"]] == [
            "late_waived", "entered_differs_from_raw"]
        assert row["entered"] == "7" and "grading floor" in row["warnings"][1]["text"]


@pytest.mark.parametrize("grade_mode", ["post_score", "feedback_only"])
def test_agent_commentary_never_reaches_a_canvas_payload(
    monkeypatch, tmp_path, _set_active_courses, grade_mode,
):
    """LAW: the agent's teacher-only note is not in the payload builder's output
    nor in any send the apply makes, in either grade mode."""
    from api.powergrader import scoring_apply, session_actions

    session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    sent = _canvas_recorder(monkeypatch, session)
    results = _result(8)
    results[0]["agent_commentary"] = COMMENTARY
    staged = tools.stage_scoring_results(
        "session-1", results, _digest(bundle), grade_mode=grade_mode)
    student = session["students"][0]
    assert staged["status"] == "staged" and student["agent_commentary"] == COMMENTARY

    built = [
        session_actions._payload({**student, "teacher_score": student["ai_score"],
                                  "teacher_feedback": student["ai_feedback"]},
                                 grade_mode=grade_mode),
        scoring_apply._projected_payload(student, session, grade_mode=grade_mode),
    ]
    applied = tools.apply_staged_scoring_results("session-1", staged["stage_digest"])

    assert applied["counts"]["finalized"] == 1 and sent
    for payload in built + [item[2] for item in sent]:
        assert payload and "plagiarism" not in _blob(payload) and COMMENTARY not in _blob(payload)


def test_preview_needs_a_stage_and_goes_stale_exactly_when_apply_would_refuse(
    monkeypatch, tmp_path, _set_active_courses,
):
    """LAW: no stage means nothing_staged; a session that no longer matches its
    stage digest is preview_stale, the same condition apply refuses as stage_changed."""
    session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    assert tools.get_scoring_preview("session-1")["code"] == "nothing_staged"
    assert tools.get_scoring_preview("missing")["code"] == "session_not_found"
    staged = tools.stage_scoring_results("session-1", _result(8), _digest(bundle))
    assert tools.get_scoring_preview("session-1")["ok"] is True

    session["students"][0]["ai_feedback"] = "Edited after it was staged."

    stale = tools.get_scoring_preview("session-1")
    assert stale["ok"] is False and stale["code"] == "preview_stale"
    refused = tools.apply_staged_scoring_results("session-1", staged["stage_digest"])
    assert refused["code"] == "stage_changed"


def test_preview_goes_stale_when_the_packet_changes_after_staging(
    monkeypatch, tmp_path, _set_active_courses,
):
    """LAW: a packet that moved since staging is preview_stale, the same moment
    apply refuses it as stale_packet, so the teacher never reviews a dead stage."""
    _session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    staged = tools.stage_scoring_results("session-1", _result(8), _digest(bundle))
    assert tools.get_scoring_preview("session-1")["ok"] is True

    bundle["students"][0]["responses"][0]["response"] = "A different answer after a resubmission."
    (tmp_path / "safe-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")

    assert tools.get_scoring_preview("session-1")["code"] == "preview_stale"
    assert tools.apply_staged_scoring_results(
        "session-1", staged["stage_digest"])["code"] == "stale_packet"


def test_preview_goes_stale_when_the_staged_curve_is_deactivated(
    monkeypatch, tmp_path, _set_active_courses,
):
    """LAW: a curve rule deactivated after staging is preview_stale, the same
    moment apply refuses it as stage_changed."""
    from api import score_curves

    session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    _canvas_recorder(monkeypatch, session)
    session["assignment"]["points_possible"] = 100
    bundle["students"][0]["responses"][0]["possible"] = 100
    (tmp_path / "safe-bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    session["students"][0]["submission_baseline"] = {"attempt": 1, "entered_score": None}
    rule = score_curves.create_rule("course-1", {"model": "gap_close", "fraction": .30},
                                    "assignment-1", root=tmp_path)
    staged = tools.stage_scoring_results("session-1", _result(53), _digest(bundle))
    assert staged["status"] == "staged", staged
    assert tools.get_scoring_preview("session-1")["rows"][0]["entered"] == "67"

    score_curves.deactivate_rule("course-1", rule["rule_id"], root=tmp_path)

    assert tools.get_scoring_preview("session-1")["code"] == "preview_stale"
    assert tools.apply_staged_scoring_results(
        "session-1", staged["stage_digest"])["code"] == "stage_changed"


def test_the_posting_read_does_not_hold_the_scope_lock(
    monkeypatch, tmp_path, _set_active_courses,
):
    """LAW: a slow Canvas answer must not stall apply, refresh or prepare for the
    assignment, so the posting-policy read happens before the scope lock is taken."""
    import contextlib
    from api.powergrader import session_store

    _session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    held = []

    @contextlib.contextmanager
    def lock(_course, _assignment):
        held.append(True)
        try:
            yield
        finally:
            held.pop()

    monkeypatch.setattr(session_store, "scope_lock", lock)
    seen = []
    _posting_read(monkeypatch, lambda path: seen.append(bool(held)) or ({"post_manually": True}, None))

    staged = tools.stage_scoring_results("session-1", _result(8), _digest(bundle))

    assert staged["status"] == "staged", staged
    assert seen == [False]


@pytest.mark.parametrize("read, expected, post_manually", [
    (lambda path: ({"post_manually": False}, None), ["posts_automatically"], False),
    (lambda path: ({"post_manually": True}, None), [], True),
    (lambda path: (None, "HTTP 500: boom"), ["posting_unchecked"], None),
    (lambda path: ({"name": "no posting field"}, None), ["posting_unchecked"], None),
], ids=["automatic", "manual", "failed_read", "field_missing"])
def test_posting_policy_warning_never_blocks_staging(
    monkeypatch, tmp_path, _set_active_courses, read, expected, post_manually,
):
    """CONTRACT: automatic or unknown posting is a warning on the stage and the
    preview; manual posting is none; the read is one GET of the exact assignment."""
    _session, bundle, sessions = _wire(monkeypatch, tmp_path, _set_active_courses)
    reads = []
    _posting_read(monkeypatch, lambda path: reads.append(path) or read(path))

    staged = tools.stage_scoring_results("session-1", _result(8), _digest(bundle))

    assert staged["status"] == "staged", staged
    assert reads == ["/api/v1/courses/course-1/assignments/assignment-1"]
    summary = staged["preview_summary"]
    assert [w["code"] for w in summary["warnings"]] == expected
    assert summary["posting"]["post_manually"] is post_manually
    assert sessions["session-1"]["posting_policy"] == summary["posting"]
    preview = tools.get_scoring_preview("session-1")
    assert [w["code"] for w in preview["warnings"]] == expected
    assert preview["posting"] == summary["posting"]


def test_a_raising_posting_read_is_a_warning_and_the_policy_is_not_in_the_stage_digest(
    monkeypatch, tmp_path, _set_active_courses,
):
    """LAW: posting_policy never changes the stage digest or makes the preview stale."""
    _session, bundle, _sessions = _wire(monkeypatch, tmp_path, _set_active_courses)

    def boom(path):
        raise RuntimeError("transport exploded")

    _posting_read(monkeypatch, boom)
    first = tools.stage_scoring_results("session-1", _result(8), _digest(bundle))
    assert first["status"] == "staged"
    assert [w["code"] for w in first["preview_summary"]["warnings"]] == ["posting_unchecked"]

    _posting_read(monkeypatch, lambda path: ({"post_manually": False}, None))
    second = tools.stage_scoring_results("session-1", _result(8), _digest(bundle))

    assert second["stage_digest"] == first["stage_digest"]
    assert [w["code"] for w in second["preview_summary"]["warnings"]] == ["posts_automatically"]
    _posting_read(monkeypatch, lambda path: ({"post_manually": True}, None))
    assert tools.get_scoring_preview("session-1")["ok"] is True


def test_review_questions_carry_a_preview_summary_and_held_rows_are_listed(
    monkeypatch, tmp_path, _set_active_courses,
):
    """EXAMPLE: needs_teacher_input also carries the summary; an unscored student
    is listed as held with no invented reason."""
    _session, bundle, _sessions = _wire_two_students(monkeypatch, tmp_path, _set_active_courses)
    digest = _digest(bundle)
    only_a = [_result_for(PSEUDONYM_A, 8)]

    first = tools.stage_scoring_results("session-1", only_a, digest)

    assert first["status"] == "needs_teacher_input"
    assert [q["id"] for q in first["questions"]] == ["held_not_scored"]
    assert first["preview_summary"]["counts"]["ready"] == 1
    assert first["preview_summary"]["counts"]["held"] == 1

    staged = tools.stage_scoring_results(
        "session-1", only_a, digest, review_digest=first["review_digest"],
        answers={"held_not_scored": "proceed"})
    preview = tools.get_scoring_preview("session-1")

    assert staged["status"] == "staged"
    assert [row["pseudonym"] for row in preview["rows"]] == [PSEUDONYM_A]
    assert preview["held"] == [{"pseudonym": PSEUDONYM_B, "reason": None}]
    assert [w["code"] for w in preview["warnings"]] == ["held_rows"]
    assert REAL_ID_B not in _blob(preview) and REAL_NAME_B not in _blob(preview)
