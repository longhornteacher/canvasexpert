"""The planner subprocess boundary itself: encoding, stdin, and diagnosis.

Every other quiz test mocks ``run_json_object`` away, which is exactly how a
boundary bug survived: the parent decoded the child's stdout as utf-8 while
nothing told the child to produce it. On Windows a piped stdout encodes as
cp1252 and the planners print with ``ensure_ascii=False``, so the checkmark
and cross that QuizForge writes into every per-choice rationale raised
UnicodeEncodeError inside the child. The push came back as the bare words
"planner failed", and no quiz could be previewed or pushed at all.

These tests run the real subprocess, so they hold that boundary.
"""
import os
import subprocess

import pytest

from api.webui import runner

# Tracked QuizForge sample with per-choice rationales, so the plan carries the
# glyphs the renderer adds rather than any character the author typed.
QUIZ_FIXTURE = os.path.join(
    runner.API_DIR, "qf_materials", "qf quiz examples", "ela7_lantern_formA.txt"
)
RATIONALE_GLYPHS = ("\u2713", "\u2717")


def _plan(**kwargs):
    return runner.run_json_object(
        ["qf_pusher.py", QUIZ_FIXTURE, "--plan-json"],
        extra_env={"QF_PUSH_SETTINGS": "{}", **kwargs},
    )


def test_the_fixture_still_exercises_the_glyph_path():
    """Guard the guard: a sample that lost its rationales proves nothing."""
    plan = _plan()

    for glyph in RATIONALE_GLYPHS:
        assert glyph in str(plan["items"]), (
            "the sample quiz no longer carries the rationale glyphs this test "
            "exists to protect; point it at one that does"
        )


def test_plan_round_trips_glyphs_the_windows_codepage_cannot_encode():
    """The boundary's encoding is not the caller's or the host locale's choice.

    ``PYTHONIOENCODING`` is passed as cp1252 here on purpose: the runner must
    override it, because the parent hard-decodes utf-8. That makes this test
    fail on any platform if the override is removed, rather than only on a
    machine whose locale happens to be cp1252.
    """
    plan = _plan(PYTHONIOENCODING="cp1252")

    assert plan["version"] == 1
    assert plan["items"]
    for glyph in RATIONALE_GLYPHS:
        assert glyph in str(plan["items"])


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
