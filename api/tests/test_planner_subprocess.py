"""The planner subprocess boundary itself: encoding, stdin, and diagnosis."""
import os
import json
import subprocess

import pytest

from api.webui import runner
from api import qf_pusher

# Tracked QuizForge sample with teacher-authored, optional per-choice feedback.
QUIZ_FIXTURE = os.path.join(
    runner.API_DIR, "qf_materials", "qf quiz examples", "ela7_lantern_formA.txt"
)
RATIONALE_GLYPHS = ("\u2713", "\u2717")


def _plan(path=QUIZ_FIXTURE, **kwargs):
    return runner.run_json_object(
        ["qf_pusher.py", path, "--plan-json"],
        extra_env={"QF_PUSH_SETTINGS": "{}", **kwargs},
    )


def test_fixture_keeps_only_teacher_authored_feedback():
    """Optional authored explanations reach the plan without generated verdicts."""
    plan = _plan()

    rendered = str(plan["items"])
    assert "A passage's central idea is the main point" in rendered
    assert not any(glyph in rendered for glyph in RATIONALE_GLYPHS)


def test_plan_round_trips_author_supplied_glyph_feedback(tmp_path):
    """Teacher-authored Unicode feedback survives the Windows child boundary."""
    path = tmp_path / "authored-feedback.quizforge.txt"
    path.write_text(
        "<QUIZFORGE_JSON>\n" + json.dumps({
            "version": "3.0-json",
            "title": "Unicode feedback",
            "items": [{
                "id": "q1", "type": "MC", "points": 1,
                "prompt": "Pick one.",
                "choices": [
                    {"id": "A", "text": "First", "correct": True},
                    {"id": "B", "text": "Second", "correct": False},
                ],
            }],
            "rationales": [{"item_id": "q1", "choices": [
                    {"id": "A", "rationale": "✓ Authored explanation; keep it exact."},
            ]}],
        }, ensure_ascii=False) + "\n</QUIZFORGE_JSON>\n",
        encoding="utf-8",
    )
    plan = _plan(str(path), PYTHONIOENCODING="cp1252")

    assert plan["version"] == 1
    feedback = plan["items"][0]["payload"]["item"]["entry"]["answer_feedback"]
    assert list(feedback.values()) == ["<p>✓ Authored explanation; keep it exact.</p>"]
    assert "✗" not in str(feedback)


def test_quiz_plan_normalizes_title_and_item_text(tmp_path):
    path = tmp_path / "student-facing.quizforge.txt"
    path.write_text(
        "<QUIZFORGE_JSON>\n" + json.dumps({
            "version": "3.0-json",
            "title": "Unit \u2014 check",
            "items": [{
                "id": "q1", "type": "TF",
                "points": 1,
                "prompt": "The claim \u2014 is it true?", "answer": True,
            }],
            "rationales": [],
        }) + "\n</QUIZFORGE_JSON>\n",
        encoding="utf-8",
    )

    plan = qf_pusher.build_push_plan(path)

    assert plan["title"] == "Unit - check"
    assert plan["quiz_payload"]["quiz"]["title"] == "Unit - check"
    assert "\u2014" not in repr(plan)


def test_planner_child_gets_utf8_no_stdin_and_no_canvas_credentials(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, b'{"version":1}', b"")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setenv("CANVAS_TOKEN", "not-a-real-token")
    monkeypatch.setenv("CANVAS_BASE", "https://example.instructure.com")
    monkeypatch.setenv("COURSE_ID", "101")

    assert runner.run_json_object(["qf_pusher.py", "x", "--plan-json"]) == {"version": 1}

    assert seen["env"]["PYTHONIOENCODING"] == "utf-8"
    # The MCP server speaks JSON-RPC over its own stdin; a child must not
    # inherit that handle.
    assert seen["stdin"] is subprocess.DEVNULL
    for key in ("CANVAS_TOKEN", "CANVAS_BASE", "COURSE_ID"):
        assert key not in seen["env"], f"{key} reached a local planner"


def test_planner_failure_carries_its_reason_without_local_paths():
    """The bare words "planner failed" cannot be reported to a teacher.

    The sample folder also holds AssignmentForge and PageForge envelopes, so
    the wrong kind is a real refusal to describe, not a synthetic one.
    """
    wrong_kind = os.path.join(
        runner.API_DIR, "qf_materials", "qf quiz examples", "pf_unit_hub_sampler.txt"
    )

    with pytest.raises(ValueError) as excinfo:
        runner.run_json_object(["qf_pusher.py", wrong_kind, "--plan-json"])

    message = str(excinfo.value)
    assert message.startswith("planner failed: ")
    assert "plan error" in message
    assert runner.API_DIR not in message


@pytest.mark.parametrize("item_type", ["ESSAY", "FILEUPLOAD"])
def test_live_planner_rejects_writing_before_preparation_or_transform(
    monkeypatch, tmp_path, item_type,
):
    path = tmp_path / "writing-quiz.txt"
    path.write_text(
        "<QUIZFORGE_JSON>\n" + json.dumps({
            "version": "3.0-json",
            "title": "Major - Questions",
            "items": [{"id": "writing-1", "type": item_type, "points": 1, "prompt": "Write."}],
            "rationales": [],
        }) + "\n</QUIZFORGE_JSON>\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        qf_pusher, "prepare_items",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("writing reached item preparation")
        ),
    )
    monkeypatch.setattr(
        qf_pusher.transform, "build_item",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("writing reached transformation")
        ),
    )

    with pytest.raises(ValueError) as excinfo:
        qf_pusher.build_push_plan(path)

    assert item_type in str(excinfo.value)
    assert "separate AssignmentForge assignment" in str(excinfo.value)
    assert "100 points" not in str(excinfo.value)


@pytest.mark.parametrize("line, expected", [
    (rb"plan error: Expecting value: line 1 column 1 (char 0)",
     ": plan error: Expecting value: line 1 column 1 (char 0)"),
    (rb"plan error: unsupported item type 'matching_v2'",
     ": plan error: unsupported item type 'matching_v2'"),
    (rb"plan error: cannot read C:\Users\user\CE\inbox\ch1.txt",
     ": plan error: cannot read <path>"),
    (rb"plan error: cannot read /home/user/ce/inbox/ch1.txt",
     ": plan error: cannot read <path>"),
    (rb"", ""),
    (b"   \n  \n", ""),
])
def test_planner_detail_is_bounded_and_path_free(line, expected):
    assert runner._planner_detail(line) == expected


def test_planner_detail_redacts_every_segment_of_a_path_with_spaces():
    r"""A real workspace path holds a space ("...\Quiz Inbox\..."), and the
    redaction has to span the spaces inside it, so assert that no folder
    name survives rather than only that a ``<path>`` appeared."""
    detail = runner._planner_detail(
        rb"plan error: no such file 'C:\Users\user\CE Space\Quiz Inbox\ch1.txt'"
    )

    assert "<path>" in detail
    for leaked in ("C:", "user", "Space", "ch1.txt"):
        assert leaked not in detail, f"{leaked!r} survived redaction: {detail!r}"


def test_planner_detail_takes_the_last_line_and_caps_its_length():
    stderr = b"Traceback (most recent call last):\n  File x\n" + b"E" * 500
    detail = runner._planner_detail(stderr)

    assert detail.startswith(": EEE")
    assert len(detail) == 202  # ": " plus the 200-character cap
