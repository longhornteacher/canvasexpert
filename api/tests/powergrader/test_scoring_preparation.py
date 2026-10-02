"""Assignment-scoped local-mirror preparation laws and examples."""
from __future__ import annotations

import copy
import json

import pytest

from api.powergrader import scoring_preparation, session_store


def _assignment(**overrides):
    value = {"id": "a1", "name": "Essay", "description": "Write.",
             "points_possible": 10, "is_quiz_lti_assignment": False,
             "quiz_kind": "", "rubric": []}
    value.update(overrides)
    return value


def _submission():
    return {"user_id": "u1", "workflow_state": "submitted",
            "submission_type": "online_text_entry", "body": "A submitted answer.",
            "submitted_at": "2026-09-18T10:00:00Z", "user": {"name": "Learner"}}


def _fresh(age=0, needs=False):
    return {"course_id": "c1", "course_name": "Course", "state": "current",
            "last_success_at": "2026-09-21T12:00:00Z", "age_minutes": age,
            "requires_teacher_confirmation": needs}


def _wire(monkeypatch, tmp_path, *, assignment=None, submissions=None, freshness=None, guidance="Guidance"):
    monkeypatch.setattr(scoring_preparation.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(scoring_preparation.config, "course_display_name", lambda _id: "Course")
    monkeypatch.setattr(scoring_preparation.config, "get_roster_student_settings", lambda _id: {})
    monkeypatch.setattr(scoring_preparation.config, "get_monitored_students", lambda: {})
    monkeypatch.setattr(scoring_preparation.config, "get_extra_time", lambda _id: [])
    monkeypatch.setattr(scoring_preparation.assignment_refresh, "prepare_assignment_from_mirror",
        lambda _course, _assignment: (list(submissions or [_submission()]), assignment or _assignment(),
                                       {"status": "mirror", "manifest_path": None,
                                        "freshness": freshness or _fresh()}))
    bundle_path = tmp_path / "safe-bundle.json"
    bundle_path.write_text(json.dumps({"students": [{"pseudonym": "Pikachu", "responses": [{
        "item_id": "item-1", "prompt": "Explain.", "response": "A response.", "possible": 10,
    }]}]}), encoding="utf-8")
    monkeypatch.setattr(scoring_preparation.scoring_artifacts, "build_scoring_artifacts", lambda **_kwargs: {
        "ok": True, "privacy_steps": [], "privacy_artifacts": {"safe_bundle": str(bundle_path)},
        "ai_by_uid": {}, "ai_item_by_uid": {}, "copilot_packet": None})
    monkeypatch.setattr(scoring_preparation.session_builder, "build_students",
        lambda **_kwargs: [{"user_id": "u1", "status": "pending", "posted": False}])
    saved = {}
    return saved, lambda: scoring_preparation.prepare_scoring_session(
        "c1", "a1", guidance, save_session=lambda session: saved.setdefault(session["session_id"], session))


def test_successful_preparation_uses_local_mirror_and_persists_freshness(monkeypatch, tmp_path):
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}]))
    result = prepare()
    assert result["status"] == "ready"
    session = saved[result["scoring_session_id"]]
    assert session["scoring_freshness"]["state"] == "current"
    assert session["scoring_freshness"]["use_existing_mirror"] is False


def test_missing_norms_is_teacher_input_without_persistence(monkeypatch, tmp_path):
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(description="", rubric=[]), guidance="")
    result = prepare()
    assert result["code"] == "needs_scoring_norms"
    assert saved == {}


def test_new_quiz_stops_before_safe_work_or_persistence(monkeypatch, tmp_path):
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        quiz_kind="new_quiz", is_quiz_lti_assignment=True))
    result = prepare()
    assert result["code"] == "new_quiz_writing_requires_assignment"
    assert saved == {}


