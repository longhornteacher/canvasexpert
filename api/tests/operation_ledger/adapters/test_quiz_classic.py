"""Classic Quiz delivery through the real Operation Ledger, against a stateful fake Canvas.

One law (existence is proven through the assignment), and three examples: a whole push,
a resume after an interrupted question write, and a Hub push. The fake Canvas and the
prepare/apply helper live in this directory's ``conftest.py``.
"""
import json

from api import content_push
from api.operation_ledger import executor, operations
from api.operation_ledger.adapters import quiz_classic

MC = {"id": "mc1", "type": "MC", "prompt": "<p>Which comes first?</p>",
      "points": 35,
      "choices": [{"id": "A", "text": "Alpha", "correct": True},
                  {"id": "B", "text": "Beta", "correct": False}]}
DROPDOWN = {"id": "dd1", "type": "FITB", "prompt": "The [blank] is the powerhouse.",
            "points": 35,
            "answer_mode": "dropdown", "options": ["mitochondria", "nucleus"],
            "accept": ["mitochondria"]}
ESSAY = {"id": "es1", "type": "ESSAY", "prompt": "<p>Explain your choice.</p>", "points": 20}
UPLOAD = {"id": "fu1", "type": "FILEUPLOAD", "prompt": "<p>Upload your diagram.</p>", "points": 10}
RATIONALES = [
    {"item_id": "mc1", "choices": [
        {"id": "A", "correct": True, "rationale": "Alpha is the first letter."},
        {"id": "B", "correct": False, "rationale": "Beta is the second letter."}]},
    {"item_id": "dd1", "rationale": "Mitochondria release the cell's energy."},
]


def _envelope(*items, **extra):
    return {"version": "3.0-json", "quiz_engine": "classic", "title": "Cell Check",
            "instructions": "<p>Answer every question.</p>", "items": list(items),
            "rationales": RATIONALES, **extra}


WHOLE = _envelope(MC, DROPDOWN, ESSAY, UPLOAD)
SETTINGS = {"published": True, "module_name": "Unit 1", "assignment_group_name": "Quizzes",
            "post_to_sis": True, "due_at": "2026-10-01T23:59:00Z"}


def _steps(operation_id):
    return operations.get_operation(operation_id)["targets"][0]["steps"]


def test_whole_push_runs_the_checkpointed_steps_in_order(classic_push, classic_canvas):
    adapter, operation_id, result = classic_push(WHOLE, SETTINGS)

    assert result["ok"] is True and result["status"] == "applied"
    assert [step["step_key"] for step in _steps(operation_id)] == [
        "create_quiz:0", "create_question:0:1", "create_question:0:2", "create_question:0:3",
        "create_question:0:4", "save_quiz:0", "patch_assignment:0", "attach_module:0",
        "publish_quiz:0"]
    assert [(method, path.rsplit("/", 1)[-1]) for method, path, _ in classic_canvas.sends] == [
        ("POST", "quizzes"), ("POST", "questions"), ("POST", "questions"), ("POST", "questions"),
        ("POST", "questions"), ("PUT", "9001"), ("PUT", "501"), ("POST", "items"), ("PUT", "9001")]

    create = classic_canvas.writes("POST", "/quizzes")[0][2]["quiz"]
    assert create["published"] is False and create["only_visible_to_overrides"] is False
    assert create["quiz_type"] == "assignment"
    questions = classic_canvas.questions["9001"]
    assert [q["question_type"] for q in questions] == [
        "multiple_choice_question", "multiple_dropdowns_question", "essay_question",
        "file_upload_question"]
    assert [q["points_possible"] for q in questions] == [35.0, 35.0, 20.0, 10.0]
    assert questions[0]["answers"][0]["answer_comment_html"] == "<p>Alpha is the first letter.</p>"
    assert questions[1]["neutral_comments_html"] == "<p>Mitochondria release the cell's energy.</p>"

    # Points are computed by the settings save, and the step proves them before moving on.
    assert classic_canvas.quizzes["9001"]["points_possible"] == 100.0
    assert classic_canvas.quizzes["9001"]["due_at"] == "2026-10-01T23:59:00Z"
    # The patch addresses the quiz's assignment, never the quiz id.
    assert classic_canvas.writes("PUT", "/assignments/501")[0][2] == {
        "assignment": {"post_to_sis": True, "assignment_group_id": 7}}
    assert classic_canvas.module_items["10"] == [
        {"id": 3001, "title": "Cell Check", "type": "Quiz", "content_id": 9001}]
    assert classic_canvas.writes("PUT", "/quizzes/9001")[-1][2] == {"quiz": {"published": True}}

    assert result["target_results"][0]["returned_object_id"] == "9001"
    assert result["target_results"][0]["returned_object_url"] == "https://canvas.invalid/courses/101/quizzes/9001"
    projection = content_push._result_projection(operations.get_operation(operation_id), result)
    assert projection["verify_hint"] == [{"course_id": "101", "kind": "quiz", "id": "501"}]

    target = operations.get_operation(operation_id)["targets"][0]
    payload = operations.get_operation(operation_id)["normalized_payload"]
    settled = adapter.reconcile(payload, target, {})
    assert settled["state"] == "applied" and settled["module_item_id"] == "3001"


