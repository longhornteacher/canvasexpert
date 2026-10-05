"""Private acquisition receipts become scrubbed, durable text evidence."""

from api.mirror.evidence_index import EvidenceIndex
from api.mirror.evidence_paths import local_source_root
from api.mirror.evidence_publish import EvidencePublisher
import pytest


from api.tests.mirror.acquisition_samples import SyntheticVault


def _publisher(tmp_path):
    root = tmp_path / "teacher-workspace"
    root.mkdir()
    return EvidencePublisher(workspace_root=root, source_key="a" * 64,
                             course_id="1", vault=SyntheticVault()), root


def test_complete_text_receipt_publishes_two_students_and_preserves_attempts(tmp_path):
    publisher, root = _publisher(tmp_path)
    result = publisher.publish_text_assignment(
        course_title="ELA", assignment={
            "id": "10", "title": "Draft study", "due_at": "2026-01-01T18:00:00-06:00",
            "rubric": [{"id": "7", "description": "Clear claim", "points": 4,
                        "ratings": [{"id": "8", "description": "Strong", "points": 4}]}],
        },
        roster=[{"id": "991001", "name": "Avery Sample"},
                {"id": "991002", "name": "Morgan Sample"}],
        submissions=[
            {"user_id": "991001", "attempt": 2,
             "submitted_at": "2026-01-02T00:00:00Z",
             "body": "Avery revised! https://canvas.example.test/files/8?token=secret",
             "submission_history": [
                 {"attempt": 1, "submitted_at": "2026-01-01T00:00:00Z",
                  "body": "First, draft."},
             ]},
            {"user_id": "991002", "attempt": 1,
             "submitted_at": "2026-01-03T00:00:00Z", "body": "Second draft."},
        ], roster_complete=True, submissions_complete=True,
        writer_key="writer-a", run_id="run-a",
        acquired_at="2026-01-04T00:00:00Z",
    )
    assert result.gaps == ()
    snapshot = publisher.store.scan()
    scope = snapshot.scopes[("a" * 64, "1", "assignment.submissions", "10")]
    assert scope.status == "ready"
    assert scope.membership_complete is True
    assert len(scope.member_keys) == 2
    assert len([f for f in snapshot.facts.values() if f["kind"] == "attempt_observation"]) == 3
    assert scope.established_submitted_at["attempt:10:Pikachu:1"] == "2026-01-01T00:00:00Z"
    context = next(f["payload"] for f in snapshot.facts.values() if f["kind"] == "assignment")
    assert context["due_at"] == "2026-01-02T00:00:00Z"
    assert context["rubric"][0]["ratings"][0]["description"] == "Strong"

    safe_root = root / "CanvasMirror"
    assert not (safe_root / "reader.v1.json").exists()
    safe_bytes = b"".join(path.read_bytes() for path in safe_root.rglob("*.json"))
    for forbidden in (b"Avery", b"Morgan", b"991001", b"991002", b"token=secret"):
        assert forbidden not in safe_bytes
    index_path = local_source_root("a" * 64, root) / "query.sqlite3"
    index = EvidenceIndex(index_path)
    index.ingest(snapshot, selected_courses=["1"])
    page = index.query_page("attempt_history", source_key="a" * 64,
                            course_id="1", assignment_id="10", limit=10)
    assert len(page["records"]) == 3
    db_bytes = index_path.read_bytes()
    assert b"Avery" not in db_bytes and b"991001" not in db_bytes


def test_unregistrable_roster_withholds_student_scopes_but_keeps_course_context(tmp_path):
    publisher, _ = _publisher(tmp_path)
    result = publisher.publish_text_assignment(
        course_title="ELA", assignment={"id": "10", "title": "Draft study"},
        roster=[{"id": "991001", "name": "Avery Sample"},
                {"id": "991999", "name": "Unknown Synthetic"}],
        submissions=[{"user_id": "991001", "attempt": 1,
                      "submitted_at": "2026-01-01T00:00:00Z", "body": "Ready."}],
        roster_complete=True, submissions_complete=True,
        writer_key="writer-a", run_id="run-a",
        acquired_at="2026-01-04T00:00:00Z",
    )
    assert "identity_registration_failed" in result.gaps
    snapshot = publisher.store.scan()
    assert ("a" * 64, "1", "assignment.submissions", "10") not in snapshot.scopes
    assert any(f["kind"] == "course" for f in snapshot.facts.values())