def test_mirror_failure_is_typed_and_identity_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(scoring_preparation.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(scoring_preparation.assignment_refresh, "prepare_assignment_from_mirror",
                        lambda *_args: (None, None, {"code": "mirror_projection_unavailable"}))
    result = scoring_preparation.prepare_scoring_session("c1", "a1")
    assert result["code"] == "mirror_projection_unavailable"
    assert {"code", "stage", "retryable", "user_action"} <= result.keys()


def test_old_snapshot_requires_explicit_teacher_confirmation(monkeypatch, tmp_path):
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}]),
        freshness=_fresh(age=31, needs=True))
    result = prepare()
    assert result["code"] == "mirror_freshness_confirmation_required"
    assert result["stage"] == "freshness"
    assert result["retryable"] is True
    assert "relevant Canvas work changed" in result["user_action"]
    assert saved == {}


def test_teacher_can_durably_acknowledge_existing_snapshot(monkeypatch, tmp_path):
    saved, _prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}]),
        freshness=_fresh(age=31, needs=True))
    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", use_existing_mirror=True,
        save_session=lambda session: saved.setdefault(session["session_id"], session))
    assert result["status"] == "ready"
    assert saved[result["scoring_session_id"]]["scoring_freshness"]["use_existing_mirror"] is True


def test_unavailable_freshness_stays_fail_closed_even_with_ack(monkeypatch, tmp_path):
    saved, _prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}]),
        freshness={**_fresh(), "state": "unavailable", "last_success_at": ""})
    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", use_existing_mirror=True,
        save_session=lambda session: saved.setdefault(session["session_id"], session))
    assert result["code"] == "mirror_projection_unavailable"
    assert saved == {}


def test_guidance_projection_is_deterministic_and_bounded():
    text = "Beginning direction.\n\n" + ("middle criteria must assign points.\n\n" * 900) + "Ending direction."
    first = scoring_preparation.project_teacher_scoring_guidance(text)
    assert first == scoring_preparation.project_teacher_scoring_guidance(text)
    assert first[1]["compacted"] is True
    assert len(first[0]) <= scoring_preparation.MAX_TEACHER_SCORING_GUIDANCE_CHARS


def test_preparation_keeps_private_assignmentforge_corrections(monkeypatch, tmp_path):
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}]))
    monkeypatch.setattr(scoring_preparation.assignmentforge, "for_assignment", lambda *_args: {
        "tier": "Red", "corrections": {"item-1": {"shared": {"answer": "A", "why": "B"}, "by_tier": None}}})
    result = prepare()
    session = saved[result["scoring_session_id"]]
    assert session["assignmentforge_tier"] == "Red"
    assert session["assignmentforge_corrections"]["item-1"]["shared"]["answer"] == "A"


def test_preparation_keeps_canonical_submission_digest(monkeypatch, tmp_path):
    eligible = dict(_submission(), id="submitted-1", attempt=1,
                    submitted_at="2026-09-18T10:00:00Z", score=None)
    graded = dict(eligible, user_id="u2", id="graded-1", workflow_state="graded", submitted_at="", score=9)
    rows = [eligible, graded]
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}]), submissions=rows)
    monkeypatch.setattr("api.gradebook_snapshot.needs_grading",
                        lambda row: bool(row.get("submitted_at")) and row.get("workflow_state") == "submitted")
    result = prepare()
    session = saved[result["scoring_session_id"]]
    assert session["submission_snapshot"] == session_store.eligible_submission_snapshot_digest([eligible])
    assert session["submission_snapshot_count"] == 1


def test_old_session_staleness_law_is_no_longer_used_for_packet_flow():
    assert not hasattr(session_store, "session_staleness") or callable(session_store.session_staleness)


def test_invalid_guidance_provenance_is_refused_before_it_can_be_persisted(monkeypatch, tmp_path):
    """LAW: a bad provenance is never saved, so later bare retries cannot replay it."""
    _saved, _prepare = _wire(monkeypatch, tmp_path)

    refused = scoring_preparation.prepare_scoring_session(
        "c1", "a1", "Be kind.", scoring_guidance_provenance="bogus",
        save_session=lambda session: None)

    assert refused["code"] == "invalid_scoring_guidance_provenance"
    assert all(value in refused["user_action"] for value in scoring_preparation.GUIDANCE_PROVENANCES)
    assert session_store.load_preparation_state("c1", "a1") == {}


