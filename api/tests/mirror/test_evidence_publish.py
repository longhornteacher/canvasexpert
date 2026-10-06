"""Private acquisition receipts become scrubbed, durable text evidence."""

from api.mirror.evidence_index import EvidenceIndex
from api.mirror.evidence_paths import local_source_root
from api.mirror.evidence_publish import EvidencePublisher, IdentitySnapshot
from api.mirror.evidence_acquisition import (
    CourseAcquisitionReceipt, ScopeReceipt, publish_course_receipt,
)
import re

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


def test_section_name_is_scrubbed_before_complete_publication_and_index(tmp_path):
    publisher, root = _publisher(tmp_path)
    label = "Avery Sample's Seminar"
    receipt = CourseAcquisitionReceipt("1", "2026-01-04T00:00:00Z",
        "2026-01-04T00:01:00Z", (ScopeReceipt("course.sections", "1",
            ({"id": "800001", "name": label},), True),))

    result = publish_course_receipt(publisher=publisher, receipt=receipt,
                                    writer_key="writer-a", run_id="run-a")

    assert result.gaps == ()
    assert result.successful_scopes == (("course.sections", "1"),)
    snapshot = publisher.store.scan()
    scope = snapshot.scopes[("a" * 64, "1", "course.sections", "1")]
    assert scope.membership_complete is True
    section = next(f for f in snapshot.facts.values() if f["kind"] == "section")
    assert section["payload"]["name"] == "Pikachu's Seminar"
    safe_bytes = b"".join(path.read_bytes() for path in (root / "CanvasMirror").rglob("*.json"))
    for forbidden in (b"Avery", b"Sample", b"991001"):
        assert forbidden not in safe_bytes

    index_path = local_source_root("a" * 64, root) / "query.sqlite3"
    index = EvidenceIndex(index_path)
    index.ingest(snapshot, selected_courses=["1"])
    page = index.query_page("sections", source_key="a" * 64,
                             course_id="1", limit=10)
    assert page["records"][0]["name"] == "Pikachu's Seminar"
    index_bytes = index_path.read_bytes()
    for forbidden in (b"Avery", b"Sample", b"991001"):
        assert forbidden not in index_bytes


def test_section_name_that_scrubber_cannot_map_still_fails_closed(tmp_path):
    root = tmp_path / "teacher-workspace"
    root.mkdir()
    vault = SyntheticVault()
    vault.people["991001"] = ("", "Avery Sample")
    publisher = EvidencePublisher(workspace_root=root, source_key="a" * 64,
                                  course_id="1", vault=vault)
    receipt = CourseAcquisitionReceipt("1", "2026-01-04T00:00:00Z",
        "2026-01-04T00:01:00Z", (ScopeReceipt("course.sections", "1",
            ({"id": "800001", "name": "Avery's Seminar"},), True),))

    result = publish_course_receipt(publisher=publisher, receipt=receipt,
                                    writer_key="writer-a", run_id="run-a")

    assert result.successful_scopes == ()
    assert "privacy_refused" in result.gaps
    assert not any(f["kind"] == "section" for f in publisher.store.scan().facts.values())
    safe_bytes = b"".join(path.read_bytes() for path in (root / "CanvasMirror").rglob("*.json"))
    assert b"Avery" not in safe_bytes and b"Sample" not in safe_bytes


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


# --- Verification key: one exact digest of every input verify_safe depends on ----------

class _KeyVault(SyntheticVault):
    """Synthetic vault whose pseudonyms can be provisional (not yet stable)."""

    frozen_verification = False

    def __init__(self):
        super().__init__()
        self.provisional = set()

    def entries(self):
        rows = super().entries()
        for row in rows:
            row["provisional"] = row["canvas_id"] in self.provisional
        return rows


def _key_publisher(tmp_path, vault, *, source_key="a" * 64, course_id="1"):
    return EvidencePublisher(workspace_root=tmp_path, source_key=source_key,
                             course_id=course_id, vault=vault)


def _add_name_token(vault):
    vault.add_nicknames("991001", ["Zedmund"])


def _add_identifier(vault):
    vault.sis_ids["991001"] = "S-424242"


def _add_short_identifier(vault):
    vault.sis_ids["991001"] = "ab"  # len < 3 is ignored by verify_safe


def _add_duplicate_token(vault):
    vault.add_nicknames("991001", ["Avery", "Sample"])  # already-known tokens


def _make_provisional(vault):
    vault.provisional.add("991001")


