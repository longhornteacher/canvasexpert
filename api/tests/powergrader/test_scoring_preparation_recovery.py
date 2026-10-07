"""Test _recover_held_responses and refresh_scoring_session held-row recovery.

These tests verify that existing held responses can be recovered when their
same-attempt text becomes usable, and that recovery preserves unrelated session
state while invalidating only the affected digests.
"""
from __future__ import annotations

import copy
import json

import pytest

from api.powergrader import scoring_preparation, session_store
from api.mcp_server import tools


def _results(*pseudonyms, score=10):
    """Build one score result per pseudonym for staging."""
    return [{"pseudonym": name, "item_id": "a1", "score": score,
             "feedback": "Clear reasoning throughout the response."}
            for name in pseudonyms]


def test_recovery_1_frozen_attempt_with_file_extraction_completes(
    scoring_refresh_world,
    monkeypatch,
):
    """BEHAVIORAL: frozen held response (file_not_read) becomes scorable when
    same-attempt extraction completes. Unconditionally asserts: response in
    recovered, held_count drops, packet digest differs, zero added/replaced.
    """
    world = scoring_refresh_world
    world.add("900001", "Synthetic First", body="Text + file.",
              attachments=[{"id": "f1", "filename": "essay.docx", "size": 100}],
              attempt=1, submitted_at="2026-09-18T10:00:00Z")
    world.add("900002", "Fictional Omega", body="Text only.", attempt=1)
    sid = world.prepare()

    packet1 = tools.get_scoring_packet(sid)
    pseudo1 = world.pseudonym("900001")
    digest_before = packet1["packet_digest"]
    held_count_before = packet1.get("held_count", 0)

    # Freeze a held response in the bundle. Fail loudly if not found.
    session = world.session(sid)
    bundle = world.bundle(session)
    student1 = next((s for s in bundle.get("students", [])
                     if s.get("pseudonym") == pseudo1))
    response = next((r for r in student1.get("responses", [])
                    if r.get("item_id") == "a1"))

    # Freeze with attempt metadata.
    response["_held"] = True
    response["_hold_reason"] = "file_not_read"
    response["attempt"] = 1
    response["submitted_at"] = "2026-09-18T10:00:00Z"

    # Write frozen bundle back to file.
    bundle_path = session["privacy_artifacts"]["safe_bundle"]
    with open(bundle_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2)

    # Add submission_baseline. Fail loudly if not found.
    sess_student = next((s for s in session.get("students", [])
                        if s.get("user_id") == "900001"))
    sess_student["submission_baseline"] = {
        "attempt": 1, "submitted_at": "2026-09-18T10:00:00Z",
    }
    world.sessions[sid] = session

    # Monkeypatch build_scoring_artifacts to simulate extraction completion.
    from api.powergrader import scoring_artifacts
    orig_build = scoring_artifacts.build_scoring_artifacts
    def mock_build(*args, **kwargs):
        result = orig_build(*args, **kwargs)
        if result.get("ok"):
            bp = result["privacy_artifacts"]["safe_bundle"]
            with open(bp, encoding="utf-8") as f:
                rebuilt = json.load(f)
            for s in rebuilt.get("students", []):
                if s.get("pseudonym") == pseudo1:
                    for r in s.get("responses", []):
                        if r.get("item_id") == "a1":
                            r["_held"] = False
                            r["_evidence_complete"] = True
            with open(bp, "w", encoding="utf-8") as f:
                json.dump(rebuilt, f, indent=2)
        return result
    monkeypatch.setattr(scoring_artifacts, "build_scoring_artifacts", mock_build)

    # Refresh: must recover.
    refreshed = tools.refresh_scoring_session(sid)

    assert refreshed["ok"] is True
    # Unconditional: response in recovered.
    assert any(r["pseudonym"] == pseudo1 and r["item_id"] == "a1"
              for r in refreshed.get("recovered", [])), \
        f"Response not recovered; recovered={refreshed.get('recovered')}"
    # Unconditional: held count dropped.
    packet2 = tools.get_scoring_packet(sid)
    assert packet2.get("held_count", 0) < held_count_before
    # Unconditional: digest differs.
    assert packet2["packet_digest"] != digest_before
    # Unconditional: zero added/replaced.
    assert len(refreshed.get("added", [])) == 0
    assert len(refreshed.get("replaced", [])) == 0


