"""Read-index contract and transaction laws over synthetic safe evidence."""
import sqlite3

import pytest

from api.mirror.evidence_index import EvidenceIndex, IndexReadError, VIEW_COLUMNS


def test_named_views_match_registry_and_no_private_control_tables(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path / "safe")
    index = EvidenceIndex(tmp_path / "query.sqlite3")
    index.ingest(store.scan())
    with index.read_connection() as db:
        for view, columns in VIEW_COLUMNS.items():
            assert tuple(row[1] for row in db.execute(f"PRAGMA table_info({view})")) == columns
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"index_metadata", "safe_facts", "current_refs", "history_refs", "scope_coverage", "course_selection"}
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM safe_facts")


def test_rebuild_and_explicit_retention_converge(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path / "safe")
    fact = evidence_factory["fact"]("assignment", "assignment:42", {"assignment_id": "42", "title": "Synthetic task"})
    ref = store.publish_fact(fact)
    store.publish_commit(evidence_factory["commit"](refs=[ref], members=["assignment:42"], scope="course.assignments", scope_id="course"))
    snapshot = store.scan()
    first = EvidenceIndex(tmp_path / "a.sqlite3")
    second = EvidenceIndex(tmp_path / "b.sqlite3")
    assert first.ingest(snapshot) == second.ingest(snapshot)
    assert first.query_page("assignment_context") == second.query_page("assignment_context")
    assert first.query_page("courses")["records"][0]["selection_status"] == "retained"
    first.ingest(snapshot, selected_courses=[fact["course_id"]])
    assert first.query_page("courses")["records"][0]["selection_status"] == "selected"


def test_read_transaction_pins_revision_across_ingest(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path / "safe")
    index = EvidenceIndex(tmp_path / "query.sqlite3")
    initial = index.ingest(store.scan())
    with index.read_connection() as reader:
        assert index.query_page("courses", connection=reader)["revision"] == initial
        changed = index.ingest(store.scan(), selected_courses=["42"])
        assert changed != initial
        assert index.query_page("courses", connection=reader, revision=initial)["revision"] == initial
    with pytest.raises(IndexReadError, match="revision_changed"):
        index.query_page("courses", revision=initial)


@pytest.mark.parametrize("options", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"assignment_id": "42"}])
def test_queries_refuse_unbounded_or_inapplicable_options(tmp_path, options):
    with pytest.raises(IndexReadError):
        EvidenceIndex(tmp_path / "absent.sqlite3").query_page("courses", **options)



def test_unsafe_snapshot_is_refused_before_sqlite_bytes(tmp_path, evidence_factory):
    from api.mirror.evidence_schema import digest_record
    store = evidence_factory["store"](tmp_path / "safe")
    snapshot = store.scan()
    unsafe = evidence_factory["fact"](payload={"student_id": "synthetic-private-id"})
    snapshot.facts[digest_record(unsafe)] = unsafe
    path = tmp_path / "query.sqlite3"
    with pytest.raises(ValueError, match="unexpected_fields"):
        EvidenceIndex(path).ingest(snapshot)
    assert not path.exists()


def test_current_membership_tombstone_keeps_attempt_history(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path / "safe")
    fact = evidence_factory["fact"]()
    ref = store.publish_fact(fact)
    observation = evidence_factory["fact"]("attempt_observation", "attempt:10:Pikachu:1")
    observation_ref = store.publish_fact(observation)
    head = store.publish_commit(evidence_factory["commit"](refs=[ref, observation_ref], members=[fact["entity_key"]]))
    index = EvidenceIndex(tmp_path / "query.sqlite3")
    index.ingest(store.scan())
    assert len(index.query_page("current_submissions")["records"]) == 1
    history = index.query_page("attempt_history")["records"]
    assert len(history) == 1
    assert history[0]["fact_ref"] == observation_ref
    store.publish_commit(evidence_factory["commit"](parents=[head]))
    index.ingest(store.scan())
    assert index.query_page("current_submissions")["records"] == []
    assert index.query_page("attempt_history")["records"] == history


def test_bounded_pages_keep_deterministic_provenance(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path / "safe")
    refs, members = [], []
    for name in ("Pikachu", "Eevee"):
        key = f"submission:10:{name}"
        refs.append(store.publish_fact(evidence_factory["fact"](entity_key=key, pseudonym=name)))
        members.append(key)
    store.publish_commit(evidence_factory["commit"](refs=refs, members=members))
    index = EvidenceIndex(tmp_path / "query.sqlite3")
    index.ingest(store.scan())
    first = index.query_page("current_submissions", limit=1)
    second = index.query_page("current_submissions", limit=1, offset=first["next_offset"], revision=first["revision"])
    assert second["next_offset"] is None
    assert first["records"][0]["pseudonym"] == "Eevee"
    assert second["records"][0]["pseudonym"] == "Pikachu"
    assert {first["records"][0]["fact_ref"], second["records"][0]["fact_ref"]} == set(refs)



@pytest.mark.parametrize("metadata", [
    {"code": "synthetic-private-name"},
    {"code": "invalid_commit", "scope": "assignment.submissions", "scope_id": "private/path", "source_key": "a" * 64, "course_id": "1"},
    {"code": "invalid_commit", "digest": "private-path"},
])
def test_forged_issue_metadata_is_refused_before_index_creation(tmp_path, evidence_factory, metadata):
    from dataclasses import replace
    from api.mirror.evidence_store import StoreIssue
    snapshot = evidence_factory["store"](tmp_path / "safe").scan()
    snapshot = replace(snapshot, issues=(StoreIssue(**metadata),))
    path = tmp_path / "query.sqlite3"
    with pytest.raises(ValueError):
        EvidenceIndex(path).ingest(snapshot)
    assert not path.exists()
