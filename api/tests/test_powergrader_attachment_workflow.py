"""Synthetic attachment workflow regressions; no live service probes."""

from __future__ import annotations

import io
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image
import requests

from api.powergrader import ai_workflow
from api.powergrader import canvas_fetch
from api.powergrader import new_quiz_fetch as nq
from api.powergrader import session_builder
from api.powergrader import student_attachments
from api.powergrader.assignment_refresh import RefreshBudget, REFRESH_BINARY_LIMIT
from api.powergrader import assignment_refresh
from api.platform_services.config import courses


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (3, 3), "blue").save(output, format="PNG")
    return output.getvalue()


def test_course_display_name_prefers_saved_nickname(monkeypatch):
    monkeypatch.setattr(courses._io_mod, "_synced_state", lambda: {
        "saved_courses": [{
            "id": "course-1",
            "name": "Fictional Biology",
            "nickname": "Biology Period",
        }],
    })

    assert courses.course_display_name("course-1") == "Biology Period"
    assert courses.course_display_name("missing-course") == "missing-course"


def test_media_recording_has_one_expected_evidence_item_when_canvas_source_is_missing(monkeypatch):
    monkeypatch.setattr(canvas_fetch, "canvas_headers", lambda: ({"Authorization": "synthetic"}, "https://canvas.test"))
    submissions = [{"user_id": "synthetic-user", "submission_type": "media_recording", "attachments": []}]
    canvas_fetch.ingest_media_recordings(
        submissions, course_name="Synthetic", course_id="course", assignment_name="Assignment", assignment_id="assignment",
    )
    row = submissions[0]
    assert row["expected_attachment_count"] == 1
    assert len(row["attachments"]) == 1
    assert row["attachments"][0]["error_code"] == "media_source_missing"


def test_media_source_url_is_transport_only_and_removed_from_submissions(monkeypatch):
    monkeypatch.setattr(canvas_fetch, "canvas_headers", lambda: ({"Authorization": "synthetic"}, "https://canvas.test"))
    submissions = [{"user_id": "synthetic-user", "submission_type": "media_recording", "attachments": [],
                    "media_comment": {"media_id": "media", "display_name": "synthetic.wav",
                                      "media_type": "audio/wav", "url": "https://canvas.test/signed-private"}}]
    canvas_fetch.ingest_media_recordings(
        submissions, course_name="Synthetic", course_id="course", assignment_name="Assignment", assignment_id="assignment",
        download=lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("synthetic download failure")),
    )
    assert "media_comment" not in submissions[0]
    assert "signed-private" not in json.dumps(submissions)


def test_ordinary_ingestion_preserves_all_formats_and_routes_shared_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(assignment_refresh.canvas_fetch, "canvas_headers", lambda: ({"Authorization": "synthetic"}, "https://canvas.test"))
    monkeypatch.setattr(canvas_fetch.workspace, "workspace_root", lambda: str(tmp_path))

    payloads = {
        "note.txt": b"synthetic written upload",
        "photo.png": _png_bytes(),
        "paper.pdf": b"synthetic pdf bytes",
    }

    def download(url, dest, **kwargs):
        name = Path(dest).name.split(" (")[0]
        Path(dest).write_bytes(payloads[name])
        return {"actual_size": len(payloads[name]), "declared_size": len(payloads[name])}

    submissions = [
        {
            "user_id": "user-1",
            "attempt": 2,
            "user": {"name": "Fictional Student", "sortable_name": "Fictional Student"},
            "attachments": [
                {"filename": "note.txt", "size": len(payloads["note.txt"]), "url": "https://canvas.test/upload-1"},
                {"filename": "photo.png", "size": len(payloads["photo.png"]), "url": "https://canvas.test/upload-2"},
            ],
        },
        {
            "user_id": "user-2",
            "user": {"name": "Fictional Reviewer", "sortable_name": "Fictional Reviewer"},
            "attachments": [
                {"filename": "paper.pdf", "size": len(payloads["paper.pdf"]), "url": "https://canvas.test/upload-3"},
            ],
        },
    ]

    canvas_fetch.ingest_ordinary_attachments(
        submissions,
        course_name="Fictional Biology",
        course_id="course-1",
        assignment_name="Fictional Essay",
        assignment_id="assignment-1",
        download=download,
    )

    first = submissions[0]
    assert first["expected_attachment_count"] == 2
    assert len(first["attachments"]) == 2
    assert all(a["download_status"] == "downloaded" for a in first["attachments"])
    assert all(a["ai_eligible"] for a in first["attachments"])
    assert all("url" not in a for a in first["attachments"])
    assert all("Fictional Biology — course-1" in a["local_path"] for a in first["attachments"])
    assert all("Fictional Essay — assignment-1" in a["local_path"] for a in first["attachments"])
    assert all("Attempt 2" in a["local_path"] for a in first["attachments"])
    assert "code_files" not in first

    second = submissions[1]
    assert second["attachments"][0]["local_only"] is True
    assert student_attachments.eligibility_decision(
        second["attachments"], expected_count=second["expected_attachment_count"]
    )["held"] is True
    assert all(Path(a["local_path"]).exists() for a in first["attachments"] + second["attachments"])


