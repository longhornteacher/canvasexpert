"""Synthetic tests for the single chat-facing Scoring Session submit call."""
from __future__ import annotations

import json

from api.feedback_vault import Vault
from api.mcp_server import tools
from api.powergrader import scoring_packet


REAL_ID = "900123"
REAL_NAME = "Ada Lovelace"
PSEUDONYM = "Pikachu"


def _wire(monkeypatch, tmp_path, _set_active_courses, *, new_quiz=False):
    _set_active_courses(["course-1"])
    from api.powergrader import session_store

    bundle = {
        "contract_version": "1.0",
        "students": [{"pseudonym": PSEUDONYM, "responses": [{
            "item_id": "item-1", "prompt": "Explain.",
            "response": "A sufficiently long synthetic answer.", "possible": 10,
        }]}],
    }
    bundle_path = tmp_path / "safe-bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    vault = Vault(str(tmp_path / "vault.json"))
    vault.get_or_assign(REAL_ID, real_name=REAL_NAME)
    vault.set_pseudonym(REAL_ID, PSEUDONYM)
    vault.save()
    monkeypatch.setattr(tools, "_vault_factory", lambda: vault)
    session = {
        "session_id": "session-1", "course_id": "course-1",
        "assignment_id": "assignment-1", "assignment_name": "Essay",
        "scoring_basis": {"source": "canvas_rubric", "label": "Canvas rubric"},
        "privacy_artifacts": {"safe_bundle": str(bundle_path)},
        "new_quiz_item_finalization_supported": new_quiz,
        "assignment": {"points_possible": 10},
        "students": [{"user_id": REAL_ID, "new_quiz_items": []}],
    }
    monkeypatch.setattr(session_store, "load_session", lambda _sid: session)
    monkeypatch.setattr(session_store, "save_session", lambda updated: session.update(updated))

    class _Lock:
        def __enter__(self): return self
        def __exit__(self, *_exc): return False

    monkeypatch.setattr(session_store, "session_lock", lambda _sid: _Lock())
    return session, bundle


def _result(score=8):
    return [{"pseudonym": PSEUDONYM, "item_id": "item-1", "score": score,
             "feedback": "Clear reasoning."}]


def _digest(bundle):
    return scoring_packet.packet_digest("session-1", bundle)


def _blob(payload):
    return json.dumps(payload, default=str)


def test_ordinary_results_submit_once_without_exposing_private_identity(
    monkeypatch, tmp_path, _set_active_courses,
):
    session, bundle = _wire(monkeypatch, tmp_path, _set_active_courses)
    from api.powergrader import session_store
    from api.powergrader import scoring_apply
    writes = []

    class _TrackedLock:
        depth = 0

        def __enter__(self):
            self.depth += 1
            return self

        def __exit__(self, *_exc):
            self.depth -= 1
            return False

    lock = _TrackedLock()
    monkeypatch.setattr(session_store, "session_lock", lambda _sid: lock)

    monkeypatch.setattr(scoring_apply, "build_plan", lambda candidate, **_kw: {
        "ok": True, "candidate_ids": [REAL_ID], "questions": [],
        "digest": "frozen-review", "notes": [],
    })
    def apply_plan(*_args, **_kwargs):
        assert lock.depth == 1, "staging and apply must remain in one session transaction"
        assert session["students"][0]["ai_score"] == 8
        writes.append("verified write")
        return ({"ok": True, "pushed": [REAL_ID],
                 "results": [{"user_id": REAL_ID, "status": "pushed"}]}, 200)

    monkeypatch.setattr(scoring_apply, "apply_plan", apply_plan)

    result = tools.submit_scoring_results(
        "session-1", _result(), _digest(bundle))

    assert writes == ["verified write"]
    assert result["counts"] == {"finalized": 1, "already_applied": 0, "held": 0, "failed": 0}
    assert result["results"] == [{"pseudonym": PSEUDONYM, "status": "finalized"}]
    assert REAL_ID not in _blob(result) and REAL_NAME not in _blob(result)
    assert session["students"][0]["ai_score"] == 8


def test_new_quiz_missing_score_holds_instead_of_offering_comment_only(
    monkeypatch, tmp_path, _set_active_courses,
):
    session, bundle = _wire(monkeypatch, tmp_path, _set_active_courses, new_quiz=True)
    vault, error = tools._open_vault()
    assert error is None

    questions = tools._new_quiz_scoring_questions(
        session,
        [{"pseudonym": PSEUDONYM, "item_id": "item-1", "score": None,
          "feedback": "Useful feedback."}],
        bundle, {REAL_ID: PSEUDONYM}, [], vault,
    )
    [question] = questions
    [public_question] = tools._new_quiz_public_questions(questions, {REAL_ID: PSEUDONYM})

    assert question["kind"] == "missing_score"
    assert public_question["answer_with"] == ["skip_those"]
    assert "comment-only posting is unavailable" in public_question["detail"].lower()
    assert tools._resolve_scoring_answers(questions, {"missing_score": "comment_only"}) == {
        "ok": False, "code": "invalid_answer",
    }
    resolved = tools._resolve_scoring_answers(questions, {"missing_score": "skip_those"})
    assert resolved["skip_pseudonyms"] == {PSEUDONYM}


