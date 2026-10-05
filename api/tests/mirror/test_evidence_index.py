"""Read-index contract and transaction laws over synthetic safe evidence."""
import sqlite3
from pathlib import Path

import pytest

from api.mirror.evidence_index import EvidenceIndex, IndexBusy, IndexReadError, VIEW_COLUMNS


@pytest.mark.parametrize("kind", ["missing", "metadata_absent", "no_metadata_row", "mismatched", "corrupt"])
def test_read_refuses_missing_mismatched_and_corrupt_index_without_writing(tmp_path, kind):
    path = tmp_path / "query.sqlite3"
    if kind == "mismatched":
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE index_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            db.execute("INSERT INTO index_metadata VALUES ('schema_version','1')")
    elif kind == "no_metadata_row":
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE index_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    elif kind == "metadata_absent":
        with sqlite3.connect(path):
            pass
    elif kind == "corrupt":
        path.write_bytes(b"not a sqlite database")
    original = path.read_bytes() if path.exists() else None
    with pytest.raises(IndexReadError) as error:
        with EvidenceIndex(path).read_connection():
            pass
    assert error.value.code == {"missing": "index_missing", "metadata_absent": "index_schema_mismatch",
        "no_metadata_row": "index_schema_mismatch", "mismatched": "index_schema_mismatch",
        "corrupt": "index_corrupt"}[kind]
    assert (path.read_bytes() if path.exists() else None) == original
    assert path.exists() is (kind != "missing")


def test_mismatch_ingest_refuses_and_discard_with_open_handle_is_busy(tmp_path, evidence_factory, monkeypatch):
    path = tmp_path / "query.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE index_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("INSERT INTO index_metadata VALUES ('schema_version','1')")
    before = path.read_bytes()
    index = EvidenceIndex(path)
    with pytest.raises(IndexReadError, match="index_schema_mismatch"):
        index.ingest(evidence_factory["store"](tmp_path / "safe").scan())
    assert path.read_bytes() == before

    path.write_bytes(b"corrupt")
    with pytest.raises(IndexReadError, match="index_corrupt"):
        with index.read_connection():
            pass
    wal, shm = Path(str(path) + "-wal"), Path(str(path) + "-shm")
    wal.write_bytes(b"wal")
    shm.write_bytes(b"shm")
    before_sidecars = wal.read_bytes(), shm.read_bytes()
    unlink = Path.unlink
    def denied(target, *args, **kwargs):
        if target == path:
            raise PermissionError("synthetic open-handle lock")
        return unlink(target, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(IndexBusy) as error:
        index.discard()
    assert error.value.code == "index_busy"
    assert path.read_bytes() == b"corrupt"
    assert (wal.read_bytes(), shm.read_bytes()) == before_sidecars


def test_discard_preserves_a_healthy_supported_index(tmp_path, evidence_factory):
    path = tmp_path / "query.sqlite3"
    index = EvidenceIndex(path)
    index.ingest(evidence_factory["store"](tmp_path / "safe").scan())
    before = path.read_bytes()
    db = sqlite3.connect(path)
    try:
        assert db.execute("SELECT value FROM index_metadata WHERE key='schema_version'").fetchone()[0] == "2"
    finally:
        db.close()
    with pytest.raises(IndexReadError, match="index_healthy"):
        index.discard()
    assert path.read_bytes() == before


def test_interrupted_ingest_rolls_back(tmp_path, evidence_factory, monkeypatch):
    store = evidence_factory["store"](tmp_path / "safe")
    index = EvidenceIndex(tmp_path / "query.sqlite3")
    original_revision = index.ingest(store.scan())
    changed = evidence_factory["fact"](body="new indexed value")
    ref = store.publish_fact(changed)
    store.publish_commit(evidence_factory["commit"](refs=[ref], members=[changed["entity_key"]], run_id="new"))
    snapshot = store.scan()
    def interrupted(*args, **kwargs):
        raise RuntimeError("interrupted")
    monkeypatch.setattr(EvidenceIndex, "_project_derived", staticmethod(interrupted))
    with pytest.raises(RuntimeError, match="interrupted"):
        index.ingest(snapshot)
    with index.read_connection() as db:
        assert db.execute("SELECT value FROM index_metadata WHERE key='revision'").fetchone()[0] == original_revision


def test_named_views_match_registry_and_no_private_control_tables(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path / "safe")
    index = EvidenceIndex(tmp_path / "query.sqlite3")
    index.ingest(store.scan())
    with index.read_connection() as db:
        for view, columns in VIEW_COLUMNS.items():
            assert tuple(row[1] for row in db.execute(f"PRAGMA table_info({view})")) == columns
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"index_metadata", "safe_facts", "current_refs", "history_refs", "scope_coverage", "course_selection", "attachment_block_rows", "comparison_rows"}
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


