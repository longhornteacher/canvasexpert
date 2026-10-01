"""The QuizForge planner: New Quiz output is frozen, classic output follows the declared engine."""
import json
from pathlib import Path

import pytest

from api import qf_pusher, transform

EXAMPLES = Path(qf_pusher.__file__).parent / "qf_materials" / "qf quiz examples"
GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "quiz_plan_new_engine_golden.json")
                    .read_text(encoding="utf-8"))
RICH_SETTINGS = {
    "shuffle_answers": False, "shuffle_questions": True, "access_code": "abc",
    "allow_multiple_attempts": True, "allowed_attempts": 3, "score_to_keep": "latest",
    "build_on_last_attempt": True, "attempt_cooldown": 5, "has_time_limit": True,
    "time_limit_minutes": 30, "calculator_type": "basic", "one_at_a_time": True,
    "allow_backtracking": False, "hide_results": True,
    "due_at": "2026-10-01T23:59:00Z", "unlock_at": "2026-09-30T08:00:00Z",
    "lock_at": "2026-10-02T23:59:00Z", "assignment_group_name": "Quizzes",
    "post_to_sis": True, "published": True, "module_name": "Unit 1",
}


def _write(tmp_path, payload):
    path = tmp_path / "draft.txt"
    path.write_text("<QUIZFORGE_JSON>\n" + json.dumps(payload) + "\n</QUIZFORGE_JSON>\n", encoding="utf-8")
    return path


def _tf(item_id, **extra):
    return {"id": item_id, "type": "TF", "prompt": "<p>True?</p>", "answer": True,
            "points": 1, **extra}


def _essay(item_id, points):
    return {"id": item_id, "type": "ESSAY", "prompt": "<p>Discuss.</p>", "points": points}


def _classic(items, **extra):
    return {"version": "3.0-json", "quiz_engine": "classic", "title": "Unit Check",
            "items": items, "rationales": [], **extra}


def test_new_engine_plan_is_unchanged_for_existing_files(monkeypatch):
    """The New Quiz plan is the contract every stored operation and receipt was built on."""
    counter = iter(range(10**6))
    monkeypatch.setattr(transform, "_u", lambda: f"uuid-{next(counter):06d}")
    built = {}
    for key, name, settings in (("all_types_rich_settings", "all_types_sampler.txt", RICH_SETTINGS),
                                ("lantern_form_a_default", "ela7_lantern_formA.txt", {})):
        plan = qf_pusher.build_push_plan(EXAMPLES / name, settings)
        plan.pop("source_path")
        built[key] = json.loads(json.dumps(plan))
    assert built == GOLDEN


@pytest.mark.parametrize("engine", [None, "new"])
@pytest.mark.parametrize("item_type", ["ESSAY", "FILEUPLOAD"])
def test_writing_never_reaches_a_new_quiz_plan(tmp_path, engine, item_type):
    payload = _classic([_tf("t1"), _essay("w1", 20)])
    payload["items"][1]["type"] = item_type
    payload.pop("quiz_engine")
    if engine:
        payload["quiz_engine"] = engine
    with pytest.raises(ValueError, match="cannot be pushed as a Canvas New Quiz"):
        qf_pusher.build_push_plan(_write(tmp_path, payload))


@pytest.mark.parametrize(("items", "expected_points"), [
    ([_tf("a", points=0.25), _tf("b", points=0), _tf("c", points=2.125)], [0.25, 0.0, 2.125]),
    ([_tf("a", points=0.5), _tf("b", points=1.25), _essay("w", 40)], [0.5, 1.25, 40.0]),
    ([_tf("a", points=5), _tf("b", points=0), _essay("w", 20)], [5.0, 0.0, 20.0]),
    ([_essay("w", 30)], [30.0]),
])
def test_classic_plan_preserves_each_authored_point_value(tmp_path, items, expected_points):
    plan = qf_pusher.build_push_plan(_write(tmp_path, _classic(items)))

    assert [row["payload"]["question"]["points_possible"] for row in plan["items"]] == expected_points
    assert plan["quiz_payload"]["quiz"]["points_possible_expected"] == pytest.approx(sum(expected_points))


