"""First focused test coverage for `api/student_packet.py::build_packet`.

Establishes the local-current / live-fallback split, the focused per-submission
attachment fetch, and the comment-author display rule this brief introduces —
`build_packet` had zero existing tests before this slice.

Every test drives the *real* private mirror through `api/mirror/store.py`
(no store-level monkeypatching) and only fakes the Canvas-reaching transport
functions (`_get_all_pages`, `_fetch_submission`, `_download_binary`,
`requests.Session`) so a stray live call fails loudly instead of silently
degrading into a "skipped" line.
"""
from __future__ import annotations

import json
import os

import pytest

from api import report_local_reads, student_packet
from api.mirror import store
from api.platform_services import workspace

COURSE_ID = "111"
ASSIGNMENT_ID = "700010"
USER_ID = "900001"
STUDENT_NAME = "Learner One"
STAMP = "2026-07-18T12:00:00Z"


@pytest.fixture(autouse=True)
def _workspace(monkeypatch, tmp_path):
    # build_packet/report_local_reads never take a root= — they always resolve
    # through the configured workspace, so tests redirect it here.
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    # STAMP below is a fixed historical timestamp, older than the real
    # serve-age bound (default 6h) by the time this suite actually runs.
    # Neutralize that bound so existing "local current" fixtures keep
    # resolving to the mirror path regardless of wall-clock time.
    monkeypatch.setattr(report_local_reads.mirror_queries, "_serve_max_age_hours", lambda: 1e9)
    return tmp_path


def _course_dict():
    return {"id": COURSE_ID, "name": "Sample Course"}


def _seed_local_current_course(*, submission_type="online_text_entry",
                               submission_comments=None, body="", url=""):
    store.write_assignments(COURSE_ID, [{
        "id": ASSIGNMENT_ID, "name": "Essay 1", "due_at": "2026-07-01T23:59:00Z",
        "points_possible": 10, "published": True,
        "submission_types": [submission_type],
    }], attempted_at=STAMP)
    row = {
        "assignment_id": ASSIGNMENT_ID, "user_id": USER_ID,
        "workflow_state": "submitted", "submitted_at": STAMP,
        "score": 9, "grade": "9", "submission_type": submission_type,
        "body": body, "url": url,
    }
    if submission_comments is not None:
        row["submission_comments"] = submission_comments
    store.merge_submissions(COURSE_ID, ASSIGNMENT_ID, [row],
                            attempted_at=STAMP, replace=True)
    # Only recording the "full" pass is what makes private_submissions "current".
    store.record_pass(COURSE_ID, "full", ok=True, attempted_at=STAMP)
    # The comment scope's own sidecar must also be current -- 1.0beta-06e
    # requires it alongside assignments for the mirror path to be used.
    store.record_submission_comments_state(COURSE_ID, ok=True, attempted_at=STAMP)


