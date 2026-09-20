"""MCP preparation and direct session-resolution examples."""
from __future__ import annotations

from contextlib import nullcontext

import pytest

from api.mcp_server import tools
from api.powergrader import session_store


def test_prepare_requires_exact_current_scope(monkeypatch):
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1", "name": "Course"}])
    result = tools.prepare_scoring_session("c1", "")
    assert result["code"] == "invalid_scope"
    assert result["stage"] == "validate"
    assert result["retryable"] is False


def test_prepare_calls_one_full_refresh_and_returns_ready(monkeypatch):
    calls = []
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1", "name": "Course"}])

    def prepare(course_id, assignment_id, guidance, *, refresh_course, save_session=session_store.save_session):
        calls.append((course_id, assignment_id, guidance))
        assert refresh_course(course_id) is True
        return {"ok": True, "status": "ready", "scoring_session_id": "s1"}

    monkeypatch.setattr("api.powergrader.scoring_preparation.prepare_scoring_session", prepare)
    monkeypatch.setattr(tools, "_refresh_course_for_scoring", lambda course_id: calls.append(("refresh", course_id)) or True)
    result = tools.prepare_scoring_session("c1", "a1", "Writing")
    assert result["status"] == "ready"
    assert calls == [("c1", "a1", "Writing"), ("refresh", "c1")]


def test_prepare_refuses_to_resync_when_a_usable_session_is_open(monkeypatch):
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1"}])
    session = {
        "session_id": "s1", "course_id": "c1", "assignment_id": "a1",
        "session_kind": "scoring_assignment", "status": "ready",
    }
    monkeypatch.setattr(session_store, "current_actionable_session",
                        lambda _course, _assignment: session)
    monkeypatch.setattr(session_store, "packet_health",
                        lambda _session: {"ok": True})
    monkeypatch.setattr(tools, "_ensure_session_usable",
                        lambda _session: {"ok": True})
    monkeypatch.setattr(
        "api.powergrader.scoring_preparation.prepare_scoring_session",
        lambda *_args, **_kwargs: pytest.fail("duplicate preparation refreshed the mirror"),
    )

    result = tools.prepare_scoring_session("c1", "a1")

    assert result == {
        "ok": False,
        "code": "scoring_session_already_open",
        "stage": "prepare",
        "retryable": False,
        "scoring_session_id": "s1",
        "user_action": (
            "Use get_scoring_packet with the existing scoring_session_id, "
            "work locally on that snapshot, then submit once. Do not prepare "
            "or refresh this assignment again."
        ),
        "error": "A usable Scoring Session is already open for this assignment.",
    }


def test_prepare_allows_replacement_when_the_open_session_is_stale(monkeypatch):
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1"}])
    session = {
        "session_id": "s1", "course_id": "c1", "assignment_id": "a1",
        "session_kind": "scoring_assignment", "status": "ready",
    }
    monkeypatch.setattr(session_store, "current_actionable_session",
                        lambda _course, _assignment: session)
    monkeypatch.setattr(tools, "_ensure_session_usable",
                        lambda _session: {"ok": False, "code": "session_stale"})
    monkeypatch.setattr(session_store, "packet_health",
                        lambda _session: pytest.fail("stale session needs no packet check"))
    monkeypatch.setattr(
        "api.powergrader.scoring_preparation.prepare_scoring_session",
        lambda *_args, **_kwargs: {"ok": True, "status": "ready", "scoring_session_id": "s2"},
    )

    result = tools.prepare_scoring_session("c1", "a1")

    assert result["status"] == "ready"


def test_prepare_owner_exception_returns_safe_typed_failure(monkeypatch):
    private_text = "private student identity should never escape"
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1"}])

    def prepare(*_args, **_kwargs):
        raise RuntimeError(private_text)

    monkeypatch.setattr("api.powergrader.scoring_preparation.prepare_scoring_session", prepare)
    result = tools.prepare_scoring_session("c1", "a1")

    assert result["ok"] is False
    assert result["code"] == "safe_preparation_failed"
    assert result["stage"] == "prepare"
    assert result["retryable"] is True
    assert result["user_action"] == (
        "The SAFE scoring packet could not be prepared. Retry this exact assignment."
    )
    assert private_text not in str(result)
    assert "RuntimeError" not in str(result)


def test_list_ignores_unsupported_records_and_lists_assignment_sessions(monkeypatch):
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1"}])
    sessions = {
        "root": {"session_id": "root", "session_kind": "scoring_session", "course_id": "c1"},
        "legacy": {"session_id": "legacy", "session_kind": "legacy_root", "course_id": "c1"},
        "s1": {"session_id": "s1", "session_kind": "scoring_assignment", "course_id": "c1",
               "assignment_id": "a1", "assignment_name": "Essay",
               "created": "2026-01-01", "status": "ready",
               "students": [{"status": "approved", "posted": True}]},
    }
    monkeypatch.setattr(session_store, "list_session_summaries", lambda: [
        {"session_id": sid, "session_kind": value.get("session_kind"), "course_id": value.get("course_id"),
         "assignment_id": value.get("assignment_id"),
         "assignment_name": value.get("assignment_name"), "created": value.get("created"),
         "status": value.get("status"), "total": len(value.get("students") or []),
         "approved": 1, "posted": 1} for sid, value in sessions.items()
    ])
    monkeypatch.setattr(session_store, "load_session", lambda sid: sessions.get(sid))
    result = tools.list_scoring_sessions()
    assert [row[0] for row in result["sessions"]["rows"]] == ["s1"]
    assert "queue" not in str(result).lower()


