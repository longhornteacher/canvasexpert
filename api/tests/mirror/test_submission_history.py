"""Laws for durable ordinary-assignment attempt evidence."""
from __future__ import annotations

import hashlib
import os

import pytest

from api.mirror import store, submission_history, sync
from api.platform_services import workspace


COURSE = "711"
ASSIGNMENT = "82001"
SUBMITTED = "2026-09-01T10:00:00Z"
ORIGIN = "https://canvas.example.edu"


def _history(root):
    return submission_history.read_history(COURSE, ASSIGNMENT, root=str(root))


def test_attempt_and_original_survive_sparse_merge_and_assignment_prune(tmp_path, submission_history_support):
    row = submission_history_support["row"]
    file_record = submission_history_support["file_record"]
    Response = submission_history_support["Response"]
    payload = b"Original upload"
    response = Response([payload], declared=len(payload))
    store.merge_submissions(
        COURSE, ASSIGNMENT, [row(attachments=[file_record()])], root=str(tmp_path),
        stream_get=lambda url: (response, None), canvas_origin=ORIGIN,
    )
    first = _history(tmp_path)
    key, attempt = next(iter(first["attempts"].items()))
    assert attempt["observations"][0]["body"] == "First draft"
    assert attempt["files"][0]["status"] == "captured"
    assert attempt["files"][0]["digest"] == hashlib.sha256(payload).hexdigest()
    assert response.closed

    downloads = []
    store.merge_submissions(
        COURSE, ASSIGNMENT, [row(attachments=[file_record()])], root=str(tmp_path),
        stream_get=lambda url: downloads.append(url) or (None, "must not download"),
        canvas_origin=ORIGIN,
    )
    assert downloads == []

    # A sparse graded update cannot erase the captured draft in either store.
    store.merge_submissions(
        COURSE, ASSIGNMENT,
        [row(body="", attachments=[] ) | {"score": 8, "grade": "B"}],
        root=str(tmp_path),
    )
    cached = store.read_submissions(COURSE, ASSIGNMENT, root=str(tmp_path))
    assert cached["submissions"]["991001"]["attempts"]["1"]["body"] == "First draft"
    assert store.prune_submission_files(COURSE, [], root=str(tmp_path)) == [ASSIGNMENT]
    assert store.read_submissions(COURSE, ASSIGNMENT, root=str(tmp_path)) is None
    retained = _history(tmp_path)
    assert retained["attempts"][key]["observations"][0]["body"] == "First draft"
    assert retained["attempts"][key]["files"][0]["digest"] == hashlib.sha256(payload).hexdigest()
    blob_path = submission_history.file_path(
        COURSE, ASSIGNMENT, hashlib.sha256(payload).hexdigest(), root=str(tmp_path))
    assert blob_path and open(workspace.extended_path(blob_path), "rb").read() == payload


def test_conflicts_are_digest_keyed_and_replay_is_idempotent(tmp_path, submission_history_support):
    row = submission_history_support["row"]
    store.merge_submissions(COURSE, ASSIGNMENT, [row()], root=str(tmp_path))
    store.merge_submissions(COURSE, ASSIGNMENT, [row(body="Revised draft")], root=str(tmp_path))
    after_conflict = _history(tmp_path)
    attempt = next(iter(after_conflict["attempts"].values()))
    assert len(attempt["observations"]) == 2
    assert attempt["conflict"] is True
    revision = after_conflict["revision"]
    store.merge_submissions(COURSE, ASSIGNMENT, [row(body="Revised draft")], root=str(tmp_path))
    replay = _history(tmp_path)
    assert len(next(iter(replay["attempts"].values()))["observations"]) == 2
    assert replay["revision"] == revision


def test_identical_file_then_sparse_full_and_prune_has_one_observation_and_no_conflict(
        tmp_path, submission_history_support):
    row = submission_history_support["row"]
    file_record = submission_history_support["file_record"]
    Response = submission_history_support["Response"]
    original = file_record(payload=b"same original")
    first_response = Response([b"same original"], declared=len(b"same original"))
    initial = row(attachments=[original])
    store.merge_submissions(
        COURSE, ASSIGNMENT, [initial], root=str(tmp_path),
        stream_get=lambda _url: (first_response, None), canvas_origin=ORIGIN,
    )

    sparse = row(body="", attachments=[]) | {"score": 8, "grade": "B"}
    store.merge_submissions(COURSE, ASSIGNMENT, [sparse], root=str(tmp_path))
    full_response_calls = []
    store.merge_submissions(
        COURSE, ASSIGNMENT, [initial], root=str(tmp_path), replace=True,
        stream_get=lambda url: full_response_calls.append(url) or (None, "unexpected"),
        canvas_origin=ORIGIN,
    )
    assert full_response_calls == []
    assert store.prune_submission_files(COURSE, [], root=str(tmp_path)) == [ASSIGNMENT]

    attempt = next(iter(_history(tmp_path)["attempts"].values()))
    assert len(attempt["observations"]) == 1
    assert attempt["conflict"] is False
    assert len(attempt["files"]) == 1
    assert attempt["files"][0]["status"] == "captured"
    assert first_response.closed


