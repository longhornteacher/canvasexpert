"""Durability, synchronization and causal history laws in disposable roots."""
import shutil

import pytest

from api.mirror.evidence_schema import EvidenceValidationError, canonical_bytes, digest_record
from api.mirror.evidence_store import EvidenceStore, StoreSnapshot, reduce_scope


def test_validation_and_privacy_finish_before_any_safe_bytes(tmp_path, evidence_factory):
    def reject(record):
        if "Sensitive Synthetic" in canonical_bytes(record).decode():
            raise ValueError("refused private value")
    safe = tmp_path / "safe"
    store = EvidenceStore(safe, "a" * 64, "1", verify_safe=reject, private_diagnostics_root=tmp_path.parent / (tmp_path.name + "-diagnostics"))
    with pytest.raises(EvidenceValidationError, match="privacy_refused"):
        store.publish_fact(evidence_factory["fact"](body="Sensitive Synthetic"))
    assert not safe.exists()
    invalid = evidence_factory["fact"]()
    invalid["payload"]["user_id"] = "private"
    with pytest.raises(EvidenceValidationError):
        store.publish_fact(invalid)
    assert not safe.exists()


def test_immutable_fact_reuse_and_local_dependency_order(tmp_path, evidence_factory):
    store = EvidenceStore(tmp_path, "a" * 64, "1", verify_safe=lambda record: None, private_diagnostics_root=tmp_path.parent / (tmp_path.name + "-diagnostics"))
    record = evidence_factory["fact"]()
    ref = store.publish_fact(record)
    assert store.publish_fact(record) == ref
    assert len(list(tmp_path.rglob("*.json"))) == 1
    with pytest.raises(EvidenceValidationError, match="publication_dependencies_missing"):
        store.publish_commit(evidence_factory["commit"](refs=["b" * 64], members=[record["entity_key"]]))
    path = next(tmp_path.rglob("*.json"))
    path.write_bytes(b"corrupt")
    with pytest.raises(EvidenceValidationError, match="immutable_corruption"):
        store.publish_fact(record)
    assert path.read_bytes() == b"corrupt"


def test_sync_pending_preserves_last_good_then_converges(tmp_path, evidence_factory):
    writer = EvidenceStore(tmp_path / "writer", "a" * 64, "1", verify_safe=lambda record: None, private_diagnostics_root=tmp_path.parent / (tmp_path.name + "-diagnostics"))
    reader = EvidenceStore(tmp_path / "reader", "a" * 64, "1", verify_safe=lambda record: None, private_diagnostics_root=tmp_path.parent / (tmp_path.name + "-diagnostics"))
    first = evidence_factory["fact"](body="First")
    ref1 = writer.publish_fact(first)
    c1 = writer.publish_commit(evidence_factory["commit"](refs=[ref1], members=[first["entity_key"]]))
    shutil.copytree(writer.course_root, reader.course_root)
    second = evidence_factory["fact"](body="Second", attempt=2)
    ref2 = writer.publish_fact(second)
    c2 = writer.publish_commit(evidence_factory["commit"](refs=[ref2], parents=[c1], members=[second["entity_key"]], run_id="run-2"))
    target = reader._path("commits", c2)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(writer._path("commits", c2), target)
    pending = reduce_scope(reader.scan(), "assignment.submissions", "10")
    assert pending.status == "sync_pending"
    assert pending.current_refs == (ref1,)
    assert pending.pending_commits == (c2,)
    target = reader._path("objects", ref2)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(writer._path("objects", ref2), target)
    assert reader.scan().revision == writer.scan().revision
    assert reduce_scope(reader.scan(), "assignment.submissions", "10").current_refs == (ref2,)


