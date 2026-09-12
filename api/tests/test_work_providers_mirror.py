"""Offline tests: Work Registry discovery providers read the CanvasMirror.

Fixture style mirrors ``api/tests/test_mirror_queries.py``: workspace root is
redirected to ``tmp_path`` and the mirror is populated with ``store.write_*``
writers + ``store.record_pass(course_id, "full", ok=True)``. ``store.now_iso()``
marks a pass fresh; a pinned old ISO string marks it stale.
"""
from __future__ import annotations

import time

from api.mirror import store
from api.platform_services import config, workspace
from api.work_registry.providers import WorkCourseReads
from api.work_registry.providers import grading_debt, home_attention, late_work, roster_warnings

COURSE = "555001"

ASSIGNMENTS = [
    {"id": 700100, "name": "Essay", "due_at": "2026-07-01T09:00:00Z",
     "points_possible": 10, "published": True, "html_url": "u",
     "submission_types": ["online_text_entry"]},
    {"id": 700101, "name": "Reflection", "due_at": "2026-07-05T09:00:00Z",
     "points_possible": 10, "published": True, "html_url": "u",
     "submission_types": ["online_text_entry"]},
]
USERS = [
    {"id": 900101, "name": "Learner One", "sortable_name": "One, Learner",
     "short_name": "Lee", "sis_user_id": "SIS-900101", "enrollments": []},
    {"id": 900102, "name": "Learner Two", "sortable_name": "Two, Learner",
     "short_name": "Learner Two", "sis_user_id": "SIS-900102", "enrollments": []},
]

# Assignment 700100: one on-time ungraded submission (grading debt candidate,
# no comments) and two genuinely late submissions with string ids (aggregation
# check). Assignment 700101: a submission carrying submission_comments in the
# exact slice-5 mirror shape ({author_id, comment, created_at}) so
# grading_debt._teacher_touched and home_attention's comment classifier are
# both exercised against mirror rows, not live Canvas rows.
SUBMISSIONS_700100 = [
    {"assignment_id": 700100, "user_id": 900101, "workflow_state": "submitted",
     "submitted_at": "2026-07-02T09:00:00Z", "score": None,
     "submission_comments": []},
    {"assignment_id": 700100, "user_id": "900103", "workflow_state": "late",
     "late": True, "submitted_at": "2026-07-05T09:00:00Z", "score": 7,
     "submission_comments": []},
    {"assignment_id": 700100, "user_id": "900104", "workflow_state": "late",
     "late": True, "submitted_at": "2026-07-06T09:00:00Z", "score": 8,
     "submission_comments": []},
]
SUBMISSIONS_700101 = [
    {"assignment_id": 700101, "user_id": 900102, "workflow_state": "submitted",
     "submitted_at": "2026-07-06T10:30:00Z", "score": None,
     "submission_comments": [
         {"author_id": "900201", "comment": "Please revise your intro.",
          "created_at": "2026-07-06T09:00:00Z"},
         {"author_id": "900102", "comment": "Updated, thanks!",
          "created_at": "2026-07-06T10:00:00Z"},
     ]},
]


def _populate(root, *, fresh=True):
    stamp = store.now_iso() if fresh else "2026-01-01T00:00:00Z"
    store.write_roster(COURSE, USERS, {}, root=root, attempted_at=stamp)
    store.write_assignments(COURSE, ASSIGNMENTS, root=root, attempted_at=stamp)
    store.merge_submissions(COURSE, "700100", SUBMISSIONS_700100, root=root,
                            attempted_at=stamp, replace=True)
    store.merge_submissions(COURSE, "700101", SUBMISSIONS_700101, root=root,
                            attempted_at=stamp, replace=True)
    for pass_name in ("full", "roster"):
        store.record_pass(COURSE, pass_name, ok=True, attempted_at=stamp, root=root)


def _explode(*_args, **_kwargs):
    raise AssertionError("live Canvas read attempted")


def _mount(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))


