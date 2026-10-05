"""Direct read contract and semantic query path use one safe index."""

import json
import sqlite3

import pytest

from api.mirror.evidence_index import EvidenceIndex, VIEW_COLUMNS
from api.mirror.evidence_queries import (
    EvidenceQueryService, write_reader_descriptor,
)


def test_descriptor_is_local_registry_derived_and_revision_bound(tmp_path, evidence_factory):
    index = EvidenceIndex(tmp_path / "local" / "query.sqlite3")
    revision = index.ingest(evidence_factory["store"](tmp_path / "safe").scan())
    path = write_reader_descriptor(index.path, revision=revision)
    descriptor = json.loads(path.read_text(encoding="utf-8"))
    assert path == index.path.parent / "reader.json"
    assert descriptor["views"] == {name: list(columns) for name, columns in sorted(VIEW_COLUMNS.items())}
    assert {"roster", "sections"} <= descriptor["views"].keys()
    assert descriptor["index_revision"] == revision
    assert descriptor["index_schema_version"] == 2
    assert descriptor["index_file"] == "query.sqlite3"


@pytest.mark.parametrize("view", sorted(VIEW_COLUMNS))
def test_direct_sql_matches_query_service(tmp_path, evidence_factory, view):
    index = EvidenceIndex(tmp_path / "query.sqlite3")
    revision = index.ingest(evidence_factory["store"](tmp_path / "safe").scan())
    service_page = EvidenceQueryService(index.path).read(view, source_key="a" * 64, course_id="1")
    uri = index.path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        rows = db.execute(f"SELECT * FROM {view} WHERE source_key=? AND course_id=?", ("a" * 64, "1")).fetchall()
    assert service_page["revision"] == revision
    assert len(service_page["records"]) == len(rows)


def test_attachment_summary_counts_pending_gaps_and_extractions(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path / "safe")
    association_refs, association_entities = [], []
    for ordinal, status in enumerate(("pending", "captured", "unavailable")):
        key = f"{ordinal + 1:016x}"
        entity = f"attachment:10:Pikachu:1:{key}"
        payload = {"assignment_id": "10", "pseudonym": "Pikachu", "attempt": 1,
                   "attachment_key": key, "status": status, "revision": 1}
        if status == "captured":
            payload.update(original_digest="b" * 64, media_type="text/plain", size=12)
        ref = store.publish_fact(evidence_factory["fact"]("attachment", entity, payload))
        association_refs.append(ref)
        association_entities.append(entity)
    store.publish_commit(evidence_factory["commit"](scope="assignment.attachments", scope_id="10",
        refs=association_refs, members=association_entities, run_id="attachments"))
    extraction_refs, extraction_entities = [], []
    for ordinal, availability in enumerate(("complete", "partial")):
        key = f"{ordinal + 1:016x}"
        entity = f"extraction:10:Pikachu:1:{key}"
        payload = {"assignment_id": "10", "pseudonym": "Pikachu", "attempt": 1,
                   "attachment_key": key, "original_digest": "c" * 64,
                   "availability": availability, "method": "native", "blocks": []}
        ref = store.publish_fact(evidence_factory["fact"]("attachment_extraction", entity, payload))
        extraction_refs.append(ref)
        extraction_entities.append(entity)
    store.publish_commit(evidence_factory["commit"](scope="assignment.extractions", scope_id="10",
        refs=extraction_refs, members=extraction_entities, run_id="extractions"))
    index = EvidenceIndex(tmp_path / "local" / "query.sqlite3")
    index.ingest(store.scan())
    result = EvidenceQueryService(index.path).read_assignment_evidence(
        "attachments", source_key="a" * 64, course_id="1", assignment_id="10")
    assert result["attachment_summary"] == {
        "associations": 3, "captured": 1, "pending": 1, "gaps": 1,
        "extracted": 2, "extraction_gaps": 1,
    }