def test_future_record_is_reported_and_last_good_stays_current(tmp_path, evidence_factory):
    from api.mirror.evidence_schema import canonical_bytes, digest_record
    store = evidence_factory["store"](tmp_path / "safe")
    first = evidence_factory["fact"]()
    first_ref = store.publish_fact(first)
    first_commit = store.publish_commit(evidence_factory["commit"](
        refs=[first_ref], members=[first["entity_key"]]))
    future = evidence_factory["fact"](body="Future synthetic fact")
    future["schema_version"] = 2
    future["future_field"] = "reader must not copy diagnostics"
    future_ref = digest_record(future)
    future_path = store._path("objects", future_ref)
    future_path.parent.mkdir(parents=True, exist_ok=True)
    future_path.write_bytes(canonical_bytes(future))
    future_commit = evidence_factory["commit"](refs=[future_ref], parents=[first_commit],
        members=[future["entity_key"]], run_id="future-run")
    commit_ref = digest_record(future_commit)
    commit_path = store._path("commits", commit_ref)
    commit_path.parent.mkdir(parents=True, exist_ok=True)
    commit_path.write_bytes(canonical_bytes(future_commit))

    snapshot = store.scan()
    assert any(issue.code == "unsupported_schema" for issue in snapshot.issues)
    assert not list(store.private_diagnostics_root.rglob("*.bin"))
    state = reduce_scope(snapshot, "assignment.submissions", "10")
    assert state.status == "sync_pending"
    assert state.current_refs == state.last_good_refs == (first_ref,)


def test_future_commit_keeps_only_valid_scope_metadata_without_diagnostics(tmp_path, evidence_factory):
    from api.mirror.evidence_schema import canonical_bytes, digest_record
    store = evidence_factory["store"](tmp_path / "safe")
    fact = evidence_factory["fact"]("course", "course:1", {"title": "Synthetic course"})
    ref = store.publish_fact(fact)
    store.publish_commit(evidence_factory["commit"](scope="course.context", scope_id="1",
        refs=[ref], members=[fact["entity_key"]]))
    future = evidence_factory["commit"](scope="assignment.submissions", scope_id="10")
    future["schema_version"] = 2
    future["future_field"] = "synthetic future metadata"
    digest = digest_record(future)
    path = store._path("commits", digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(future))

    snapshot = store.scan()
    issue = next(issue for issue in snapshot.issues if issue.code == "unsupported_schema")
    assert (issue.scope, issue.scope_id, issue.source_key, issue.course_id) == (
        "assignment.submissions", "10", store.source_key, store.course_id)
    assert reduce_scope(snapshot, "assignment.submissions", "10").status == "sync_pending"
    assert reduce_scope(snapshot, "course.context", "1").status == "ready"
    assert not list(store.private_diagnostics_root.rglob("*.bin"))


def test_partial_and_delta_do_not_delete_but_complete_empty_tombstones(tmp_path, evidence_factory):
    store = EvidenceStore(tmp_path, "a" * 64, "1", verify_safe=lambda record: None, private_diagnostics_root=tmp_path.parent / (tmp_path.name + "-diagnostics"))
    record = evidence_factory["fact"]()
    ref = store.publish_fact(record)
    first = store.publish_commit(evidence_factory["commit"](refs=[ref], members=[record["entity_key"]]))
    partial = store.publish_commit(evidence_factory["commit"](parents=[first], complete=False, gaps=[{"code": "pagination_failed"}], run_id="partial"))
    delta = store.publish_commit(evidence_factory["commit"](parents=[partial], mode="delta", complete=False, run_id="delta"))
    assert reduce_scope(store.scan(), "assignment.submissions", "10").current_refs == (ref,)
    store.publish_commit(evidence_factory["commit"](parents=[delta], run_id="empty"))
    state = reduce_scope(store.scan(), "assignment.submissions", "10")
    assert state.current_refs == ()
    assert state.tombstones == (record["entity_key"],)
    assert state.history_refs == (ref,)