def _reads(live_reader, deadline=None):
    """Build a WorkCourseReads with a test live_reader."""
    return WorkCourseReads(
        COURSE, deadline=deadline or time.monotonic() + 5, live_reader=live_reader,
    )


# --- the shared wrapper itself --------------------------------------------------

def test_wrapper_serves_fresh_mirror_for_all_three_shapes_with_zero_live_calls(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    reads = _reads(_explode)

    assignments = reads.assignments()
    assert {a["id"] for a in assignments} == {"700100", "700101"}

    users = reads.students()
    assert {u["id"] for u in users} == {"900101", "900102"}

    submissions = reads.submissions()
    assert {s["assignment_id"] for s in submissions} == {"700100", "700101"}
    # str-id rows: user ids stored either as int or str at write time both
    # come back as strings from the mirror.
    assert {s["user_id"] for s in submissions} == {"900101", "900102", "900103", "900104"}


def test_wrapper_serves_fresh_comment_scope_with_zero_live_calls(monkeypatch, tmp_path):
    """Fresh comment sidecar state serves the rich read entirely from the mirror."""
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    store.record_submission_comments_state(COURSE, ok=True, attempted_at=store.now_iso(), root=str(tmp_path))
    reads = _reads(_explode)

    submissions = reads.submissions(include_comments=True)
    assert {s["assignment_id"] for s in submissions} == {"700100", "700101"}


def test_wrapper_rich_read_falls_back_live_when_comment_sidecar_is_stale(monkeypatch, tmp_path):
    """Stale comment sidecar state (old timestamp) triggers exactly one rich live call."""
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    store.record_submission_comments_state(
        COURSE, ok=True, attempted_at="2026-01-01T00:00:00Z", root=str(tmp_path))
    calls = []
    live_rows = [{"id": "live-1", "assignment_id": "live-1", "user_id": "live-user",
                  "submission_comments": [{"author_id": "x", "comment": "hi"}]}]

    def fake_get(path, params=None, timeout=None, deadline=None):
        calls.append((path, dict(params or {})))
        return live_rows, None

    reads = _reads(fake_get)
    result = reads.submissions(include_comments=True)
    assert result == live_rows
    assert len(calls) == 1
    path, params = calls[0]
    assert path == f"/api/v1/courses/{COURSE}/students/submissions"
    assert params.get("include[]") == "submission_comments"


def test_wrapper_rich_read_falls_back_live_when_comment_sidecar_is_corrupt(monkeypatch, tmp_path):
    """A corrupt/missing comment sidecar (never recorded) also triggers exactly one rich live call."""
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))  # comment sidecar never recorded -> unavailable, not "current"
    calls = []
    live_rows = [{"id": "live-1", "assignment_id": "live-1", "user_id": "live-user",
                  "submission_comments": []}]

    def fake_get(path, params=None, timeout=None, deadline=None):
        calls.append(path)
        return live_rows, None

    reads = _reads(fake_get)
    result = reads.submissions(include_comments=True)
    assert result == live_rows
    assert calls == [f"/api/v1/courses/{COURSE}/students/submissions"]


def test_wrapper_plain_then_rich_keeps_separate_caches_and_still_acquires_rich(monkeypatch, tmp_path):
    """Plain read (fresh mirror) must never satisfy a later rich request; the
    rich request still performs its own dedicated acquisition even though the
    plain result is already cached."""
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    # Comment sidecar is never recorded here, so the rich scope is unavailable
    # and must fall back live even though the plain mirror scope is current.
    calls = []

    def fake_get(path, params=None, timeout=None, deadline=None):
        calls.append((path, dict(params or {})))
        return [{"id": "live-rich", "assignment_id": "live-rich", "user_id": "live-user",
                 "submission_comments": [{"author_id": "x", "comment": "hi"}]}], None

    reads = _reads(fake_get)
    plain = reads.submissions()
    assert {s["assignment_id"] for s in plain} == {"700100", "700101"}
    assert calls == []  # plain mirror scope was fresh; no live call yet

    rich = reads.submissions(include_comments=True)
    assert calls  # the rich request performed its own live acquisition
    assert calls[0][1].get("include[]") == "submission_comments"
    assert rich != plain
    assert rich[0]["id"] == "live-rich"

    # Calling plain again still returns the original mirror-backed result,
    # not the rich live result.
    assert reads.submissions() == plain


