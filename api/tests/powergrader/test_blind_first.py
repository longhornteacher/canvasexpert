"""Blind-first scoring: the AI's score is withheld until the teacher commits their own."""

import copy

from api.powergrader import blind_first, session_actions


def _student(user_id="9001", **overrides):
    student = {
        "user_id": user_id,
        "real_name": "Ada Lovelace",
        "ai_score": 8.0,
        "ai_feedback": "Clear thesis, thin evidence.",
        "teacher_score": None,
        "teacher_feedback": "",
        "status": "pending",
    }
    student.update(overrides)
    return student


def _session(mode="assisted", enabled=True, threshold=1.0, students=None):
    session = {
        "session_id": "sid",
        "mode": mode,
        "course_id": "1",
        "assignment_id": "2",
        "points_possible": 10,
        "students": students if students is not None else [_student()],
    }
    if enabled is not None:
        session["blind_first"] = {"enabled": enabled, "threshold": threshold}
    return session


def _loaders(session):
    """Injected loader/saver pair over one in-memory session, as the routes do."""
    saved = []

    def load_session(_session_id):
        return session

    def save_session(value):
        saved.append(copy.deepcopy(value))

    return load_session, save_session, saved


# ── Projection ───────────────────────────────────────────────────────────

def test_disabled_session_is_returned_untouched():
    """An existing session must behave exactly as it did before this feature."""
    session = _session(enabled=False)
    assert blind_first.project_session(session) is session


def test_session_without_blind_first_key_is_returned_untouched():
    session = _session(enabled=None)
    assert blind_first.project_session(session) is session


def test_fast_mode_is_ineligible_even_if_enabled_is_set():
    """Score myself has no AI suggestion, so there is nothing to withhold."""
    session = _session(mode="fast", enabled=True)
    assert blind_first.is_eligible(session) is False
    assert blind_first.is_active(session) is False
    assert blind_first.project_session(session) is session


def test_unrevealed_ai_score_is_withheld_from_the_projection():
    view = blind_first.project_session(_session())
    student = view["students"][0]
    assert student["ai_score"] is None
    assert student["ai_feedback"] == ""
    assert student["blind_withheld"] is True
    # Existence is disclosed so the queue can tell "hidden" from "never scored".
    assert student["blind_has_ai_suggestion"] is True
    assert view["blind_first_withheld"] == 1


def test_projection_never_mutates_the_stored_session():
    """The caller holds the dict the session file is written from."""
    session = _session()
    before = copy.deepcopy(session)
    blind_first.project_session(session)
    assert session == before
    assert session["students"][0]["ai_score"] == 8.0
    assert session["students"][0]["ai_feedback"] == "Clear thesis, thin evidence."


def test_revealed_student_keeps_the_ai_score_visible():
    session = _session(students=[_student(ai_revealed=True)])
    view = blind_first.project_session(session)
    assert view["students"][0]["ai_score"] == 8.0
    assert "blind_withheld" not in view["students"][0]


def test_a_student_the_model_never_scored_is_marked_as_such():
    session = _session(students=[_student(ai_score=None, ai_feedback="")])
    student = blind_first.project_session(session)["students"][0]
    assert student["blind_withheld"] is True
    assert student["blind_has_ai_suggestion"] is False


def test_mixed_session_withholds_only_the_unrevealed():
    session = _session(students=[
        _student("9001"),
        _student("9002", ai_revealed=True),
        _student("9003"),
    ])
    view = blind_first.project_session(session)
    assert view["blind_first_withheld"] == 2
    assert view["students"][1]["ai_score"] == 8.0
    assert view["students"][0]["ai_score"] is None
    assert view["students"][2]["ai_score"] is None


# ── Reveal ───────────────────────────────────────────────────────────────

def test_reveal_records_the_blind_score_and_returns_the_ai_score():
    session = _session()
    load, save, saved = _loaders(session)
    payload, status = blind_first.reveal(
        "sid", user_id="9001", blind_score="7", blind_feedback="Needs evidence.",
        load_session=load, save_session=save,
    )
    assert status == 200 and payload["ok"] is True
    assert payload["ai_score"] == 8.0
    assert payload["ai_feedback"] == "Clear thesis, thin evidence."
    assert payload["blind_score"] == 7.0
    assert payload["blind_committed"] is True
    assert payload["delta"] == 1.0
    assert payload["verdict"] == "agrees"
    student = session["students"][0]
    assert student["ai_revealed"] is True
    assert student["blind_score"] == 7.0
    assert student["blind_feedback"] == "Needs evidence."
    assert student["blind_source"] == "reveal"
    assert saved, "the reveal must persist"


