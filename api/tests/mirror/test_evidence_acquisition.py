"""Exact acquisition scope proof, safe projection, and immutable history laws."""
from pathlib import Path
import json

import pytest

from api.mirror.evidence_acquisition import (
    CourseAcquisitionReceipt, ScopeReceipt, publish_course_receipt,
    publish_attachment_status,
)
from api.mirror.evidence_publish import EvidencePublisher, PublicationRefused
from api.tests.mirror.acquisition_samples import SyntheticVault
from api.mirror.evidence_index import VIEW_COLUMNS


def publish(tmp_path, scopes, *, run="run-a", publisher=None):
    publisher = publisher or EvidencePublisher(
        workspace_root=tmp_path, source_key="a" * 64, course_id="1", vault=SyntheticVault())
    receipt = CourseAcquisitionReceipt("1", "2026-01-04T00:00:00Z",
                                       "2026-01-04T00:01:00Z", tuple(scopes))
    result = publish_course_receipt(publisher=publisher, receipt=receipt,
                                    writer_key="writer-a", run_id=run)
    return publisher, result


def state(publisher, scope="assignment.submissions", sid="10"):
    return publisher.store.scan().scopes[("a" * 64, "1", scope, sid)]


def submission(**extra):
    return {"user_id": "991001", "assignment_id": "10", "attempt": 1,
            "submitted_at": "2026-01-01T00:00:00Z", "body": "Synthetic prose.", **extra}


@pytest.mark.parametrize("complete,error,mode,removed", [
    (True, None, "snapshot", True), (False, None, "snapshot", False),
    (True, "timeout", "snapshot", False), (True, None, "delta", False),
])
def test_only_exact_complete_snapshot_empty_proves_absence(tmp_path, complete, error, mode, removed):
    initial = ScopeReceipt("assignment.submissions", "10", (submission(),), True)
    publisher, _ = publish(tmp_path, [initial])
    empty = ScopeReceipt("assignment.submissions", "10", (), complete, error, mode)
    publish(tmp_path, [empty], publisher=publisher, run="run-b")
    current = state(publisher)
    assert ("submission:10:Pikachu" in current.tombstones) is removed
    assert ("submission:10:Pikachu" in current.member_keys) is not removed
    assert len(current.history_refs) >= 2


def test_sparse_history_keeps_original_timestamps_without_inventing_lateness(tmp_path):
    publisher, _ = publish(tmp_path, [ScopeReceipt("assignment.submissions", "10", (
        submission(attempt=3, late=False, submission_history=[
            {"attempt": 1, "submitted_at": "2025-12-31T18:00:00-06:00", "late": True},
            {"attempt": 2, "submitted_at": "2026-01-02T00:00:00Z"},
        ]),), True)])
    observations = [f["payload"] for f in publisher.store.scan().facts.values()
                    if f["kind"] == "attempt_observation"]
    assert len(observations) == 3
    old = next(p for p in observations if p["attempt"] == 1)
    assert old["submitted_at"] == "2026-01-01T00:00:00Z" and old["late"] is True
    sparse = next(p for p in observations if p["attempt"] == 2)
    assert "body" not in sparse and "late" not in sparse
    publish(tmp_path, [ScopeReceipt("assignment.submissions", "10", (
        submission(attempt=3, body="", submitted_at=None),), True)], publisher=publisher, run="run-b")
    assert len(state(publisher).history_refs) >= 4
    assert state(publisher).established_submitted_at["attempt:10:Pikachu:1"] == old["submitted_at"]


def test_unchanged_poll_reuses_fact_objects_and_preserves_receipt_interval(tmp_path):
    scope = ScopeReceipt("assignment.submissions", "10", (submission(),), True,
                         watermarks={"submitted_since": "2026-01-03T00:00:00Z"})
    publisher, first = publish(tmp_path, [scope])
    _, second = publish(tmp_path, [scope], publisher=publisher, run="run-b")
    assert first.fact_refs == second.fact_refs
    assert first.commit_refs != second.commit_refs
    commit = publisher.store.scan().commits[second.commit_refs[0]]
    assert commit["acquisition_started_at"] == "2026-01-04T00:00:00Z"
    assert commit["acquisition_finished_at"] == "2026-01-04T00:01:00Z"
    assert commit["watermarks"] == scope.watermarks