def test_wrapper_falls_back_live_when_mirror_is_stale(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path), fresh=False)
    live_rows = [{"id": 1, "name": "Live Assignment"}]

    def fake_get(path, params=None, timeout=None, deadline=None):
        return live_rows, None

    reads = _reads(fake_get)
    result = reads.assignments()
    assert result == live_rows


def test_wrapper_never_consults_mirror_for_a_non_matching_path(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))  # fresh mirror present, but path isn't one of the 3 shapes
    calls = []

    def fake_get(path, params=None, timeout=None, deadline=None):
        calls.append(path)
        return [], None

    reads = _reads(fake_get)
    reads.live_call(f"/api/v1/courses/{COURSE}/group_categories", {"per_page": 50})
    assert calls == [f"/api/v1/courses/{COURSE}/group_categories"]


def test_wrapper_falls_back_live_when_mirror_read_errors(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    from api.mirror import store as mirror_store
    monkeypatch.setattr(mirror_store, "read_assignments", lambda course_id, **kwargs: None)
    live_rows = [{"id": 1}]

    def fake_get(path, params=None, timeout=None, deadline=None):
        return live_rows, None

    reads = _reads(fake_get)
    result = reads.assignments()
    assert result == live_rows


# --- providers, end to end -------------------------------------------------------

def test_late_work_scan_course_reads_mirror_with_zero_live_calls(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    monkeypatch.setattr(late_work.config, "get_extra_time", lambda course_id: [])
    monkeypatch.setattr(
        late_work.school_calendar, "resolve_instructional_range",
        lambda date_from, date_to, known_schedule_ids, **kw: {
            "state": "ready", "date_from": date_from, "date_to": date_to,
            "days": {}, "no_count_dates": [],
        })

    reads = _reads(_explode)
    findings = late_work.scan_course(
        COURSE, now="2026-07-11T12:00:00+00:00", reads=reads,
    )
    assert len(findings) == 1
    assert findings[0]["kind"] == "late.work"
    assert findings[0]["assignment_id"] == "700100"
    # Two late, string-id submissions aggregate onto the one assignment.
    assert findings[0]["counts"] == {"total": 2, "pending": 2, "affected": 2}


def test_grading_debt_and_comment_followup_read_mirror_with_comment_shape(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    # The rich (include_comments=True) read is bounded by its own comment
    # sidecar freshness, separate from the plain submissions scope; record it
    # fresh here so this fixture continues to serve entirely from the mirror.
    store.record_submission_comments_state(
        COURSE, ok=True, attempted_at=store.now_iso(), root=str(tmp_path))

    reads = _reads(_explode)
    debt_findings = grading_debt.scan_course(
        COURSE, now="2026-07-11T12:00:00+00:00", reads=reads,
    )
    # Assignment 700100/user 900101: submitted, no score, no comments -> debt.
    # Assignment 700101/user 900102: no score but a staff comment -> touched,
    # so not counted as debt (exercises submission_comments from the mirror).
    debt_by_assignment = {item["assignment_id"]: item["counts"] for item in debt_findings}
    assert debt_by_assignment == {"700100": {"total": 1, "pending": 1, "affected": 1}}

    followups = home_attention.scan_comment_follow_up(
        COURSE, now="2026-07-11T12:00:00+00:00", reads=reads,
    )
    definite = [item for item in followups if item["kind"] == "grade.followup"]
    assert len(definite) == 1
    assert definite[0]["assignment_id"] == "700101"
    assert definite[0]["counts"] == {"total": 1, "pending": 1, "affected": 1}



def test_grading_debt_falls_back_live_when_mirror_is_stale(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path), fresh=False)

    def fake_get(path, params=None, timeout=None, deadline=None):
        if path.endswith("/assignments"):
            return [{"id": "live-1", "due_at": "2026-07-10T11:00:00+00:00"}], None
        return [
            {"assignment_id": "live-1", "user_id": "live-user", "workflow_state": "submitted",
             "submitted_at": "2026-07-11T11:00:00+00:00", "score": None, "submission_comments": []},
        ], None

    reads = _reads(fake_get)
    findings = grading_debt.scan_course(
        COURSE, now="2026-07-11T12:00:00+00:00", reads=reads,
    )
    assert len(findings) == 1
    assert findings[0]["assignment_id"] == "live-1"


def test_roster_warnings_reads_users_from_mirror_groups_stay_live(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    monkeypatch.setattr(roster_warnings.config, "get_roster_group_scheme",
                        lambda course_id: {"selected_group_category_id": "cat-1"})
    monkeypatch.setattr(roster_warnings.config, "get_extra_time",
                        lambda course_id: [{"id": "900101", "days": 0}])
    monkeypatch.setattr(roster_warnings, "_vault_context",
                        lambda: ({}, set(), {"literary": [], "dup_first": [], "common_word": []}))

    def fake_get(path, params=None, timeout=None, deadline=None):
        if path.endswith("/users"):
            raise AssertionError("live users read attempted; mirror should have served it")
        return [], None  # group_categories / groups / memberships stay live and empty

    reads = _reads(fake_get)
    findings = roster_warnings.scan_course(
        COURSE, now="2026-07-11T12:00:00+00:00", reads=reads,
    )
    assert {item["kind"] for item in findings} == {"roster.warning"}
    # Both mirrored students get missing_pseudonym + group_unset;
    # only "900101" also gets extra_time_without_days.
    assert sum(item["counts"]["affected"] for item in findings) == 5


def test_roster_warnings_uses_fresh_group_snapshot_without_live_group_calls(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    store.write_groups(COURSE, [{
        "category_id": "cat-1",
        "category_name": "Teams",
        "groups": [{
            "id": "group-1",
            "name": "Team 1",
            "memberships": [{"id": "membership-1", "user_id": "900101"}],
        }],
    }])
    monkeypatch.setattr(roster_warnings.config, "get_roster_group_scheme",
                        lambda course_id: {"selected_group_category_id": "cat-1"})
    monkeypatch.setattr(roster_warnings.config, "get_extra_time", lambda course_id: [])
    monkeypatch.setattr(roster_warnings, "_vault_context",
                        lambda: ({}, set(), {"literary": [], "dup_first": [], "common_word": []}))

    reads = _reads(_explode)
    findings = roster_warnings.scan_course(
        COURSE, now="2026-07-11T12:00:00+00:00", reads=reads,
    )

    assert sorted(item["counts"]["affected"] for item in findings) == [1, 2]
    assert all("user_id" not in item and "name" not in item for item in findings)


def _populate_roster_only(root, users, *, fresh=True):
    """Write just a roster document + its pass records -- no assignments or
    submissions, for tests that only exercise roster_warnings."""
    stamp = store.now_iso() if fresh else "2026-01-01T00:00:00Z"
    store.write_roster(COURSE, users, {}, root=root, attempted_at=stamp)
    for pass_name in ("full", "roster"):
        store.record_pass(COURSE, pass_name, ok=True, attempted_at=stamp, root=root)


def _by_suffix(findings):
    """Findings keyed by their source_ref suffix.

    That suffix is deliberately not the raw warning code for the three
    roster-change codes -- api.work_registry.models.validate_source_ref bans
    the substring "student" from any source_ref value, and those three
    codes are named with a "student_" prefix. See
    roster_warnings._safe_source_suffix: "student_added" becomes "added",
    "student_departed" becomes "departed", "student_changed_section"
    becomes "changed_section". The registry never carries identity; only
    the Roster page resolves who a finding is about.
    """
    return {item["source_ref"]["value"].rsplit(":", 1)[-1]: item for item in findings}


def test_roster_warnings_reports_added_departed_and_changed_section(monkeypatch, tmp_path):
    """The provider diffs the live mirror roster against a saved baseline
    using the exact shared computation as the Roster web UI (see
    api.roster_context.diff_roster_baseline), and turns each kind of change
    into its own roster.warning finding with an accurate affected count."""
    _mount(monkeypatch, tmp_path)
    users = [
        # Stayed on the baseline, unchanged -- must not appear in any new code.
        {"id": 990001, "name": "Stable One", "sortable_name": "One, Stable",
         "short_name": "Stable", "enrollments": [{"course_section_id": "sec-old"}]},
        # Was in sec-old on the baseline, now in sec-new: a clean section swap.
        {"id": 990002, "name": "Mover Two", "sortable_name": "Two, Mover",
         "short_name": "Mover", "enrollments": [{"course_section_id": "sec-new"}]},
        # Not present in the baseline at all.
        {"id": 990003, "name": "Fresh Three", "sortable_name": "Three, Fresh",
         "short_name": "Fresh", "enrollments": [{"course_section_id": "sec-new"}]},
    ]
    _populate_roster_only(str(tmp_path), users)
    config.set_roster_baseline(COURSE, {
        "acknowledged_at": "2026-07-01T00:00:00",
        "students": {
            "990001": ["sec-old"],
            "990002": ["sec-old"],
            "990004": ["sec-old"],  # on the baseline, gone from the live roster now
        },
    })
    monkeypatch.setattr(roster_warnings.config, "get_roster_group_scheme",
                        lambda course_id: {"selected_group_category_id": ""})
    monkeypatch.setattr(roster_warnings.config, "get_extra_time", lambda course_id: [])
    monkeypatch.setattr(roster_warnings, "_vault_context",
                        lambda: ({}, set(), {"literary": [], "dup_first": [], "common_word": []}))

    def fake_get(path, params=None, timeout=None, deadline=None):
        if path.endswith("/users"):
            raise AssertionError("live users read attempted; mirror should have served it")
        return [], None  # group_categories / groups / memberships stay live and empty

    reads = _reads(fake_get)
    findings = roster_warnings.scan_course(
        COURSE, now="2026-07-11T12:00:00+00:00", reads=reads,
    )

    by_suffix = _by_suffix(findings)
    assert by_suffix["added"]["counts"]["affected"] == 1
    assert by_suffix["departed"]["counts"]["affected"] == 1
    assert by_suffix["changed_section"]["counts"]["affected"] == 1
    # The registry's own privacy guard (models.validate_source_ref) would
    # reject any source_ref carrying student identity; confirm the finding
    # shape here never gives it a reason to.
    assert all("student" not in item["source_ref"]["value"] for item in findings)


def test_roster_warnings_reports_nothing_new_when_baseline_never_acknowledged(monkeypatch, tmp_path):
    """Without a saved baseline there is nothing to diff against, so the
    provider must not treat "no baseline" as "everyone is new"."""
    _mount(monkeypatch, tmp_path)
    users = [
        {"id": 990010, "name": "Solo Student", "sortable_name": "Student, Solo",
         "short_name": "Solo", "enrollments": [{"course_section_id": "sec-a"}]},
    ]
    _populate_roster_only(str(tmp_path), users)
    monkeypatch.setattr(roster_warnings.config, "get_roster_group_scheme",
                        lambda course_id: {"selected_group_category_id": ""})
    monkeypatch.setattr(roster_warnings.config, "get_extra_time", lambda course_id: [])
    monkeypatch.setattr(roster_warnings, "_vault_context",
                        lambda: ({}, set(), {"literary": [], "dup_first": [], "common_word": []}))

    def fake_get(path, params=None, timeout=None, deadline=None):
        return [], None

    reads = _reads(fake_get)
    findings = roster_warnings.scan_course(
        COURSE, now="2026-07-11T12:00:00+00:00", reads=reads,
    )

    suffixes = set(_by_suffix(findings))
    assert suffixes.isdisjoint({"added", "departed", "changed_section"})