def test_disagreement_past_the_threshold_is_flagged():
    session = _session(threshold=1.0)
    load, save, _ = _loaders(session)
    payload, _ = blind_first.reveal(
        "sid", user_id="9001", blind_score="4", blind_feedback="",
        load_session=load, save_session=save,
    )
    assert payload["delta"] == 4.0
    assert payload["verdict"] == "disagrees"


def test_threshold_is_inclusive():
    session = _session(threshold=2.0)
    load, save, _ = _loaders(session)
    payload, _ = blind_first.reveal(
        "sid", user_id="9001", blind_score="6", blind_feedback="",
        load_session=load, save_session=save,
    )
    assert payload["delta"] == 2.0
    assert payload["verdict"] == "agrees"


def test_reveal_without_a_score_is_recorded_as_uncommitted_not_refused():
    """Refusing would trap a teacher on an empty submission."""
    session = _session()
    load, save, _ = _loaders(session)
    payload, status = blind_first.reveal(
        "sid", user_id="9001", blind_score="", blind_feedback="",
        load_session=load, save_session=save,
    )
    assert status == 200 and payload["ok"] is True
    assert payload["blind_committed"] is False
    assert payload["delta"] is None
    assert payload["verdict"] == "uncommitted"
    assert payload["ai_score"] == 8.0


def test_second_reveal_never_overwrites_the_blind_capture():
    """The post-reveal score was formed after seeing the model; it is not blind."""
    session = _session()
    load, save, _ = _loaders(session)
    blind_first.reveal("sid", user_id="9001", blind_score="4", blind_feedback="Mine.",
                       load_session=load, save_session=save)
    payload, _ = blind_first.reveal("sid", user_id="9001", blind_score="8",
                                    blind_feedback="Copied the model.",
                                    load_session=load, save_session=save)
    assert payload["already_revealed"] is True
    assert payload["blind_score"] == 4.0
    assert payload["delta"] == 4.0
    assert session["students"][0]["blind_feedback"] == "Mine."


def test_reveal_on_a_missing_student_is_refused():
    session = _session()
    load, save, _ = _loaders(session)
    payload, status = blind_first.reveal(
        "sid", user_id="nope", blind_score="7", blind_feedback="",
        load_session=load, save_session=save,
    )
    assert status == 200 and payload["ok"] is False
    assert payload["code"] == "student_not_found"


def test_reveal_is_refused_in_fast_mode():
    session = _session(mode="fast")
    load, save, _ = _loaders(session)
    payload, _ = blind_first.reveal(
        "sid", user_id="9001", blind_score="7", blind_feedback="",
        load_session=load, save_session=save,
    )
    assert payload["ok"] is False
    assert payload["code"] == "blind_first_ineligible"


# ── Implicit capture on approve-without-reveal ───────────────────────────

def test_approving_without_revealing_records_a_blind_score():
    session = _session()
    load, save, _ = _loaders(session)
    session_actions.save_grade(
        "sid", user_id="9001", teacher_score="6", teacher_feedback="Solid.",
        status="approved", load_session=load, save_session=save,
    )
    student = session["students"][0]
    assert student["blind_score"] == 6.0
    assert student["blind_committed"] is True
    assert student["blind_delta"] == 2.0
    assert student["blind_source"] == "approved_without_reveal"
    # They never saw the suggestion, so it stays withheld if they come back.
    assert student.get("ai_revealed") is not True
    assert blind_first.is_withheld(session, student) is True


def test_an_implicit_capture_survives_a_later_reveal():
    session = _session()
    load, save, _ = _loaders(session)
    session_actions.save_grade(
        "sid", user_id="9001", teacher_score="6", teacher_feedback="Solid.",
        status="approved", load_session=load, save_session=save,
    )
    payload, _ = blind_first.reveal(
        "sid", user_id="9001", blind_score="8", blind_feedback="Now anchored.",
        load_session=load, save_session=save,
    )
    assert payload["blind_score"] == 6.0
    assert payload["delta"] == 2.0
    assert session["students"][0]["blind_source"] == "approved_without_reveal"