def test_bad_attempt_does_not_drop_other_observed_history(tmp_path):
    publisher, result = publish(tmp_path, [ScopeReceipt("assignment.submissions", "10", (
        submission(attempt=3, submission_history=[
            {"attempt": 0, "submitted_at": "2026-01-01T00:00:00Z"},
            {"attempt": 1, "submitted_at": "2025-12-31T00:00:00Z", "body": "Old prose"},
        ]),), True)])
    assert "invalid_attempt" in result.gaps
    attempts = {f["payload"]["attempt"] for f in publisher.store.scan().facts.values()
                if f["kind"] == "attempt_observation"}
    assert attempts == {1, 3}
    assert not state(publisher).membership_complete


def test_one_failed_assignment_keeps_successful_sibling_and_watermarks_scoped(tmp_path):
    failed = ScopeReceipt("assignment.submissions", "10", (submission(),), True, "timeout",
                           watermarks={"submitted_since": "2026-01-03T00:00:00Z"})
    good = ScopeReceipt("assignment.submissions", "11", (
        submission(assignment_id="11"),), True, watermarks=failed.watermarks)
    publisher, result = publish(tmp_path, [failed, good])
    assert result.successful_scopes == (("assignment.submissions", "11"),)
    commits = publisher.store.scan().commits.values()
    assert next(c for c in commits if c["scope_id"] == "10")["watermarks"] == {}
    assert state(publisher, sid="11").membership_complete is True


def test_unresolved_identity_retains_safe_siblings_and_no_complete_watermark(tmp_path):
    publisher, result = publish(tmp_path, [ScopeReceipt("assignment.submissions", "10", (
        submission(), submission(user_id="991999")), True)])
    assert "identity_unresolved" in result.gaps
    assert state(publisher).membership_complete is False
    assert state(publisher).member_keys == ("submission:10:Pikachu",)
    assert result.successful_scopes == ()


def test_comments_overrides_course_and_roster_are_safe_and_separately_scoped(tmp_path):
    scopes = [
        ScopeReceipt("course.context", "1", ({"id": 1, "name": "Avery Sample course",
                      "workflow_state": "available"},), True),
        ScopeReceipt("course.roster", "1", ({"id": "991001", "name": "Avery Sample",
                      "enrollments": [{"course_section_id": 4}]},
                      {"id": "991001", "section_ids": ["5"]}), True),
        ScopeReceipt("course.assignments", "1", ({"id": 10, "name": "Draft",
                      "description": "<p>Morgan Sample prompt</p>",
                      "due_at": "2026-01-02T00:00:00Z"},), True),
        ScopeReceipt("assignment.comments", "10", ({"id": 8, "user_id": "991001",
                      "author_id": "991002", "comment": "Avery Sample revised.",
                      "created_at": "2026-01-02T00:00:00Z"},), True),
        ScopeReceipt("assignment.overrides", "10", ({"id": 9,
                      "student_ids": ["991001", "991002"], "group_id": 7,
                      "due_at": "2026-01-03T00:00:00Z", "title": "private name"},), True),
    ]
    publisher, result = publish(tmp_path, scopes)
    assert result.gaps == ()
    facts = list(publisher.store.scan().facts.values())
    roster = next(f["payload"] for f in facts if f["kind"] == "student" and f["payload"]["section_ids"] == ["4", "5"])
    assert roster["pseudonym"] == "Pikachu"
    comment = next(f["payload"] for f in facts if f["kind"] == "comment")
    assert comment["author_pseudonym"] == "Eevee"
    override = next(f["payload"] for f in facts if f["kind"] == "override")
    assert override["student_pseudonyms"] == ["Eevee", "Pikachu"]
    assert override["due_at"] == "2026-01-03T00:00:00Z"
    assert state(publisher, "assignment.comments").membership_complete is True
    safe_bytes = b"".join(p.read_bytes() for p in (tmp_path / "CanvasMirror").rglob("*.json"))
    for forbidden in (b"991001", b"991002", b"Avery Sample", b"Morgan Sample", b"private name"):
        assert forbidden not in safe_bytes


def test_unsupported_scope_does_not_block_supported_due_context(tmp_path):
    publisher, result = publish(tmp_path, [
        ScopeReceipt("course.quizzes", "1", ({"id": 8},), True),
        ScopeReceipt("course.context", "1", ({"id": 1, "name": "ELA", "term": {}},), True),
        ScopeReceipt("assignment.submissions", "10", (submission(cached_due_date="2026-01-02T00:00:00Z"),), True),
    ])
    assert set(result.gaps) == {"unsupported_scope"}
    assert len(result.commit_refs) == 2
    assert state(publisher).membership_complete
    current = next(f["payload"] for f in publisher.store.scan().facts.values() if f["kind"] == "submission")
    assert current["effective_due_at"] == "2026-01-02T00:00:00Z"