def test_question_blocks_write_then_matching_digest_and_answer_submit_same_results(
    monkeypatch, tmp_path, _set_active_courses,
):
    _wire(monkeypatch, tmp_path, _set_active_courses)
    from api.powergrader import scoring_apply
    writes = []
    question = {"id": "score_above_possible", "kind": "score_above_possible",
                "detail": "Score exceeds the available points.", "user_ids": [REAL_ID],
                "options": ["post_anyway", "skip_those"]}
    monkeypatch.setattr(scoring_apply, "build_plan", lambda *_a, **_kw: {
        "ok": True, "candidate_ids": [REAL_ID], "questions": [question],
        "digest": "review-1", "notes": [],
    })
    monkeypatch.setattr(scoring_apply, "apply_plan", lambda *_a, **_kw: (
        writes.append("verified write") or ({"ok": True, "pushed": [REAL_ID],
            "results": [{"user_id": REAL_ID, "status": "pushed"}]}, 200)))
    bundle = json.loads((tmp_path / "safe-bundle.json").read_text(encoding="utf-8"))
    digest = _digest(bundle)

    question_result = tools.submit_scoring_results("session-1", _result(12), digest)
    assert question_result["status"] == "needs_teacher_input"
    assert question_result["questions"][0]["students"] == [PSEUDONYM]
    assert writes == []
    assert REAL_ID not in _blob(question_result) and REAL_NAME not in _blob(question_result)

    result = tools.submit_scoring_results(
        "session-1", _result(12), digest, review_digest="review-1",
        answers={"score_above_possible": "post_anyway"})
    assert writes == ["verified write"]
    assert result["counts"]["finalized"] == 1


def test_stale_review_and_malformed_results_fail_closed_without_write(
    monkeypatch, tmp_path, _set_active_courses,
):
    _wire(monkeypatch, tmp_path, _set_active_courses)
    from api.powergrader import scoring_apply
    writes = []
    question = {"id": "score_above_possible", "kind": "score_above_possible",
                "detail": "Score exceeds the available points.", "user_ids": [REAL_ID],
                "options": ["post_anyway", "skip_those"]}
    monkeypatch.setattr(scoring_apply, "build_plan", lambda *_a, **_kw: {
        "ok": True, "candidate_ids": [REAL_ID], "questions": [question],
        "digest": "review-1", "notes": [],
    })
    monkeypatch.setattr(scoring_apply, "apply_plan", lambda *_a, **_kw: (
        writes.append(True) or ({"ok": True, "pushed": [REAL_ID], "results": []}, 200)))
    bundle = json.loads((tmp_path / "safe-bundle.json").read_text(encoding="utf-8"))
    digest = _digest(bundle)

    stale = tools.submit_scoring_results("session-1", _result(12), digest,
                                         review_digest="stale", answers={"score_above_possible": "post_anyway"})
    malformed = tools.submit_scoring_results("session-1", [{"pseudonym": PSEUDONYM}], digest)
    assert stale["code"] == "review_changed"
    assert malformed["code"] == "invalid_results"
    assert writes == []


def test_new_quiz_uses_same_submit_and_hides_internal_finalize_coordinates(
    monkeypatch, tmp_path, _set_active_courses,
):
    _wire(monkeypatch, tmp_path, _set_active_courses, new_quiz=True)
    calls = []

    def preview(session_id):
        calls.append(("review", session_id))
        return {"ok": True, "operation_id": "private-operation", "review_digest": "private-digest"}

    def apply(operation_id, review_digest):
        calls.append(("finalize", operation_id, review_digest))
        return {"ok": True, "operation_id": operation_id,
                "counts": {"finalized": 1, "already_applied": 0, "failed": 0},
                "results": [{"pseudonym": PSEUDONYM, "status": "finalized"}]}

    monkeypatch.setattr(tools, "_prepare_new_quiz_finalization", preview)
    monkeypatch.setattr(tools, "_finalize_new_quiz_results", apply)
    bundle = json.loads((tmp_path / "safe-bundle.json").read_text(encoding="utf-8"))

    result = tools.submit_scoring_results("session-1", _result(), _digest(bundle))

    assert calls == [("review", "session-1"), ("finalize", "private-operation", "private-digest")]
    assert result["counts"]["finalized"] == 1
    assert "operation_id" not in result and "private-operation" not in _blob(result)
    assert REAL_ID not in _blob(result) and REAL_NAME not in _blob(result)