def test_skipping_records_no_blind_score():
    session = _session()
    load, save, _ = _loaders(session)
    session_actions.save_grade(
        "sid", user_id="9001", teacher_score="", teacher_feedback="",
        status="skipped", load_session=load, save_session=save,
    )
    assert blind_first.has_blind_capture(session["students"][0]) is False


def test_save_grade_records_nothing_when_blind_first_is_off():
    session = _session(enabled=False)
    load, save, _ = _loaders(session)
    session_actions.save_grade(
        "sid", user_id="9001", teacher_score="6", teacher_feedback="Solid.",
        status="approved", load_session=load, save_session=save,
    )
    student = session["students"][0]
    assert blind_first.has_blind_capture(student) is False
    assert student["teacher_score"] == 6.0


# ── Settings ─────────────────────────────────────────────────────────────

def test_enabling_sets_a_timestamp_and_keeps_the_threshold():
    session = _session(enabled=False, threshold=2.5)
    load, save, _ = _loaders(session)
    payload, status = blind_first.set_enabled(
        "sid", enabled="true", threshold="", load_session=load, save_session=save,
    )
    assert status == 200 and payload["ok"] is True
    assert session["blind_first"]["enabled"] is True
    assert session["blind_first"]["threshold"] == 2.5
    assert session["blind_first"]["enabled_at"]


def test_disabling_never_un_reveals_a_student():
    """A score the teacher has seen cannot be un-seen."""
    session = _session(students=[_student(ai_revealed=True, blind_score=7.0,
                                          blind_recorded_at="2026-08-07T00:00:00+00:00")])
    load, save, _ = _loaders(session)
    blind_first.set_enabled("sid", enabled="false", threshold="",
                            load_session=load, save_session=save)
    student = session["students"][0]
    assert student["ai_revealed"] is True
    assert student["blind_score"] == 7.0


def test_negative_threshold_is_refused():
    session = _session()
    load, save, _ = _loaders(session)
    payload, _ = blind_first.set_enabled(
        "sid", enabled="true", threshold="-1", load_session=load, save_session=save,
    )
    assert payload["ok"] is False
    assert payload["code"] == "invalid_threshold"


def test_fast_mode_cannot_enable_blind_first():
    session = _session(mode="fast", enabled=False)
    load, save, _ = _loaders(session)
    payload, _ = blind_first.set_enabled(
        "sid", enabled="true", threshold="", load_session=load, save_session=save,
    )
    assert payload["ok"] is False
    assert payload["code"] == "blind_first_ineligible"


def test_summary_counts_disagreements_against_the_threshold():
    session = _session(threshold=1.0, students=[
        _student("9001", ai_revealed=True, blind_score=8.0, blind_committed=True,
                 blind_delta=0.0, blind_recorded_at="t"),
        _student("9002", ai_revealed=True, blind_score=3.0, blind_committed=True,
                 blind_delta=5.0, blind_recorded_at="t"),
        _student("9003"),
    ])
    result = blind_first.summary(session)
    assert result["ai_scored"] == 3
    assert result["revealed"] == 2
    assert result["withheld"] == 1
    assert result["compared"] == 2
    assert result["disagreements"] == 1


# ── Write boundary ───────────────────────────────────────────────────────

def test_blind_capture_never_reaches_the_canvas_payload():
    """Blind scores are teacher-only session data, not a Canvas write."""
    student = _student(
        teacher_score=7.0, teacher_feedback="Good work.",
        blind_score=4.0, blind_feedback="Private first pass.",
        blind_delta=4.0, blind_committed=True, blind_recorded_at="t", ai_revealed=True,
    )
    payload = session_actions._payload(student)
    assert payload["submission"]["posted_grade"] == "7"
    assert payload["comment"]["text_comment"] == "Good work."
    serialized = repr(payload)
    for leak in ("blind_", "Private first pass", "4.0"):
        assert leak not in serialized, f"{leak!r} must not reach Canvas"