def test_course_lifecycle_receipt_preserves_normalized_term_and_caller_state(tmp_path):
    publisher, result = publish(tmp_path, [ScopeReceipt(
        "course.context", "1", ({"id": 1, "name": "ELA", "concluded": False,
                                "term": {"end_at": "2026-06-01T00:00:00Z"},
                                "enrollment_states": ["active"]},), True)])
    assert result.gaps == ()
    course = next(f["payload"] for f in publisher.store.scan().facts.values()
                  if f["kind"] == "course")
    assert course["course_concluded"] is False
    assert course["term_end_at"] == "2026-06-01T00:00:00Z"
    assert course["enrollment_states"] == ["active"]


def test_structure_projection_supports_catalog_aliases_and_scrubs_nested_fields(tmp_path):
    scopes = [
        ScopeReceipt("course.groups", "1", ({"id": 2, "name": "Avery Sample group",
                      "group_category_id": 8, "group_category_name": "Morgan Sample teams",
                      "users": [{"id": "991001"}, {"id": "991002"}]},), True),
        ScopeReceipt("course.modules", "1", ({"id": 3, "name": "Morgan Sample module", "position": 1,
                      "items": [{"id": 4, "type": "Assignment", "title": "Avery Sample draft",
                                 "position": 1, "content_id": 10, "html_url": "secret"}]},), True),
        ScopeReceipt("course.pages", "1", ({"id": 5, "title": "Page", "body_text": "List<string> code; Avery Sample",
                      "published": True, "front_page": False},), True),
        ScopeReceipt("course.assignment_groups", "1", ({"id": 6, "name": "Practice", "position": 1,
                      "group_weight": 25},), True),
        ScopeReceipt("course.assignments", "1", ({"id": 10, "name": "Draft", "unlock_at": "2026-01-01T00:00:00Z",
                      "lock_at": "2026-02-01T00:00:00Z", "assignment_group_id": 6, "published": False,
                      "submission_types": ["online_text_entry"], "all_dates": [{"id": 9, "base": False,
                      "title": "Avery Sample extension", "due_at": "2026-01-02T00:00:00Z"}]},), True),
        ScopeReceipt("course.context", "1", ({"name": "ELA", "start_at": "2026-01-01T00:00:00Z",
                      "end_at": None, "conclude_at": None, "restrict_enrollments_to_course_dates": False},), True),
    ]
    publisher, result = publish(tmp_path, scopes)
    assert result.gaps == () and len(result.successful_scopes) == 6
    facts = list(publisher.store.scan().facts.values())
    group = next(f["payload"] for f in facts if f["kind"] == "group")
    assert group["student_pseudonyms"] == ["Eevee", "Pikachu"]
    assert group["category_name"].endswith(" teams")
    assert "Morgan Sample" not in group["category_name"]
    assert len(group["category_key"]) == 64
    page = next(f["payload"] for f in facts if f["kind"] == "page")
    assert "List<string>" in page["body"]
    assignment = next(f["payload"] for f in facts if f["kind"] == "assignment")
    assert assignment["all_dates"][0]["override_id"] == "9"
    safe_bytes = b"".join(p.read_bytes() for p in (tmp_path / "CanvasMirror").rglob("*.json"))
    for forbidden in (b"991001", b"991002", b"Avery Sample", b"Morgan Sample", b"html_url", b"secret"):
        assert forbidden not in safe_bytes


def test_empty_group_category_publishes_safe_current_fact(tmp_path):
    publisher, result = publish(tmp_path, [ScopeReceipt("course.groups", "1", (
        {"_category_only": True, "group_category_id": "8",
         "group_category_name": "Teams"},), True)])
    assert result.gaps == ()
    snapshot = publisher.store.scan()
    categories = [fact for fact in snapshot.facts.values()
                  if fact["kind"] == "group_category"]
    assert len(categories) == 1
    assert categories[0]["payload"]["category_name"] == "Teams"
    assert set(categories[0]["payload"]) == {"category_key", "category_name"}
    assert snapshot.scopes[("a" * 64, "1", "course.groups", "1")].membership_complete