def test_causal_timestamp_observations_survive_conflicts_and_tombstones(tmp_path, evidence_factory):
    store = EvidenceStore(tmp_path, "a" * 64, "1", verify_safe=lambda record: None, private_diagnostics_root=tmp_path.parent / (tmp_path.name + "-diagnostics"))
    key = "attempt:10:Pikachu:1"
    old = store.publish_fact(evidence_factory["fact"](kind="attempt_observation", entity_key=key))
    c1 = store.publish_commit(evidence_factory["commit"](refs=[old], run_id="first"))
    later = store.publish_fact(evidence_factory["fact"](kind="attempt_observation", entity_key=key, submitted_at="2026-01-02T00:00:00Z", late=False))
    c2 = store.publish_commit(evidence_factory["commit"](refs=[later, old], parents=[c1], run_id="later"))
    store.publish_commit(evidence_factory["commit"](parents=[c2], run_id="deleted"))
    state = reduce_scope(store.scan(), "assignment.submissions", "10")
    assert state.established_submitted_at[key] == "2026-01-01T00:00:00Z"
    assert set(state.observation_discrepancies[key]) == {old, later}
    assert set(state.history_refs) == {old, later}


def test_concurrent_conflict_keeps_common_last_good_until_reconciliation(tmp_path, evidence_factory):
    store = EvidenceStore(tmp_path, "a" * 64, "1", verify_safe=lambda record: None, private_diagnostics_root=tmp_path.parent / (tmp_path.name + "-diagnostics"))
    key = evidence_factory["fact"]()["entity_key"]
    refs = [store.publish_fact(evidence_factory["fact"](body=text)) for text in ("Base", "Branch A", "Branch B")]
    base = store.publish_commit(evidence_factory["commit"](refs=[refs[0]], members=[key]))
    branches = [store.publish_commit(evidence_factory["commit"](refs=[ref], parents=[base], members=[key], run_id=f"branch-{number}")) for number, ref in enumerate(refs[1:])]
    state = reduce_scope(store.scan(), "assignment.submissions", "10")
    assert state.status == "ambiguous"
    assert state.current_refs == state.last_good_refs == (refs[0],)
    assert state.ambiguous_entities == (key,)
    store.publish_commit(evidence_factory["commit"](refs=[refs[1]], parents=branches, members=[key], run_id="reconciled"))
    state = reduce_scope(store.scan(), "assignment.submissions", "10")
    assert state.status == "ready"
    assert state.current_refs == (refs[1],)


def test_conflict_copies_deduplicate_and_refuse_corruption_and_private_text(tmp_path, evidence_factory):
    def verifier(record):
        if "Synthetic Private" in canonical_bytes(record).decode():
            raise ValueError("private")
    store = EvidenceStore(tmp_path, "a" * 64, "1", verify_safe=verifier, private_diagnostics_root=tmp_path.parent / (tmp_path.name + "-diagnostics"))
    record = evidence_factory["fact"]()
    ref = store.publish_fact(record)
    commit = store.publish_commit(evidence_factory["commit"](refs=[ref], members=[record["entity_key"]]))
    original = store._path("objects", ref)
    copy = original.with_name(f"{ref}-provider arbitrary copy.json")
    shutil.copyfile(original, copy)
    assert len(store.scan().facts) == 1
    assert store.scan().issues == ()
    copy.write_bytes(b"corrupt")
    unsafe = evidence_factory["fact"](body="Synthetic Private")
    digest = digest_record(unsafe)
    unsafe_path = store._path("objects", digest)
    unsafe_path.parent.mkdir(parents=True, exist_ok=True)
    unsafe_path.write_bytes(canonical_bytes(unsafe))
    snapshot = store.scan()
    assert digest not in snapshot.facts
    assert len(snapshot.issues) == 2
    state = reduce_scope(snapshot, "assignment.submissions", "10")
    assert state.current_refs == (ref,)
    assert state.status == "sync_pending"
    assert snapshot.commits[commit]["record_refs"] == [ref]
    saved = [p.read_bytes() for p in store.private_diagnostics_root.rglob("*.bin")]
    assert b"corrupt" in saved
    assert canonical_bytes(unsafe) in saved
    assert copy.read_bytes() == b"corrupt"
    assert unsafe_path.read_bytes() == canonical_bytes(unsafe)
    assert str(tmp_path) not in repr(snapshot.issues)


def test_diagnostics_cannot_overlap_safe_root(tmp_path):
    with pytest.raises(EvidenceValidationError, match="diagnostics_root_overlap"):
        EvidenceStore(tmp_path, "a" * 64, "1", verify_safe=lambda record: None, private_diagnostics_root=tmp_path / "private")