def test_provenance_without_guidance_or_contract_is_ignored(monkeypatch, tmp_path):
    """CONTRACT: provenance describes guidance; with none supplied it is ignored,
    even when invalid, and preparation proceeds as default."""
    saved, _prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}]))

    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", "", scoring_guidance_provenance="bogus",
        save_session=lambda session: saved.setdefault(session["session_id"], session))

    assert result["status"] == "ready"
    assert session_store.load_preparation_state("c1", "a1") == {}


@pytest.mark.parametrize("policy, expected", [(None, "ask"), ("waive", "waive"), ("apply", "apply")])
def test_the_late_policy_is_stored_on_the_private_session(monkeypatch, tmp_path, policy, expected):
    saved, _prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}]))
    kwargs = {} if policy is None else {"late_policy": policy}

    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", "Guidance", save_session=lambda session: saved.setdefault(
            session["session_id"], session), **kwargs)

    assert saved[result["scoring_session_id"]]["late_policy"] == expected


# --- refresh_scoring_session ---------------------------------------------------

def _two_student_session(world):
    world.add("900001", "Synthetic First")
    world.add("900002", "Fictional Omega")
    return world.prepare()


def _student(world, sid, user_id):
    return next(s for s in world.sessions[sid]["students"] if s["user_id"] == user_id)


def _safe_files(world):
    return sorted(path.name for path in (world.tmp_path / "SAFE").iterdir())


def test_refresh_appends_a_late_student_and_preserves_every_existing_record(
    scoring_refresh_world,
):
    """LAW: refresh adds only the new student, at the end, and rewrites nothing else."""
    from api.powergrader import scoring_packet

    world = scoring_refresh_world
    sid = _two_student_session(world)
    _student(world, sid, "900001").update(ai_score=8, ai_feedback="Staged feedback.")
    _student(world, sid, "900002").update(posted=True, status="posted", push_state="sent")
    world.sessions[sid].update(
        status="staged", staged_scoring_apply={"stage_digest": "old"},
        push_idempotency={"key": {"state": "sent"}}, push_log=[{"event": "sent"}])
    before, old_bundle = world.session(sid), world.bundle()
    world.add("900003", "Late Learner")

    result = scoring_preparation.refresh_scoring_session(sid)

    after = world.session(sid)
    assert result["ok"] is True and result["changed"] is True
    assert result["added"] == [world.pseudonym("900003")]
    assert result["replaced"] == result["resubmitted_not_replaced"] == result["posted_resubmitted"] == []
    assert result["first_new_offset"] == 2 and result["held_added"] == 0
    assert after["students"][:2] == before["students"]
    assert [s["user_id"] for s in after["students"]] == (
        [s["user_id"] for s in before["students"]] + ["900003"])
    assert after["push_idempotency"] == before["push_idempotency"]
    assert after["push_log"] == before["push_log"]
    assert after["status"] == "ready" and "staged_scoring_apply" not in after
    assert after["mirror_revision"] == world.revision
    assert after["mirror_snapshot_id"] == f"c1:{world.revision}"
    new_bundle = world.bundle(after)
    assert new_bundle["students"][:2] == old_bundle["students"]
    assert new_bundle["students"][2]["pseudonym"] == result["added"][0]
    assert scoring_packet.validate_safe_bundle(new_bundle)["ok"] is True
    assert result["packet_digest"] == scoring_packet.packet_digest(
        sid, new_bundle, course_id="c1", assignment_id="a1")
    blob = json.dumps(new_bundle)
    assert "900003" not in blob and "Late Learner" not in blob
    assert after["privacy_artifacts"]["safe_bundle"] != before["privacy_artifacts"]["safe_bundle"]
    assert world.bundle(before) == old_bundle


