"""Focused evidence-manifest and mirror preparation laws."""
from api.powergrader import assignment_refresh
from api.platform_services import config
import pytest


def _refresh_result(tmp_path, monkeypatch, submission, *, conflicts=False):
    monkeypatch.setattr(assignment_refresh.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(config, "course_display_name", lambda _: "Synthetic")
    monkeypatch.setattr(assignment_refresh.new_quizzes, "read_fresh_snapshot", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr(assignment_refresh.canvas_fetch, "fetch_submissions", lambda *args, **kwargs: ([submission], {"name": "Synthetic"}, None))
    monkeypatch.setattr(assignment_refresh.canvas_fetch, "ingest_ordinary_attachments", lambda *args, **kwargs: None)
    monkeypatch.setattr(assignment_refresh.canvas_fetch, "ingest_media_recordings", lambda *args, **kwargs: None)
    monkeypatch.setattr(assignment_refresh.workspace, "assignment_evidence_conflicts", lambda *args, **kwargs: ["conflict"] if conflicts else [])
    monkeypatch.setattr(assignment_refresh.workspace, "write_assignment_evidence_manifest", lambda *_args, **_kwargs: str(tmp_path / "manifest.json"))
    return assignment_refresh.refresh_assignment("course", "assignment", session_id="synthetic")[2]


def test_unsubmitted_row_does_not_hold_complete_submitted_media_manifest(tmp_path, monkeypatch):
    submitted = {
        "id": "submission", "user_id": "synthetic-user", "attempt": 1,
        "submission_type": "media_recording", "workflow_state": "submitted",
        "user": {"name": "Synthetic User"},
        "attachments": [{"media_recording": True, "item_id": "media", "download_status": "downloaded",
                         "extraction_status": "validated", "canonical_path": str(tmp_path / "ready.wav"),
                         "original_path": str(tmp_path / "original.wav"), "original_sha256": "a", "canonical_sha256": "b"}],
    }
    held = {"user_id": "other", "workflow_state": "unsubmitted", "submission_type": None, "attachments": []}
    (tmp_path / "ready.wav").write_bytes(b"wav")
    (tmp_path / "original.wav").write_bytes(b"source")
    monkeypatch.setattr(assignment_refresh.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(config, "course_display_name", lambda _: "Synthetic")
    monkeypatch.setattr(assignment_refresh.new_quizzes, "read_fresh_snapshot", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr(assignment_refresh.canvas_fetch, "fetch_submissions", lambda *args, **kwargs: ([submitted, held], {"name": "Synthetic"}, None))
    monkeypatch.setattr(assignment_refresh.canvas_fetch, "ingest_ordinary_attachments", lambda *args, **kwargs: None)
    monkeypatch.setattr(assignment_refresh.canvas_fetch, "ingest_media_recordings", lambda *args, **kwargs: None)
    monkeypatch.setattr(assignment_refresh.workspace, "assignment_evidence_conflicts", lambda *args, **kwargs: [])
    written = {}
    monkeypatch.setattr(assignment_refresh.workspace, "write_assignment_evidence_manifest", lambda value, **kwargs: written.setdefault("value", value) and str(tmp_path / "manifest.json"))
    _subs, _assignment, result = assignment_refresh.refresh_assignment("course", "assignment", session_id="synthetic")
    assert result["status"] == "current"
    assert [entry["evidence_id"] for entry in written["value"]["evidence"]] == ["media"]


@pytest.mark.parametrize("case", ["missing_user", "missing_attempt", "missing_item_id", "failed", "missing_path", "missing_original_path", "missing_original_hash", "missing_canonical_hash", "budget_held"])
def test_submitted_media_boundary_failures_hold_manifest(tmp_path, monkeypatch, case):
    ready, original = tmp_path / "ready.wav", tmp_path / "original.wav"
    ready.write_bytes(b"wav"); original.write_bytes(b"source")
    attachment = {"media_recording": True, "item_id": "media", "download_status": "downloaded", "extraction_status": "validated", "canonical_path": str(ready), "original_path": str(original), "original_sha256": "a", "canonical_sha256": "b"}
    submission = {"id": "submission", "user_id": "synthetic", "attempt": 1, "submission_type": "media_recording", "workflow_state": "submitted", "attachments": [attachment]}
    if case == "missing_user": submission["user_id"] = ""
    elif case == "missing_attempt": submission["attempt"] = None
    elif case == "missing_item_id": attachment["item_id"] = ""
    elif case == "failed": attachment["download_status"] = "failed"
    elif case == "missing_path": attachment["canonical_path"] = ""
    elif case == "missing_original_path": attachment["original_path"] = ""
    elif case == "missing_original_hash": attachment["original_sha256"] = ""
    elif case == "missing_canonical_hash": attachment["canonical_sha256"] = ""
    elif case == "budget_held": attachment.update(download_status="held", error_code="media_class_limit_exceeded")
    assert _refresh_result(tmp_path, monkeypatch, submission)["status"] == "incomplete"


def test_conflicted_manifest_remains_incomplete(tmp_path, monkeypatch):
    submission = {"user_id": "synthetic", "attempt": 1, "submission_type": "online_text_entry", "workflow_state": "submitted", "attachments": []}
    assert _refresh_result(tmp_path, monkeypatch, submission, conflicts=True)["status"] == "incomplete"


def test_empty_unsubmitted_non_roster_row_does_not_block_submitted_work(mirror_assignment_input):
    mirror = mirror_assignment_input
    entries = mirror.document["submissions"]
    entries.update({
        "member": mirror.entry("member", workflow_state="submitted", attempt=1,
                               submitted_at="2026-09-01T12:00:00Z",
                               submission_type="online_text_entry", body="Synthetic reasoning."),
        "waiting-member": mirror.entry("waiting-member"),
        "nonmember": mirror.entry(),
        "historical": mirror.entry("historical", workflow_state="graded", score=5, grade="5"),
    })
    before = mirror.snapshot()

    rows, assignment, result = mirror.prepare()

    assert result.get("error") is None
    assert [row["user_id"] for row in rows] == ["member", "waiting-member"]
    assert rows[0]["body"] == "Synthetic reasoning."
    assert assignment["id"] == "assignment"
    assert result["mirror_revision"] == 1
    assert mirror.snapshot() == before


@pytest.mark.parametrize("section, field, value, ignored", [
    ("current", "attempt", None, True),
    ("current", "attempt", 0, True),
    ("current", "attempt", "", True),
    ("current", "body", " \n\t", True),
    ("current", "workflow_state", "submitted", False),
    ("current", "workflow_state", "pending_review", False),
    ("current", "workflow_state", "", False),
    ("current", "workflow_state", "unknown", False),
    ("current", "user_id", "", False),
    ("current", "user_id", " ", False),
    ("current", "user_id", ["nonmember"], False),
    ("current", "submitted_at", "2026-09-01T12:00:00Z", False),
    ("current", "graded_at", "2026-09-01T12:00:00Z", False),
    ("current", "score", 0, False),
    ("current", "entered_score", 0, False),
    ("current", "grade", "0", False),
    ("current", "body", "Synthetic evidence.", False),
    ("current", "url", "https://example.invalid/response", False),
    ("current", "submission_type", "online_upload", False),
    ("current", "submission_type", "media_recording", False),
    ("current", "attempt", 1, False),
    ("current", "attempt", False, False),
    ("current", "attempt", "0", False),
    ("current", "body", 0, False),
    ("current", "url", [], False),
    ("current", "submitted_at", False, False),
    ("current", "submission_comments", [{"comment": "Synthetic feedback."}], False),
    ("current", "submission_comments", None, False),
    ("attempts", None, {"1": {"attachment_names": ["synthetic.txt"]}}, False),
    ("attempts", None, {"1": {}}, False),
    ("attempts", None, None, False),
    ("attempts", None, [], False),
    ("missing", "entered_score", None, False),
    ("missing", "submission_comments", None, False),
])
def test_only_provably_empty_unsubmitted_orphans_are_ignored(
        mirror_assignment_input, section, field, value, ignored):
    mirror = mirror_assignment_input
    entry = mirror.entry()
    if section == "missing":
        entry["current"].pop(field)
    elif section == "attempts":
        entry["attempts"] = value
    else:
        entry[section][field] = value
    mirror.document["submissions"]["nonmember"] = entry
    before = mirror.snapshot()

    rows, assignment, result = mirror.prepare()

    if ignored:
        assert rows == []
        assert assignment["id"] == "assignment"
        assert result.get("error") is None
    else:
        assert rows is None and assignment is None
        assert result["code"] == "mirror_submission_identity_mismatch"
        assert "nonmember" not in str(result)
    assert mirror.snapshot() == before
