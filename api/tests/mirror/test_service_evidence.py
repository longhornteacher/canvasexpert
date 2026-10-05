"""Complete local evidence rebuilds across delayed course files."""
from __future__ import annotations

from contextlib import nullcontext

from api.mirror import service
from api.mirror.evidence_index import EvidenceIndex
from api.mirror.evidence_paths import local_source_root
from api.mirror.evidence_store import EvidenceStore


def test_rebuild_preserves_all_courses_until_delayed_files_arrive(
        tmp_path, monkeypatch, evidence_factory):
    from api import runtime_paths
    from api.mirror import store as mirror_store
    from api.tests.mirror.test_evidence_publish import SyntheticVault

    source = evidence_factory["source"]
    root = tmp_path / "workspace"
    safe_root = root / "CanvasMirror"
    monkeypatch.setattr(runtime_paths, "local_cache_dir", lambda: tmp_path / "local")
    monkeypatch.setattr(mirror_store, "_vault_transaction",
                        lambda _root: nullcontext(SyntheticVault()))
    monkeypatch.setattr(service.config, "active_courses", lambda: [{"id": "1"}])

    def publish_course(course_id):
        store = EvidenceStore(safe_root, source, course_id,
            verify_safe=lambda record: None,
            private_diagnostics_root=tmp_path / "diagnostics")
        entity = f"course:{course_id}"
        fact = {"schema_version": 1, "kind": "course", "source_key": source,
                "course_id": course_id, "entity_key": entity,
                "payload": {"title": f"Course {course_id}"}}
        ref = store.publish_fact(fact)
        commit = evidence_factory["commit"](
            scope="course.context", scope_id=course_id,
            refs=[ref], members=[entity])
        commit["course_id"] = course_id
        store.publish_commit(commit)

    publish_course("1")
    publish_course("2")
    index_path = local_source_root(source, root) / "query.sqlite3"
    first = service.rebuild_evidence_index(root=root, source_key=source)
    assert first["state"] == "current" and first["courses"] == 2
    index = EvidenceIndex(index_path)
    assert {r["course_id"] for r in index.query_page("courses")["records"]} == {"1", "2"}

    # A third cloud directory can appear before its immutable files do. The
    # previous complete index remains readable until the files arrive.
    (safe_root / "sources" / source / "courses" / "3").mkdir(parents=True)
    pending = service.rebuild_evidence_index(root=root, source_key=source)
    assert pending["state"] == "pending"
    assert index.query_page("courses")["revision"] == first["revision"]
    publish_course("3")
    latest = service.rebuild_evidence_index(root=root, source_key=source)
    assert latest["state"] == "current" and latest["revision"] != first["revision"]
    assert {r["course_id"] for r in index.query_page("courses")["records"]} == {"1", "2", "3"}

    # A previously indexed directory can also be absent while another course
    # remains locally available. That partial source must not replace the index.
    course_two = safe_root / "sources" / source / "courses" / "2"
    delayed_course_two = tmp_path / "delayed-course-two"
    course_two.rename(delayed_course_two)
    try:
        pending = service.rebuild_evidence_index(root=root, source_key=source)
        assert pending["state"] == "pending"
        assert index.query_page("courses")["revision"] == latest["revision"]
        assert {r["course_id"] for r in index.query_page("courses")["records"]} == {"1", "2", "3"}
    finally:
        delayed_course_two.rename(course_two)
    assert service.rebuild_evidence_index(root=root, source_key=source)["state"] == "current"

    # Local corruption is disposable; a rebuild restores the same safe rows.
    index_path.write_bytes(b"broken sqlite")
    repaired = service.rebuild_evidence_index(root=root, source_key=source)
    assert repaired["state"] == "current"
    assert {r["course_id"] for r in index.query_page("courses")["records"]} == {"1", "2", "3"}