def test_receipt_completeness_is_scoped_separately(tmp_path):
    publisher, _ = _publisher(tmp_path)
    publisher.publish_text_assignment(
        course_title="ELA", assignment={"id": "10", "title": "Draft"},
        roster=[{"id": "991001", "name": "Avery Sample"}],
        submissions=[{"user_id": "991001", "attempt": 1,
                      "submitted_at": "2026-01-01T00:00:00Z", "body": "Ready."}],
        roster_complete=False, submissions_complete=True,
        writer_key="writer-a", run_id="run-a",
        acquired_at="2026-01-02T00:00:00Z",
    )
    scopes = publisher.store.scan().scopes
    assert scopes[("a" * 64, "1", "course.roster", "1")].membership_complete is False
    assert scopes[("a" * 64, "1", "assignment.submissions", "10")].membership_complete is True


def test_verifier_rejects_identity_in_any_allowed_string_before_safe_write(tmp_path):
    publisher, root = _publisher(tmp_path)
    record = {
        "schema_version": 1, "kind": "submission", "source_key": "a" * 64,
        "course_id": "1", "entity_key": "submission:10:Pikachu",
        "payload": {"assignment_id": "10", "pseudonym": "Pikachu",
                    "attempt": 1, "grade": "991001", "body": "Ready"},
    }
    with pytest.raises(ValueError, match="privacy_refused"):
        publisher.store.publish_fact(record)
    assert not (root / "CanvasMirror").exists()


def test_html_attribute_secrets_are_removed_without_flattening_code(tmp_path):
    publisher, root = _publisher(tmp_path)
    publisher.publish_text_assignment(
        course_title="CS", assignment={"id": "10", "title": "Draft"},
        roster=[{"id": "991001", "name": "Avery Sample"}],
        submissions=[{"user_id": "991001", "attempt": 1,
                      "submission_type": "online_text_entry",
                      "submitted_at": "2026-01-01T00:00:00Z",
                      "body": '<p>if (a &lt; b) {\treturn “yes”; }</p>'
                              '<a href="https://canvas.example.test/files/1?token=secret">source</a>'
                              ' https://user:password@canvas.example.test/path'
                              ' C:/Users/user/private.txt'}],
        roster_complete=True, submissions_complete=True,
        writer_key="writer-a", run_id="run-a",
        acquired_at="2026-01-02T00:00:00Z",
    )
    facts = publisher.store.scan().facts.values()
    body = next(f["payload"]["body"] for f in facts if f["kind"] == "submission")
    assert 'if (a < b) {\treturn “yes”; }' in body
    assert "source" in body
    assert "token=secret" not in body and "password" not in body
    assert "C:/Users/user" not in body
    safe_bytes = b"".join(path.read_bytes() for path in (root / "CanvasMirror").rglob("*.json"))
    for forbidden in (b"token=secret", b"password", b"C:/Users/user"):
        assert forbidden not in safe_bytes


def test_plain_code_angle_brackets_are_not_interpreted_as_html(tmp_path):
    publisher, _ = _publisher(tmp_path)
    publisher.publish_text_assignment(
        course_title="CS", assignment={"id": "10", "title": "Code"},
        roster=[{"id": "991001", "name": "Avery Sample"}],
        submissions=[{"user_id": "991001", "attempt": 1,
                      "submitted_at": "2026-01-01T00:00:00Z",
                      "body": "List<string> names;\n\treturn names;"}],
        roster_complete=True, submissions_complete=True,
        writer_key="writer-a", run_id="run-a",
        acquired_at="2026-01-02T00:00:00Z",
    )
    body = next(f["payload"]["body"] for f in publisher.store.scan().facts.values()
                if f["kind"] == "submission")
    assert body == "List<string> names;\n\treturn names;"


def test_navigation_identifier_cannot_carry_known_real_name(tmp_path):
    publisher, root = _publisher(tmp_path)
    record = {
        "schema_version": 1, "kind": "override", "source_key": "a" * 64,
        "course_id": "1", "entity_key": "override:10:Avery",
        "payload": {"assignment_id": "10", "override_id": "7",
                    "section_id": "Avery"},
    }
    with pytest.raises(ValueError, match="privacy_refused"):
        publisher.store.publish_fact(record)
    assert not (root / "CanvasMirror").exists()


@pytest.mark.parametrize("field", ["module_id", "page_id", "assignment_group_id",
                                   "item_id", "content_id"])
def test_structure_navigation_identifiers_are_ascii_decimal(tmp_path, field):
    publisher, _ = _publisher(tmp_path)
    record = {"schema_version": 1, "kind": "module", "source_key": "a" * 64,
              "course_id": "1", "entity_key": "module:10",
              "payload": {field: "١٠"}}
    with pytest.raises(ValueError, match="invalid_navigation_id"):
        publisher.verify_safe(record)