def test_cross_assignment_references_and_ancestry_are_refused(tmp_path, evidence_factory):
    store = EvidenceStore(tmp_path, "a" * 64, "1", verify_safe=lambda record: None, private_diagnostics_root=tmp_path.parent / (tmp_path.name + "-diagnostics"))
    ref = store.publish_fact(evidence_factory["fact"]())
    with pytest.raises(EvidenceValidationError, match="reference_scope_mismatch"):
        store.publish_commit(evidence_factory["commit"](refs=[ref], scope_id="20"))
    parent = store.publish_commit(evidence_factory["commit"](scope_id="20"))
    with pytest.raises(EvidenceValidationError, match="parent_scope_mismatch"):
        store.publish_commit(evidence_factory["commit"](parents=[parent]))


def test_concurrent_attempt_timestamps_remain_explicitly_unresolved(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path)
    key = "attempt:10:Pikachu:1"
    refs = [store.publish_fact(evidence_factory["fact"](kind="attempt_observation", entity_key=key, submitted_at=stamp)) for stamp in ("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")]
    for number, ref in enumerate(refs):
        store.publish_commit(evidence_factory["commit"](refs=[ref], run_id=f"branch-{number}"))
    state = reduce_scope(store.scan(), "assignment.submissions", "10")
    assert state.status == "ambiguous"
    assert state.established_submitted_at[key] is None
    assert set(state.observation_discrepancies[key]) == set(refs)


def test_import_arrival_never_displaces_live_or_proves_absence(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path)
    key = evidence_factory["fact"]()["entity_key"]
    live = store.publish_fact(evidence_factory["fact"](body="Live"))
    store.publish_commit(evidence_factory["commit"](refs=[live], members=[key]))
    historical = store.publish_fact(evidence_factory["fact"](body="Historical"))
    store.publish_commit(evidence_factory["commit"](refs=[historical], members=[key], mode="import", complete=False, run_id="import"))
    state = reduce_scope(store.scan(), "assignment.submissions", "10")
    assert state.current_refs == (live,)
    assert set(state.history_refs) == {live, historical}


def test_tombstones_are_bound_to_their_exact_membership_scope(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path)
    key = evidence_factory["fact"]()["entity_key"]
    first = store.publish_fact(evidence_factory["fact"]())
    other = store.publish_fact(evidence_factory["fact"](entity_key="submission:20:Pikachu", assignment_id="20"))
    parent = store.publish_commit(evidence_factory["commit"](refs=[first], members=[key]))
    store.publish_commit(evidence_factory["commit"](refs=[other], members=["submission:20:Pikachu"], scope_id="20"))
    store.publish_commit(evidence_factory["commit"](parents=[parent], run_id="deleted"))
    snapshot = store.scan()
    assert reduce_scope(snapshot, "assignment.submissions", "10").tombstones == (key,)
    state = reduce_scope(snapshot, "assignment.submissions", "20")
    assert state.current_refs == (other,)
    assert state.tombstones == ()


def test_long_causal_history_does_not_depend_on_python_recursion(evidence_factory):
    fact = evidence_factory["fact"]()
    ref = digest_record(fact)
    commits = {}
    parents = []
    for number in range(1100):
        commit = evidence_factory["commit"](refs=[ref], members=[fact["entity_key"]], parents=parents, mode="delta", complete=False, run_id=f"run-{number}")
        digest = digest_record(commit)
        commits[digest] = commit
        parents = [digest]
    snapshot = StoreSnapshot({ref: fact}, commits, (), "c" * 64, lambda record: None)
    state = reduce_scope(snapshot, "assignment.submissions", "10")
    assert state.status == "ready"
    assert state.current_refs == (ref,)
    assert state.heads == tuple(parents)