def test_multi_course_ingest_keeps_one_complete_source_projection(tmp_path, evidence_factory):
    from api.mirror.evidence_store import EvidenceStore
    first = evidence_factory["store"](tmp_path / "safe")
    second = EvidenceStore(tmp_path / "safe", evidence_factory["source"], "2",
        verify_safe=lambda record: None,
        private_diagnostics_root=tmp_path / "diagnostics")
    for store, course_id, title in ((first, "1", "First"), (second, "2", "Second")):
        entity = f"course:{course_id}"
        fact = {"schema_version": 1, "kind": "course", "source_key": evidence_factory["source"],
                "course_id": course_id, "entity_key": entity, "payload": {"title": title}}
        ref = store.publish_fact(fact)
        commit = evidence_factory["commit"](
            refs=[ref], members=[entity], scope="course.context", scope_id=course_id)
        commit["course_id"] = course_id
        store.publish_commit(commit)
    snapshots = [first.scan(), second.scan()]
    index = EvidenceIndex(tmp_path / "query.sqlite3")
    revision = index.ingest_many(snapshots, selected_courses=["1"])
    assert {row["course_id"] for row in index.query_page("courses")["records"]} == {"1", "2"}
    assert {row["course_id"] for row in index.query_page("courses")["records"]
            if row["selection_status"] == "selected"} == {"1"}
    rebuilt = EvidenceIndex(tmp_path / "rebuilt.sqlite3")
    assert rebuilt.ingest_many(snapshots, selected_courses=["1"]) == revision


def test_attachment_blocks_are_indexed_per_block_with_locators(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path / "safe")
    entity = "extraction:10:Pikachu:1:bbbbbbbbbbbbbbbb"
    fact = evidence_factory["fact"]("attachment_extraction", entity, {
        "assignment_id": "10", "pseudonym": "Pikachu", "attempt": 1,
        "attachment_key": "b" * 64, "original_digest": "c" * 64,
        "availability": "complete", "method": "native", "blocks": [
            {"block_id": "b1", "kind": "paragraph", "text": "Opening text", "locator": {"page": 1}},
            {"block_id": "b2", "kind": "paragraph", "text": "Closing text", "locator": {"page": 2}},
        ]})
    ref = store.publish_fact(fact)
    store.publish_commit(evidence_factory["commit"](refs=[ref], members=[entity],
        scope="assignment.extractions", scope_id="10"))
    index = EvidenceIndex(tmp_path / "query.sqlite3")
    index.ingest(store.scan())
    blocks = index.query_page("attachment_blocks", course_id="1", assignment_id="10")["records"]
    assert len(blocks) == 2
    assert len({block["fact_ref"] for block in blocks}) == 2
    import json
    assert {json.loads(block["payload"])["locator"]["page"] for block in blocks} == {1, 2}


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


@pytest.mark.parametrize("kind,payload", [
    ("group", {"group_id": "20", "title": "Workshop", "student_pseudonyms": ["Pikachu"]}),
    ("module", {"module_id": "30", "title": "Unit", "position": 1, "items": []}),
    ("page", {"page_id": "40", "title": "Directions", "body": "Read carefully"}),
    ("assignment_group", {"assignment_group_id": "50", "title": "Writing", "position": 1, "group_weight": 30}),
])
def test_structure_views_rebuild_and_tombstone_current_only(tmp_path, evidence_factory, kind, payload):
    scope = {"group": "course.groups", "module": "course.modules", "page": "course.pages", "assignment_group": "course.assignment_groups"}[kind]
    entity = f"{kind}:10"
    store = evidence_factory["store"](tmp_path / "safe")
    ref = store.publish_fact(evidence_factory["fact"](kind, entity, payload))
    parent = store.publish_commit(evidence_factory["commit"](scope=scope, scope_id="course", refs=[ref], members=[entity]))
    index = EvidenceIndex(tmp_path / "query.sqlite3")
    revision = index.ingest(store.scan())
    assert index.ingest(store.scan()) == revision
    result = index.query_page(f"{kind}_context", course_id="1")
    assert result["records"][0]["fact_ref"] == ref
    rebuilt = EvidenceIndex(tmp_path / "rebuilt.sqlite3")
    rebuilt.ingest(store.scan())
    assert rebuilt.query_page(f"{kind}_context") == result
    store.publish_commit(evidence_factory["commit"](scope=scope, scope_id="course", parents=[parent]))
    index.ingest(store.scan())
    assert index.query_page(f"{kind}_context")["records"] == []
    with index.read_connection() as db:
        assert db.execute("SELECT COUNT(*) FROM safe_facts WHERE fact_ref=?", (ref,)).fetchone()[0] == 1