@pytest.mark.parametrize("scope,row,gap", [
    ("course.groups", {"id": 2, "name": "Group"}, "group_membership_unavailable"),
    ("course.modules", {"id": 3, "name": "Module"}, "module_items_unavailable"),
    ("course.pages", {"id": 4, "title": "Page"}, "page_body_unavailable"),
    ("course.groups", {"id": 2, "users": ["991001", "991999"]}, "identity_unresolved"),
])
def test_nested_structure_gap_never_proves_membership(tmp_path, scope, row, gap):
    publisher, result = publish(tmp_path, [ScopeReceipt(scope, "1", (row,), True)])
    assert gap in result.gaps
    assert not state(publisher, scope, "1").membership_complete
    assert result.successful_scopes == ()


def test_ancillary_evidence_gaps_do_not_invalidate_submission_enumeration(tmp_path):
    publisher, result = publish(tmp_path, [ScopeReceipt("assignment.submissions", "10", (
        submission(attachments=[{"id": 3, "filename": "private"}], submission_comments=[{"id": 4}]),), True,
        watermarks={"updated_since": "2026-01-03T00:00:00Z"})])
    # Attachment associations are now published facts under their own scope, so
    # they no longer appear as a submission-scope gap. Comments still do.
    assert set(result.gaps) == {"comments_scope_required"}
    assert result.successful_scopes == (("assignment.submissions", "10"),
                                        ("assignment.attachments", "10"))
    assert state(publisher).membership_complete
    commit = publisher.store.scan().commits[result.commit_refs[0]]
    assert commit["watermarks"] and commit["gaps"] == []
    attachments = [f for f in publisher.store.scan().facts.values()
                   if f["kind"] == "attachment"]
    assert len(attachments) == 1
    assert attachments[0]["payload"]["status"] == "pending"
    assert "filename" not in attachments[0]["payload"]
    assert state(publisher, "assignment.attachments", "10").membership_complete


def test_import_mode_and_wrong_scope_are_refused_before_scope_publication(tmp_path):
    publisher, result = publish(tmp_path, [
        ScopeReceipt("course.roster", "2", (), True),
        ScopeReceipt("assignment.submissions", "10", (submission(),), True, mode="import"),
    ])
    assert result.commit_refs == () and result.fact_refs == ()
    assert set(result.gaps) == {"scope_mismatch", "invalid_mode"}


def test_malformed_watermark_does_not_abort_next_scope(tmp_path):
    publisher, result = publish(tmp_path, [
        ScopeReceipt("assignment.comments", "10", (), True, watermarks={"updated_since": "bad"}),
        ScopeReceipt("assignment.comments", "11", (), True),
    ])
    assert result.successful_scopes == (("assignment.comments", "11"),)
    assert result.gaps == ("commit_refused",)


def test_scope_mismatch_top_level_has_no_safe_publication(tmp_path):
    publisher = EvidencePublisher(workspace_root=tmp_path, source_key="a" * 64,
                                  course_id="1", vault=SyntheticVault())
    receipt = CourseAcquisitionReceipt("2", "2026-01-04T00:00:00Z", "2026-01-04T00:01:00Z", ())
    with pytest.raises(PublicationRefused, match="scope_mismatch"):
        publish_course_receipt(publisher=publisher, receipt=receipt, writer_key="writer-a", run_id="run-a")
    assert not (tmp_path / "CanvasMirror").exists()