def test_first_refused_scoped_commit_remains_visible_without_accepted_commit(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path)
    record = evidence_factory["commit"](refs=["b" * 64], members=["submission:10:Pikachu"])
    digest = digest_record(record)
    path = store._path("commits", digest)
    path.parent.mkdir(parents=True)
    path.write_bytes(canonical_bytes(record) + b"\n")
    snapshot = store.scan()
    assert snapshot.commits == {}
    key = ("a" * 64, "1", "assignment.submissions", "10")
    assert snapshot.scopes[key].status == "sync_pending"
    assert snapshot.scopes[key].current_refs == ()
    assert snapshot.issues[0].source_key == "a" * 64
    assert str(tmp_path) not in repr(snapshot.issues)


# --- Receipt-scoped publication context -----------------------------------------

class _ScanSpy:
    def __init__(self, monkeypatch):
        self.count = 0
        real = EvidenceStore.scan

        def scan(store):
            self.count += 1
            return real(store)
        monkeypatch.setattr(EvidenceStore, "scan", scan)


def test_context_is_private_and_bound_to_its_store(tmp_path, evidence_factory):
    from api.mirror.evidence_store import PublicationContext
    store = evidence_factory["store"](tmp_path / "a")
    other = evidence_factory["store"](tmp_path / "b")
    with pytest.raises(TypeError, match="publication_context_private"):
        PublicationContext(object(), store, store.scan(), 0.0)
    context = store.begin_publication()
    record = evidence_factory["fact"]()
    for call in (lambda: other.publish_fact(record, context=context),
                 lambda: other.publish_commit(evidence_factory["commit"](), context=context),
                 lambda: store.publish_fact(record, context={"facts": {}})):
        with pytest.raises(EvidenceValidationError, match="publication_context_mismatch"):
            call()
    assert not list((tmp_path / "a").rglob("*.json")) and not list((tmp_path / "b").rglob("*.json"))


def test_context_scans_once_and_chains_same_scope_commits(tmp_path, evidence_factory, monkeypatch):
    store = evidence_factory["store"](tmp_path)
    key = evidence_factory["fact"]()["entity_key"]
    spy = _ScanSpy(monkeypatch)
    context = store.begin_publication()
    first_fact = store.publish_fact(evidence_factory["fact"](body="One"), context=context)
    c1 = store.publish_commit(evidence_factory["commit"](refs=[first_fact], members=[key]), context=context)
    second_fact = store.publish_fact(evidence_factory["fact"](body="Two"), context=context)
    c2 = store.publish_commit(evidence_factory["commit"](
        refs=[second_fact], parents=[c1], members=[key], run_id="run-2"), context=context)
    assert spy.count == 1
    assert context.stats["facts_published"] == 2 and context.stats["commits_published"] == 2
    monkeypatch.undo()
    state = reduce_scope(store.scan(), "assignment.submissions", "10")
    assert (state.status, state.heads, state.current_refs) == ("ready", (c2,), (second_fact,))


def test_context_state_comes_from_the_initial_scan_not_caller_input(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path)
    fact = evidence_factory["fact"]()
    ref = store.publish_fact(fact)
    c1 = store.publish_commit(evidence_factory["commit"](refs=[ref], members=[fact["entity_key"]]))
    context = store.begin_publication()
    key = (store.source_key, store.course_id, "assignment.submissions", "10")
    assert context.initial_heads(key) == [c1]
    assert context.initial_heads((store.source_key, store.course_id, "assignment.submissions", "99")) == []
    assert context.fact(ref)["entity_key"] == fact["entity_key"]
    # Mutating a caller record after publication cannot alter validated state.
    record = evidence_factory["fact"](body="Later")
    digest = store.publish_fact(record, context=context)
    record["payload"]["body"] = "Tampered"
    assert context.fact(digest)["payload"]["body"] == "Later"