def test_refresh_never_overwrites_an_earlier_bundle_file(scoring_refresh_world):
    """LAW: every refresh writes its own bundle file; earlier ones stay on disk."""
    world = scoring_refresh_world
    sid = _two_student_session(world)
    files = [_safe_files(world)]
    for user_id, name in (("900003", "Late Learner"), ("900004", "Later Learner")):
        world.add(user_id, name)
        scoring_preparation.refresh_scoring_session(sid)
        files.append(_safe_files(world))

    history = world.session(sid)["scoring_refreshes"]
    assert [len(names) for names in files] == [1, 2, 3]
    assert set(files[0]) < set(files[1]) < set(files[2])
    assert len(history) == 2
    assert history[1]["previous_safe_bundle"].endswith(
        next(iter(set(files[1]) - set(files[0]))))


@pytest.mark.parametrize("case, code", [
    ("missing", "session_not_found"),
    ("superseded", "session_superseded"),
    ("completed", "session_completed"),
    ("completed_with_holds", "session_completed"),
    ("sent_unknown", "canvas_write_attention"),
    ("old_snapshot", "mirror_freshness_confirmation_required"),
    ("not_current", "mirror_projection_unavailable"),
])
def test_refresh_refusals_change_no_state(scoring_refresh_world, case, code):
    """CONTRACT: each refusal returns its typed code with the session and files untouched."""
    world = scoring_refresh_world
    sid = _two_student_session(world)
    world.add("900003", "Late Learner")
    if case in {"superseded", "completed", "completed_with_holds"}:
        world.sessions[sid]["status"] = case
    elif case == "sent_unknown":
        _student(world, sid, "900001")["push_state"] = "sent_unknown"
    elif case == "old_snapshot":
        world.freshness.update(age_minutes=999, requires_teacher_confirmation=True)
    elif case == "not_current":
        world.freshness.update(state="unavailable", last_success_at="")
    snapshot, files = copy.deepcopy(world.sessions), _safe_files(world)

    result = scoring_preparation.refresh_scoring_session("missing-id" if case == "missing" else sid)

    assert result["ok"] is False and result["code"] == code
    assert {"stage", "retryable", "user_action"} <= result.keys()
    assert world.sessions == snapshot and _safe_files(world) == files


def test_refresh_accepts_an_old_snapshot_the_teacher_acknowledged(scoring_refresh_world):
    """EXAMPLE: use_existing_mirror=true passes the same gate prepare uses."""
    world = scoring_refresh_world
    sid = _two_student_session(world)
    world.add("900003", "Late Learner")
    world.freshness.update(age_minutes=999, requires_teacher_confirmation=True)

    result = scoring_preparation.refresh_scoring_session(sid, use_existing_mirror=True)

    assert result["ok"] is True and result["changed"] is True


def test_refresh_replaces_only_unposted_resubmissions_and_only_when_asked(scoring_refresh_world):
    """LAW: a posted row is only reported; an unposted one is cleared and re-added on request."""
    world = scoring_refresh_world
    sid = _two_student_session(world)
    _student(world, sid, "900001").update(
        ai_score=8, ai_feedback="Staged feedback.", teacher_score=7, status="approved",
        grading={"insincere": False})
    _student(world, sid, "900002").update(posted=True, status="posted")
    world.resubmit("900001")
    world.resubmit("900002", body="A posted student's revised response.")
    unposted, posted = world.pseudonym("900001"), world.pseudonym("900002")
    before, files = world.session(sid), _safe_files(world)

    reported = scoring_preparation.refresh_scoring_session(sid)

    assert reported["changed"] is False and reported["replaced"] == []
    assert reported["resubmitted_not_replaced"] == [unposted]
    assert reported["posted_resubmitted"] == [posted]
    assert world.session(sid)["students"] == before["students"] and _safe_files(world) == files

    replaced = scoring_preparation.refresh_scoring_session(sid, replace_resubmitted=True)

    after = world.session(sid)
    assert replaced["changed"] is True and replaced["replaced"] == [unposted]
    assert replaced["resubmitted_not_replaced"] == [] and replaced["posted_resubmitted"] == [posted]
    fresh = next(s for s in after["students"] if s["user_id"] == "900001")
    assert fresh["ai_score"] is None and not fresh["ai_feedback"]
    assert fresh["teacher_score"] is None and fresh["status"] == "pending" and "grading" not in fresh
    assert fresh["submission_baseline"] == {"attempt": 2, "submitted_at": "2026-09-19T10:00:00Z"}
    assert next(s for s in after["students"] if s["user_id"] == "900002") == next(
        s for s in before["students"] if s["user_id"] == "900002")
    rows = world.bundle(after)["students"]
    assert [row["pseudonym"] for row in rows] == [posted, unposted]
    assert "revised" in rows[-1]["responses"][0]["response"]
    assert replaced["first_new_offset"] == 1