def test_ordinary_download_failure_keeps_failed_evidence_and_expected_parity(tmp_path, monkeypatch):
    monkeypatch.setattr(canvas_fetch, "canvas_headers", lambda: ({"Authorization": "synthetic"}, "https://canvas.test"))
    monkeypatch.setattr(canvas_fetch.workspace, "workspace_root", lambda: str(tmp_path))

    submissions = [{
        "user_id": "user-1",
        "user": {"name": "Fictional Student"},
        "attachments": [
            {"filename": "answer.txt", "size": 4, "url": "https://canvas.test/upload"},
        ],
    }]

    def fail_download(*args, **kwargs):
        raise ValueError("synthetic failure")

    canvas_fetch.ingest_ordinary_attachments(
        submissions,
        course_name="Fictional Biology",
        course_id="course-1",
        assignment_name="Fictional Essay",
        assignment_id="assignment-1",
        download=fail_download,
    )
    record = submissions[0]["attachments"][0]
    assert submissions[0]["expected_attachment_count"] == 1
    assert record["download_status"] == "failed"
    assert record["error_code"] == "download_failed"
    decision = student_attachments.eligibility_decision(
        submissions[0]["attachments"], expected_count=1
    )
    assert decision["held"] is True
    assert decision["attachment_count"] == decision["expected_count"] == 1


def test_focused_refresh_budget_reuses_exact_match_and_skips_over_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(canvas_fetch, "canvas_headers", lambda: ({"Authorization": "synthetic"}, "https://canvas.test"))
    monkeypatch.setattr(canvas_fetch.workspace, "workspace_root", lambda: str(tmp_path))
    budget = RefreshBudget(4)
    existing = tmp_path / "managed.txt"
    existing.write_text("keep", encoding="utf-8")
    submissions = [{"user_id": "user-1", "user": {"name": "Fictional"}, "attachments": [
        {"id": "same", "filename": "same.txt", "size": 4, "url": "https://canvas.test/same"},
        {"id": "too-big", "filename": "large.txt", "size": 5, "url": "https://canvas.test/large"},
    ]}]
    calls = []
    def destination(_submission, source, filename, attempt):
        return str(tmp_path / f"{source['id']}-{filename}"), attempt
    def download(*args, **kwargs):
        calls.append(args[0]); Path(args[1]).write_bytes(b"data"); return {"actual_size": 4, "declared_size": 4}
    canvas_fetch.ingest_ordinary_attachments(submissions, course_name="Course", course_id="c",
        assignment_name="Assignment", assignment_id="a", byte_budget=budget, target_path=destination,
        require_identity=True, download=download, reusable_records={"same": {
            "local_path": str(existing), "content_indicator": {"size": 4}, "actual_size": 4,
        }})
    first, second = submissions[0]["attachments"]
    assert first["download_status"] == "reused"
    assert second["error_code"] == "refresh_budget_exceeded"
    assert calls == []


def test_focused_refresh_budget_has_exact_ten_mib_boundary():
    budget = RefreshBudget()
    assert budget.reserve(REFRESH_BINARY_LIMIT) is True
    assert budget.reserve(1) is False


