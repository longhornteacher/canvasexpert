"""Attempts-grant adapter: laws, the per-kind step contract, and one resume example.

Laws: an extension or attempts write is never blind, and a reopen never
duplicates its override. Contracts: the refusal-free step mapping is driven from
``KIND_TABLE``. The fake Canvas, vault, and step context live in
``api/tests/conftest.py``; the service-level contracts are in
``api/tests/test_attempts_grant.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.mirror import read_service
from api.operation_ledger import executor, models, operations
from api.operation_ledger.adapters import attempts_grant as module
from api.operation_ledger.adapters.attempts_grant import (
    KIND_TABLE, AttemptsGrantAdapter, classify_assignment, path_for,
)

WINDOW = {"due_at": "2099-01-10T05:00:00Z", "lock_at": "2099-01-11T05:00:00Z"}
IDS = {"course_id": "course-1", "assignment_id": "assignment-1", "quiz_id": "quiz-1"}

CLASSIFICATION = {
    "online upload": ({"submission_types": ["online_upload"]}, "regular"),
    "all three regular types": (
        {"submission_types": ["online_upload", "online_url", "online_text_entry"]}, "regular"),
    "classic quiz": ({"submission_types": ["online_quiz"], "quiz_id": 9}, "classic_quiz"),
    "new quiz": ({"submission_types": ["external_tool"], "is_quiz_lti_assignment": True},
                 "new_quiz"),
    "external tool": ({"submission_types": ["external_tool"]}, None),
    "classic without a quiz id": ({"submission_types": ["online_quiz"]}, None),
    "paper": ({"submission_types": ["on_paper"]}, None),
    "regular plus discussion": (
        {"submission_types": ["online_upload", "discussion_topic"]}, None),
    "nothing": ({"submission_types": []}, None),
}


@pytest.mark.parametrize("name", sorted(CLASSIFICATION))
def test_assignments_classify_into_the_three_supported_kinds_or_none(name):
    assignment, expected = CLASSIFICATION[name]

    assert classify_assignment(assignment) == expected


def _student_payload(kind, entries, *, reopen=None, extra=2):
    return {
        "course_id": "course-1", "assignment_id": "assignment-1", "kind": kind,
        "quiz_id": "quiz-1" if kind == "classic_quiz" else None,
        "scope": "students", "whole_class": {}, "reopen": reopen,
        "grant": {"students": [entry["pseudonym"] for entry in entries],
                  **({"extra_attempts": extra} if extra else {})},
        "entries": entries,
    }


def _entry(user_id, pseudonym, before, after):
    return {"user_id": user_id, "pseudonym": pseudonym,
            "before_extra": before, "after_extra": after}


def _run(payload, context, steps=None):
    target = {"course_id": "course-1", "failed_items": None,
              "steps": steps or [models.new_step(key) for key in module.step_keys(payload)]}
    return AttemptsGrantAdapter().execute(payload, target, {}, {}, context)


# name -> (before, live value on Canvas, writes expected, row outcome)
EXTENSION_LAW = {
    "live still equals before": (1, 1, 1, "done"),
    "absent equals a before of zero": (0, None, 1, "done"),
    "already at the target": (1, 3, 0, "already_at_target"),
    "changed to something else": (1, 9, 0, "changed_since_preview"),
}


@pytest.mark.parametrize("name", sorted(EXTENSION_LAW))
@pytest.mark.parametrize("kind", ["regular", "classic_quiz"])
def test_an_extension_write_is_never_blind(attempts_world, step_context, kind, name):
    """Law: write ``before + N`` only while the live value is still the frozen before."""
    before, live, writes, outcome = EXTENSION_LAW[name]
    world = attempts_world(kind)
    if live is not None:
        world.canvas.extra["student-1"] = live
    payload = _student_payload(kind, [_entry("student-1", "Pikachu", before, before + 2)])

    result = _run(payload, step_context)

    sends = world.canvas.writes("POST", "/extensions")
    assert len(sends) == writes
    if writes:
        body = sends[0][2]["quiz_extensions" if kind == "classic_quiz"
                           else "assignment_extensions"]
        assert body == [{"user_id": "student-1", "extra_attempts": before + 2}]
    assert [row["outcome"] for row in result["failed_items"]] == [outcome]
    assert result["state"] == ("applied" if writes or outcome == "already_at_target"
                               else "partial")


@pytest.mark.parametrize("kind", ["regular", "classic_quiz"])
def test_a_2xx_listing_no_row_for_the_student_fails_that_row_without_a_readback(
        attempts_world, kind):
    """Law: Canvas's own empty list means nothing was applied; the row fails, the rest go on."""
    world = attempts_world(kind)
    world.canvas.ineligible.add("student-2")  # Eevee cannot see the assignment
    preview = world.preview({"students": ["Pikachu", "Eevee"], "extra_attempts": 2})

    applied = world.apply(preview)

    assert applied["ok"] is False and applied["status"] == "partial"
    assert applied["counts"] == {"granted": 1, "skipped": 0, "failed": 1}
    eevee = next(row for row in applied["outcomes"] if row.get("pseudonym") == "Eevee")
    assert eevee["outcome"] == "failed" and eevee["reason"] == "extension_not_applied"
    assert "may not be able to see this assignment" in eevee["message"]
    assert world.canvas.extra == {"student-1": 2}
    assert len(world.canvas.writes("POST", "/extensions")) == 2


