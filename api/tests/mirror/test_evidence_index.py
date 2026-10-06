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
    from dataclasses import replace
    store = evidence_factory["store"](tmp_path / "safe")
    snapshot = replace(store.scan())  # a sealed scan is trusted; unsealed input is fully checked
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


# --- Validated-scan seal: skip duplicate per-record checks, never for unsealed input ---


def _counting_verifier():
    calls = {"n": 0}

    def verify(record):
        calls["n"] += 1
    return verify, calls


def _populate(store, factory):
    """Two assignments (two submission scopes), history, an extraction and course context."""
    def publish(facts, **commit):
        refs = [store.publish_fact(fact) for fact in facts]
        members = [f["entity_key"] for f in facts if f["kind"] != "attempt_observation"]
        store.publish_commit(factory["commit"](refs=refs, members=members, **commit))
    publish([factory["fact"]("assignment", "assignment:10", {"assignment_id": "10", "title": "Task A"}),
             factory["fact"]("assignment", "assignment:11", {"assignment_id": "11", "title": "Task B"})],
            scope="course.assignments", scope_id="course")
    publish([factory["fact"](), factory["fact"](entity_key="submission:10:Eevee", pseudonym="Eevee"),
             factory["fact"]("attempt_observation", "attempt:10:Pikachu:1")],
            scope="assignment.submissions", scope_id="10")
    publish([factory["fact"](entity_key="submission:11:Pikachu", assignment_id="11")],
            scope="assignment.submissions", scope_id="11")
    publish([factory["fact"]("attachment_extraction", "extraction:10:Pikachu:1:bbbbbbbbbbbbbbbb", {
        "assignment_id": "10", "pseudonym": "Pikachu", "attempt": 1, "attachment_key": "b" * 64,
        "original_digest": "c" * 64, "availability": "complete", "method": "native",
        "blocks": [{"block_id": "b1", "kind": "paragraph", "text": "Opening", "locator": {"page": 1}}]})],
            scope="assignment.extractions", scope_id="10")


def _dump(index):
    with index.read_connection() as db:
        return {view: sorted((tuple(row) for row in db.execute(f"SELECT {','.join(columns)} FROM {view}")), key=repr)
                for view, columns in VIEW_COLUMNS.items()}


def _counted_store(factory, root):
    verify, calls = _counting_verifier()
    store = factory["store"](root, verify_safe=verify)
    _populate(store, factory)
    return store, calls


def test_sealed_and_unsealed_ingest_agree_on_revision_and_every_view(tmp_path, evidence_factory):
    from dataclasses import replace
    from api.mirror.evidence_store import is_validated_scan
    store, _ = _counted_store(evidence_factory, tmp_path / "safe")
    sealed = store.scan()
    unsealed = replace(sealed)
    assert is_validated_scan(sealed) and not is_validated_scan(unsealed)
    fast, full = EvidenceIndex(tmp_path / "fast.sqlite3"), EvidenceIndex(tmp_path / "full.sqlite3")
    assert fast.ingest(sealed, selected_courses=["1"]) == full.ingest(unsealed, selected_courses=["1"])
    dumped = _dump(fast)
    assert dumped == _dump(full)
    for view in ("courses", "assignment_context", "current_submissions", "attempt_history", "attachment_blocks", "scope_status"):
        assert dumped[view], view


def test_sealed_ingest_never_mutates_the_snapshot_records(tmp_path, evidence_factory):
    from copy import deepcopy
    store, _ = _counted_store(evidence_factory, tmp_path / "safe")
    snapshot = store.scan()
    before = deepcopy((snapshot.facts, snapshot.commits))
    EvidenceIndex(tmp_path / "query.sqlite3").ingest(snapshot)
    assert (snapshot.facts, snapshot.commits) == before


def test_sealed_ingest_runs_no_per_record_validation(tmp_path, evidence_factory, monkeypatch):
    from dataclasses import replace
    import api.mirror.evidence_index as module
    store, calls = _counted_store(evidence_factory, tmp_path / "safe")
    snapshot = store.scan()
    seen = {"validate_fact": 0, "validate_commit": 0, "digest_record": 0}
    for name in seen:
        def spy(record, real=getattr(module, name), name=name):
            seen[name] += 1
            return real(record)
        monkeypatch.setattr(module, name, spy)
    calls["n"] = 0
    EvidenceIndex(tmp_path / "query.sqlite3").ingest(snapshot)
    assert calls["n"] == 0 and set(seen.values()) == {0}
    EvidenceIndex(tmp_path / "other.sqlite3").ingest(replace(snapshot))  # control: full checks
    assert calls["n"] == len(snapshot.facts) + len(snapshot.commits)
    assert (seen["validate_fact"], seen["validate_commit"]) == (len(snapshot.facts), len(snapshot.commits))


