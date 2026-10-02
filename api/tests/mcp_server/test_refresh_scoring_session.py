"""MCP contracts for refresh_scoring_session and the refreshed stage/apply path."""
from __future__ import annotations

import json

import pytest

from api.mcp_server import tools
from api.powergrader import scoring_preparation, session_store


def _results(*pseudonyms, score=10):
    return [{"pseudonym": name, "item_id": "a1", "score": score,
             "feedback": "Clear reasoning throughout the response."}
            for name in pseudonyms]


def _two_student_session(world):
    world.add("900001", "Synthetic First")
    world.add("900002", "Fictional Omega")
    return world.prepare()


def _owner_must_not_run(monkeypatch):
    monkeypatch.setattr(scoring_preparation, "refresh_scoring_session",
                        lambda *_a, **_kw: pytest.fail("the owner ran"))


def test_refresh_tool_refuses_unknown_and_non_current_sessions_before_the_owner(
    scoring_refresh_world, monkeypatch,
):
    """CONTRACT: lookup, currentness, lease, and course gate all precede the owner."""
    world = scoring_refresh_world
    first = _two_student_session(world)
    _owner_must_not_run(monkeypatch)

    assert tools.refresh_scoring_session("missing-id")["code"] == "session_not_found"

    monkeypatch.setattr(tools, "_scoring_work_lease_refusal",
                        lambda _sid: {"ok": False, "code": "work_item_held_elsewhere"})
    assert tools.refresh_scoring_session(first)["code"] == "work_item_held_elsewhere"
    monkeypatch.setattr(tools, "_scoring_work_lease_refusal", lambda _sid: None)

    original_gate = tools._course_gate_check
    monkeypatch.setattr(tools, "_course_gate_check", lambda _cid: "not a Current course")
    gated = tools.refresh_scoring_session(first)
    assert gated["code"] == "invalid_scope" and gated["stage"] == "validate"
    monkeypatch.setattr(tools, "_course_gate_check", original_gate)

    world.prepare()
    assert tools.refresh_scoring_session(first)["code"] == "session_superseded"


def test_refresh_tool_gates_pseudonym_output_and_attaches_the_next_procedure(
    scoring_refresh_world, monkeypatch,
):
    """CONTRACT: a success is scanned by the pseudonym gate, then carries its static next."""
    world = scoring_refresh_world
    sid = _two_student_session(world)
    monkeypatch.setattr(scoring_preparation, "refresh_scoring_session",
                        lambda *_a, **_kw: {"ok": True, "changed": False, "added": []})

    ok = tools.refresh_scoring_session(sid)

    assert ok["ok"] is True and ok["next"] == tools._NEXT_STEPS["refresh_scoring_session"]

    monkeypatch.setattr(scoring_preparation, "refresh_scoring_session",
                        lambda *_a, **_kw: {"ok": True, "added": ["900001"]})
    blocked = tools.refresh_scoring_session(sid)

    assert blocked["ok"] is False and "next" not in blocked
    assert "900001" not in json.dumps(blocked)


def test_refresh_tool_owner_exception_returns_a_safe_typed_failure(
    scoring_refresh_world, monkeypatch,
):
    world = scoring_refresh_world
    sid = _two_student_session(world)
    private_text = "private student identity should never escape"
    monkeypatch.setattr(scoring_preparation, "refresh_scoring_session",
                        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError(private_text)))

    result = tools.refresh_scoring_session(sid)

    assert result["code"] == "safe_refresh_failed" and result["retryable"] is True
    assert private_text not in json.dumps(result)


def test_a_mirror_change_refuses_the_packet_without_superseding_until_refresh(
    scoring_refresh_world,
):
    """LAW: session_mirror_changed names the refresh and changes nothing; refresh restores reads."""
    world = scoring_refresh_world
    sid = _two_student_session(world)
    assert tools.get_scoring_packet(sid)["ok"] is True
    world.add("900003", "Late Learner")
    before = world.session(sid)

    refused = tools.get_scoring_packet(sid)

    assert refused["code"] == "session_mirror_changed"
    assert "refresh_scoring_session" in refused["next"]
    assert world.session(sid) == before and before["status"] == "ready"

    refreshed = tools.refresh_scoring_session(sid)
    page = tools.get_scoring_packet(sid, offset=refreshed["first_new_offset"])

    assert refreshed["ok"] is True and refreshed["added"] == [world.pseudonym("900003")]
    assert page["ok"] is True
    assert [row[0] for row in page["students"]["rows"]] == [world.pseudonym("900003")]
    assert page["packet_digest"] == refreshed["packet_digest"]


def test_staging_only_the_new_row_then_applying_posts_it_with_the_staged_rows_once(
    scoring_refresh_world,
):
    """EXAMPLE: A was posted earlier, B staged before the refresh, C arrives late.

    After the refresh the old stage is gone, staging only C with the new digest
    covers B and C, and apply sends exactly those two rows (never A again).
    """
    world = scoring_refresh_world
    sid = _two_student_session(world)
    first, second = world.pseudonym("900001"), world.pseudonym("900002")
    posted_row = next(s for s in world.sessions[sid]["students"] if s["user_id"] == "900001")
    posted_row.update(posted=True, status="posted")
    staged = tools.stage_scoring_results(
        sid, _results(second), tools.get_scoring_packet(sid)["packet_digest"])
    assert staged["status"] == "staged"
    world.add("900003", "Late Learner")

    refreshed = tools.refresh_scoring_session(sid)

    assert refreshed["changed"] is True and world.session(sid)["status"] == "ready"
    assert tools.apply_staged_scoring_results(sid, staged["stage_digest"])["code"] == "stage_unavailable"
    restaged = tools.stage_scoring_results(
        sid, _results(world.pseudonym("900003")), refreshed["packet_digest"])
    assert restaged["status"] == "staged" and restaged["counts"]["ready"] == 2

    applied = tools.apply_staged_scoring_results(sid, restaged["stage_digest"])

    sent_to = sorted(path.rstrip("/").split("/")[-1] for _method, path, _payload in world.sent)
    assert sent_to == ["900002", "900003"]
    assert applied["counts"]["finalized"] == 2
    finalized = sorted(row["pseudonym"] for row in applied["results"] if row["status"] == "finalized")
    assert finalized == sorted([second, world.pseudonym("900003")]) and first not in finalized
    assert "900003" not in json.dumps(applied)
    assert all(s["posted"] for s in world.session(sid)["students"])


def test_the_refreshed_session_stays_the_current_one_for_its_scope(scoring_refresh_world):
    """LAW: refresh never forks or supersedes; the same session id stays current."""
    world = scoring_refresh_world
    sid = _two_student_session(world)
    world.add("900003", "Late Learner")

    tools.refresh_scoring_session(sid)

    assert session_store.is_current_session(sid) is True
    assert len(world.sessions) == 1
