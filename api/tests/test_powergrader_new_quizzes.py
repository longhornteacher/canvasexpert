"""Synthetic New Quiz report coverage; fixture data intentionally contains no PII."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api.powergrader import new_quiz_fetch as nq
from api.feedback_artifacts import pseudonymize_submissions
from api.feedback_vault import Vault
from api.powergrader import session_actions
from api.powergrader import new_quiz_grader
from api.powergrader import new_quiz_csv


class _Response:
    def __init__(self, status, payload=None, bad_json=False):
        self.status_code, self.payload, self.bad_json = status, payload, bad_json
    def json(self):
        if self.bad_json:
            raise ValueError("synthetic malformed json")
        return self.payload


class _Session:
    def __init__(self, *, posts=(), gets=()):
        self.posts, self.gets = list(posts), list(gets)
        self.post_calls, self.get_calls = 0, 0
    def post(self, *args, **kwargs):
        self.post_calls += 1
        return self.posts.pop(0)
    def get(self, *args, **kwargs):
        self.get_calls += 1
        return self.gets.pop(0)


class _NativeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self.payload = payload
        self.text = text
        self.url = ""
    def json(self):
        return self.payload


class _NativeSession:
    def __init__(self):
        self.cookies = {}
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if "sessionless_launch" in url:
            return _NativeResponse(payload={"url": "https://canvas.invalid/new-quiz-launch"})
        if url.endswith("new-quiz-launch"):
            env = {"NEW_QUIZZES": {"params": {"backend_url": "https://nq.invalid", "resource": "x"},
                                  "signature": "signature"}, "ACCOUNT_ID": "account"}
            return _NativeResponse(text=f"<script>ENV = {__import__('json').dumps(env)};</script>")
        if "/participants" in url:
            return _NativeResponse(payload=[{"canvas_user_id": "fake-user", "participant_sessions": [{"id": "ps-1"}]}])
        if "/participant_sessions/ps-1/results" in url:
            return _NativeResponse(payload={"quiz_host": "https://quiz.invalid", "result_token": "raw-signature", "quiz_session_id": "qs-1"})
        if url.endswith("/api/quiz_sessions/qs-1"):
            return _NativeResponse(payload={"authoritative_result": {"id": "ar-1", "attempt": 2}})
        if "session_item_results" in url:
            return _NativeResponse(payload=[{"item_id": "file-item", "scored_data": {"value": [
                {"id": "file-1", "name": "answer.png", "size": 4, "url": "https://signed.invalid/one"}
            ]}}])
        raise AssertionError(f"unexpected native GET {url}")
    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if "/api/v1/jwts" in url:
            return _NativeResponse(payload={"token": "workflow-jwt"})
        if url.endswith("/api/native/launch"):
            return _NativeResponse(payload={"access_token": "native-token", "entry_path": {"resourceId": "resource"}})
        raise AssertionError(f"unexpected native POST {url}")


def test_normalize_uses_nested_entry_id_and_latest_attempt_safe_text(tmp_path):
    core = [{"user_id": "fake-01", "submitted_at": "2026-01-02T00:00:00Z", "score": 4,
             "late": True, "seconds_late": 60, "user": {"name": "Fictional Student"}}]
    rows = [
        {"attempt": 1, "submitted_at": "2026-01-01T00:00:00Z", "student_data": {"id": "fake-01"}, "item_responses": [{"item_id": "nested-essay", "answer": "<p>old</p>"}]},
        {"attempt": 2, "submitted_at": "2026-01-02T00:00:00Z", "student_data": {"id": "fake-01"}, "item_responses": [{"item_id": "nested-essay", "answer": "<p>latest answer</p>"}, {"item_id": "nested-file", "files": [{"filename": "fictional.pdf", "url": "never-fetch"}]}]},
    ]
    items = [{"id": "outer-wrong", "entry": [{"id": "nested-essay", "item_type": "essay", "item_body": "<p>Explain.</p>", "points_possible": 5}, {"id": "nested-file", "item_type": "file_upload", "item_body": "Upload.", "points_possible": 1}]}]
    subs = nq.normalize(core, rows, items)
    assert subs[0]["new_quiz_attempt"] == 2
    assert "latest answer" in subs[0]["new_quiz_items"][0]["raw_html_answer"]
    file_placeholder = subs[0]["new_quiz_items"][1]["files"][0]
    assert file_placeholder["filename"] == "fictional.pdf"
    assert file_placeholder["download_status"] == "pending"
    assert "url" not in file_placeholder
    assert subs[0]["attachments"][0]["item_id"] == "nested-file"
    vault = Vault(str(tmp_path / "vault.json"))
    bundle = pseudonymize_submissions(subs, vault, "Fictional Quiz")
    responses = bundle["students"][0]["responses"]
    # The PDF upload has no locally-extracted text, so the item stays local.
    assert [item["response"] for item in responses] == ["latest answer"]
    assert "fake-01" not in str(bundle)


def test_normalize_live_shape_maps_slug_points_and_seeds_upload_expectation():
    """Live /items entries carry interaction_type_slug + OUTER points, and live
    reports describe uploads as filename-only answers with item_type but no
    file refs (shape captured 2026-07-14 against a real course)."""
    core = [{"user_id": "fake-01", "submitted_at": "2026-01-02T00:00:00Z", "user": {"name": "Fictional Student"}}]
    rows = [{
        "student_data": {"id": "fake-01", "attempt": 1, "submitted_at": "2026-01-02T00:00:00Z"},
        "item_responses": [
            {"item_id": "entry-essay", "item_type": "essay", "answer": "<p>an answer</p>", "score": None},
            {"item_id": "entry-upload", "item_type": "file-upload", "answer": "fictional-notes.txt", "score": None},
        ],
    }]
    items = [
        {"id": "outer-1", "points_possible": 50.0, "entry_type": "Item",
         "entry": {"id": "entry-essay", "interaction_type_slug": "essay", "item_body": "<p>Explain.</p>"}},
        {"id": "outer-2", "points_possible": 50.0, "entry_type": "Item",
         "entry": {"id": "entry-upload", "interaction_type_slug": "file-upload", "item_body": "<p>Upload.</p>"}},
    ]
    subs = nq.normalize(core, rows, items)
    essay, upload = subs[0]["new_quiz_items"]
    assert essay["type"] == "essay" and essay["possible"] == 50.0
    assert upload["type"] == "file-upload" and upload["possible"] == 50.0
    seeded = upload["files"][0]
    assert seeded["filename"] == "fictional-notes.txt"
    assert seeded["download_status"] == "pending"
    assert "url" not in seeded
    assert subs[0]["attachments"][0]["filename"] == "fictional-notes.txt"


def test_pseudonymize_new_quiz_uploads_use_extracted_text_or_stay_local(tmp_path):
    """Upload items reach the AI only as locally-extracted text; unreadable
    uploads (image/PDF/failed) are excluded entirely, never sent as filenames."""
    extracted = tmp_path / "notes__extracted.txt"
    extracted.write_text("Synthetic extracted essay text.", encoding="utf-8")
    subs = [{
        "user_id": "fake-01", "user": {"name": "Fictional Student"},
        "new_quiz_items": [
            {"item_id": "essay-1", "type": "essay", "prompt": "<p>Explain.</p>",
             "raw_html_answer": "<p>typed answer</p>", "possible": 50},
            {"item_id": "upload-1", "type": "file-upload", "prompt": "<p>Upload notes.</p>",
             "raw_html_answer": "notes.txt", "possible": 25,
             "files": [{"filename": "notes.txt", "extracted_text_path": str(extracted)}]},
            {"item_id": "upload-2", "type": "file-upload", "prompt": "<p>Upload a photo.</p>",
             "raw_html_answer": "photo.jpg", "possible": 25,
             "files": [{"filename": "photo.jpg", "extraction_status": "validated"}]},
        ],
    }, {
        "user_id": "fake-02", "user": {"name": "Second Fictional"},
        "new_quiz_items": [
            {"item_id": "upload-2", "type": "file-upload", "prompt": "<p>Upload a photo.</p>",
             "raw_html_answer": "other.jpg", "possible": 25,
             "files": [{"filename": "other.jpg"}]},
        ],
    }]
    vault = Vault(str(tmp_path / "vault.json"))
    bundle = pseudonymize_submissions(subs, vault, "Fictional Quiz")
    students = bundle["students"]
    assert len(students) == 1                       # photo-only student stays local
    responses = students[0]["responses"]
    assert [r["item_id"] for r in responses] == ["essay-1", "upload-1"]
    assert responses[0]["response"] == "typed answer"
    assert "Synthetic extracted essay text." in responses[1]["response"]
    assert responses[1]["possible"] == 25
    assert "photo.jpg" not in str(bundle)           # ignored uploads leave no trace in the AI payload
    assert students[0]["local_attachments"] == []   # New Quiz files never enter the media lane


def test_normalize_uses_nested_student_analysis_attempt_and_timestamp():
    core = [{"user_id": "fictional-user", "submitted_at": "2026-01-02T00:00:00Z"}]
    rows = [
        {"student_data": {"id": "fictional-user", "attempt": 1, "submitted_at": "2026-01-01T00:00:00Z"}, "item_responses": [{"item_id": "essay", "answer": "older"}]},
        {"student_data": {"id": "fictional-user", "attempt": 2, "submitted_at": "2026-01-02T00:00:00Z"}, "item_responses": [{"item_id": "essay", "answer": "nested latest"}]},
    ]
    items = [{"entry": [{"id": "essay", "item_body": "Prompt", "points_possible": 1}]}]
    normalized = nq.normalize(core, rows, items)
    assert normalized[0]["new_quiz_attempt"] == 2
    assert normalized[0]["new_quiz_reported_at"] == "2026-01-02T00:00:00Z"
    assert normalized[0]["new_quiz_items"][0]["raw_html_answer"] == "nested latest"


def test_timestamp_parser_fails_closed():
    assert nq._parse_time("not-a-date") is None
    assert nq._parse_time("2026-01-01T00:00:00Z") is not None


def test_report_transport_accepts_wrapped_and_bare_progress_with_bounded_409():
    for created in ({"progress": {"url": "https://fake/progress"}}, {"url": "https://fake/progress"}):
        session = _Session(posts=[_Response(201, created)], gets=[
            _Response(409), _Response(200, {"progress": {"workflow_state": "completed", "results": {"url": "https://fake/result"}}}),
            _Response(409), _Response(200, [{"student_data": {"id": "fake"}}]),
        ])
        sleeps = []
        rows, error = nq._create_report(session, "https://base", "course", "quiz", {}, lambda seconds: sleeps.append(seconds))
        assert error is None and rows == [{"student_data": {"id": "fake"}}]
        assert sleeps == [nq.POLL_SECONDS, nq.POLL_SECONDS]


def test_report_transport_errors_are_actionable_and_bounded():
    for status in (401, 403):
        rows, error = nq._create_report(_Session(posts=[_Response(status)]), "https://base", "c", "q", {}, lambda _: None)
        assert rows is None and "active Canvas enrollment" in error
    failed = _Session(posts=[_Response(201, {"url": "progress"})], gets=[_Response(200, {"workflow_state": "failed"})])
    assert nq._create_report(failed, "https://base", "c", "q", {}, lambda _: None)[0] is None
    malformed = _Session(posts=[_Response(201, None, bad_json=True)])
    assert nq._create_report(malformed, "https://base", "c", "q", {}, lambda _: None)[0] is None
    timeout = _Session(posts=[_Response(201, {"url": "progress"})], gets=[_Response(200, {"workflow_state": "queued"})] * nq.POLL_LIMIT)
    assert nq._create_report(timeout, "https://base", "c", "q", {}, lambda _: None)[0] is None
    retries = _Session(gets=[_Response(409)] * (nq.REPORT_409_RETRIES + 1))
    assert nq._get_report(retries, "https://base", "result", {}, lambda _: None)[0] is None
    assert retries.get_calls == nq.REPORT_409_RETRIES + 1


def test_freshness_regenerates_once_then_fails_closed(monkeypatch):
    monkeypatch.setattr(nq, "canvas_headers", lambda: ({"Authorization": "Bearer synthetic"}, "https://base"))
    core = [{"user_id": "fake", "submitted_at": "2026-01-03T00:00:00Z", "user": {"name": "Fictional"}}]
    item = [{"entry": [{"id": "essay", "item_body": "Prompt", "points_possible": 1}]}]
    stale = [{"student_data": {"id": "fake", "attempt": 1, "submitted_at": "2026-01-01T00:00:00Z"}, "item_responses": [{"item_id": "essay", "answer": "old"}]}]
    session = _Session(posts=[_Response(201, {"url": "p1"}), _Response(201, {"url": "p2"})], gets=[
        _Response(200, item), _Response(200, {"workflow_state": "completed", "results": {"url": "r1"}}), _Response(200, stale),
        _Response(200, {"workflow_state": "completed", "results": {"url": "r2"}}), _Response(200, stale),
    ])
    subs, error = nq.fetch("course", "quiz", core, session=session, sleep=lambda _: None)
    assert subs is None and "still older" in error
    assert session.post_calls == 2


def test_new_quiz_server_gates_block_assignment_total_push_without_canvas():
    session = {"canvas_writeback_supported": False, "students": []}
    load = lambda _: session
    payload, _ = session_actions.review_push("synthetic", user_ids="[]", load_session=load, save_session=lambda _: None, canvas_get=lambda *_args, **_kwargs: None)
    assert payload["code"] == "canvas_writeback_unsupported"
    payload, _ = session_actions.push_grades("synthetic", user_ids="[]", review_token="x", load_session=load, save_session=lambda _: None, canvas_send=lambda *_args: None, canvas_get=lambda *_args, **_kwargs: None)
    assert payload["code"] == "canvas_writeback_unsupported"


def test_native_transport_accepts_live_result_shape(tmp_path, monkeypatch):
    """Live results use host/token/quiz_api_quiz_session_id, no attempt inside
    authoritative_result (shape captured 2026-07-14 against a real course)."""
    monkeypatch.setattr(nq.workspace, "workspace_root", lambda: str(tmp_path))

    class _LiveSession(_NativeSession):
        def get(self, url, **kwargs):
            if "/participant_sessions/ps-1/results" in url:
                return _NativeResponse(payload={
                    "host": "https://quiz.invalid", "token": "raw-signature",
                    "quiz_api_quiz_session_id": "qs-1", "attempt_history": [],
                })
            if url.endswith("/api/quiz_sessions/qs-1"):
                return _NativeResponse(payload={
                    "attempt": 2, "authoritative_result": {"id": "ar-1"},
                })
            return super().get(url, **kwargs)

    target = {
        "user_id": "fake-user", "user": {"name": "Fictional Student"},
        "assignment": {"name": "Synthetic Upload"}, "new_quiz_attempt": 2,
        "new_quiz_items": [{"item_id": "file-item", "files": [{"filename": "answer.png"}]}],
        "attachments": [],
    }

    def fake_download(url, dest, *, declared_size=None):
        Path(dest).write_bytes(b"data")
        return {"actual_size": 4}

    count, error = nq._native_file_transport(
        _LiveSession(), "https://canvas.invalid", {"Authorization": "Bearer synthetic"},
        "course-1", "assignment-1", [target], session_id="session-1", download=fake_download,
    )
    assert error is None and count == 1
    assert target["attachments"][0]["download_status"] == "downloaded"


def test_native_file_transport_joins_same_attempt_and_drops_signed_url(tmp_path, monkeypatch):
    monkeypatch.setattr(nq.workspace, "workspace_root", lambda: str(tmp_path))
    session = _NativeSession()
    target = {
        "user_id": "fake-user", "user": {"name": "Fictional Student"},
        "assignment": {"name": "Synthetic Upload"}, "new_quiz_attempt": 2,
        "new_quiz_items": [{"item_id": "file-item", "files": [{"filename": "answer.png"}]}],
        "attachments": [],
    }

    def fake_download(url, dest, *, declared_size=None):
        assert url.startswith("https://")
        Path(dest).write_bytes(b"data")
        return {"actual_size": 4}

    count, error = nq._native_file_transport(
        session, "https://canvas.invalid", {"Authorization": "Bearer synthetic"},
        "course-1", "assignment-1", [target], session_id="session-1", download=fake_download,
    )
    assert error is None and count == 1
    blob = __import__('json').dumps(target)
    assert "signed.invalid" not in blob
    file_meta = target["attachments"][0]
    assert file_meta["item_id"] == "file-item"
    assert file_meta["attempt"] == 2
    assert Path(file_meta["local_path"]).exists()


def test_new_quiz_feedback_composition_keeps_teacher_and_ta_separate():
    assert new_quiz_grader.compose_feedback("", "TA block") == "Autofeedback from an automated assistant:\n\nTA block"
    combined = new_quiz_grader.compose_feedback("My note", "TA block")
    assert combined == "MY FEEDBACK\n\nMy note\n\n-------\n\nAutofeedback from an automated assistant:\n\nTA block"


def test_csv_fallback_session_keeps_digest_and_minimum_private_provenance():
    raw = (Path(__file__).parent / "fixtures" / "student_analysis_sample.csv").read_bytes()
    session = new_quiz_csv.make_csv_session(
        session_id="synthetic", course_id="course-1", assignment_id="assignment-1",
        assignment_name="Synthetic quiz", raw=raw,
    )
    provenance = session["new_quiz_csv_provenance"]
    assert provenance["digest"] and len(provenance["digest"]) == 64
    assert provenance["course_id"] == "course-1"
    assert set(provenance["rows"][0]) == {"user_id", "attempt", "item_ids"}
    assert "Name,ID,SISID" not in json.dumps(session)
    assert all(student["speedgrader_required"] for student in session["students"])


def test_csv_fallback_rejects_malformed_identity_without_retaining_input():
    raw = b"Name,ID\nSynthetic,\n"
    with pytest.raises(new_quiz_csv.CsvProvenanceError):
        new_quiz_csv.build_csv_students(raw)


def test_csv_provenance_resolver_binds_only_matching_current_attempt_and_items(monkeypatch):
    current = _grader_state()
    current["authoritative"]["attempt"] = 2
    monkeypatch.setattr(new_quiz_grader, "_signed_context", lambda **_kwargs: (object(), "unused", {}, "unused"))
    monkeypatch.setattr(new_quiz_grader, "_read_current", lambda _context: current)
    bound = new_quiz_grader.resolve_csv_provenance(
        canvas_base="https://canvas.invalid", token="synthetic", assignment_id="assignment",
        user_id="student", attempt=2, item_ids=["essay-1", "auto-1"],
    )
    assert set(bound) == {"result_id", "state_digest", "attempt"}
    with pytest.raises(new_quiz_grader.GraderError, match="csv_provenance_mismatch"):
        new_quiz_grader.resolve_csv_provenance(
            canvas_base="https://canvas.invalid", token="synthetic", assignment_id="assignment",
            user_id="student", attempt=1, item_ids=["essay-1", "auto-1"],
        )


def test_csv_session_blocks_finalization_until_resolved_then_fails_closed_on_mismatch():
    session = {
        "session_id": "synthetic", "course_id": "course-1", "assignment_id": "assignment-1",
        "new_quiz_item_finalization_supported": True,
        "students": [{"user_id": "fake-user", "new_quiz_attempt": 1,
                      "speedgrader_required": True, "csv_native_manual_evidence": False}],
        "new_quiz_csv_provenance": {"course_id": "course-1", "assignment_id": "assignment-1",
            "rows": [{"user_id": "fake-user", "attempt": 1, "item_ids": ["essay-1"]}], "bindings": {}},
    }
    decisions = '[{"item_id":"essay-1","score":2,"teacher_feedback":"","ta_feedback":"TA"}]'
    blocked, _ = session_actions.review_new_quiz_finalization(
        "synthetic", user_id="fake-user", decisions_json=decisions,
        load_session=lambda _: session, save_session=lambda _: None, preflight=lambda *_: {},
    )
    assert blocked["code"] == "csv_provenance_unresolved"
    failed, _ = session_actions.resolve_new_quiz_csv_provenance(
        "synthetic", user_id="fake-user", load_session=lambda _: session, save_session=lambda _: None,
        resolver=lambda *_: (_ for _ in ()).throw(new_quiz_grader.GraderError("csv_provenance_mismatch")),
    )
    assert failed["code"] == "csv_provenance_mismatch"
    assert session["students"][0]["speedgrader_required"] is True


def test_csv_session_requires_binding_to_match_preflight_result():
    session = {
        "session_id": "synthetic", "new_quiz_item_finalization_supported": True,
        "students": [{"user_id": "fake-user", "speedgrader_required": False}],
        "new_quiz_csv_provenance": {"bindings": {"fake-user": {
            "result_id": "older-result", "state_digest": "a" * 64, "attempt": 1}}},
    }
    result, _ = session_actions.review_new_quiz_finalization(
        "synthetic", user_id="fake-user",
        decisions_json='[{"item_id":"essay-1","score":2,"teacher_feedback":"","ta_feedback":"TA"}]',
        load_session=lambda _: session, save_session=lambda _: None,
        preflight=lambda *_: {"result_id": "newer-result", "state_digest": "b" * 64, "decision_digest": "c" * 64},
    )
    assert result["code"] == "csv_provenance_stale"
    assert session["students"][0]["speedgrader_required"] is True


def test_new_quiz_finalization_review_is_frozen_and_idempotent():
    session = {
        "session_id": "synthetic", "new_quiz_item_finalization_supported": True,
        "students": [{"user_id": "fake-user", "speedgrader_required": False}],
    }
    saved = []
    decisions = '[{"item_id":"essay-1","score":2,"teacher_feedback":"My note","ta_feedback":"TA block"}]'
    baseline = {"result_id": "result-1", "state_digest": "a" * 64,
                "decision_digest": session_actions._digest([{"item_id": "essay-1", "score": 2.0, "teacher_feedback": "My note", "ta_feedback": "TA block"}])}
    review, code = session_actions.review_new_quiz_finalization(
        "synthetic", user_id="fake-user", decisions_json=decisions,
        load_session=lambda _: session, save_session=lambda value: saved.append(copy.deepcopy(value)),
        preflight=lambda *_args: baseline,
    )
    assert code == 200 and review["ok"] is True
    applied = []
    result, code = session_actions.finalize_new_quiz(
        "synthetic", user_id="fake-user", review_token=review["review_token"], decisions_json=decisions,
        load_session=lambda _: session, save_session=lambda value: saved.append(copy.deepcopy(value)),
        apply=lambda *_args: applied.append(True) or {"state_digest": "b" * 64},
    )
    assert code == 200 and result["status"] == "finalized" and applied == [True]
    repeat, code = session_actions.finalize_new_quiz(
        "synthetic", user_id="fake-user", review_token=review["review_token"], decisions_json=decisions,
        load_session=lambda _: session, save_session=lambda _: None, apply=lambda *_args: (_ for _ in ()).throw(AssertionError("must not write twice")),
    )
    assert code == 200 and repeat["status"] == "already_applied"
    assert "token" not in json.dumps(session.get("new_quiz_receipts", []))
    assert set(session.get("new_quiz_receipts", [])[0]) == {"ts", "user_id", "outcome", "result_digest", "content_minimized"}


def test_new_quiz_finalization_does_not_retry_an_unverified_write():
    session = {
        "session_id": "synthetic", "new_quiz_item_finalization_supported": True,
        "students": [{"user_id": "fake-user", "speedgrader_required": False}],
    }
    decisions = '[{"item_id":"essay-1","score":2,"teacher_feedback":"","ta_feedback":"TA block"}]'
    baseline = {"result_id": "result-1", "state_digest": "a" * 64,
                "decision_digest": session_actions._digest([{"item_id": "essay-1", "score": 2.0, "teacher_feedback": "", "ta_feedback": "TA block"}])}
    review, _ = session_actions.review_new_quiz_finalization(
        "synthetic", user_id="fake-user", decisions_json=decisions,
        load_session=lambda _: session, save_session=lambda _: None, preflight=lambda *_args: baseline,
    )
    error = new_quiz_grader.GraderError("write_unknown")
    failed, status = session_actions.finalize_new_quiz(
        "synthetic", user_id="fake-user", review_token=review["review_token"], decisions_json=decisions,
        load_session=lambda _: session, save_session=lambda _: None,
        apply=lambda *_args: (_ for _ in ()).throw(error),
    )
    assert status == 409 and failed["code"] == "write_unknown"
    retried, status = session_actions.finalize_new_quiz(
        "synthetic", user_id="fake-user", review_token=review["review_token"], decisions_json=decisions,
        load_session=lambda _: session, save_session=lambda _: None,
        apply=lambda *_args: (_ for _ in ()).throw(AssertionError("must not retry an unknown write")),
    )
    assert status == 409 and retried["code"] == "review_mismatch"
    assert "pending_new_quiz_review" not in session


def _grader_state(result_id="result-1", essay_score=0.0, essay_feedback="", total=2.0):
    rows = [
        {"id": "row-essay", "item_id": "essay-1", "points_possible": 5.0,
         "score": essay_score, "feedback": essay_feedback},
        {"id": "row-auto", "item_id": "auto-1", "points_possible": 2.0,
         "score": 2.0, "feedback": "Auto feedback"},
    ]
    return {
        "host": "https://synthetic-quiz.invalid",
        "headers": {"Authorization": "synthetic-signed-credential"},
        "quiz_session_id": "session-1",
        "authoritative": {"id": result_id, "fudge_points": 0.0, "score": total},
        "rows": rows,
    }


class _WriteSession:
    def __init__(self, status=201):
        self.status = status
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _NativeResponse(status=self.status)


def _final_decisions():
    return [{"item_id": "essay-1", "score": 3.0,
             "teacher_feedback": "Teacher note", "ta_feedback": "TA draft"}]


def test_new_quiz_adapter_writes_complete_collection_and_verifies(monkeypatch):
    before = _grader_state()
    after = _grader_state("result-2", 3.0,
                          "MY FEEDBACK\n\nTeacher note\n\n-------\n\nAutofeedback from an automated assistant:\n\nTA draft", 5.0)
    http = _WriteSession()
    states = [before, after]
    monkeypatch.setattr(new_quiz_grader, "_signed_context", lambda **_kwargs: (http, "unused", {}, "unused"))
    monkeypatch.setattr(new_quiz_grader, "_read_current", lambda _context: states.pop(0))
    baseline = {
        "result_id": "result-1",
        "state_digest": new_quiz_grader._digest(new_quiz_grader._result_state(before["authoritative"], before["rows"])),
    }
    result = new_quiz_grader.apply(
        canvas_base="https://canvas.invalid", token="synthetic-pat", assignment_id="assignment",
        user_id="student", decisions=_final_decisions(), baseline=baseline,
    )
    assert result["result_id"] == "result-2"
    assert len(http.posts) == 1
    payload = http.posts[0][1]["json"]
    assert set(payload) == {"results", "fudge_points"}
    assert all("id" not in row for row in payload["results"])
    assert payload["results"][1]["score"] == 2.0
    assert payload["results"][1]["feedback"] == "Auto feedback"


def test_new_quiz_adapter_fails_closed_on_item_mismatch_and_drift(monkeypatch):
    before = _grader_state()
    monkeypatch.setattr(new_quiz_grader, "_signed_context", lambda **_kwargs: (object(), "unused", {}, "unused"))
    monkeypatch.setattr(new_quiz_grader, "_read_current", lambda _context: before)
    with pytest.raises(new_quiz_grader.GraderError, match="item_mismatch"):
        new_quiz_grader.preflight(
            canvas_base="https://canvas.invalid", token="synthetic-pat", assignment_id="assignment",
            user_id="student", decisions=[{"item_id": "not-present", "score": 1.0, "ta_feedback": "TA"}],
        )
    drift = _grader_state("result-2")
    http = _WriteSession()
    monkeypatch.setattr(new_quiz_grader, "_signed_context", lambda **_kwargs: (http, "unused", {}, "unused"))
    monkeypatch.setattr(new_quiz_grader, "_read_current", lambda _context: drift)
    with pytest.raises(new_quiz_grader.GraderError, match="result_version_drift"):
        new_quiz_grader.apply(
            canvas_base="https://canvas.invalid", token="synthetic-pat", assignment_id="assignment",
            user_id="student", decisions=_final_decisions(),
            baseline={"result_id": "result-1", "state_digest": "not-the-current-state"},
        )
    assert http.posts == []


def test_new_quiz_adapter_marks_ambiguous_write_without_retry(monkeypatch):
    before = _grader_state()
    after = _grader_state("result-2", 3.0,
                          "MY FEEDBACK\n\nTeacher note\n\n-------\n\nAutofeedback from an automated assistant:\n\nTA draft", 5.0)
    http = _WriteSession(status=500)
    monkeypatch.setattr(new_quiz_grader, "_signed_context", lambda **_kwargs: (http, "unused", {}, "unused"))
    states = [before, after]
    monkeypatch.setattr(new_quiz_grader, "_read_current", lambda _context: states.pop(0))
    baseline = {
        "result_id": "result-1",
        "state_digest": new_quiz_grader._digest(new_quiz_grader._result_state(before["authoritative"], before["rows"])),
    }
    result = new_quiz_grader.apply(
        canvas_base="https://canvas.invalid", token="synthetic-pat", assignment_id="assignment",
        user_id="student", decisions=_final_decisions(), baseline=baseline,
    )
    assert result["result_id"] == "result-2"
    assert len(http.posts) == 1


def test_new_quiz_preflight_receipt_is_credential_hygienic(monkeypatch):
    current = _grader_state()
    monkeypatch.setattr(new_quiz_grader, "_signed_context", lambda **_kwargs: (object(), "https://signed.invalid", {"Authorization": "synthetic-secret"}, "participant"))
    monkeypatch.setattr(new_quiz_grader, "_read_current", lambda _context: current)
    receipt = new_quiz_grader.preflight(
        canvas_base="https://canvas.invalid", token="synthetic-pat", assignment_id="assignment",
        user_id="student", decisions=_final_decisions(),
    )
    serialized = json.dumps(receipt)
    assert set(receipt) == {"result_id", "state_digest", "decision_digest"}
    assert "synthetic-secret" not in serialized and "signed.invalid" not in serialized
