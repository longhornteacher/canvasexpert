"""Direct QuizForge planning and its teacher-facing preview boundary."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import qf_pusher, transform
from api.operation_ledger.adapters.quiz import _build_plan

QUIZ_FIXTURE = (Path(qf_pusher.__file__).parent / "qf_materials" / "qf quiz examples"
                / "ela7_lantern_formA.txt")


def _write_quiz(tmp_path, payload):
    import json
    path = tmp_path / "draft.txt"
    path.write_text("<QUIZFORGE_JSON>\n" + json.dumps(payload, ensure_ascii=False)
                    + "\n</QUIZFORGE_JSON>\n", encoding="utf-8")
    return path


def test_direct_planner_keeps_author_supplied_feedback_and_unicode():
    plan = qf_pusher.build_push_plan(QUIZ_FIXTURE)

    rendered = str(plan["items"])
    assert "A passage's central idea is the main point" in rendered
    assert "✓" not in rendered
    assert "✗" not in rendered


def test_quiz_plan_normalizes_title_and_item_text(tmp_path):
    path = _write_quiz(tmp_path, {
        "version": "3.0-json", "title": "Unit — check",
        "items": [{"id": "q1", "type": "TF", "points": 1,
                   "prompt": "The claim — is it true?", "answer": True}],
        "rationales": [],
    })

    plan = qf_pusher.build_push_plan(path)

    assert plan["title"] == "Unit - check"
    assert plan["quiz_payload"]["quiz"]["title"] == "Unit - check"
    assert "—" not in repr(plan)


@pytest.mark.parametrize("item_type", ["ESSAY", "FILEUPLOAD"])
def test_live_planner_rejects_writing_before_preparation_or_transform(
    monkeypatch, tmp_path, item_type,
):
    path = _write_quiz(tmp_path, {
        "version": "3.0-json", "title": "Writing", "rationales": [],
        "items": [{"id": "writing-1", "type": item_type, "points": 1, "prompt": "Write."}],
    })
    monkeypatch.setattr(qf_pusher, "prepare_items", lambda *_args, **_kwargs: pytest.fail(
        "writing reached item preparation"))
    monkeypatch.setattr(transform, "build_item", lambda *_args, **_kwargs: pytest.fail(
        "writing reached transformation"))

    with pytest.raises(ValueError) as excinfo:
        qf_pusher.build_push_plan(path)

    assert item_type in str(excinfo.value)
    assert "separate AssignmentForge assignment" in str(excinfo.value)
    assert "100 points" not in str(excinfo.value)


def test_direct_adapter_planner_preserves_subprocess_error_wording_and_redaction(tmp_path):
    missing = tmp_path / "Workspace with spaces" / "missing quiz.txt"

    with pytest.raises(ValueError) as excinfo:
        _build_plan(str(missing), {})

    message = str(excinfo.value)
    assert message.startswith("planner failed: plan error: ")
    assert "<path>" in message
    assert str(tmp_path) not in message


def test_direct_adapter_planner_uses_last_error_line_and_200_character_cap(monkeypatch):
    def fail(_path, _settings):
        raise ValueError("first line\n" + "x" * 300)

    monkeypatch.setattr(qf_pusher, "build_push_plan", fail)

    with pytest.raises(ValueError) as excinfo:
        _build_plan("quiz.txt", {})

    assert str(excinfo.value) == "planner failed: " + "x" * 200


def test_quiz_preview_returns_summary_without_canvas_configuration():
    from api.webui.server import app

    response = TestClient(app).post("/api/push/preview", data={
        "course_id": "101", "path": str(QUIZ_FIXTURE), "settings": "{}",
    })

    assert response.status_code == 200
    result = response.json()
    assert result["ok"] is True
    assert "Title: " in result["output"]
    assert "Engine: New Quiz" in result["output"]
    assert "Questions: " in result["output"]
    assert "Points: " in result["output"]
    assert "Settings: default settings" in result["output"]


def test_quiz_preview_reports_malformed_settings_in_existing_response_shape():
    from api.webui.server import app

    response = TestClient(app).post("/api/push/preview", data={
        "course_id": "101", "path": str(QUIZ_FIXTURE), "settings": "{",
    })

    assert response.status_code == 200
    assert response.json() == {"ok": False, "output": "plan error: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"}


def test_quiz_preview_reports_malformed_quiz_with_authored_error_text(tmp_path):
    from api.webui.server import app

    path = _write_quiz(tmp_path, {
        "version": "3.0-json", "title": "Missing points",
        "items": [{"id": "q1", "type": "TF", "prompt": "True?", "answer": True}],
        "rationales": [],
    })
    response = TestClient(app).post("/api/push/preview", data={
        "course_id": "101", "path": str(path), "settings": "{}",
    })

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "output": "plan error: TF item q1 needs an explicit finite, nonnegative points value.",
    }