def test_reconcile_treats_a_quiz_as_missing_when_its_assignment_is_gone(classic_push, classic_canvas):
    adapter, operation_id, _result = classic_push(WHOLE, SETTINGS)
    operation = operations.get_operation(operation_id)
    payload, target = operation["normalized_payload"], operation["targets"][0]
    classic_canvas.send("DELETE", "/api/v1/courses/101/quizzes/9001", {})

    quiz_answers, _error = classic_canvas.get("/api/v1/courses/101/quizzes/9001")
    assert quiz_answers is not None  # the deleted quiz still answers its own GET
    assert adapter.reconcile(payload, target, {})["state"] == "sent_unknown"
    untouched = {**target, "steps": [{"step_key": "create_quiz:0", "state": "applied",
                                      "returned_object_id": "9001", "assignment_id": "501"}]}
    assert adapter.reconcile(payload, untouched, {})["state"] == "pending"


def test_reconcile_requires_the_exact_question_set_and_computed_points(classic_push, classic_canvas):
    adapter, operation_id, _result = classic_push(WHOLE, SETTINGS)
    operation = operations.get_operation(operation_id)
    payload, target = operation["normalized_payload"], operation["targets"][0]
    quiz_questions, quiz = classic_canvas.questions["9001"], classic_canvas.quizzes["9001"]

    quiz_questions.append({"id": 9999, "question_type": "essay_question", "points_possible": 0})
    assert adapter.reconcile(payload, target, {})["state"] == "sent_unknown"      # a stray question
    quiz_questions.pop()
    quiz["points_possible"] = None
    assert adapter.reconcile(payload, target, {})["state"] == "sent_unknown"      # points never computed
    quiz["points_possible"] = 100.0
    assert adapter.reconcile(payload, target, {})["state"] == "applied"


def test_resume_after_an_interrupted_question_write_adds_no_duplicate(classic_push, classic_canvas):
    classic_canvas.lose_response = ("POST", "/questions", 4)   # the write lands, the reply does not

    adapter, operation_id, first = classic_push(WHOLE, SETTINGS)

    assert first["target_results"][0]["state"] == "sent_unknown"
    assert len(classic_canvas.questions["9001"]) == 4 and not classic_canvas.writes("PUT", "/quizzes/9001")

    resumed = executor.retry_operation(operation_id)

    assert resumed["status"] == "applied"
    assert len(classic_canvas.writes("POST", "/quizzes")) == 1
    assert len(classic_canvas.writes("POST", "/questions")) == 4
    assert len(classic_canvas.questions["9001"]) == 4
    assert classic_canvas.quizzes["9001"]["points_possible"] == 100.0
    assert classic_canvas.quizzes["9001"]["published"] is True