@pytest.mark.parametrize("entry_point", ["receipt", "text"])
def test_publication_ignores_conflicting_shared_descriptor(tmp_path, entry_point):
    from api.platform_services import workspace
    publisher = EvidencePublisher(workspace_root=tmp_path, source_key="a" * 64,
                                  course_id="1", vault=SyntheticVault())
    safe_root = Path(workspace.canvas_mirror_evidence_root(tmp_path))
    safe_root.mkdir(parents=True)
    old = safe_root / "reader.v1.json"
    old_contract = {
        "schema_version": 1,
        "description": "Pseudonymized CanvasMirror evidence; read-only local SQLite projection",
        "views": {name: list(columns) for name, columns in sorted(VIEW_COLUMNS.items())
                  if name not in {"roster", "sections"}},
        "read_mode": "SQLite URI mode=ro with PRAGMA query_only=ON; no immutable=1",
        "sql_example": ("SELECT assignment_id, pseudonym, attempt, submitted_at, payload "
                        "FROM attempt_history WHERE source_key = ? AND course_id = ? "
                        "AND assignment_id = ? ORDER BY pseudonym, attempt, fact_ref LIMIT 50"),
        "privacy": "Pseudonymized and scrubbed, not anonymous. Originals and identity mappings are outside this root.",
    }
    old_bytes = (json.dumps(old_contract, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":")) + "\n").encode("utf-8")
    old.write_bytes(old_bytes)
    if entry_point == "receipt":
        _, result = publish(tmp_path, [
            ScopeReceipt("course.roster", "1", ({"id": "991001", "name": "Avery Sample"},), True),
            ScopeReceipt("assignment.submissions", "10",
                         (submission(user_id="991001"),), True),
        ], publisher=publisher)
        assert result.gaps == ()
        assert result.successful_scopes == (("course.roster", "1"),
                                             ("assignment.submissions", "10"))
    else:
        result = publisher.publish_text_assignment(
            course_title="ELA", assignment={"id": "10", "title": "Draft"},
            roster=[{"id": "991001", "name": "Avery Sample"}],
            submissions=[{"user_id": "991001", "attempt": 1, "body": "Synthetic."}],
            roster_complete=True, submissions_complete=True,
            writer_key="writer-a", run_id="run-a")
        assert result.gaps == ()
        scopes = publisher.store.scan().scopes
        assert scopes[("a" * 64, "1", "course.roster", "1")].membership_complete
        assert scopes[("a" * 64, "1", "assignment.submissions", "10")].membership_complete
    assert old.read_bytes() == old_bytes
    assert sorted(path.name for path in safe_root.glob("reader*.json")) == ["reader.v1.json"]


@pytest.mark.parametrize("entry_point", ["receipt", "text"])
def test_first_acquisition_scrubs_sis_id_and_nickname(tmp_path, entry_point):
    vault = SyntheticVault()
    vault.people.clear()
    vault.sis_ids.clear()
    vault.nicknames.clear()
    publisher = EvidencePublisher(workspace_root=tmp_path, source_key="a" * 64,
                                  course_id="1", vault=vault)
    roster = [{"id": "synthetic-user-01", "name": "Synthetic Learner One",
               "sis_user_id": "sis-private-991", "short_name": "Av-private"}]
    body = "sis-private-991 Av-private"
    if entry_point == "receipt":
        receipt = CourseAcquisitionReceipt("1", "2026-01-04T00:00:00Z",
            "2026-01-04T00:01:00Z", (
                ScopeReceipt("course.roster", "1", tuple(roster), True),
                ScopeReceipt("assignment.submissions", "10",
                    (submission(user_id="synthetic-user-01", body=body),), True),
            ))
        publish_course_receipt(publisher=publisher, receipt=receipt,
                               writer_key="writer-a", run_id="run-a")
    else:
        publisher.publish_text_assignment(
            course_title="ELA", assignment={"id": "10", "title": "Draft"},
            roster=roster, submissions=[{"user_id": "synthetic-user-01", "attempt": 1, "body": body}],
            roster_complete=True, submissions_complete=True,
            writer_key="writer-a", run_id="run-a")
    safe_root = publisher.store.safe_root
    safe_bytes = b"".join(path.read_bytes() for path in safe_root.rglob("*.json"))
    assert b"sis-private-991" not in safe_bytes and b"Av-private" not in safe_bytes
    snapshot = publisher.store.scan()
    submissions = [row for row in snapshot.facts.values() if row["kind"] == "submission"]
    assert len(submissions) == 1
    assert "sis-private-991" not in submissions[0]["payload"]["body"]
    assert "Av-private" not in submissions[0]["payload"]["body"]
    entry = next(row for row in vault.entries() if row["canvas_id"] == "synthetic-user-01")
    assert entry["sis_id"] == "sis-private-991"
    assert "Av-private" in entry["nicknames"]