def test_a_whole_class_patch_blocks_as_drift_when_canvas_changed_since_preview(
        attempts_world):
    world = attempts_world("regular")
    preview = world.preview({"students": "all", "extra_attempts": 1, "reopen": WINDOW})
    world.canvas.assignment["allowed_attempts"] = 9

    applied = world.apply(preview)

    assert applied["ok"] is False and applied["status"] == "attention"
    assert applied["attention"]["code"] == "drift_detected"
    puts = world.canvas.writes("PUT", "")
    assert len(puts) == 1 and puts[0][2]["assignment"].keys() == {"due_at", "lock_at"}
    assert world.canvas.assignment["allowed_attempts"] == 9
    attempts_row = next(row for row in applied["outcomes"] if row["item"] == "attempts")
    assert attempts_row == {"item": "attempts", "before": 2, "after": 3,
                            "outcome": "skipped", "reason": "drift_detected"}


def test_an_existing_exact_override_is_adopted_never_duplicated(
        attempts_world, step_context):
    """Law: a reopen whose POST landed but was never checkpointed creates no second override."""
    world = attempts_world("regular")
    world.canvas.overrides.append({
        "id": 4242, "student_ids": ["student-1", "student-2"],
        "due_at": "2099-01-10T05:00:00+00:00", "lock_at": WINDOW["lock_at"]})
    payload = _student_payload(
        "regular", [_entry("student-1", "Pikachu", 0, 1), _entry("student-2", "Eevee", 0, 1)],
        reopen=WINDOW, extra=None)
    claimed = {**models.new_step("reopen:0"), "state": "claimed",
               "outbound_started_at": "2026-09-30T12:00:00+00:00"}

    result = _run(payload, step_context, steps=[claimed])

    assert result["state"] == "applied"
    assert world.canvas.writes("POST", "/overrides") == []
    assert result["steps"][0]["override_id"] == "4242"
    assert result["failed_items"][0]["outcome"] == "done"


# name -> (grant fields, families written in order). Two students on the list.
STEP_MAPPING = {
    "all attempts": ({"students": "all", "extra_attempts": 2}, ["attempts_all"]),
    "all reopen": ({"students": "all", "reopen": WINDOW}, ["dates_all"]),
    "all both": ({"students": "all", "extra_attempts": 2, "reopen": WINDOW},
                 ["dates_all", "attempts_all"]),
    "list attempts": ({"students": ["Pikachu", "Eevee"], "extra_attempts": 2},
                      ["grant", "grant"]),
    "list reopen": ({"students": ["Pikachu", "Eevee"], "reopen": WINDOW}, ["reopen"]),
    "list both": ({"students": ["Pikachu", "Eevee"], "extra_attempts": 2, "reopen": WINDOW},
                  ["reopen", "grant", "grant"]),
}
EXPECTED_STEPS = {
    "all attempts": ["patch_attempts:0"], "all reopen": ["patch_dates:0"],
    "all both": ["patch_dates:0", "patch_attempts:0"],
    "list attempts": ["grant:0", "grant:1"], "list reopen": ["reopen:0"],
    "list both": ["reopen:0", "grant:0", "grant:1"],
}