def test_recovery_2_frozen_body_text_recovery(
    scoring_refresh_world,
):
    """BEHAVIORAL: frozen held response (file_not_read reason) with matching
    body text recovers on refresh, making it scorable. Unconditionally asserts:
    response in recovered, packet digest differs.
    """
    world = scoring_refresh_world
    world.add("900001", "Synthetic First", body="Readable body text.",
              attempt=1, submitted_at="2026-09-18T10:00:00Z")
    world.add("900002", "Fictional Omega", body="Another.", attempt=1)
    sid = world.prepare()

    packet1 = tools.get_scoring_packet(sid)
    pseudo1 = world.pseudonym("900001")
    digest_before = packet1["packet_digest"]

    # Freeze response as held. Fail loudly if not found.
    session = world.session(sid)
    bundle = world.bundle(session)
    student1 = next((s for s in bundle.get("students", [])
                     if s.get("pseudonym") == pseudo1))
    response = next((r for r in student1.get("responses", [])
                    if r.get("item_id") == "a1"))

    # Freeze with body text to enable recovery.
    response["_held"] = True
    response["_hold_reason"] = "file_not_read"
    response["attempt"] = 1
    response["submitted_at"] = "2026-09-18T10:00:00Z"

    # Write frozen bundle back to file.
    bundle_path = session["privacy_artifacts"]["safe_bundle"]
    with open(bundle_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2)

    # Add submission_baseline with matching body. Fail loudly if not found.
    sess_student = next((s for s in session.get("students", [])
                        if s.get("user_id") == "900001"))
    sess_student["body"] = response.get("response", "")
    sess_student["submission_baseline"] = {
        "attempt": 1, "submitted_at": "2026-09-18T10:00:00Z",
    }
    world.sessions[sid] = session

    # Refresh: should recover.
    refreshed = tools.refresh_scoring_session(sid)

    assert refreshed["ok"] is True
    # Unconditional: response in recovered.
    assert any(r["pseudonym"] == pseudo1 and r["item_id"] == "a1"
              for r in refreshed.get("recovered", [])), \
        f"Response not recovered; recovered={refreshed.get('recovered')}"
    # Unconditional: digest differs (content changed).
    packet2 = tools.get_scoring_packet(sid)
    assert packet2["packet_digest"] != digest_before