def test_context_refuses_missing_refs_parents_and_invalid_graphs(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path)
    ref = store.publish_fact(evidence_factory["fact"]())
    context = store.begin_publication()
    unknown = "b" * 64
    with pytest.raises(EvidenceValidationError, match="publication_dependencies_missing"):
        store.publish_commit(evidence_factory["commit"](refs=[unknown], members=["submission:10:Pikachu"]), context=context)
    with pytest.raises(EvidenceValidationError, match="publication_dependencies_missing"):
        store.publish_commit(evidence_factory["commit"](refs=[ref], parents=[unknown], members=["submission:10:Pikachu"]), context=context)
    # A fact on disk but unknown to this context is never trusted.
    late = evidence_factory["fact"](body="Arrived after scan")
    late_ref = store.publish_fact(late)
    with pytest.raises(EvidenceValidationError, match="publication_dependencies_missing"):
        store.publish_commit(evidence_factory["commit"](refs=[late_ref], members=[late["entity_key"]]), context=context)
    with pytest.raises(EvidenceValidationError, match="reference_scope_mismatch"):
        store.publish_commit(evidence_factory["commit"](refs=[ref], scope_id="20"), context=context)
    parent = store.publish_commit(evidence_factory["commit"](scope_id="20"), context=context)
    with pytest.raises(EvidenceValidationError, match="parent_scope_mismatch"):
        store.publish_commit(evidence_factory["commit"](parents=[parent]), context=context)
    same = store.publish_fact(evidence_factory["fact"](body="Other"), context=context)
    with pytest.raises(EvidenceValidationError, match="conflicting_commit_entity"):
        store.publish_commit(evidence_factory["commit"](
            refs=[ref, same], members=["submission:10:Pikachu"], run_id="dup"), context=context)
    assert len(list((tmp_path / "sources").rglob("commits/*/*.json"))) == 1


def test_context_refuses_privacy_failure_before_any_bytes(tmp_path, evidence_factory):
    def reject(record):
        if "Sensitive Synthetic" in canonical_bytes(record).decode():
            raise ValueError("refused")
    store = evidence_factory["store"](tmp_path / "safe", verify_safe=reject)
    context = store.begin_publication()
    with pytest.raises(EvidenceValidationError, match="privacy_refused"):
        store.publish_fact(evidence_factory["fact"](body="Sensitive Synthetic"), context=context)
    assert not list((tmp_path / "safe").rglob("*.json"))
    assert context.stats["facts_published"] == 0


def test_context_refuses_dependency_corrupted_or_deleted_after_the_scan(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path)
    fact = evidence_factory["fact"]()
    ref = store.publish_fact(fact)
    parent = store.publish_commit(evidence_factory["commit"](refs=[ref], members=[fact["entity_key"]]))
    context = store.begin_publication()
    new_fact = evidence_factory["fact"](body="New")
    new_ref = store.publish_fact(new_fact, context=context)
    child = evidence_factory["commit"](refs=[new_ref], parents=[parent],
                                      members=[new_fact["entity_key"]], run_id="child")
    store._path("commits", parent).write_bytes(b"corrupt")
    with pytest.raises(EvidenceValidationError, match="publication_dependencies_missing"):
        store.publish_commit(child, context=context)
    store._path("commits", parent).unlink()
    with pytest.raises(EvidenceValidationError, match="publication_dependencies_missing"):
        store.publish_commit(child, context=context)
    # A fact this context published, then lost or truncated, is also refused.
    for number, damage in enumerate((lambda path: path.write_bytes(b"x"), lambda path: path.unlink())):
        fresh = store.begin_publication()
        extra = store.publish_fact(evidence_factory["fact"](body=f"Extra {number}"), context=fresh)
        damage(store._path("objects", extra))
        with pytest.raises(EvidenceValidationError, match="publication_dependencies_missing"):
            store.publish_commit(evidence_factory["commit"](
                refs=[extra], members=["submission:10:Pikachu"], run_id="extra"), context=fresh)
    assert fresh.stats["dependency_checks"] >= 1


def test_context_accepts_provider_conflict_copy_as_the_only_dependency_file(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path)
    fact = evidence_factory["fact"]()
    ref = store.publish_fact(fact)
    original = store._path("objects", ref)
    original.rename(original.with_name(f"{ref}-provider arbitrary copy.json"))
    context = store.begin_publication()
    assert context.fact(ref) is not None
    store.publish_commit(evidence_factory["commit"](refs=[ref], members=[fact["entity_key"]]), context=context)