@pytest.mark.parametrize("name", sorted(STEP_MAPPING))
@pytest.mark.parametrize("kind", sorted(KIND_TABLE))
def test_each_kind_writes_the_transports_its_table_names(attempts_world, kind, name):
    grant, families = STEP_MAPPING[name]
    world = attempts_world(kind)
    before_total = world.canvas.attempts_total()

    preview = world.preview(grant)
    assert preview["ok"] is True
    steps = operations.get_operation(preview["operation_id"])["targets"][0]["steps"]
    assert [step["step_key"] for step in steps] == EXPECTED_STEPS[name]

    applied = world.apply(preview)

    assert applied["ok"] is True and applied["status"] == "applied"
    expected = [path_for(kind, family, IDS) for family in families]
    assert [(method, path) for method, path, _ in world.canvas.sends] == expected
    if "extra_attempts" in grant and grant["students"] == "all":
        assert world.canvas.attempts_total() == before_total + 2
    if "reopen" in grant and grant["students"] == "all":
        live = world.canvas.quiz if kind == "classic_quiz" else world.canvas.assignment
        assert (live["due_at"], live["lock_at"]) == (WINDOW["due_at"], WINDOW["lock_at"])
    if kind == "new_quiz" and "attempts_all" in families:
        settings = world.canvas.new_quiz["quiz_settings"]["multiple_attempts"]
        assert settings["score_to_keep"] == "latest"  # preserved from the live read


@pytest.mark.parametrize("kind", sorted(KIND_TABLE))
def test_unlimited_for_the_whole_class_is_expressed_the_way_each_kind_does(
        attempts_world, kind):
    world = attempts_world(kind)
    preview = world.preview({"students": "all", "extra_attempts": "unlimited"})

    assert world.apply(preview)["ok"] is True

    assert world.canvas.attempts_total() == -1
    body = world.canvas.sends[0][2]
    if kind == "new_quiz":
        settings = body["quiz_settings"]["multiple_attempts"]
        assert settings["attempt_limit"] is False and "max_attempts" not in settings
    else:
        assert next(iter(body.values()))["allowed_attempts"] == -1


# name -> (Canvas reply or error, row outcome, step state, later student still attempted)
NEW_QUIZ_VERDICTS = {
    "listed as successful": (
        lambda body: {"successful": [{"user_id": body[0]["user_id"]}], "failed": []},
        None, "done", "applied", True),
    "listed as failed": (
        lambda body: {"successful": [], "failed": [{"user_id": body[0]["user_id"]}]},
        None, "accommodation_failed", "failed", True),
    "in neither list": (
        lambda body: {"successful": [], "failed": []},
        None, "accommodation_unconfirmed", "sent_unknown", False),
    "rejected outright": (None, "HTTP 422: nope", "accommodation_rejected", "failed", True),
    "transport unknown": (None, "timed out", "accommodation_uncertain", "sent_unknown", False),
}


@pytest.mark.parametrize("name", sorted(NEW_QUIZ_VERDICTS))
def test_a_new_quiz_accommodation_is_decided_by_canvas_lists_not_a_readback(
        attempts_world, step_context, name):
    reply, error, outcome, state, continues = NEW_QUIZ_VERDICTS[name]
    world = attempts_world("new_quiz")
    if reply:
        world.canvas.accommodation_reply = reply
    else:
        world.canvas.fault("POST", "/accommodations", error)
    payload = _student_payload("new_quiz", [_entry("student-1", "Pikachu", None, 2),
                                            _entry("student-2", "Eevee", None, 2)])

    result = _run(payload, step_context)

    assert result["steps"][0]["state"] == state
    assert result["failed_items"][0]["outcome"] == outcome
    assert len(world.canvas.writes("POST", "/accommodations")) == (2 if continues else 1)
    assert world.canvas.sends[0][2] == [{"user_id": "student-1", "extra_attempts": 2}]
    if not continues:
        assert result["state"] == "sent_unknown"


@pytest.mark.parametrize("error, continues", [("HTTP 422: nope", True),
                                              ("timed out", False)], ids=["4xx", "unknown"])
def test_a_definite_rejection_fails_one_row_and_a_transport_unknown_stops(
        attempts_world, step_context, error, continues):
    world = attempts_world("regular")
    world.canvas.fault("POST", "/extensions", error)
    payload = _student_payload("regular", [_entry("student-1", "Pikachu", 0, 2),
                                           _entry("student-2", "Eevee", 0, 2)])

    result = _run(payload, step_context)

    assert len(world.canvas.writes("POST", "/extensions")) == (2 if continues else 1)
    if continues:
        assert result["state"] == "partial"
        assert [row["outcome"] for row in result["failed_items"]] == [
            "extension_rejected", "done"]
    else:
        assert result["state"] == "sent_unknown"
        assert result["error_code"] == "extension_uncertain"