@pytest.mark.parametrize("sealed", [True, False])
def test_ingest_reduces_each_scope_at_most_once(tmp_path, evidence_factory, monkeypatch, sealed):
    from dataclasses import replace
    import api.mirror.evidence_store as store_module
    store, _ = _counted_store(evidence_factory, tmp_path / "safe")
    snapshot = store.scan()
    if not sealed:
        snapshot = replace(snapshot)
    real, runs = store_module.reduce_scope, []

    def counting(snap, scope, scope_id, **kwargs):
        runs.append((kwargs.get("source_key"), kwargs.get("course_id"), scope, scope_id))
        return real(snap, scope, scope_id, **kwargs)
    monkeypatch.setattr(store_module, "reduce_scope", counting)
    EvidenceIndex(tmp_path / "query.sqlite3").ingest(snapshot)
    assert len(runs) == len(set(runs)) <= 4  # four scopes; the scan may already hold their reductions


@pytest.mark.parametrize("seal", [None, "forged"])
def test_unsealed_or_forged_seal_keeps_every_check(tmp_path, evidence_factory, seal):
    from dataclasses import replace
    from api.mirror.evidence_store import is_validated_scan
    store, calls = _counted_store(evidence_factory, tmp_path / "safe")
    scanned = store.scan()
    candidate = replace(scanned)
    if seal is not None:
        object.__setattr__(candidate, "sealed", object())  # forged, not the module seal
    assert not is_validated_scan(candidate)
    calls["n"] = 0
    EvidenceIndex(tmp_path / "ok.sqlite3").ingest(candidate)
    assert calls["n"] == len(scanned.facts) + len(scanned.commits)
    path = tmp_path / "refused.sqlite3"
    with pytest.raises(ValueError, match="privacy_refusal"):
        EvidenceIndex(path).ingest(replace(candidate, verify_safe=lambda record: False))
    assert not path.exists()


@pytest.mark.parametrize("kind", ["fact", "commit"])
def test_unsealed_digest_mismatch_is_refused(tmp_path, evidence_factory, kind):
    from dataclasses import replace
    store, _ = _counted_store(evidence_factory, tmp_path / "safe")
    snapshot = replace(store.scan())
    facts, commits = dict(snapshot.facts), dict(snapshot.commits)
    if kind == "fact":
        facts["f" * 64] = evidence_factory["fact"](entity_key="submission:10:Misfiled", pseudonym="Misfiled")
    else:
        commits["f" * 64] = evidence_factory["commit"](scope="assignment.submissions", scope_id="10", run_id="other")
    path = tmp_path / "query.sqlite3"
    with pytest.raises(ValueError, match=f"{kind}_digest_mismatch"):
        EvidenceIndex(path).ingest(replace(snapshot, facts=facts, commits=commits))
    assert not path.exists()


def test_ingest_many_seals_only_when_every_part_is_a_validated_scan(tmp_path, evidence_factory, monkeypatch):
    from dataclasses import replace
    from api.mirror.evidence_store import EvidenceStore, is_validated_scan
    first_verify, first_calls = _counting_verifier()
    second_verify, second_calls = _counting_verifier()
    first = evidence_factory["store"](tmp_path / "safe", verify_safe=first_verify)
    second = EvidenceStore(tmp_path / "safe", evidence_factory["source"], "2", verify_safe=second_verify,
                           private_diagnostics_root=tmp_path / "diagnostics")
    for store, course_id in ((first, "1"), (second, "2")):
        entity = f"course:{course_id}"
        fact = {"schema_version": 1, "kind": "course", "source_key": evidence_factory["source"],
                "course_id": course_id, "entity_key": entity, "payload": {"title": f"Course {course_id}"}}
        ref = store.publish_fact(fact)
        commit = evidence_factory["commit"](refs=[ref], members=[entity], scope="course.context", scope_id=course_id)
        commit["course_id"] = course_id
        store.publish_commit(commit)
    scans = [first.scan(), second.scan()]
    aggregates, real = [], EvidenceIndex.ingest

    def spy(self, snapshot, **kwargs):
        aggregates.append(snapshot)
        return real(self, snapshot, **kwargs)
    monkeypatch.setattr(EvidenceIndex, "ingest", spy)

    first_calls["n"] = second_calls["n"] = 0
    fast = EvidenceIndex(tmp_path / "fast.sqlite3").ingest_many(scans)
    assert is_validated_scan(aggregates[-1])
    assert first_calls["n"] == second_calls["n"] == 0

    full = EvidenceIndex(tmp_path / "full.sqlite3").ingest_many([scans[0], replace(scans[1])])
    assert not is_validated_scan(aggregates[-1])
    assert first_calls["n"] > 0 and second_calls["n"] > 0  # one unsealed part taints the whole aggregate
    assert fast == full