@pytest.mark.parametrize("baseline", [None, {}, {"attempt": None, "submitted_at": None}])
def test_refresh_never_reports_a_resubmission_without_a_stored_baseline(
    scoring_refresh_world, baseline,
):
    """LAW: an unknown baseline is never a resubmission."""
    world = scoring_refresh_world
    sid = _two_student_session(world)
    _student(world, sid, "900001")["submission_baseline"] = baseline
    world.resubmit("900001")

    result = scoring_preparation.refresh_scoring_session(sid, replace_resubmitted=True)

    assert result["changed"] is False
    assert result["replaced"] == result["resubmitted_not_replaced"] == []


def test_refresh_holds_a_new_submission_that_fails_the_safety_scan(
    scoring_refresh_world, monkeypatch,
):
    """CONTRACT: a failing new row is a held student, never a bundle row."""
    from api.powergrader import scoring_packet

    world = scoring_refresh_world
    sid = _two_student_session(world)
    world.add("900003", "Late Learner", body="SURVIVOR synthetic response.")
    scrub = scoring_preparation.scoring_artifacts.feedback_scrub
    original = scrub.verify_clean
    monkeypatch.setattr(
        scrub, "verify_clean",
        lambda text, vault: ["survivor"] if "SURVIVOR" in text else original(text, vault))

    result = scoring_preparation.refresh_scoring_session(sid)

    after = world.session(sid)
    bundle = world.bundle(after)
    assert result["added"] == [world.pseudonym("900003")]
    assert result["held_added"] == 1 and result["first_new_offset"] is None
    assert world.pseudonym("900003") not in {s["pseudonym"] for s in bundle["students"]}
    assert scoring_packet.validate_safe_bundle(bundle)["ok"] is True
    assert [s["user_id"] for s in after["students"]][-1] == "900003"


def test_refresh_with_nothing_new_changes_nothing_but_the_recorded_mirror(scoring_refresh_world):
    """LAW: no new bundle, no stage change; only a moved mirror is recorded so reads resume."""
    world = scoring_refresh_world
    sid = _two_student_session(world)
    world.sessions[sid].update(status="staged", staged_scoring_apply={"stage_digest": "kept"})
    before, files = world.session(sid), _safe_files(world)

    same = scoring_preparation.refresh_scoring_session(sid)

    assert same["changed"] is False and same["added"] == [] and same["first_new_offset"] is None
    assert world.session(sid) == before and _safe_files(world) == files

    world.revision += 1
    moved = scoring_preparation.refresh_scoring_session(sid)

    after = world.session(sid)
    mirror = {"mirror_revision", "mirror_snapshot_id"}
    assert moved["changed"] is False and moved["packet_digest"] == same["packet_digest"]
    assert after["mirror_revision"] == world.revision
    assert {k: v for k, v in after.items() if k not in mirror} == {
        k: v for k, v in before.items() if k not in mirror}
    assert _safe_files(world) == files