@pytest.mark.parametrize("mutate, changes", [
    (_add_name_token, True),
    (_add_identifier, True),
    (_make_provisional, True),
    (_add_short_identifier, False),
    (_add_duplicate_token, False),
], ids=["name-token", "identifier", "pseudonym-unstable", "short-identifier",
        "duplicate-token"])
def test_verification_key_changes_exactly_with_verifier_inputs(tmp_path, mutate, changes):
    vault = _KeyVault()
    before = _key_publisher(tmp_path, vault).verification_key()
    assert _key_publisher(tmp_path, vault).verification_key() == before
    mutate(vault)
    assert (_key_publisher(tmp_path, vault).verification_key() != before) is changes


def test_provisional_pseudonym_becoming_stable_changes_the_key(tmp_path):
    vault = _KeyVault()
    vault.provisional.add("991001")
    provisional = _key_publisher(tmp_path, vault).verification_key()
    vault.provisional.clear()
    assert _key_publisher(tmp_path, vault).verification_key() != provisional


@pytest.mark.parametrize("kwargs", [{"source_key": "b" * 64}, {"course_id": "2"}],
                         ids=["source_key", "course_id"])
def test_verification_key_covers_scope(tmp_path, kwargs):
    vault = _KeyVault()
    assert (_key_publisher(tmp_path, vault).verification_key()
            != _key_publisher(tmp_path, vault, **kwargs).verification_key())


@pytest.mark.parametrize("inputs, refusal", [
    ((frozenset({"Pikachu"}), frozenset({"zorblax"}), ()), "privacy_refused"),
    ((frozenset({"Pikachu"}), frozenset(), ("QX-7788",)), "privacy_refused"),
    ((frozenset(), frozenset(), ()), "identity_refused"),
], ids=["token", "identifier", "no-stable-pseudonym"])
def test_matcher_and_key_share_one_set_of_inputs(tmp_path, monkeypatch, inputs, refusal):
    publisher = _key_publisher(tmp_path, _KeyVault())
    record = {
        "schema_version": 1, "kind": "submission", "source_key": "a" * 64,
        "course_id": "1", "entity_key": "submission:10:Pikachu",
        "payload": {"assignment_id": "10", "pseudonym": "Pikachu", "attempt": 1,
                    "body": "zorblax QX-7788 wrote this"},
    }
    publisher.verify_safe(record)  # nothing in the vault matches yet
    baseline_key = publisher.verification_key()
    monkeypatch.setattr(publisher, "_privacy_inputs", lambda: inputs)
    assert publisher.verification_key() != baseline_key
    with pytest.raises(ValueError, match=refusal):
        publisher.verify_safe(record)


def test_frozen_vault_key_is_computed_once_and_live_vault_is_not_cached(tmp_path):
    frozen = _KeyVault()
    frozen.frozen_verification = True
    calls = []
    real = frozen.all_real_identifiers
    frozen.all_real_identifiers = lambda: (calls.append(1), real())[1]
    publisher = _key_publisher(tmp_path, frozen)
    first = publisher.verification_key()
    publisher._privacy_context()
    _add_name_token(frozen)  # a frozen vault is by contract immutable; never re-read
    assert publisher.verification_key() == first
    assert len(calls) == 1

    live = _KeyVault()
    publisher = _key_publisher(tmp_path, live)
    before = publisher.verification_key()
    _add_name_token(live)
    assert publisher.verification_key() != before


def test_identity_snapshot_key_matches_live_vault_key(tmp_path):
    vault = _KeyVault()
    vault.provisional.add("991002")
    live = _key_publisher(tmp_path, vault).verification_key()
    snapshot = IdentitySnapshot.freeze(vault, source_key="a" * 64, course_id="1", pseudonyms={})
    publisher = _key_publisher(tmp_path, snapshot)
    assert publisher.verification_key() == live
    assert publisher.verification_key() == live  # cached for the frozen snapshot


def test_publisher_wires_verification_key_into_its_store(tmp_path):
    publisher = _key_publisher(tmp_path, _KeyVault())
    assert callable(publisher.store.verification_key)
    assert publisher.store.verification_key() == publisher.verification_key()


def test_verification_key_is_an_opaque_digest_without_identity_values(tmp_path):
    vault = _KeyVault()
    vault.sis_ids["991001"] = "S-424242"
    key = _key_publisher(tmp_path, vault).verification_key()
    assert re.fullmatch(r"[0-9a-f]{64}", key)
    names, ids = vault.all_real_identifiers()
    forbidden = {token.lower() for name in names for token in name.split()} | set(ids)
    assert not any(value in key for value in forbidden if len(value) >= 4)