def test_filename_only_cached_file_is_enriched_in_place_by_stable_capture(
        tmp_path, submission_history_support):
    row = submission_history_support["row"]
    file_record = submission_history_support["file_record"]
    Response = submission_history_support["Response"]
    cached = row(attachments=[{"filename": "student-original.txt"}])
    store.merge_submissions(COURSE, ASSIGNMENT, [cached], root=str(tmp_path))
    seeded = _history(tmp_path)
    seeded_attempt = next(iter(seeded["attempts"].values()))
    assert len(seeded_attempt["files"]) == 1
    assert seeded_attempt["files"][0]["status"] == "unavailable"

    payload = b"later stable original"
    response = Response([payload], declared=len(payload))
    enriched = row(attachments=[file_record("stable-file-id", payload)])
    store.merge_submissions(
        COURSE, ASSIGNMENT, [enriched], root=str(tmp_path),
        stream_get=lambda _url: (response, None), canvas_origin=ORIGIN,
    )
    final = next(iter(_history(tmp_path)["attempts"].values()))
    assert len(final["files"]) == 1
    assert final["files"][0]["key"] == "stable-file-id"
    assert final["files"][0]["status"] == "captured"
    assert final["files"][0]["digest"] == hashlib.sha256(payload).hexdigest()
    assert len(final["observations"]) == 1
    assert final["conflict"] is False
    assert response.closed


def test_partial_stream_is_removed_and_retry_publishes_complete_blob(tmp_path, submission_history_support):
    row = submission_history_support["row"]
    file_record = submission_history_support["file_record"]
    Response = submission_history_support["Response"]
    metadata = file_record(payload=b"complete")
    partial = Response([b"part"], declared=len(b"complete"))
    with pytest.raises(InterruptedError):
        store.merge_submissions(
            COURSE, ASSIGNMENT, [row(attachments=[metadata])], root=str(tmp_path),
            stream_get=lambda url: (partial, "cancelled"), canvas_origin=ORIGIN,
        )
    # Cancellation leaves metadata pending and never publishes a partial file.
    history = _history(tmp_path)
    attempt = next(iter(history["attempts"].values()))
    assert attempt["files"][0]["status"] == "pending"
    archive_files = os.path.join(submission_history._directory(
        str(tmp_path), COURSE, ASSIGNMENT), "files")
    assert not os.path.exists(workspace.extended_path(archive_files))
    assert partial.closed

    broken = Response([b"part", OSError("synthetic stream interruption")],
                      declared=len(b"complete"))
    store.merge_submissions(
        COURSE, ASSIGNMENT, [row(attachments=[metadata])], root=str(tmp_path),
        stream_get=lambda url: (broken, None), canvas_origin=ORIGIN,
    )
    attempt = next(iter(_history(tmp_path)["attempts"].values()))
    assert attempt["files"][0]["status"] == "failed"
    assert broken.closed
    assert not os.listdir(workspace.extended_path(archive_files))

    response = Response([b"complete"], declared=len(b"complete"))
    store.merge_submissions(
        COURSE, ASSIGNMENT, [row(attachments=[metadata])], root=str(tmp_path),
        stream_get=lambda url: (response, None), canvas_origin=ORIGIN,
    )
    attempt = next(iter(_history(tmp_path)["attempts"].values()))
    assert attempt["files"][0]["status"] == "captured"
    assert attempt["files"][0]["digest"] == hashlib.sha256(b"complete").hexdigest()
    assert response.closed