def test_named_read_serves_pinned_revision_without_vault_or_canvas(tmp_path, evidence_factory):
    root = tmp_path / "safe"
    store = evidence_factory["store"](root)
    fact = evidence_factory["fact"](kind="attempt_observation",
                                    entity_key="attempt:10:Pikachu:1")
    ref = store.publish_fact(fact)
    store.publish_commit(evidence_factory["commit"](refs=[ref], complete=False))
    index = EvidenceIndex(tmp_path / "local" / "query.sqlite3")
    revision = index.ingest(store.scan(), selected_courses=["1"])
    service = EvidenceQueryService(index.path)
    page = service.read("attempt_history", source_key="a" * 64,
                        course_id="1", assignment_id="10", revision=revision)
    assert len(page["records"]) == 1
    assert page["records"][0]["payload"]["body"] == "Draft"
    assert page["membership"]["state"] == "incomplete"
    assert page["synchronization"]["state"] == "ready"
    assert page["freshness"]["age_refuses_read"] is False
    uri = index.path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        assert db.execute("SELECT COUNT(*) FROM attempt_history").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM safe_facts")


def test_assignment_context_uses_assignment_scope_not_submission_scope(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path / "safe")
    assignment = evidence_factory["fact"](
        kind="assignment", entity_key="assignment:10",
        payload={"assignment_id": "10", "title": "Prompt"},
    )
    ref = store.publish_fact(assignment)
    store.publish_commit(evidence_factory["commit"](
        refs=[ref], members=["assignment:10"],
        scope="course.assignments", scope_id="1", run_id="assignment",
    ))
    store.publish_commit(evidence_factory["commit"](
        scope="assignment.submissions", scope_id="10", complete=False,
        gaps=[{"code": "fetch_failed"}], run_id="submissions",
    ))
    index = EvidenceIndex(tmp_path / "local" / "query.sqlite3")
    index.ingest(store.scan())
    result = EvidenceQueryService(index.path).read(
        "assignment_context", source_key="a" * 64, course_id="1",
        assignment_id="10",
    )
    assert len(result["records"]) == 1
    assert result["membership"]["state"] == "complete"


def test_group_context_uses_group_scope_coverage(tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path / "safe")
    group = evidence_factory["fact"](
        kind="group", entity_key="group:20",
        payload={"group_id": "20", "title": "Blue", "student_pseudonyms": [],
                 "category_key": "b" * 64, "category_name": "Teams"},
    )
    ref = store.publish_fact(group)
    store.publish_commit(evidence_factory["commit"](
        refs=[ref], members=["group:20"], scope="course.groups",
        scope_id="1", run_id="groups",
    ))
    index = EvidenceIndex(tmp_path / "local" / "query.sqlite3")
    index.ingest(store.scan())
    result = EvidenceQueryService(index.path).read(
        "group_context", source_key="a" * 64, course_id="1",
    )
    assert len(result["records"]) == 1
    assert result["membership"]["state"] == "complete"
    assert result["synchronization"]["state"] == "ready"


def test_historical_attachment_read_keeps_captured_metadata_after_pending_refresh(
        tmp_path, evidence_factory):
    store = evidence_factory["store"](tmp_path / "safe")
    key = "a" * 32
    entity = f"attachment:10:Pikachu:1:{key}"
    captured = evidence_factory["fact"](
        kind="attachment", entity_key=entity,
        payload={"assignment_id": "10", "pseudonym": "Pikachu", "attempt": 1,
                 "attachment_key": key, "status": "captured",
                 "original_digest": "b" * 64, "media_type": "text/plain",
                 "size": 12, "revision": 1})
    captured_ref = store.publish_fact(captured)
    first = store.publish_commit(evidence_factory["commit"](
        refs=[captured_ref], members=[entity], scope="assignment.attachments",
        run_id="captured"))
    pending = evidence_factory["fact"](
        kind="attachment", entity_key=entity,
        payload={"assignment_id": "10", "pseudonym": "Pikachu", "attempt": 1,
                 "attachment_key": key, "status": "pending", "revision": 1})
    pending_ref = store.publish_fact(pending)
    store.publish_commit(evidence_factory["commit"](
        refs=[pending_ref], members=[entity], scope="assignment.attachments",
        parents=[first], run_id="pending"))
    index = EvidenceIndex(tmp_path / "local" / "query.sqlite3")
    revision = index.ingest(store.scan())
    result = EvidenceQueryService(index.path).read_attempt_attachments(
        source_key="a" * 64, course_id="1", assignment_id="10",
        attempts=[("Pikachu", 1)], revision=revision, max_files=1)
    assert result == {"records": [{"pseudonym": "Pikachu", "attempt": 1,
        "attachment_key": key, "original_digest": "b" * 64,
        "media_type": "text/plain", "size": 12, "status": "captured"}],
        "truncated": False}