def test_a_resume_after_an_interruption_between_the_override_and_the_first_grant_makes_no_second_override(
        attempts_world):
    """Example: the first grant's reply is lost after it lands; resume finishes the rest."""
    world = attempts_world("regular")
    grant = {"students": ["Pikachu", "Eevee"], "extra_attempts": 2, "reopen": WINDOW}
    preview = world.preview(grant)
    world.canvas.fault("POST", "/extensions", "timed out", lands=True)

    interrupted = world.apply(preview)

    assert interrupted["ok"] is False and interrupted["status"] == "attention"
    assert len(world.canvas.overrides) == 1
    assert world.canvas.extra == {"student-2": 2}  # Eevee's grant landed, reply lost
    stored = operations.get_operation(preview["operation_id"])
    assert stored["targets"][0]["state"] == "sent_unknown"
    assert stored["targets"][0]["steps"][0]["override_id"]

    resumed = executor.retry_operation(preview["operation_id"])

    assert resumed["ok"] is True and resumed["status"] == "applied"
    assert len(world.canvas.overrides) == 1
    assert len(world.canvas.writes("POST", "/overrides")) == 1
    assert len(world.canvas.writes("POST", "/extensions")) == 2  # no second Eevee write
    assert world.canvas.extra == {"student-1": 2, "student-2": 2}
    final = world.module._apply_projection(
        preview["operation_id"], resumed, operations.get_operation(preview["operation_id"]))
    assert final["counts"] == {"granted": 3, "skipped": 0, "failed": 0}


# name -> (live extra for student-1, reconciled state)
RECONCILE = {
    "landed": (3, "applied"),
    "did not land": (1, "pending"),
    "changed to something else": (9, "sent_unknown"),
}


@pytest.mark.parametrize("name", sorted(RECONCILE))
def test_recovery_proves_a_claimed_grant_from_the_live_value(attempts_world, name):
    live, expected = RECONCILE[name]
    world = attempts_world("regular")
    world.canvas.extra["student-1"] = live
    payload = _student_payload("regular", [_entry("student-1", "Pikachu", 1, 3)])
    step = {**models.new_step("grant:0"), "state": "claimed"}

    result = AttemptsGrantAdapter().reconcile(
        payload, {"course_id": "course-1", "steps": [step]}, {})

    assert result["state"] == expected


def test_a_new_quiz_grant_cannot_be_reconciled_so_recovery_leaves_it_unresolved(
        attempts_world):
    attempts_world("new_quiz")
    payload = _student_payload("new_quiz", [_entry("student-1", "Pikachu", None, 2)])
    step = {**models.new_step("grant:0"), "state": "sent_unknown"}

    result = AttemptsGrantAdapter().reconcile(
        payload, {"course_id": "course-1", "steps": [step]}, {})

    assert result["state"] == "sent_unknown"


def _mirror(monkeypatch, *, synced_at, rows):
    scope = {"records": [{"id": "student-1"}, {"id": "student-2"}],
             "last_success_at": synced_at, "state": "current"}
    monkeypatch.setattr(read_service, "private_roster", lambda *a, **k: dict(scope))
    monkeypatch.setattr(read_service, "private_assignments",
                        lambda *a, **k: {**scope, "records": []})
    monkeypatch.setattr(read_service, "private_submissions",
                        lambda *a, **k: {**scope, "records": rows})


def test_current_students_on_the_assignment_come_from_a_fresh_mirror(monkeypatch):
    _mirror(monkeypatch, synced_at=datetime.now(timezone.utc).isoformat(), rows=[
        {"assignment_id": "assignment-1", "user_id": "student-1"},
        {"assignment_id": "other", "user_id": "student-2"},
        {"assignment_id": "assignment-1", "user_id": "withdrawn"},
    ])

    result = module._mirror_students("course-1", "assignment-1")

    assert result["on_assignment_ids"] == ["student-1"]
    assert {row["id"] for row in result["roster"]} == {"student-1", "student-2"}


def test_a_mirror_outside_the_freshness_window_blocks_the_preview(monkeypatch):
    _mirror(monkeypatch, synced_at="2020-01-01T00:00:00Z", rows=[])

    result = module._mirror_students("course-1", "assignment-1")

    assert result["blocking_error"] == "freshness_attention"