def test_new_quiz_preserves_authored_fractional_zero_points_without_rationales(tmp_path):
    payload = {"version": "3.0-json", "title": "Fractions", "items": [
        _tf("a", points=0.25), _tf("b", points=0), _tf("c", points=2.125),
    ]}
    plan = qf_pusher.build_push_plan(_write(tmp_path, payload))

    assert [row["payload"]["item"]["points_possible"] for row in plan["items"]] == [0.25, 0, 2.125]
    assert plan["quiz_payload"]["quiz"]["points_possible"] == pytest.approx(2.375)


def test_new_quiz_preserves_optional_partial_authored_feedback(tmp_path):
    authored = "Ask the teacher. " + ("This is incorrect. " * 40)
    payload = {"version": "3.0-json", "title": "Feedback", "items": [{
        "id": "q1", "type": "MC", "prompt": "<p>Pick one.</p>", "points": 0.25,
        "choices": [{"id": "A", "text": "Alpha", "correct": True},
                    {"id": "B", "text": "Beta", "correct": False}],
    }], "rationales": [{"item_id": "q1", "choices": [
        {"id": "A", "rationale": authored},
    ]}]}

    plan = qf_pusher.build_push_plan(_write(tmp_path, payload))

    item = qf_pusher.prepare_items(payload)[0]
    assert item["_rationale"]["choices"][0]["rationale"] == authored
    feedback = plan["items"][0]["payload"]["item"]["entry"]["answer_feedback"]
    assert authored.strip() in json.dumps(feedback)


def test_new_quiz_rejects_malformed_optional_feedback_references(tmp_path):
    payload = {"version": "3.0-json", "title": "Feedback", "items": [
        _tf("q1"),
    ], "rationales": [{"item_id": "missing", "rationale": "Optional text."}]}

    with pytest.raises(ValueError, match="must match exactly one quiz item"):
        qf_pusher.build_push_plan(_write(tmp_path, payload))


def test_classic_optional_total_points_is_only_a_consistency_check(tmp_path):
    payload = _classic([_tf("a", points=0.25), _tf("b", points=0)], total_points=0.25)
    plan = qf_pusher.build_push_plan(_write(tmp_path, payload))
    assert plan["quiz_payload"]["quiz"]["points_possible_expected"] == 0.25

    payload["total_points"] = 100
    path = _write(tmp_path, payload)
    with pytest.raises(ValueError, match="total_points"):
        qf_pusher.build_push_plan(path)


@pytest.mark.parametrize("setting", [
    {"calculator_type": "basic"}, {"build_on_last_attempt": True},
    {"attempt_cooldown": 5}, {"score_to_keep": "first"}, {"shuffle_questions": True},
])
def test_classic_refuses_push_settings_it_cannot_honor(tmp_path, setting):
    with pytest.raises(ValueError, match="no Classic Quiz equivalent"):
        qf_pusher.build_push_plan(_write(tmp_path, _classic([_tf("a")])), setting)


def test_classic_plan_maps_push_settings_onto_classic_quiz_fields(tmp_path):
    settings = {"allow_multiple_attempts": True, "allowed_attempts": 3, "score_to_keep": "average",
                "has_time_limit": True, "time_limit_minutes": 45, "one_at_a_time": True,
                "allow_backtracking": False, "access_code": " open ", "hide_results": True,
                "shuffle_answers": False, "due_at": "2026-10-01T23:59:00Z", "published": True,
                "assignment_group_name": "Quizzes", "post_to_sis": False, "module_name": "Unit 1"}
    path = _write(tmp_path, _classic([_tf("a")], instructions="<p>Read all.</p>"))

    plan = qf_pusher.build_push_plan(path, settings)

    assert plan["version"] == 1 and plan["quiz_engine"] == "classic"
    assert plan["quiz_payload"]["quiz"] == {
        "title": "Unit Check", "description": "<p>Read all.</p>", "quiz_type": "assignment",
        "published": False, "shuffle_answers": False, "allowed_attempts": 3,
        "scoring_policy": "keep_average", "one_question_at_a_time": True, "cant_go_back": True,
        "show_correct_answers": False, "points_possible_expected": 1.0, "time_limit": 45,
        "access_code": "open", "hide_results": "always", "due_at": "2026-10-01T23:59:00Z"}
    assert plan["assignment_settings"] == {
        "published": True, "assignment_group_name": "Quizzes", "post_to_sis": False}
    assert plan["module"] == {"module_name": "Unit 1"}