def test_a_definitive_question_rejection_rolls_back_the_quiz_made_in_this_run(classic_push, classic_canvas):
    classic_canvas.reject = ("POST", "/questions")

    _adapter, operation_id, result = classic_push(WHOLE, SETTINGS)

    target = result["target_results"][0]
    assert target["state"] == "failed" and target["error_code"] == "question_rejected"
    assert target["rollback_state"] == "applied" and target["cleanup_required"] is False
    assert target["failed_items"][0]["item_index"] == 1 and target["failed_items"][0]["canvas_status"] == 422
    assert "501" not in classic_canvas.assignments
    assert [step["step_key"] for step in _steps(operation_id)][-2:] == [
        "create_question:0:1", "rollback_quiz:0"]


def test_hub_push_links_restricted_tier_pages_from_the_quiz_description(classic_push, classic_canvas):
    hub = _envelope(MC, ESSAY, differentiation="hub", tiers=[
        {"label": "Support", "supports": {"sentence_frames": ["I chose ___ because ___."]}},
        {"label": "Core", "supports": {"word_bank": ["claim", "evidence"]}},
    ])
    hub["rationales"] = [RATIONALES[0]]

    adapter, operation_id, prepared = classic_push(hub, {"published": True}, apply=False)
    review = prepared["review"]
    assert review["hub"]["teacher_actions"] == [
        "Assign page 'Cell Check - Silver' to Canvas differentiation tag 'Silver'."]
    assert "matched_group_id" not in json.dumps(review) and "11" not in json.dumps(review["hub"])
    assert review["writing_item_count"] == 1 and review["quiz_engine"] == "classic"
    assert review["teacher_note"] == quiz_classic.TEACHER_NOTE

    batch = prepared["batch"]
    result = executor.apply_operation(operation_id, batch["batch_id"], batch["review_digest"])

    assert result["status"] == "applied"
    assert classic_canvas.pages["101"]["title"] == "Cell Check - Silver"
    assert classic_canvas.pages["102"]["title"] == "Cell Check - Red"
    # Tag "Silver" has no live match, so its page stays restricted with nobody assigned;
    # "Red" matched, so exactly that tag group is assigned. Both are then published.
    assert classic_canvas.dates["101"] == {"visible_to_everyone": False, "overrides": []}
    assert classic_canvas.dates["102"] == {"visible_to_everyone": False, "overrides": [{"group_id": 11}]}
    assert classic_canvas.pages["101"]["published"] and classic_canvas.pages["102"]["published"]
    # Pages exist before the quiz, and the quiz description carries every verified href.
    methods = [(method, path.rsplit("/", 1)[-1]) for method, path, _ in classic_canvas.sends]
    assert methods.index(("POST", "quizzes")) > max(
        i for i, entry in enumerate(methods) if entry == ("POST", "pages"))
    description = classic_canvas.quizzes["9001"]["description"]
    assert "/courses/101/pages/support-page-1" in description
    assert "/courses/101/pages/support-page-2" in description
    assert "{{ce-tier-page" not in description and description.startswith("<p>Answer every question.</p>")
    assert classic_canvas.quizzes["9001"]["title"] == "Cell Check"

    projection = content_push._result_projection(operations.get_operation(operation_id), result)
    shown = projection["targets"][0]["hub"]
    assert shown["teacher_actions"] == review["hub"]["teacher_actions"]
    assert [tier["page_id"] for tier in shown["tiers"]] == ["101", "102"]
    assert shown["assignment"]["assignment_id"] == "501" and shown["assignment"]["published"] is True
    assert {"course_id": "101", "kind": "quiz", "id": "501"} in projection["verify_hint"]
    settled = adapter.reconcile(operations.get_operation(operation_id)["normalized_payload"],
                                operations.get_operation(operation_id)["targets"][0], {})
    assert settled["state"] == "applied"