def test_context_refuses_pending_parent_like_the_standalone_path(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path)
    fact = evidence_factory["fact"]()
    ref = store.publish_fact(fact)
    pending = store.publish_commit(evidence_factory["commit"](refs=[ref], members=[fact["entity_key"]]))
    store._path("objects", ref).unlink()  # the commit is present but its fact has not arrived
    assert reduce_scope(store.scan(), "assignment.submissions", "10").pending_commits == (pending,)
    child = evidence_factory["commit"](parents=[pending], run_id="child")
    with pytest.raises(EvidenceValidationError, match="publication_dependencies_missing"):
        store.publish_commit(child)
    with pytest.raises(EvidenceValidationError, match="publication_dependencies_missing"):
        store.publish_commit(child, context=store.begin_publication())


def test_commit_synced_after_the_scan_becomes_a_causal_sibling_not_an_overwrite(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path / "local")
    remote = evidence_factory["store"](tmp_path / "remote")
    key = evidence_factory["fact"]()["entity_key"]
    base_ref = store.publish_fact(evidence_factory["fact"](body="Base"))
    base = store.publish_commit(evidence_factory["commit"](refs=[base_ref], members=[key]))
    shutil.copytree(store.course_root, remote.course_root)
    context = store.begin_publication()
    remote_ref = remote.publish_fact(evidence_factory["fact"](body="Remote"))
    remote_commit = remote.publish_commit(evidence_factory["commit"](
        refs=[remote_ref], parents=[base], members=[key], run_id="remote"))
    for namespace, digest in (("objects", remote_ref), ("commits", remote_commit)):  # sync arrives mid-receipt
        target = store._path(namespace, digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(remote._path(namespace, digest), target)
    local_ref = store.publish_fact(evidence_factory["fact"](body="Local"), context=context)
    local_commit = store.publish_commit(evidence_factory["commit"](
        refs=[local_ref], parents=context.initial_heads((store.source_key, store.course_id, "assignment.submissions", "10")),
        members=[key], run_id="local"), context=context)
    state = reduce_scope(store.scan(), "assignment.submissions", "10")
    assert set(state.heads) == {remote_commit, local_commit}
    assert state.status == "ambiguous" and state.current_refs == (base_ref,)
    assert store._path("commits", remote_commit).exists()


def test_publication_interruption_leaves_safe_facts_and_a_retry_converges(tmp_path, evidence_factory, monkeypatch):
    store = evidence_factory["store"](tmp_path)
    fact = evidence_factory["fact"]()
    context = store.begin_publication()
    ref = store.publish_fact(fact, context=context)
    real = EvidenceStore._publish_sized

    def fail_commit(self, namespace, checked):
        if namespace == "commits":
            raise OSError("interrupted")
        return real(self, namespace, checked)
    monkeypatch.setattr(EvidenceStore, "_publish_sized", fail_commit)
    with pytest.raises(OSError):
        store.publish_commit(evidence_factory["commit"](refs=[ref], members=[fact["entity_key"]]), context=context)
    monkeypatch.undo()
    assert context.stats["commits_published"] == 0
    snapshot = store.scan()
    assert ref in snapshot.facts and not snapshot.commits and not snapshot.issues
    retry = store.begin_publication()
    store.publish_fact(fact, context=retry)
    digest = store.publish_commit(evidence_factory["commit"](refs=[ref], members=[fact["entity_key"]]), context=retry)
    assert reduce_scope(store.scan(), "assignment.submissions", "10").heads == (digest,)


def test_standalone_commit_scans_once_and_reduces_only_its_own_scope(tmp_path, evidence_factory, monkeypatch):
    store = evidence_factory["store"](tmp_path)
    fact = evidence_factory["fact"]()
    ref = store.publish_fact(fact)
    parent = store.publish_commit(evidence_factory["commit"](refs=[ref], members=[fact["entity_key"]]))
    spy = _ScanSpy(monkeypatch)
    store.publish_commit(evidence_factory["commit"](parents=[parent], run_id="next"))
    assert spy.count == 1