def test_three_attempt_originals_survive_replace_and_student_removal(tmp_path, submission_history_support):
    row = submission_history_support["row"]
    file_record = submission_history_support["file_record"]
    Response = submission_history_support["Response"]
    expected_digests = []
    author_pseudonym = None
    for number in (1, 2, 3):
        payload = f"original-{number}".encode()
        item = row(attempt=number, attachments=[file_record(str(700 + number), payload)])
        item["submitted_at"] = f"2026-09-0{number}T10:00:00Z"
        expected_digests.append(hashlib.sha256(payload).hexdigest())
        response = Response([payload], declared=len(payload))
        store.merge_submissions(
            COURSE, ASSIGNMENT, [item], root=str(tmp_path), replace=number == 1,
            stream_get=lambda url, response=response: (response, None),
            canvas_origin=ORIGIN,
        )
        assert response.closed
        if author_pseudonym is None:
            author_pseudonym = next(iter(_history(tmp_path)["attempts"].values()))["pseudonym"]

    # The full replacement contains a different student and removes the author
    # from current membership. Archiving before replacement preserves history.
    replacement = row()
    replacement["user_id"] = "992002"
    replacement["attempt"] = 1
    store.merge_submissions(COURSE, ASSIGNMENT, [replacement], root=str(tmp_path), replace=True)
    history = _history(tmp_path)
    author_attempts = [value for value in history["attempts"].values()
                       if value["pseudonym"] == author_pseudonym]
    assert {value["files"][0]["digest"] for value in author_attempts} == set(expected_digests)
    assert len([value for value in history["attempts"].values()
                if value["pseudonym"] == author_pseudonym]) == 3
    for digest in expected_digests:
        path = submission_history.file_path(COURSE, ASSIGNMENT, digest, root=str(tmp_path))
        assert path and hashlib.sha256(open(workspace.extended_path(path), "rb").read()).hexdigest() == digest


def test_streamed_size_and_pass_byte_overflow_are_visible_and_clean(tmp_path, monkeypatch, submission_history_support):
    row = submission_history_support["row"]
    file_record = submission_history_support["file_record"]
    Response = submission_history_support["Response"]
    monkeypatch.setattr(submission_history, "MAX_FILE_BYTES", 3)
    oversized = file_record(payload=b"four")
    # No declared metadata size: actual streamed bytes enforce the file limit.
    oversized["size"] = 0
    response = Response([b"four"])
    store.merge_submissions(COURSE, ASSIGNMENT, [row(attachments=[oversized])], root=str(tmp_path),
                            stream_get=lambda url: (response, None), canvas_origin=ORIGIN)
    first = next(iter(_history(tmp_path)["attempts"].values()))
    assert first["files"][0]["status"] == "too_large"
    assert response.closed

    monkeypatch.setattr(submission_history, "MAX_FILE_BYTES", 10)
    monkeypatch.setattr(submission_history, "MAX_PASS_BYTES", 3)
    first_meta, second_meta = file_record("801", b"aa"), file_record("802", b"bb")
    budget = submission_history.CaptureBudget()
    responses = [Response([b"aa"], declared=2), Response([b"bb"], declared=2)]
    index = iter(responses)
    pass_root = tmp_path / "pass-byte-budget"
    store.merge_submissions(
        COURSE, "82002", [row(attempt=1, attachments=[first_meta]),
                           row(attempt=2, attachments=[second_meta])],
        root=str(pass_root), capture_budget=budget,
        stream_get=lambda url: (next(index), None), canvas_origin=ORIGIN,
    )
    attempts = list(submission_history.read_history(COURSE, "82002", root=str(pass_root))["attempts"].values())
    statuses = sorted(file["status"] for attempt in attempts for file in attempt["files"])
    assert statuses == ["captured", "pending"]
    assert all(response.closed for response in responses)


def test_concurrent_capture_is_rebased_after_stream(tmp_path, submission_history_support):
    row = submission_history_support["row"]
    file_record = submission_history_support["file_record"]
    Response = submission_history_support["Response"]
    response = Response([b"outer"], declared=5)
    injected = False

    def stream(_url):
        nonlocal injected
        if not injected:
            injected = True
            concurrent = row(attempt=2, body="concurrent observation")
            concurrent["submitted_at"] = "2026-09-02T10:00:00Z"
            submission_history.capture_rows(
                COURSE, ASSIGNMENT, [concurrent],
                vault=store._identity_vault(str(tmp_path)), root=str(tmp_path),
            )
        return response, None

    store.merge_submissions(COURSE, ASSIGNMENT,
                            [row(attachments=[file_record(payload=b"outer")])],
                            root=str(tmp_path), stream_get=stream, canvas_origin=ORIGIN)
    history = _history(tmp_path)
    assert len(history["attempts"]) == 2
    statuses = sorted(file["status"] for attempt in history["attempts"].values()
                      for file in attempt.get("files", []))
    assert statuses == ["captured"]
    assert any(observation["body"] == "concurrent observation"
               for attempt in history["attempts"].values()
               for observation in attempt["observations"])