def test_packet_rejects_historical_root_record_directly(monkeypatch):
    monkeypatch.setattr(session_store, "load_session", lambda _sid: {
        "session_id": "root", "session_kind": "scoring_session",
    })
    assert tools.get_scoring_packet("root")["code"] == "session_not_found"


# --- Superseded and non-current refusals ------------------------------------

def _scope_sessions(monkeypatch, **overrides):
    """Bind two actionable sessions for one exact scope against the store."""
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1"}])
    sessions = {
        "sess-old": {"session_id": "sess-old", "session_kind": "scoring_assignment",
                  "course_id": "c1", "assignment_id": "a1", "created": "2026-01-01",
                  "status": "ready", "privacy_artifacts": {}},
        "sess-new": {"session_id": "sess-new", "session_kind": "scoring_assignment",
                  "course_id": "c1", "assignment_id": "a1", "created": "2026-01-05",
                  "status": "ready", "privacy_artifacts": {}},
    }
    sessions.update(overrides)
    monkeypatch.setattr(session_store, "load_session", lambda sid: sessions.get(sid))
    monkeypatch.setattr(session_store, "save_session",
                        lambda value: sessions.__setitem__(value["session_id"], value))
    monkeypatch.setattr(session_store, "list_session_summaries", lambda: [
        {"session_id": value["session_id"], "session_kind": value.get("session_kind"),
         "course_id": value.get("course_id"), "assignment_id": value.get("assignment_id"),
         "created": value.get("created"), "status": value.get("status"),
         "assignment_name": "Essay", "total": 0, "approved": 0, "posted": 0}
        for value in sessions.values()
    ])
    return sessions


def test_list_returns_only_the_current_row_for_a_duplicated_scope(monkeypatch):
    _scope_sessions(monkeypatch)

    rows = tools.list_scoring_sessions()["sessions"]["rows"]

    assert [row[0] for row in rows] == ["sess-new"]


@pytest.mark.parametrize("tool", ["get_scoring_packet", "submit_scoring_results"])
def test_non_current_duplicate_is_refused_as_session_superseded(monkeypatch, tool):
    _scope_sessions(monkeypatch)

    if tool == "get_scoring_packet":
        result = tools.get_scoring_packet("sess-old")
    else:
        result = tools.submit_scoring_results("sess-old", [], "whatever")

    assert result["ok"] is False
    assert result["code"] == "session_superseded"
    assert "sess-old" not in str(result) and "sess-new" not in str(result)


def test_newer_terminal_duplicate_suppresses_older_ready_before_submit_planning(monkeypatch):
    _scope_sessions(monkeypatch, **{
        "sess-new": {"session_id": "sess-new", "session_kind": "scoring_assignment",
                  "course_id": "c1", "assignment_id": "a1", "created": "2026-01-05",
                  "status": "completed", "privacy_artifacts": {}},
    })
    from api import feedback_pipeline as fp
    from api.powergrader import scoring_apply

    monkeypatch.setattr(fp, "validate_results",
                        lambda *_a, **_kw: pytest.fail("validation must not run"))
    monkeypatch.setattr(scoring_apply, "build_plan",
                        lambda *_a, **_kw: pytest.fail("Canvas planning must not run"))

    result = tools.submit_scoring_results("sess-old", [{"pseudonym": "X"}], "digest")

    assert result["ok"] is False
    assert result["code"] == "session_superseded"


def test_superseded_record_is_refused_as_session_superseded(monkeypatch):
    _scope_sessions(monkeypatch, **{
        "sess-old": {"session_id": "sess-old", "session_kind": "scoring_assignment",
                  "course_id": "c1", "assignment_id": "a1", "created": "2026-01-01",
                  "status": "superseded", "privacy_artifacts": {}},
    })

    assert tools.get_scoring_packet("sess-old")["code"] == "session_superseded"
    assert tools.submit_scoring_results("sess-old", [], "whatever")["code"] == "session_superseded"


def test_submit_refusal_happens_before_result_validation_or_canvas_work(monkeypatch):
    _scope_sessions(monkeypatch, **{
        "sess-old": {"session_id": "sess-old", "session_kind": "scoring_assignment",
                  "course_id": "c1", "assignment_id": "a1", "created": "2026-01-01",
                  "status": "superseded", "privacy_artifacts": {}},
    })
    from api import feedback_pipeline as fp
    from api.powergrader import scoring_apply

    monkeypatch.setattr(fp, "validate_results",
                        lambda *_a, **_kw: pytest.fail("validation must not run"))
    monkeypatch.setattr(scoring_apply, "build_plan",
                        lambda *_a, **_kw: pytest.fail("Canvas planning must not run"))
    monkeypatch.setattr(tools, "_open_vault",
                        lambda: pytest.fail("re-identification must not run"))

    result = tools.submit_scoring_results("sess-old", [{"pseudonym": "X"}], "digest")

    assert result["code"] == "session_superseded"


def test_submit_holds_the_scope_lock_across_the_currentness_check(monkeypatch):
    _scope_sessions(monkeypatch)
    acquired = []

    class _TrackedScopeLock:
        def __enter__(self):
            acquired.append(True)
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(session_store, "scope_lock",
                        lambda course_id, assignment_id: acquired.append(
                            (course_id, assignment_id)) or _TrackedScopeLock())
    monkeypatch.setattr(session_store, "session_lock", lambda _sid: nullcontext())

    result = tools.submit_scoring_results("sess-new", [], "digest")

    # The lock is taken for the exact private scope before any validation.
    assert acquired[0] == ("c1", "a1")
    assert result["code"] != "session_superseded"