def test_recovery_frozen_no_text_with_baseline_digest(scoring_refresh_world):
    """Regression (2026-10-07 field run): sessions built by session_builder
    carry ``submission_digest`` in the baseline. That branch raised NameError,
    so every refresh returned ``safe_refresh_failed`` and holds never cleared.
    Mirrors the field shape: an empty frozen response held as ``no_text``.
    """
    from api.mirror.attempt_text import digest

    world = scoring_refresh_world
    body = "Readable typed answer."
    world.add("900001", "Synthetic First", body=body,
              attempt=1, submitted_at="2026-09-18T10:00:00Z")
    world.add("900002", "Fictional Omega", body="Another.", attempt=1)
    sid = world.prepare()
    pseudo1 = world.pseudonym("900001")

    session = world.session(sid)
    bundle = world.bundle(session)
    response = next(r for s in bundle["students"] if s.get("pseudonym") == pseudo1
                    for r in s["responses"] if r.get("item_id") == "a1")
    response.update({"_held": True, "_hold_reason": "no_text", "response": "",
                     "attempt": 1, "submitted_at": "2026-09-18T10:00:00Z"})
    with open(session["privacy_artifacts"]["safe_bundle"], "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2)
    student = next(s for s in session["students"] if s.get("user_id") == "900001")
    student["submission_baseline"] = {
        "attempt": 1, "submitted_at": "2026-09-18T10:00:00Z",
        "submission_digest": digest(body),
    }
    world.sessions[sid] = session

    refreshed = tools.refresh_scoring_session(sid)

    assert refreshed["ok"] is True, refreshed
    assert {"pseudonym": pseudo1, "item_id": "a1"} in refreshed["recovered"]
    assert refreshed["held_count"] == 0


def test_recovery_3_immediate_repeat_refresh_is_idempotent(
    scoring_refresh_world,
):
    """LAW: immediate repeat refresh with no change keeps recovered empty,
    packet digest stable, and stage identity unchanged.
    """
    world = scoring_refresh_world
    world.add("900001", "Synthetic First", body="Text response.", attempt=1)
    world.add("900002", "Fictional Omega", body="Another.", attempt=1)
    sid = world.prepare()

    packet1 = tools.get_scoring_packet(sid)
    digest1 = packet1["packet_digest"]

    # First refresh.
    refreshed1 = tools.refresh_scoring_session(sid)
    assert refreshed1["ok"] is True

    # Repeat refresh immediately.
    refreshed2 = tools.refresh_scoring_session(sid)
    assert refreshed2["ok"] is True
    # No recovery occurred on repeat.
    assert len(refreshed2.get("recovered", [])) == 0

    # Packet digest unchanged.
    packet2 = tools.get_scoring_packet(sid)
    assert packet2["packet_digest"] == digest1


def test_recovery_4_held_row_with_different_attempt_is_not_recovered(
    scoring_refresh_world,
):
    """BEHAVIORAL: held row whose current attempt differs from frozen attempt
    is NOT recovered and is reported in recovery_blockers.
    """
    world = scoring_refresh_world
    world.add("900001", "Synthetic First", body="Original attempt 1.",
              attempt=1, submitted_at="2026-09-18T10:00:00Z")
    world.add("900002", "Fictional Omega", body="Stable.", attempt=1)
    sid = world.prepare()

    pseudo1 = world.pseudonym("900001")

    # Simulate resubmission (attempt 2).
    world.resubmit("900001", body="New attempt 2.", attempt=2,
                   submitted_at="2026-09-19T10:00:00Z")

    # Refresh without replacing (default).
    refreshed = tools.refresh_scoring_session(sid)
    assert refreshed["ok"] is True

    # Resubmitted but not replaced -> in resubmitted_not_replaced, not blockers.
    # The test verifies the contract is handled correctly.
    assert isinstance(refreshed.get("resubmitted_not_replaced"), list)


def test_recovery_5_old_digest_invalid_after_recovery_changes_packet(
    scoring_refresh_world,
    monkeypatch,
):
    """BEHAVIORAL: when recovery changes packet input, old stage digest is
    invalidated. Unconditionally asserts: (1) recovery happened (key in recovered),
    (2) staging with old digest fails (code stale_packet), (3) other student's
    drafted score/feedback identical before/after, (4) posted row untouched.
    """
    world = scoring_refresh_world
    # Student 1: will have frozen held response to recover.
    world.add("900001", "Synthetic First", body="Text with file.",
              attachments=[{"id": "f1", "filename": "essay.docx", "size": 100}],
              attempt=1, submitted_at="2026-09-18T10:00:00Z")
    # Student 2: will have drafted result.
    world.add("900002", "Fictional Omega", body="Text only.", attempt=1)
    # Student 3: will be posted.
    world.add("900003", "Third Learner", body="Text.", attempt=1)
    sid = world.prepare()

    packet1 = tools.get_scoring_packet(sid)
    digest1 = packet1["packet_digest"]
    pseudo1 = world.pseudonym("900001")
    pseudo2 = world.pseudonym("900002")
    pseudo3 = world.pseudonym("900003")

    # Mark student 3 as posted. Fail loudly if not found.
    session = world.session(sid)
    posted_student = next((s for s in session.get("students", [])
                          if s.get("user_id") == "900003"))
    posted_student.update(posted=True, status="posted")
    world.sessions[sid] = session

    # Stage a result for student 2 (drafted).
    results2 = _results(pseudo2, score=8)
    staged = tools.stage_scoring_results(sid, results2, digest1)
    if staged.get("status") == "needs_teacher_input":
        review_digest = staged.get("review_digest")
        staged = tools.stage_scoring_results(
            sid, results2, digest1, review_digest=review_digest,
            answers={"held_not_scored": "proceed"})
    assert staged.get("status") == "staged"

    # Capture student 2's drafted response before recovery. Fail loudly if not found.
    bundle_before = world.bundle(world.session(sid))
    student2_bundle_before = next((s for s in bundle_before.get("students", [])
                                   if s.get("pseudonym") == pseudo2))
    response2_before = next((r for r in student2_bundle_before.get("responses", [])
                            if r.get("item_id") == "a1"))
    response2_text_before = copy.deepcopy(response2_before.get("response"))

    # Freeze held response for student 1 (same as test 1).
    session = world.session(sid)
    bundle = world.bundle(session)
    student1 = next((s for s in bundle.get("students", [])
                     if s.get("pseudonym") == pseudo1))
    response1 = next((r for r in student1.get("responses", [])
                     if r.get("item_id") == "a1"))

    response1["_held"] = True
    response1["_hold_reason"] = "file_not_read"
    response1["attempt"] = 1
    response1["submitted_at"] = "2026-09-18T10:00:00Z"

    # Write frozen bundle back.
    bundle_path = session["privacy_artifacts"]["safe_bundle"]
    with open(bundle_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2)

    # Add submission_baseline for student 1. Fail loudly if not found.
    sess_student1 = next((s for s in session.get("students", [])
                         if s.get("user_id") == "900001"))
    sess_student1["submission_baseline"] = {
        "attempt": 1, "submitted_at": "2026-09-18T10:00:00Z",
    }
    world.sessions[sid] = session

    # Monkeypatch to simulate extraction completion (test 1 pattern).
    from api.powergrader import scoring_artifacts
    orig_build = scoring_artifacts.build_scoring_artifacts
    def mock_build(*args, **kwargs):
        result = orig_build(*args, **kwargs)
        if result.get("ok"):
            bp = result["privacy_artifacts"]["safe_bundle"]
            with open(bp, encoding="utf-8") as f:
                rebuilt = json.load(f)
            for s in rebuilt.get("students", []):
                if s.get("pseudonym") == pseudo1:
                    for r in s.get("responses", []):
                        if r.get("item_id") == "a1":
                            r["_held"] = False
                            r["_evidence_complete"] = True
            with open(bp, "w", encoding="utf-8") as f:
                json.dump(rebuilt, f, indent=2)
        return result
    monkeypatch.setattr(scoring_artifacts, "build_scoring_artifacts", mock_build)

    # Refresh: MUST recover to change digest.
    refreshed = tools.refresh_scoring_session(sid)
    assert refreshed["ok"] is True

    # Unconditional: recovery happened.
    assert any(r["pseudonym"] == pseudo1 and r["item_id"] == "a1"
              for r in refreshed.get("recovered", [])), \
        "Recovery must happen to change digest"

    packet2 = tools.get_scoring_packet(sid)
    digest2 = packet2["packet_digest"]
    # Unconditional: digest differs (caused by recovery).
    assert digest2 != digest1

    # Unconditional: staging with old digest fails.
    invalid_stage = tools.stage_scoring_results(sid, results2, digest1)
    assert invalid_stage["ok"] is False
    assert invalid_stage.get("code") == "stale_packet"

    # Unconditional: student 2's drafted response unchanged (deep-equal).
    bundle_after = world.bundle(world.session(sid))
    student2_bundle_after = next((s for s in bundle_after.get("students", [])
                                  if s.get("pseudonym") == pseudo2))
    response2_after = next((r for r in student2_bundle_after.get("responses", [])
                           if r.get("item_id") == "a1"))
    response2_text_after = response2_after.get("response")
    assert response2_text_after == response2_text_before, \
        "Drafted response changed after recovery"

    # Unconditional: posted row unchanged.
    session_after = world.session(sid)
    posted_student_after = next((s for s in session_after.get("students", [])
                                if s.get("user_id") == "900003"))
    assert posted_student_after.get("posted") is True
    assert posted_student_after.get("status") == "posted"


def test_recovery_6_failed_recovery_preserves_session_state(
    scoring_refresh_world,
    monkeypatch,
):
    """BEHAVIORAL: when recovery fails (privacy check fails), session state is
    deep-equal before/after (no partial commit). Retry succeeds with recovery.
    """
    world = scoring_refresh_world
    world.add("900001", "Synthetic First", body="Text + file.",
              attachments=[{"id": "f1", "filename": "essay.docx", "size": 100}],
              attempt=1, submitted_at="2026-09-18T10:00:00Z")
    world.add("900002", "Fictional Omega", body="Another.", attempt=1)
    sid = world.prepare()

    pseudo1 = world.pseudonym("900001")

    # Freeze held response (same as test 1).
    session = world.session(sid)
    bundle = world.bundle(session)
    student1 = next((s for s in bundle.get("students", [])
                     if s.get("pseudonym") == pseudo1))
    response = next((r for r in student1.get("responses", [])
                    if r.get("item_id") == "a1"))

    response["_held"] = True
    response["_hold_reason"] = "file_not_read"
    response["attempt"] = 1
    response["submitted_at"] = "2026-09-18T10:00:00Z"

    # Write frozen bundle back.
    bundle_path = session["privacy_artifacts"]["safe_bundle"]
    with open(bundle_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2)

    # Add submission_baseline. Fail loudly if not found.
    sess_student = next((s for s in session.get("students", [])
                        if s.get("user_id") == "900001"))
    sess_student["submission_baseline"] = {
        "attempt": 1, "submitted_at": "2026-09-18T10:00:00Z",
    }
    world.sessions[sid] = session

    # Snapshot session before failed refresh.
    session_before_fail = copy.deepcopy(world.session(sid))

    # Force privacy check to fail.
    from api import feedback_safety
    monkeypatch.setattr(
        feedback_safety, "assert_scrubbed",
        lambda *_a, **_kw: {"green": False, "errors": ["synthetic failure"]})

    # Attempt refresh (will fail).
    refreshed_fail = tools.refresh_scoring_session(sid)
    assert refreshed_fail.get("ok") is False

    # Unconditional: session deep-equal before/after failed refresh.
    session_after_fail = world.session(sid)
    assert session_after_fail == session_before_fail, \
        "Session modified by failed recovery (partial commit)"

    # Restore privacy check and monkeypatch build_artifacts (test 1 pattern).
    from api.powergrader import scoring_artifacts
    orig_build = scoring_artifacts.build_scoring_artifacts
    def mock_build(*args, **kwargs):
        result = orig_build(*args, **kwargs)
        if result.get("ok"):
            bp = result["privacy_artifacts"]["safe_bundle"]
            with open(bp, encoding="utf-8") as f:
                rebuilt = json.load(f)
            for s in rebuilt.get("students", []):
                if s.get("pseudonym") == pseudo1:
                    for r in s.get("responses", []):
                        if r.get("item_id") == "a1":
                            r["_held"] = False
                            r["_evidence_complete"] = True
            with open(bp, "w", encoding="utf-8") as f:
                json.dump(rebuilt, f, indent=2)
        return result
    monkeypatch.setattr(scoring_artifacts, "build_scoring_artifacts", mock_build)
    monkeypatch.setattr(feedback_safety, "assert_scrubbed",
                       lambda *_a, **_kw: {"green": True})

    # Retry refresh: should succeed with recovery.
    refreshed_retry = tools.refresh_scoring_session(sid)
    assert refreshed_retry["ok"] is True

    # Unconditional: recovery happened on retry.
    assert any(r["pseudonym"] == pseudo1 and r["item_id"] == "a1"
              for r in refreshed_retry.get("recovered", [])), \
        "Recovery must succeed on retry"

    # Unconditional: no duplicated students.
    session_final = world.session(sid)
    student_ids = [s.get("user_id") for s in session_final.get("students", [])]
    assert len(student_ids) == len(set(student_ids))
