"""Direct read contract and semantic query path use one safe index."""

import json
import sqlite3

import pytest

from api.mirror.evidence_index import EvidenceIndex, VIEW_COLUMNS
from api.mirror.evidence_queries import (
    EvidenceQueryService, publish_reader_contract, reader_contract,
)


def test_generated_reader_contract_has_one_registry_and_refuses_conflict(tmp_path):
    root = tmp_path / "safe"
    path = publish_reader_contract(root)
    assert json.loads(path.read_text(encoding="utf-8"))["views"] == {
        name: list(columns) for name, columns in sorted(VIEW_COLUMNS.items())
    }
    assert publish_reader_contract(root) == path
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="reader_contract_conflict"):
        publish_reader_contract(root)


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
