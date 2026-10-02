"""Contract and privacy laws for the retained-history MCP read."""
from __future__ import annotations

import json
import zipfile

from api.mcp_server import tools
from api.mirror import store
from api.mirror import submission_history
from api.platform_services import workspace
from api.powergrader import student_attachments


def test_history_is_read_after_prune_with_scrubbed_text_and_local_only_files(tmp_path, monkeypatch, retained_history_env):
    env = retained_history_env
    _capture = env["capture"]
    COURSE, ASSIGNMENT, ORIGIN = env["course_id"], env["assignment_id"], env["origin"]
    _capture([("501", "private-name.txt", b"Alice wrote this in a file."),
              ("502", "private-scan.pdf", b"%PDF-1.4 private content")])
    manifest = submission_history.read_history(COURSE, ASSIGNMENT, root=str(tmp_path))
    stored_pseudonym = next(iter(manifest["attempts"].values()))["pseudonym"]
    assert stored_pseudonym == env["vault"].get_or_assign("991001")
    assert stored_pseudonym not in env["vault"].all_real_identifiers()[1]
    assert store.prune_submission_files(COURSE, [], root=str(tmp_path)) == [ASSIGNMENT]
    retained = json.dumps(submission_history.read_history(
        COURSE, ASSIGNMENT, root=str(tmp_path)), sort_keys=True)
    assert "991001" not in retained
    assert stored_pseudonym in retained

    # The retained reader is local-only; any Canvas attempt would fail this test.
    monkeypatch.setattr(tools.mirror_service, "canvas_get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(tools.mirror_service, "canvas_get_all",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    result = tools.get_submission_history(COURSE, ASSIGNMENT)
    assert result["ok"] is True, result
    assert result["source"] == "retained_history"
    assert result["coverage"] == "observed_only"
    assert "freshness" not in result and "current_enrollment" not in json.dumps(result)
    assert "Alice" not in result["attempts"][0]["text"]
    serialized = json.dumps(result)
    for secret in ("Alice", "private-name.txt", "private-scan.pdf", str(tmp_path),
                   ORIGIN, "private content"):
        assert secret not in serialized
    files_out = result["attempts"][0]["files"]
    assert {item["type"] for item in files_out} == {"text", "local_only"}
    pdf = next(item for item in files_out if item["type"] == "local_only")
    assert "text" not in pdf
    assert result["manifest_digest"]


def test_include_text_false_never_extracts_and_pagination_is_bounded(tmp_path, monkeypatch, retained_history_env):
    env = retained_history_env
    COURSE, ASSIGNMENT = env["course_id"], env["assignment_id"]
    env["capture"]()
    monkeypatch.setattr(student_attachments, "route_bytes",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("extraction ran")))
    first = tools.get_submission_history(COURSE, ASSIGNMENT, include_text=False, limit=1)
    assert first["ok"] is True
    assert "text" not in first["attempts"][0]
    assert first["next_offset"] is None
    assert tools.get_submission_history(COURSE, ASSIGNMENT, limit=0)["ok"] is False
    assert tools.get_submission_history(COURSE, ASSIGNMENT, max_text_chars=20001)["ok"] is False


def test_docx_text_is_scrubbed_but_pdf_remains_local_only(tmp_path, monkeypatch, retained_history_env):
    env = retained_history_env
    COURSE, ASSIGNMENT = env["course_id"], env["assignment_id"]
    docx_path = tmp_path / "draft.docx"
    with zipfile.ZipFile(docx_path, "w") as archive:
        archive.writestr("word/document.xml",
                         "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
                         "<w:body><w:p><w:r><w:t>Alice document draft</w:t></w:r></w:p></w:body></w:document>")
    docx = docx_path.read_bytes()
    env["capture"]([("503", "student-private.docx", docx)])
    result = tools.get_submission_history(COURSE, ASSIGNMENT)
    file_result = result["attempts"][0]["files"][0]
    assert file_result["type"] == "docx"
    assert "Alice" not in file_result.get("text", "")
    assert "student-private.docx" not in json.dumps(result)


def test_file_extraction_failure_is_visible_without_empty_success(tmp_path, monkeypatch, retained_history_env):
    env = retained_history_env
    COURSE, ASSIGNMENT = env["course_id"], env["assignment_id"]
    env["capture"]([("504", "draft.txt", b"safe words")])
    monkeypatch.setattr(student_attachments, "route_bytes",
                        lambda *a, **k: {"extraction_status": "failed", "text": ""})
    result = tools.get_submission_history(COURSE, ASSIGNMENT)
    assert result["attempts"][0]["files"][0]["status"] == "extraction_failed"
    assert "text" not in result["attempts"][0]["files"][0]


def _capture_many(env, user_ids, body):
    from api.mirror import store as mirror_store
    rows = [{"assignment_id": int(env["assignment_id"]), "user_id": uid, "attempt": 1,
             "submitted_at": "2026-09-01T10:00:00Z", "workflow_state": "submitted",
             "body": body, "submission_type": "online_text_entry", "attachments": []}
            for uid in user_ids]
    mirror_store.merge_submissions(env["course_id"], env["assignment_id"], rows,
                                   root=env["root"], canvas_origin=env["origin"])


def test_budget_ends_the_page_and_never_blanks_an_attempt(retained_history_env):
    """Law: the aggregate text budget ends a page between attempts; every attempt
    is returned exactly once, with its full max_text_chars-bounded text."""
    env = retained_history_env
    COURSE, ASSIGNMENT = env["course_id"], env["assignment_id"]
    user_ids = list(range(992001, 992013))
    _capture_many(env, user_ids, "<p>" + ("word " * 5000) + "</p>")  # ~25k > 20k cap

    seen, offset, pages = [], 0, 0
    while offset is not None:
        result = tools.get_submission_history(COURSE, ASSIGNMENT, max_text_chars=20000,
                                              offset=offset, limit=100)
        assert result["ok"] is True, result
        assert result["attempts"], "a page always holds at least one attempt"
        if result["next_offset"] is not None:
            assert result["page_end_reason"] == "text_budget"
            assert result["next_offset"] == offset + len(result["attempts"])
        for item in result["attempts"]:
            assert item["text_status"] in {"included", "truncated", "no_body"}
            assert item["text_status"] == "truncated"
            assert len(item["text"]) == 20000
            seen.append((item["pseudonym"], item["observation_digest"]))
        offset, pages = result["next_offset"], pages + 1
    assert pages > 1
    assert len(seen) == len(set(seen)) == len(user_ids)


def test_empty_body_with_text_file_reports_no_body_and_files_note(retained_history_env):
    env = retained_history_env
    env["capture"]([("505", "essay.txt", b"typed in a file")], body="")
    result = tools.get_submission_history(env["course_id"], env["assignment_id"])
    item = result["attempts"][0]
    assert item["text_status"] == "no_body" and item["text"] == ""
    assert "files" in item["text_note"]
    assert item["files"][0]["text"] == "typed in a file"


def test_include_text_false_marks_omitted_without_text(retained_history_env):
    env = retained_history_env
    env["capture"]()
    item = tools.get_submission_history(
        env["course_id"], env["assignment_id"], include_text=False)["attempts"][0]
    assert item["text_status"] == "omitted" and "text" not in item