def _forbid_canvas_calls(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("a Canvas-reaching function was invoked in a fully-local run")
    monkeypatch.setattr(student_packet, "_get_all_pages", _boom)
    monkeypatch.setattr(student_packet, "_fetch_submission", _boom)
    monkeypatch.setattr(student_packet, "_download_binary", _boom)
    monkeypatch.setattr(student_packet.requests, "Session", _boom)


# --- (c) local-current, no attachment work samples: zero Canvas calls -------------

def test_build_packet_makes_zero_canvas_calls_when_local_current_and_no_attachments(monkeypatch, tmp_path):
    _seed_local_current_course()
    _forbid_canvas_calls(monkeypatch)

    lines = list(student_packet.build_packet(
        USER_ID, STUDENT_NAME, ["standing", "late", "adjustments", "comments"],
        [_course_dict()], "https://canvas.test", "tok",
        str(tmp_path / "reports"), [], skip_unchanged=False))

    assert not any("skipped" in line for line in lines)
    assert any("Info document written" in line for line in lines)
    assert any(line.startswith("FOLDER:") for line in lines)


def test_build_packet_renders_private_receipt_adjustment_projection_and_detects_no_change(
        monkeypatch, tmp_path):
    _seed_local_current_course()
    _forbid_canvas_calls(monkeypatch)
    projection = [{
        "course_id": COURSE_ID,
        "assignment_id": ASSIGNMENT_ID,
        "assignment_name": "Essay 1",
        "applied_at": "2026-07-19T12:00:00Z",
        "students": [{"user_id": USER_ID, "before": 7, "after": 9}],
    }]

    first = list(student_packet.build_packet(
        USER_ID, STUDENT_NAME, ["adjustments"], [_course_dict()],
        "https://canvas.test", "tok", str(tmp_path / "reports"),
        projection, skip_unchanged=True))
    second = list(student_packet.build_packet(
        USER_ID, STUDENT_NAME, ["adjustments"], [_course_dict()],
        "https://canvas.test", "tok", str(tmp_path / "reports"),
        projection, skip_unchanged=True))

    assert any("Info document written" in line for line in first)
    assert any("no change since last packet" in line for line in second)
    local_subs = report_local_reads.local_course_submissions(COURSE_ID, USER_ID)
    blocks = student_packet._info_blocks(
        local_subs,
        ["Essay 1: Score adjusted via curve on 2026-07-19: 7 → 9"],
        ["adjustments"],
    )
    assert blocks[0][2][0] == "Essay 1: Score adjusted via curve on 2026-07-19: 7 → 9"


def test_local_assembly_produces_identical_info_blocks_to_the_old_live_shape():
    """`report_local_reads.local_course_submissions` must feed `_info_blocks`
    the same nested shape a live fetch used to — same rendered content for the
    fields this brief doesn't deliberately change (comments are the one
    exception, exercised separately below)."""
    _seed_local_current_course(submission_type="online_text_entry", body="")
    local_subs = report_local_reads.local_course_submissions(COURSE_ID, USER_ID)
    assert local_subs is not None and len(local_subs) == 1

    # What the old whole-course live fetch would have handed to _info_blocks.
    live_shaped_subs = [{
        "assignment_id": ASSIGNMENT_ID, "user_id": USER_ID,
        "workflow_state": "submitted", "submitted_at": STAMP, "graded_at": None,
        "score": 9, "grade": "9", "late": False, "missing": False, "excused": False,
        "cached_due_date": None, "seconds_late": None, "attempt": None,
        "grade_matches_current_submission": None, "submission_type": "online_text_entry",
        "body": "", "url": "", "submission_comments": [],
        "assignment": {"id": ASSIGNMENT_ID, "name": "Essay 1",
                       "due_at": "2026-07-01T23:59:00Z", "points_possible": 10},
    }]

    sections = ["standing", "late", "adjustments", "comments"]
    assert (student_packet._info_blocks(local_subs, [], sections)
            == student_packet._info_blocks(live_shaped_subs, [], sections))


# --- (d) course whose mirror is not current falls back to the live fetch, unchanged ---

def test_build_packet_falls_back_to_live_fetch_when_submissions_pass_never_recorded(monkeypatch, tmp_path):
    # Assignments + a submission exist, but no "full"/"delta" pass was ever
    # recorded -> private_submissions reports state "unavailable", not "current".
    store.write_assignments(COURSE_ID, [{
        "id": ASSIGNMENT_ID, "name": "Essay 1", "due_at": "", "points_possible": 10,
        "published": True, "submission_types": ["online_text_entry"],
    }], attempted_at=STAMP)
    store.merge_submissions(COURSE_ID, ASSIGNMENT_ID, [{
        "assignment_id": ASSIGNMENT_ID, "user_id": USER_ID,
        "workflow_state": "submitted", "submitted_at": STAMP, "score": 9,
        "submission_type": "online_text_entry",
    }], attempted_at=STAMP, replace=True)
    assert report_local_reads.local_course_submissions(COURSE_ID, USER_ID) is None

    calls = []

    def fake_get_all_pages(session, url, params):
        calls.append((url, params))
        return [{
            "assignment_id": ASSIGNMENT_ID, "user_id": USER_ID,
            "workflow_state": "submitted", "submitted_at": STAMP, "score": 9,
            "submission_type": "online_text_entry", "body": "", "submission_comments": [],
            "assignment": {"id": ASSIGNMENT_ID, "name": "Essay 1", "points_possible": 10},
        }]

    monkeypatch.setattr(student_packet, "_get_all_pages", fake_get_all_pages)

    lines = list(student_packet.build_packet(
        USER_ID, STUDENT_NAME, ["standing"], [_course_dict()],
        "https://canvas.test", "tok", str(tmp_path / "reports"), [], skip_unchanged=False))

    assert len(calls) == 1
    url, params = calls[0]
    assert url == f"https://canvas.test/api/v1/courses/{COURSE_ID}/students/submissions"
    assert params == {"student_ids[]": USER_ID,
                      "include[]": ["assignment", "submission_comments"], "per_page": 100}
    assert any("Info document written" in line for line in lines)


def test_build_packet_falls_back_to_live_fetch_when_no_local_document_exists(monkeypatch, tmp_path):
    # No write_assignments/merge_submissions call at all for this course.
    assert report_local_reads.local_course_submissions(COURSE_ID, USER_ID) is None

    calls = []
    monkeypatch.setattr(student_packet, "_get_all_pages",
                        lambda *a, **k: calls.append(a) or [])

    lines = list(student_packet.build_packet(
        USER_ID, STUDENT_NAME, ["standing"], [_course_dict()],
        "https://canvas.test", "tok", str(tmp_path / "reports"), [], skip_unchanged=False))

    assert len(calls) == 1
    assert any("student not found" in line for line in lines)


# --- (e) online_upload triggers exactly one focused fetch, only when "work" is asked ---

def test_online_upload_submission_triggers_one_focused_fetch_when_work_requested(monkeypatch, tmp_path):
    _seed_local_current_course(submission_type="online_upload")

    fetch_calls = []

    def fake_fetch_submission(session, base, course_id, assignment_id, user_id):
        fetch_calls.append((base, course_id, assignment_id, user_id))
        return {"attachments": [{"filename": "final.pdf", "url": "https://signed.example/final.pdf"}]}

    download_calls = []
    monkeypatch.setattr(student_packet, "_fetch_submission", fake_fetch_submission)
    monkeypatch.setattr(student_packet, "_download_binary",
                        lambda session, url, dest: download_calls.append((url, dest)))
    monkeypatch.setattr(student_packet, "_get_all_pages", lambda *a, **k: (_ for _ in ())
                        .throw(AssertionError("whole-course fetch must not be called")))

    list(student_packet.build_packet(
        USER_ID, STUDENT_NAME, ["work"], [_course_dict()],
        "https://canvas.test", "tok", str(tmp_path / "reports"), [], skip_unchanged=False))

    assert fetch_calls == [("https://canvas.test", COURSE_ID, ASSIGNMENT_ID, USER_ID)]
    assert len(download_calls) == 1
    assert download_calls[0][0] == "https://signed.example/final.pdf"


def test_online_upload_submission_triggers_zero_focused_fetches_when_work_not_requested(monkeypatch, tmp_path):
    _seed_local_current_course(submission_type="online_upload")

    fetch_calls = []
    monkeypatch.setattr(student_packet, "_fetch_submission",
                        lambda *a, **k: fetch_calls.append(a) or None)

    list(student_packet.build_packet(
        USER_ID, STUDENT_NAME, ["standing"], [_course_dict()],
        "https://canvas.test", "tok", str(tmp_path / "reports"), [], skip_unchanged=False))

    assert fetch_calls == []


# --- (f) comment-author display: self / staff / unattributable-but-kept -----------

def test_apply_comment_display_resolves_self_staff_and_blank_cases():
    subs = [{
        "submission_comments": [
            {"author_id": USER_ID, "author_role": "student",
             "comment": "My own note.", "created_at": STAMP},
            {"author_id": "800001", "author_role": "teacher",
             "comment": "Nice work.", "created_at": STAMP},
            {"author_id": "800002", "author_role": "",
             "comment": "Unrelated comment.", "created_at": STAMP},
        ],
    }]
    report_local_reads.apply_comment_display(subs, USER_ID, STUDENT_NAME)
    comments = subs[0]["submission_comments"]

    assert len(comments) == 3  # every comment is kept, never dropped
    assert comments[0]["author_name"] == STUDENT_NAME
    assert comments[0]["comment"] == "My own note."
    assert comments[1]["author_name"] == report_local_reads.STAFF_LABEL
    assert comments[1]["comment"] == "Nice work."
    # Unattributable: name is blanked (not guessed at), comment text kept.
    assert comments[2]["author_name"] == ""
    assert comments[2]["comment"] == "Unrelated comment."


def test_info_blocks_omits_name_prefix_but_keeps_comment_when_author_name_is_blank():
    subs = [{"submission_comments": [
        {"author_name": "", "created_at": STAMP, "comment": "Unrelated comment."},
        {"author_name": "Ms. Real Teacher", "created_at": STAMP, "comment": "Nice work."},
    ]}]
    heading, kind, rendered = student_packet._info_blocks(subs, [], ["comments"])[0]
    assert heading == "Comments" and kind == "lines"
    assert f"{STAMP[:10]}: Unrelated comment." in rendered  # no dangling "— :"
    assert f"{STAMP[:10]} — Ms. Real Teacher: Nice work." in rendered


def test_build_packet_end_to_end_keeps_unattributable_local_comment_without_name(tmp_path):
    _seed_local_current_course(submission_comments=[
        {"author_id": USER_ID, "author_role": "student",
         "comment": "My own note.", "created_at": STAMP},
        {"author_id": "800001", "author_role": "teacher",
         "comment": "Nice work.", "created_at": STAMP},
        {"author_id": "800002", "author_role": "",
         "comment": "Unrelated comment.", "created_at": STAMP},
    ])

    lines = list(student_packet.build_packet(
        USER_ID, STUDENT_NAME, ["standing", "comments"], [_course_dict()],
        "https://canvas.test", "tok", str(tmp_path / "reports"), [], skip_unchanged=False))

    assert any("Info document written" in line for line in lines)
    local_subs = report_local_reads.local_course_submissions(COURSE_ID, USER_ID)
    report_local_reads.apply_comment_display(local_subs, USER_ID, STUDENT_NAME)
    blocks = student_packet._info_blocks(local_subs, [], ["comments"])
    heading, kind, rendered = blocks[0]
    assert heading == "Comments" and kind == "lines"
    assert any("My own note." in line and STUDENT_NAME in line for line in rendered)
    assert any("Nice work." in line and report_local_reads.STAFF_LABEL in line for line in rendered)
    # Kept, not dropped — but with no dangling "— :" name prefix.
    assert any(line == f"{STAMP[:10]}: Unrelated comment." for line in rendered)


# --- (a) live-fallback keeps Canvas's own author_name; the display rule never runs ---

def test_build_packet_live_fallback_never_relabels_comments(monkeypatch, tmp_path):
    # No local mirror written for this course -> live fallback. The raw Canvas
    # response already carries real author_name values; the local-only
    # comment-display rule must not touch them.
    def _forbid(*args, **kwargs):
        raise AssertionError("apply_comment_display must not run for the live-fallback path")
    monkeypatch.setattr(student_packet.report_local_reads, "apply_comment_display", _forbid)

    def fake_get_all_pages(session, url, params):
        return [{
            "assignment_id": ASSIGNMENT_ID, "user_id": USER_ID,
            "workflow_state": "submitted", "submitted_at": STAMP, "score": 9,
            "submission_type": "online_text_entry", "body": "",
            "submission_comments": [
                {"author_id": "800001", "author_name": "Ms. Real Teacher",
                 "author_role": "teacher", "comment": "Nice work.", "created_at": STAMP},
            ],
            "assignment": {"id": ASSIGNMENT_ID, "name": "Essay 1", "points_possible": 10},
        }]
    monkeypatch.setattr(student_packet, "_get_all_pages", fake_get_all_pages)

    lines = list(student_packet.build_packet(
        USER_ID, STUDENT_NAME, ["comments"], [_course_dict()],
        "https://canvas.test", "tok", str(tmp_path / "reports"), [], skip_unchanged=False))

    assert any("Info document written" in line for line in lines)  # no AssertionError raised


# --- 1.0beta-06d: private source/freshness manifest --------------------------------

def test_build_packet_writes_source_manifest_entry_per_processed_course(tmp_path):
    _seed_local_current_course()
    lines = list(student_packet.build_packet(
        USER_ID, STUDENT_NAME, ["standing"], [_course_dict()],
        "https://canvas.test", "tok", str(tmp_path / "reports"), [], skip_unchanged=False))
    stu_root = next(line for line in lines if line.startswith("FOLDER:")).split("FOLDER: ", 1)[1]

    with open(os.path.join(stu_root, "_source_manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    assert set(manifest.keys()) == {COURSE_ID}
    entry = manifest[COURSE_ID]
    assert entry["course_name"] == "Sample Course"
    assert entry["source"] == "mirror"
    assert entry["synced_at"] == STAMP
    assert entry["generated_at"]

    # Fully separate file/concern from `_manifest.json`'s dedupe cache.
    with open(os.path.join(stu_root, "_manifest.json"), encoding="utf-8") as f:
        dedupe = json.load(f)
    assert set(dedupe[COURSE_ID].keys()) == {"signature", "last_run"}


def test_build_packet_writes_source_manifest_even_when_skip_unchanged_skips_the_course(tmp_path):
    _seed_local_current_course()
    reports_root = str(tmp_path / "reports")
    # First run establishes the `_manifest.json` dedupe signature.
    list(student_packet.build_packet(
        USER_ID, STUDENT_NAME, ["standing"], [_course_dict()],
        "https://canvas.test", "tok", reports_root, [], skip_unchanged=True))

    # Second run against unchanged data: skip_unchanged skips Info/work output
    # for the course, but the source/freshness disclosure is independent of
    # that dedupe outcome and must still be written.
    lines = list(student_packet.build_packet(
        USER_ID, STUDENT_NAME, ["standing"], [_course_dict()],
        "https://canvas.test", "tok", reports_root, [], skip_unchanged=True))
    assert any("no change since last packet" in line for line in lines)

    stu_root = os.path.join(reports_root, student_packet.safe_name(STUDENT_NAME))
    with open(os.path.join(stu_root, "_source_manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest[COURSE_ID]["source"] == "mirror"
    assert manifest[COURSE_ID]["synced_at"] == STAMP


def test_build_packet_writes_canvas_source_for_a_live_fallback_course(monkeypatch, tmp_path):
    def fake_get_all_pages(session, url, params):
        return [{
            "assignment_id": ASSIGNMENT_ID, "user_id": USER_ID,
            "workflow_state": "submitted", "submitted_at": STAMP, "score": 9,
            "submission_type": "online_text_entry", "body": "", "submission_comments": [],
            "assignment": {"id": ASSIGNMENT_ID, "name": "Essay 1", "points_possible": 10},
        }]
    monkeypatch.setattr(student_packet, "_get_all_pages", fake_get_all_pages)

    lines = list(student_packet.build_packet(
        USER_ID, STUDENT_NAME, ["standing"], [_course_dict()],
        "https://canvas.test", "tok", str(tmp_path / "reports"), [], skip_unchanged=False))
    stu_root = next(line for line in lines if line.startswith("FOLDER:")).split("FOLDER: ", 1)[1]

    with open(os.path.join(stu_root, "_source_manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    entry = manifest[COURSE_ID]
    assert entry["course_name"] == "Sample Course"
    assert entry["source"] == "canvas"
    assert entry["synced_at"] == ""
    assert entry["generated_at"]


def test_build_packet_writes_no_manifest_entry_for_a_course_the_student_is_not_in(tmp_path):
    # Local-current course, but only some *other* student has a submission in
    # it -- local_course_submissions(cid, USER_ID) returns [], not None, and
    # the course is skipped as "student not in this course". Per this brief's
    # default assumption, a course producing no output gets no manifest entry.
    store.write_assignments(COURSE_ID, [{
        "id": ASSIGNMENT_ID, "name": "Essay 1", "due_at": "", "points_possible": 10,
        "published": True, "submission_types": ["online_text_entry"],
    }], attempted_at=STAMP)
    store.merge_submissions(COURSE_ID, ASSIGNMENT_ID, [{
        "assignment_id": ASSIGNMENT_ID, "user_id": "someone_else", "workflow_state": "submitted",
        "submitted_at": STAMP, "score": 9, "submission_type": "online_text_entry",
    }], attempted_at=STAMP, replace=True)
    store.record_pass(COURSE_ID, "full", ok=True, attempted_at=STAMP)
    store.record_submission_comments_state(COURSE_ID, ok=True, attempted_at=STAMP)
    assert report_local_reads.local_course_submissions(COURSE_ID, USER_ID) == []

    reports_root = str(tmp_path / "reports")
    lines = list(student_packet.build_packet(
        USER_ID, STUDENT_NAME, ["standing"], [_course_dict()],
        "https://canvas.test", "tok", reports_root, [], skip_unchanged=False))
    assert any("student not found in any selected course" in line for line in lines)

    stu_root = os.path.join(reports_root, student_packet.safe_name(STUDENT_NAME))
    with open(os.path.join(stu_root, "_source_manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest == {}
