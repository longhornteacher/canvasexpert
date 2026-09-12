"""Tests for the start_scoring_session MCP tool.

start_scoring_session is a thin wrapper over
api.powergrader.start_workflow.run_start_session: it never reimplements
session-start orchestration, only locks the call down to packet mode with
every optional side effect turned off, then reads back the session it just
saved for the assistant-facing summary. run_start_session itself is stubbed
throughout, since exercising the real assignment-refresh/mirror path belongs
to start_workflow's own tests.
"""
from __future__ import annotations

import json

import pytest

from api.mcp_server import tools


def _fake_run_start_session(*, captured=None, session_id="sess-1",
                            assignment_name="Essay 1", student_count=2,
                            ok=True, error=""):
    def fake(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        if not ok:
            return {"ok": False, "payload": {"ok": False, "error": error, "privacy_steps": []}}
        return {
            "ok": True,
            "payload": {
                "ok": True,
                "session_id": session_id,
                "student_count": student_count,
                "assignment_name": assignment_name,
                "mode": "packet",
                "mode_label": "AI chat",
                "ai_scored": 0,
                "privacy_steps": [],
                "packet_zip": None,
                "copilot_batch_count": 0,
                "copilot_packet_folder": None,
                "evidence_status": "ok",
            },
            "session_id": session_id,
            "mode": "packet",
            "auto_post_enabled": False,
        }
    return fake


def _fake_session(session_id, course_id, *, new_quiz_supported=False, bundle_path=None):
    session = {
        "session_id": session_id,
        "course_id": course_id,
        "mode": "packet",
        "scoring_basis": {"source": "canvas_rubric", "label": "Canvas rubric"},
        "privacy_artifacts": {},
        "students": [],
    }
    if bundle_path:
        session["privacy_artifacts"]["safe_bundle"] = bundle_path
    return session


def _write_bundle(tmp_path, students, name="bundle.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"students": students}), encoding="utf-8")
    return str(path)


def _bind_session_store(monkeypatch, sessions: dict):
    monkeypatch.setattr("api.powergrader.session_store.load_session",
                        lambda sid: sessions.get(sid))
    monkeypatch.setattr("api.powergrader.session_store.save_session",
                        lambda s: sessions.__setitem__(s["session_id"], s))


def _refuse_if_called(**_kwargs):
    raise AssertionError("run_start_session must not be called")


# --- example: packet-mode session created end to end ------------------------

def test_start_scoring_session_creates_packet_session_end_to_end(monkeypatch, tmp_path, _set_active_courses):
    _set_active_courses(["111"])
    captured = {}
    monkeypatch.setattr(
        "api.powergrader.start_workflow.run_start_session",
        _fake_run_start_session(captured=captured, session_id="sess-1",
                                assignment_name="Essay 1", student_count=2),
    )
    bundle_path = _write_bundle(tmp_path, [
        {"pseudonym": "Pikachu", "responses": [
            {"item_id": "i1", "response": "answer one", "prompt": "Q1", "possible": 10},
            {"item_id": "i2", "response": "answer two", "prompt": "Q2", "possible": 10},
        ]},
        {"pseudonym": "Eevee", "responses": [
            {"item_id": "i1", "response": "answer three", "prompt": "Q1", "possible": 10},
        ]},
    ])
    sessions = {"sess-1": _fake_session("sess-1", "111", new_quiz_supported=True, bundle_path=bundle_path)}
    _bind_session_store(monkeypatch, sessions)

    result = tools.start_scoring_session("111", "700010")

    assert result == {
        "ok": True,
        "scoring_session_id": "sess-1",
        "assignment_name": "Essay 1",
        "student_count": 2,
        # 2 students x 2 items + 1 student x 1 item = 3 scorable rows, the
        # same "total" get_scoring_packet would report for this bundle. This
        # is deliberately not equal to student_count, proving the count is
        # read from the SAFE bundle rather than echoed from student_count.
        "response_count": 3,
        # Every response carried text, so nothing is held and no pseudonym
        # rides along in the summary.
        "held": 0,
        "scoring_basis": {"source": "canvas_rubric", "label": "Canvas rubric"},
        "next": tools._NEXT_STEPS["start_scoring_session"],
    }
    assert captured["course_id"] == "111"
    assert captured["assignment_id"] == "700010"


def test_start_scoring_session_refuses_to_expose_a_session_without_a_safe_bundle(monkeypatch, _set_active_courses):
    _set_active_courses(["111"])
    monkeypatch.setattr(
        "api.powergrader.start_workflow.run_start_session",
        _fake_run_start_session(session_id="sess-2", assignment_name="Quiz 1", student_count=5),
    )
    sessions = {"sess-2": _fake_session("sess-2", "111", new_quiz_supported=False, bundle_path=None)}
    _bind_session_store(monkeypatch, sessions)

    result = tools.start_scoring_session("111", "700020")

    assert result == {
        "ok": False, "code": "packet_missing",
        "error": "The SAFE scoring packet was not completed; no session was exposed.",
    }


# --- example: held work is reported, not silently counted as nothing --------

def _held_bundle(tmp_path, pseudonyms, *, scorable=()):
    """A bundle whose responses carry no text, as attachment-only work does."""
    students = [
        {"pseudonym": name, "responses": [
            {"item_id": "i1", "response": "", "prompt": "Q1", "possible": 100},
        ]}
        for name in pseudonyms
    ]
    students += [
        {"pseudonym": name, "responses": [
            {"item_id": "i1", "response": "real answer", "prompt": "Q1", "possible": 100},
        ]}
        for name in scorable
    ]
    return _write_bundle(tmp_path, students, name="held-bundle.json")


def test_start_scoring_session_names_held_students_instead_of_reporting_an_empty_session(
    monkeypatch, tmp_path, _set_active_courses, _use_vault,
):
    """The reported failure mode: an attachment-only assignment came back as
    response_count 0 against 19 students with no reason given, which reads as
    an empty assignment rather than work that is deliberately kept local."""
    _set_active_courses(["111"])
    monkeypatch.setattr(
        "api.powergrader.start_workflow.run_start_session",
        _fake_run_start_session(session_id="sess-held", assignment_name="My Poem",
                                student_count=3),
    )
    bundle_path = _held_bundle(tmp_path, ["Pikachu", "Eevee", "Snorlax"])
    sessions = {"sess-held": _fake_session("sess-held", "111", bundle_path=bundle_path)}
    _bind_session_store(monkeypatch, sessions)

    result = tools.start_scoring_session("111", "700030")

    assert result["ok"] is True
    assert result["scoring_session_id"] == "sess-held"
    assert result["response_count"] == 0
    assert result["held"] == 3
    assert "held_pseudonyms" not in result
    # The one static hint has to carry the held branch too, so a held-only
    # session is never left reading as an empty assignment.
    assert "held responses could not be scored from text" in result["next"]


def test_start_scoring_session_keeps_the_standard_hint_when_some_work_is_scorable(
    monkeypatch, tmp_path, _set_active_courses, _use_vault,
):
    """Partly-held is still a scoring session: the packet reports held itself."""
    _set_active_courses(["111"])
    monkeypatch.setattr(
        "api.powergrader.start_workflow.run_start_session",
        _fake_run_start_session(session_id="sess-mixed", student_count=3),
    )
    bundle_path = _held_bundle(tmp_path, ["Pikachu"], scorable=["Eevee", "Snorlax"])
    sessions = {"sess-mixed": _fake_session("sess-mixed", "111", bundle_path=bundle_path)}
    _bind_session_store(monkeypatch, sessions)

    result = tools.start_scoring_session("111", "700040")

    assert result["response_count"] == 2
    assert result["held"] == 1
    assert "held_pseudonyms" not in result
    assert result["next"] == tools._NEXT_STEPS["start_scoring_session"]


def test_start_scoring_session_drops_held_names_rather_than_the_scoring_session_id(
    monkeypatch, tmp_path, _set_active_courses,
):
    """The names take the outbound scan every student-data read takes. When it
    cannot run, the session is already on disk, so losing its scoring_session_id
    would strand it: the count survives and only the names go."""
    _set_active_courses(["111"])
    monkeypatch.setattr(
        "api.powergrader.start_workflow.run_start_session",
        _fake_run_start_session(session_id="sess-novault", student_count=2),
    )
    bundle_path = _held_bundle(tmp_path, ["Pikachu", "Eevee"])
    sessions = {"sess-novault": _fake_session("sess-novault", "111", bundle_path=bundle_path)}
    _bind_session_store(monkeypatch, sessions)

    def _unavailable():
        raise tools._VaultUnavailable("Identity Vault is unavailable.")

    monkeypatch.setattr(tools, "_vault_factory", _unavailable)

    result = tools.start_scoring_session("111", "700050")

    assert result["ok"] is True
    assert result["scoring_session_id"] == "sess-novault"
    assert result["held"] == 2
    assert "held_pseudonyms" not in result


# --- laws: refusal behaviour --------------------------------------------------

def test_start_scoring_session_refuses_a_non_current_course_without_starting_one(monkeypatch, _set_active_courses):
    _set_active_courses(["222"])  # "111" is not Current
    monkeypatch.setattr("api.powergrader.start_workflow.run_start_session", _refuse_if_called)

    result = tools.start_scoring_session("111", "700010")

    assert result["ok"] is False
    assert "not a Current course" in result["error"]


@pytest.mark.parametrize("error_text", [
    "No submissions found for this assignment.",
    "No workspace configured, finish setup first.",
])
def test_start_scoring_session_surfaces_run_start_session_refusals_verbatim(
    monkeypatch, _set_active_courses, error_text,
):
    _set_active_courses(["111"])
    monkeypatch.setattr(
        "api.powergrader.start_workflow.run_start_session",
        _fake_run_start_session(ok=False, error=error_text),
    )

    result = tools.start_scoring_session("111", "700010")

    assert result == {"ok": False, "code": "start_failed", "error": error_text}


# --- law: this tool can never open an assisted session or enable auto_post --

def test_start_scoring_session_never_requests_assisted_mode_or_auto_post(monkeypatch, _set_active_courses):
    """Pin the security-relevant property directly at the run_start_session call.

    assisted mode would run the teacher's own AI key from a chat request, and
    auto_post can post to Canvas on a trigger; this tool must never be able
    to reach either, no matter what a future caller passes in (today it takes
    only course_id/assignment_id, so there is nothing to pass).
    """
    _set_active_courses(["111"])
    captured = {}
    monkeypatch.setattr(
        "api.powergrader.start_workflow.run_start_session",
        _fake_run_start_session(captured=captured),
    )
    sessions = {"sess-1": _fake_session("sess-1", "111")}
    _bind_session_store(monkeypatch, sessions)

    tools.start_scoring_session("111", "700010")

    assert captured["mode"] == "packet"
    assert captured["auto_post"] == "false"
    assert captured["source_uploads"] is None
    assert captured["source_files_json"] == ""
    assert captured["source_text"] == ""
    assert captured["oral_reading_enabled"] == "false"
    assert captured["oral_reading_passage"] == ""


# --- server wiring ------------------------------------------------------------

def test_start_scoring_session_is_registered_and_wraps_the_tool(monkeypatch):
    from api.mcp_server import server

    monkeypatch.setattr(
        tools, "start_scoring_session",
        lambda course_id, assignment_id, rubric_name="", scoring_guidance="": {
            "ok": True, "course_id_seen": course_id, "assignment_id_seen": assignment_id,
        },
    )
    wire = server.start_scoring_session("111", "700010")
    assert wire == '{"ok":true,"course_id_seen":"111","assignment_id_seen":"700010"}'