@pytest.mark.parametrize("entry_point", ["receipt", "text"])
def test_registration_failure_omits_student_scope_and_keeps_prior_safe_facts(
        tmp_path, monkeypatch, entry_point):
    from api import roster_service
    publisher = EvidencePublisher(workspace_root=tmp_path, source_key="a" * 64,
                                  course_id="1", vault=SyntheticVault())
    if entry_point == "receipt":
        publish(tmp_path, [
            ScopeReceipt("course.roster", "1", ({"id": "991001", "name": "Avery Sample"},), True),
            ScopeReceipt("assignment.submissions", "10",
                (submission(user_id="991001", body="Prior safe student evidence."),), True),
        ], publisher=publisher)
        before = publisher.store.scan().scopes
        previous_roster_refs = before[("a" * 64, "1", "course.roster", "1")].current_refs
        previous_submission_refs = before[("a" * 64, "1", "assignment.submissions", "10")].current_refs
        receipt = CourseAcquisitionReceipt("1", "2026-01-04T00:00:00Z",
            "2026-01-04T00:01:00Z", (
                ScopeReceipt("course.context", "1", ({"id": 1, "name": "Current course"},), True),
                ScopeReceipt("course.roster", "1", ({"id": "991001", "name": "Avery Sample"},), True),
                ScopeReceipt("assignment.submissions", "10",
                    (submission(body="Student body must not publish."),), True),
            ))
        def fail_after_one(vault, rows):
            vault.get_or_assign(rows[0]["id"], rows[0]["name"], rows[0].get("sis_user_id", ""))
            raise RuntimeError("registration failure")
        monkeypatch.setattr(roster_service, "upsert_roster", fail_after_one)
        result = publish_course_receipt(publisher=publisher, receipt=receipt,
                                        writer_key="writer-a", run_id="run-b")
        assert "identity_registration_failed" in result.gaps
        after = publisher.store.scan().scopes
        assert after[("a" * 64, "1", "course.roster", "1")].current_refs == previous_roster_refs
        assert after[("a" * 64, "1", "assignment.submissions", "10")].current_refs == previous_submission_refs
    else:
        publisher.publish_text_assignment(course_title="Prior safe course",
            assignment={"id": "10", "title": "Prior"},
            roster=[{"id": "991001", "name": "Avery Sample"}],
            submissions=[{"user_id": "991001", "attempt": 1,
                          "body": "Prior safe student evidence."}],
            roster_complete=True, submissions_complete=True,
            writer_key="writer-a", run_id="run-a")
        before = publisher.store.scan().scopes
        previous_roster_refs = before[("a" * 64, "1", "course.roster", "1")].current_refs
        previous_submission_refs = before[("a" * 64, "1", "assignment.submissions", "10")].current_refs
        def fail_after_one(vault, rows):
            vault.get_or_assign(rows[0]["id"], rows[0]["name"], rows[0].get("sis_user_id", ""))
            raise RuntimeError("registration failure")
        monkeypatch.setattr(roster_service, "upsert_roster", fail_after_one)
        result = publisher.publish_text_assignment(course_title="Current course",
            assignment={"id": "10", "title": "Current"},
            roster=[{"id": "991001", "name": "Avery Sample"}],
            submissions=[{"user_id": "991001", "attempt": 1,
                          "body": "Student body must not publish."}],
            roster_complete=True, submissions_complete=True,
            writer_key="writer-a", run_id="run-b")
        assert "identity_registration_failed" in result.gaps
        after = publisher.store.scan().scopes
        assert after[("a" * 64, "1", "course.roster", "1")].current_refs == previous_roster_refs
        assert after[("a" * 64, "1", "assignment.submissions", "10")].current_refs == previous_submission_refs
    all_bytes = b"".join(path.read_bytes() for path in publisher.store.safe_root.rglob("*.json"))
    assert b"Student body must not publish." not in all_bytes
    assert any(b"Prior safe student evidence." in path.read_bytes()
               for path in publisher.store.safe_root.rglob("*.json"))


def test_attachment_status_republishes_gap_without_digest(tmp_path):
    from types import SimpleNamespace
    publisher = EvidencePublisher(workspace_root=tmp_path, source_key="a" * 64,
                                  course_id="1", vault=SyntheticVault())
    job = SimpleNamespace(assignment_id="10", pseudonym="Pikachu", attempt=1,
                          attachment_key="f" * 64, media_type="application/pdf", size=123)
    with pytest.raises(PublicationRefused, match="invalid_status"):
        publish_attachment_status(publisher=publisher, job=job, status="pending",
                                  writer_key="writer-a", run_id="run-a")
    publish_attachment_status(publisher=publisher, job=job, status="unavailable",
                             writer_key="writer-a", run_id="run-a")
    fact = next(f for f in publisher.store.scan().facts.values() if f["kind"] == "attachment")
    assert fact["payload"]["status"] == "unavailable"
    assert fact["payload"]["original_digest"] is None