def test_second_focused_refresh_reuses_manifest_in_saved_course_folder(tmp_path, monkeypatch):
    """The Canvas response name must not redirect a second refresh to a new folder."""
    from api.platform_services import config as refresh_config
    monkeypatch.setattr(assignment_refresh.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(refresh_config, "course_display_name", lambda _course_id: "Saved Course")
    monkeypatch.setattr(canvas_fetch, "canvas_headers", lambda: ({"Authorization": "synthetic"}, "https://canvas.test"))
    calls = []
    def fake_fetch(*_args, **_kwargs):
        return ([{"id": "submission-1", "user_id": "user-1", "attempt": 1,
                  "user": {"name": "Fictional Student"}, "attachments": [{
                      "id": "file-1", "filename": "answer.txt", "size": 4,
                      "url": "https://canvas.test/file-1",
                  }]}], {"name": "Essay", "course_name": "Different Canvas Course"}, None)
    def fake_download(_url, destination, **_kwargs):
        calls.append(destination)
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(b"work")
        return {"actual_size": 4, "declared_size": 4}
    monkeypatch.setattr(assignment_refresh.canvas_fetch, "fetch_submissions", fake_fetch)
    monkeypatch.setattr(assignment_refresh.canvas_fetch, "_download_canvas_attachment", fake_download)

    first_subs, _, first = assignment_refresh.refresh_assignment("course-1", "assignment-1", session_id="first")
    second_subs, _, second = assignment_refresh.refresh_assignment("course-1", "assignment-1", session_id="second")

    assert first["status"] == second["status"] == "current"
    assert "Saved Course" in first["manifest_path"]
    assert first_subs[0]["attachments"][0]["download_status"] == "downloaded"
    assert second_subs[0]["attachments"][0]["download_status"] == "reused"
    assert len(calls) == 1


class _DownloadResponse:
    def __init__(self, *, status=200, body=b"", headers=None, url=""):
        self.status_code = status
        self.body = body
        self.headers = headers or {}
        self.url = url
        self.closed = False

    def iter_content(self, chunk_size=0):
        yield self.body

    def close(self):
        self.closed = True


class _DownloadSession:
    def __init__(self, responses):
        self.headers = {
            "Authorization": "Bearer synthetic-canvas",
            "Cookie": "synthetic-cookie",
        }
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_canvas_downloader_direct_success(tmp_path):
    session = _DownloadSession([_DownloadResponse(body=b"direct")])
    dest = tmp_path / "direct.txt"

    result = canvas_fetch._download_canvas_attachment(
        "https://canvas.test/files/direct", str(dest), declared_size=6,
        http_session=session, canvas_base="https://canvas.test",
    )

    assert result == {"actual_size": 6, "declared_size": 6}
    assert dest.read_bytes() == b"direct"
    assert not Path(str(dest) + ".partial").exists()


def test_canvas_downloader_size_mismatch_cleans_partial(tmp_path):
    session = _DownloadSession([_DownloadResponse(body=b"four")])
    dest = tmp_path / "mismatch.txt"

    with pytest.raises(ValueError, match="size did not match"):
        canvas_fetch._download_canvas_attachment(
            "https://canvas.test/files/mismatch", str(dest), declared_size=5,
            http_session=session, canvas_base="https://canvas.test",
        )

    assert not dest.exists()
    assert not Path(str(dest) + ".partial").exists()


def test_canvas_downloader_follows_same_host_redirect_with_session_auth(tmp_path):
    session = _DownloadSession([
        _DownloadResponse(status=302, headers={"Location": "/files/final"}),
        _DownloadResponse(status=200, body=b"same-host", url="https://canvas.test/files/final"),
    ])
    dest = tmp_path / "same-host.txt"

    canvas_fetch._download_canvas_attachment(
        "https://canvas.test/files/start", str(dest), declared_size=9,
        http_session=session, canvas_base="https://canvas.test",
    )

    assert [call[0] for call in session.calls] == [
        "https://canvas.test/files/start", "https://canvas.test/files/final",
    ]
    assert "headers" not in session.calls[1][1]
    assert session.headers["Authorization"] == "Bearer synthetic-canvas"
    assert dest.read_bytes() == b"same-host"


def test_canvas_downloader_follows_off_host_https_without_canvas_authorization(tmp_path):
    session = _DownloadSession([
        _DownloadResponse(status=302, headers={"Location": "https://cdn.test/files/final"}),
        _DownloadResponse(status=200, body=b"cdn-file", url="https://cdn.test/files/final"),
    ])
    dest = tmp_path / "cdn.txt"

    canvas_fetch._download_canvas_attachment(
        "https://canvas.test/files/start", str(dest), declared_size=8,
        http_session=session, canvas_base="https://canvas.test",
    )

    off_host_headers = session.calls[1][1]["headers"]
    assert off_host_headers["Authorization"] is None
    assert off_host_headers["Cookie"] is None
    assert dest.read_bytes() == b"cdn-file"


class _AmbiguousNativeSession:
    def __init__(self):
        self.cookies = {}
        self.item_result_calls = 0

    def get(self, url, **kwargs):
        if "sessionless_launch" in url:
            return _NativeResponse(payload={"url": "https://canvas.test/new-quiz-launch"})
        if url.endswith("new-quiz-launch"):
            env = {"NEW_QUIZZES": {"params": {"backend_url": "https://quiz.test", "resource": "synthetic"}, "signature": "synthetic"}, "ACCOUNT_ID": "account"}
            return _NativeResponse(text=f"<script>ENV = {json.dumps(env)};</script>")
        if "/participants" in url:
            return _NativeResponse(payload=[
                {"canvas_user_id": "user-1", "participant_sessions": [{"id": "ps-1"}]},
                {"canvas_user_id": "user-1", "participant_sessions": [{"id": "ps-2"}]},
            ])
        if "/participant_sessions/" in url:
            ps_id = url.rsplit("/", 2)[-2]
            return _NativeResponse(payload={
                "quiz_host": "https://quiz.test",
                "result_token": f"synthetic-{ps_id}",
                "quiz_session_id": f"qs-{ps_id}",
            })
        if "/api/quiz_sessions/" in url and "session_item_results" not in url:
            ps_id = url.rsplit("/", 1)[-1]
            return _NativeResponse(payload={"authoritative_result": {"id": f"result-{ps_id}", "attempt": 2}})
        if "session_item_results" in url:
            self.item_result_calls += 1
            raise AssertionError("ambiguous joins must not fetch item results")
        raise AssertionError(f"unexpected native request {url}")

    def post(self, url, **kwargs):
        if "/api/v1/jwts" in url:
            return _NativeResponse(payload={"token": "synthetic-workflow"})
        if url.endswith("/api/native/launch"):
            return _NativeResponse(payload={"access_token": "synthetic-native", "entry_path": {"resourceId": "resource"}})
        raise AssertionError(f"unexpected native request {url}")


class _NativeResponse:
    def __init__(self, *, payload=None, status=200, text=""):
        self.payload = payload
        self.status_code = status
        self.text = text
        self.url = ""

    def json(self):
        return self.payload


def _file_target(user_id="user-1"):
    return {
        "user_id": user_id,
        "user": {"name": "Fictional Student"},
        "new_quiz_attempt": 2,
        "new_quiz_items": [{"item_id": "file-item", "files": [{"filename": "answer.txt"}]}],
        "attachments": [],
    }


class _TwoStudentExceptionNativeSession:
    def __init__(self, *, candidate_failure_uid="", item_failure_uid=""):
        self.candidate_failure_uid = candidate_failure_uid
        self.item_failure_uid = item_failure_uid

    def get(self, url, **kwargs):
        if "sessionless_launch" in url:
            return _NativeResponse(payload={"url": "https://canvas.test/new-quiz-launch"})
        if url.endswith("new-quiz-launch"):
            env = {
                "NEW_QUIZZES": {
                    "params": {"backend_url": "https://quiz.test", "resource": "synthetic"},
                    "signature": "synthetic",
                },
                "ACCOUNT_ID": "account",
            }
            return _NativeResponse(text=f"<script>ENV = {json.dumps(env)};</script>")
        if "/participants" in url:
            return _NativeResponse(payload=[
                {"canvas_user_id": "user-1", "participant_sessions": [{"id": "ps-1"}]},
                {"canvas_user_id": "user-2", "participant_sessions": [{"id": "ps-2"}]},
            ])
        if "/participant_sessions/" in url:
            ps_id = url.rsplit("/", 2)[-2]
            uid = "user-1" if ps_id == "ps-1" else "user-2"
            if uid == self.candidate_failure_uid:
                raise requests.RequestException("synthetic candidate-resolution failure")
            return _NativeResponse(payload={
                "quiz_host": "https://quiz.test",
                "result_token": f"synthetic-{ps_id}",
                "quiz_session_id": f"qs-{ps_id}",
            })
        if "/api/quiz_sessions/" in url and "session_item_results" not in url:
            qs_id = url.rsplit("/", 1)[-1]
            return _NativeResponse(payload={"authoritative_result": {"id": f"result-{qs_id}", "attempt": 2}})
        if "session_item_results" in url:
            qs_id = url.split("/api/quiz_sessions/", 1)[1].split("/", 1)[0]
            uid = "user-1" if qs_id == "qs-ps-1" else "user-2"
            if uid == self.item_failure_uid:
                raise requests.RequestException("synthetic item-result failure")
            return _NativeResponse(payload=[{
                "item_id": "file-item",
                "scored_data": {"value": [{"url": "https://quiz.test/file", "name": "answer.txt", "size": 4}]},
            }])
        raise AssertionError(f"unexpected native request {url}")

    def post(self, url, **kwargs):
        if "/api/v1/jwts" in url:
            return _NativeResponse(payload={"token": "synthetic-workflow"})
        if url.endswith("/api/native/launch"):
            return _NativeResponse(payload={"access_token": "synthetic-native", "entry_path": {"resourceId": "resource"}})
        raise AssertionError(f"unexpected native request {url}")


def _run_two_student_native_exception_case(tmp_path, *, candidate_failure_uid="", item_failure_uid=""):
    session = _TwoStudentExceptionNativeSession(
        candidate_failure_uid=candidate_failure_uid,
        item_failure_uid=item_failure_uid,
    )
    targets = [_file_target("user-1"), _file_target("user-2")]

    def download(url, dest, **kwargs):
        Path(dest).write_bytes(b"file")
        return {"actual_size": 4, "declared_size": 4}

    count, error = nq._native_file_transport(
        session, "https://canvas.test", {"Authorization": "synthetic"},
        "course-1", "assignment-1", targets, session_id="session-1",
        download=download, course_name="Fictional Biology", assignment_name="Fictional Essay",
    )
    return count, error, targets


def test_new_quiz_candidate_exception_isolated_to_one_student(tmp_path, monkeypatch):
    monkeypatch.setattr(nq.workspace, "workspace_root", lambda: str(tmp_path))

    count, error, targets = _run_two_student_native_exception_case(
        tmp_path, candidate_failure_uid="user-1",
    )

    assert count == 1 and error is None
    assert targets[0]["attachments"][0]["error_code"] == "candidate_resolution_network"
    assert targets[1]["attachments"][0]["download_status"] == "downloaded"


def test_new_quiz_item_result_exception_isolated_to_one_student(tmp_path, monkeypatch):
    monkeypatch.setattr(nq.workspace, "workspace_root", lambda: str(tmp_path))

    count, error, targets = _run_two_student_native_exception_case(
        tmp_path, item_failure_uid="user-1",
    )

    assert count == 1 and error is None
    assert targets[0]["attachments"][0]["error_code"] == "item_results_network"
    assert targets[1]["attachments"][0]["download_status"] == "downloaded"


def test_new_quiz_ambiguous_same_attempt_holds_without_item_download(tmp_path, monkeypatch):
    monkeypatch.setattr(nq.workspace, "workspace_root", lambda: str(tmp_path))
    target = _file_target()
    session = _AmbiguousNativeSession()
    count, error = nq._native_file_transport(
        session,
        "https://canvas.test",
        {"Authorization": "synthetic"},
        "course-1",
        "assignment-1",
        [target],
        session_id="session-1",
        download=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ambiguous evidence must not download")),
        course_name="Fictional Biology",
        assignment_name="Fictional Essay",
    )
    assert count == 0 and error is None
    assert session.item_result_calls == 0
    assert target["attachments"][0]["error_code"] == "ambiguous_attempt_join"
    assert target["new_quiz_items"][0]["files"][0]["download_status"] == "failed"
    assert target["new_quiz_files_error"]["code"] == "ambiguous_attempt_join"


def test_new_quiz_global_failure_marks_every_expected_file(tmp_path, monkeypatch):
    monkeypatch.setattr(nq.workspace, "workspace_root", lambda: str(tmp_path))
    targets = [_file_target("user-1"), _file_target("user-2")]
    session = _AmbiguousNativeSession()
    session.get = lambda url, **kwargs: _NativeResponse(status=503) if "sessionless_launch" in url else (_ for _ in ()).throw(AssertionError("global launch failure should stop native work"))
    count, error = nq._native_file_transport(
        session, "https://canvas.test", {"Authorization": "synthetic"},
        "course-1", "assignment-1", targets, session_id="session-1",
        course_name="Fictional Biology", assignment_name="Fictional Essay",
    )
    assert count == 0
    assert error["code"] == "launch_unavailable"
    assert all(t["attachments"][0]["download_status"] == "failed" for t in targets)


def test_session_builder_preserves_expected_count_and_generic_ai_failure():
    students = session_builder.build_students(
        submitted=[{
            "user_id": "user-1",
            "user": {"name": "Fictional Student"},
            "body": "Written response remains visible.",
            "expected_attachment_count": 1,
            "attachments": [{
                "filename": "answer.txt", "download_status": "failed",
                "extraction_status": "failed", "ai_eligible": False,
                "error_code": "download_failed", "error_message": "generic",
            }],
        }],
        ai_by_uid={},
        ai_failures={"user-1": "No AI draft was produced; manual grading is required."},
        roster_settings={}, tier_map={}, monitored={}, extra_time_map={},
    )
    student = students[0]
    assert student["attachment_expected_count"] == 1
    assert student["attachment_eligibility"]["held"] is True
    assert student["ai_scoring_error"] == "No AI draft was produced; manual grading is required."