@pytest.mark.parametrize("case, expected", [
    ("foreign", "foreign_origin"),
    ("too_large", "too_large"),
    ("budget", "pending"),
])
def test_unsafe_or_deferred_downloads_do_not_reach_transport(tmp_path, case, expected, submission_history_support):
    row = submission_history_support["row"]
    file_record = submission_history_support["file_record"]
    Response = submission_history_support["Response"]
    metadata = file_record()
    budget = submission_history.CaptureBudget()
    origin = ORIGIN
    if case == "foreign":
        metadata["url"] = "https://files.example.net/foreign"
    elif case == "too_large":
        metadata["size"] = submission_history.MAX_FILE_BYTES + 1
    else:
        budget.download_attempts = submission_history.MAX_PASS_DOWNLOADS
    calls = []
    store.merge_submissions(
        COURSE, ASSIGNMENT, [row(attachments=[metadata])], root=str(tmp_path),
        stream_get=lambda url: calls.append(url) or (Response([b"x"]), None),
        canvas_origin=origin, capture_budget=budget,
    )
    attempt = next(iter(_history(tmp_path)["attempts"].values()))
    assert attempt["files"][0]["status"] == expected
    assert calls == []


def test_archive_write_failure_refuses_to_prune_cached_attempt(tmp_path, monkeypatch, submission_history_support):
    row = submission_history_support["row"]
    store.merge_submissions(COURSE, ASSIGNMENT, [row()], root=str(tmp_path))
    path = submission_history._manifest_path(str(tmp_path), COURSE, ASSIGNMENT)
    os.remove(workspace.extended_path(path))

    def fail(*args, **kwargs):
        raise OSError("synthetic archive failure")

    monkeypatch.setattr(submission_history, "_write", fail)
    assert store.prune_submission_files(COURSE, [], root=str(tmp_path)) == []
    assert store.read_submissions(COURSE, ASSIGNMENT, root=str(tmp_path)) is not None


def test_manifest_contains_neither_signed_urls_nor_tokens(tmp_path, submission_history_support):
    row = submission_history_support["row"]
    file_record = submission_history_support["file_record"]
    store.merge_submissions(
        COURSE, ASSIGNMENT, [row(attachments=[file_record()])], root=str(tmp_path),
    )
    path = submission_history._manifest_path(str(tmp_path), COURSE, ASSIGNMENT)
    raw = open(workspace.extended_path(path), encoding="utf-8").read()
    assert "download_secret" not in raw
    assert ORIGIN not in raw
    assert "Authorization" not in raw


def test_course_delta_shares_download_attempt_budget_across_assignments(tmp_path, submission_history_support):
    row = submission_history_support["row"]
    file_record = submission_history_support["file_record"]
    Response = submission_history_support["Response"]
    assignment_ids = [str(83000 + index) for index in range(21)]
    store.write_assignments(
        COURSE, [{"id": value, "name": "Essay", "published": True}
                 for value in assignment_ids], root=str(tmp_path))
    store.record_pass(COURSE, "full", ok=True,
                      attempted_at="2026-09-01T12:00:00Z",
                      watermarks={"submitted_since": "2026-09-01T00:00:00Z",
                                  "graded_since": "2026-09-01T00:00:00Z"},
                      root=str(tmp_path))
    rows = []
    for index, assignment_id in enumerate(assignment_ids):
        item = row(attachments=[file_record(str(600 + index), b"x")])
        item["assignment_id"] = assignment_id
        rows.append(item)

    def canvas_get_all(_path, params=None, **_kwargs):
        return (rows if params and "submitted_since" in params else [], None)

    requests = []

    def stream(url):
        requests.append(url)
        return Response([b"x"], declared=1), None

    result = sync.refresh_submissions_course_delta(
        COURSE, canvas_get_all=canvas_get_all, root=str(tmp_path),
        stream_get=stream, canvas_origin=ORIGIN,
        now="2026-09-02T12:00:00Z",
    )
    assert result["ok"] is True
    assert len(requests) == submission_history.MAX_PASS_DOWNLOADS
    statuses = [submission_history.capture_summary(COURSE, value, root=str(tmp_path))
                for value in assignment_ids]
    assert sum(item["captured"] for item in statuses) == 20
    assert sum(item["pending"] for item in statuses) == 1
