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


# --- read_scoring_discovery: one-transaction, student-free counters -----------

def _discovery_index(tmp_path, evidence_factory, *, roster=("Pikachu", "Eevee"),
                     roster_complete=True, assignments=(("10", True),),
                     submissions=(), submissions_complete=True, with_roster=True):
    """Publish a synthetic course and index it; ``submissions`` rows are dicts."""
    fact, commit = evidence_factory["fact"], evidence_factory["commit"]
    store = evidence_factory["store"](tmp_path / "safe")
    if with_roster:
        refs = [store.publish_fact(fact("student", f"student:{p}", {"pseudonym": p})) for p in roster]
        store.publish_commit(commit(scope="course.roster", scope_id="1", refs=refs,
                                    members=[f"student:{p}" for p in roster],
                                    complete=roster_complete, run_id="roster"))
    arefs = [store.publish_fact(fact("assignment", f"assignment:{a}", {
        "assignment_id": a, "title": f"Task {a}", "points_possible": 10, "published": pub,
        "due_at": "2026-02-01T00:00:00Z"})) for a, pub in assignments]
    store.publish_commit(commit(scope="course.assignments", scope_id="1", refs=arefs,
                                members=[f"assignment:{a}" for a, _ in assignments], run_id="assignments"))
    by_assignment = {}
    for row in submissions:
        by_assignment.setdefault(row.get("assignment_id", "10"), []).append(row)
    for aid, rows in by_assignment.items():
        refs, members = [], []
        for row in rows:
            body = {"assignment_id": aid, "attempt": 1, "submitted_at": "2026-01-05T00:00:00Z",
                    "workflow_state": "submitted", "excused": False, "late": False, "score": None,
                    **row}
            key = f"submission:{aid}:{body['pseudonym']}"
            refs.append(store.publish_fact(fact("submission", key, body)))
            members.append(key)
        store.publish_commit(commit(scope="assignment.submissions", scope_id=aid, refs=refs,
                                    members=members, complete=submissions_complete,
                                    run_id=f"subs-{aid}"))
    index = EvidenceIndex(tmp_path / "local" / "query.sqlite3")
    index.ingest(store.scan())
    return EvidenceQueryService(index.path)


def _discover(service, evidence_factory, courses=("1",)):
    return service.read_scoring_discovery(source_key=evidence_factory["source"], course_ids=list(courses))


def test_discovery_counts_match_needs_grading_law(tmp_path, evidence_factory):
    service = _discovery_index(tmp_path, evidence_factory, roster=("Pikachu", "Eevee", "Mew", "Ditto"),
        submissions=[
            {"pseudonym": "Pikachu", "score": 0, "late": True},           # zero is partially scored
            {"pseudonym": "Eevee"},                                       # plain ungraded
            {"pseudonym": "Mew", "workflow_state": "graded"},             # graded: not counted
            {"pseudonym": "Ditto", "excused": True},                      # excused: not counted
            {"pseudonym": "Outsider"},                                    # not on roster
        ])
    result = _discover(service, evidence_factory)
    course = result["courses"][0]
    (row,) = course["assignments"]
    assert (row["ungraded"], row["partially_scored"], row["late_ungraded"]) == (2, 1, 1)
    assert row["coverage"] == "complete" and row["counts_complete"] is True
    assert row["name"] == "Task 10"
    assert result["revision"] == course["revision"]


def test_discovery_distinguishes_unknown_from_zero(tmp_path, evidence_factory):
    complete = _discover(_discovery_index(tmp_path / "a", evidence_factory,
                         submissions=[{"pseudonym": "Pikachu", "workflow_state": "graded"}]), evidence_factory)
    assert complete["courses"][0]["assignments"][0]["ungraded"] == 0
    # No submissions scope observed at all: counters are null, never zero.
    unobserved = _discover(_discovery_index(tmp_path / "b", evidence_factory), evidence_factory)
    row = unobserved["courses"][0]["assignments"][0]
    assert row["ungraded"] is None and row["coverage"] == "unknown" and row["counts_complete"] is False
    # No roster: membership unknown, so counts are unknown even with submissions.
    no_roster = _discover(_discovery_index(tmp_path / "c", evidence_factory, with_roster=False,
                          submissions=[{"pseudonym": "Pikachu"}]), evidence_factory)
    assert no_roster["courses"][0]["assignments"][0]["ungraded"] is None
    assert no_roster["courses"][0]["roster"]["coverage"] == "unknown"


def test_discovery_partial_scopes_report_observed_counts_but_incomplete(tmp_path, evidence_factory):
    partial = _discover(_discovery_index(tmp_path / "p", evidence_factory, submissions_complete=False,
                        submissions=[{"pseudonym": "Pikachu"}]), evidence_factory)
    row = partial["courses"][0]["assignments"][0]
    assert row["ungraded"] == 1 and row["coverage"] == "incomplete" and row["counts_complete"] is False
    short = _discover(_discovery_index(tmp_path / "r", evidence_factory, roster_complete=False,
                      submissions=[{"pseudonym": "Pikachu"}]), evidence_factory)
    assert short["courses"][0]["roster"]["coverage"] == "incomplete"
    assert short["courses"][0]["assignments"][0]["counts_complete"] is False


def test_discovery_excludes_unpublished_and_never_truncates(tmp_path, evidence_factory):
    names = [f"S{i:03d}" for i in range(150)]
    service = _discovery_index(tmp_path, evidence_factory, roster=names,
        assignments=(("10", True), ("11", False)),
        submissions=[{"pseudonym": n} for n in names] + [{"pseudonym": "S000", "assignment_id": "11"}])
    course = _discover(service, evidence_factory)["courses"][0]
    assert [a["assignment_id"] for a in course["assignments"]] == ["10"]
    assert course["assignments"][0]["ungraded"] == 150  # beyond the 50/100 page sizes


def test_discovery_empty_course_list_means_no_courses_and_unknown_course_is_isolated(tmp_path, evidence_factory):
    service = _discovery_index(tmp_path, evidence_factory, submissions=[{"pseudonym": "Pikachu"}])
    assert service.read_scoring_discovery(source_key=evidence_factory["source"], course_ids=[]) == {
        "revision": None, "courses": []}
    result = _discover(service, evidence_factory, courses=("1", "999"))
    healthy, missing = result["courses"]
    assert healthy["assignments"][0]["ungraded"] == 1
    assert missing["roster"]["coverage"] == "unknown" and missing["assignments"] == []
    assert missing["assignments_scope"]["coverage"] == "unknown"


def test_discovery_uses_one_pinned_read_connection_and_no_private_fields(tmp_path, evidence_factory, monkeypatch):
    service = _discovery_index(tmp_path, evidence_factory, submissions=[{"pseudonym": "Pikachu"}])
    opened = []
    real = service.index.read_connection
    monkeypatch.setattr(service.index, "read_connection", lambda: (opened.append(1), real())[1])
    result = _discover(service, evidence_factory)
    assert len(opened) == 1
    assert "Pikachu" not in json.dumps(result) and "Eevee" not in json.dumps(result)


def test_discovery_ambiguous_submission_is_omitted_and_flagged(tmp_path, evidence_factory):
    service = _discovery_index(tmp_path, evidence_factory, submissions=[{"pseudonym": "Pikachu"}])
    # Competing facts for one entity: add a second differing row to the index copy.
    db = sqlite3.connect(service.index.path)
    ref, payload = db.execute("SELECT fact_ref,payload FROM safe_facts WHERE kind='submission'").fetchone()
    db.execute("INSERT INTO safe_facts SELECT 'rival',source_key,course_id,kind,entity_key,assignment_id,"
               "pseudonym,attempt,submitted_at,? FROM safe_facts WHERE fact_ref=?",
               (json.dumps({**json.loads(payload), "workflow_state": "graded"}), ref))
    db.execute("INSERT INTO current_refs SELECT 'rival',scope,scope_id FROM current_refs WHERE fact_ref=?", (ref,))
    db.commit()
    db.close()
    row = _discover(service, evidence_factory)["courses"][0]["assignments"][0]
    assert row["ungraded"] == 0 and row["ambiguous_entities"] == 1 and row["counts_complete"] is False



def test_discovery_query_cost_is_constant_in_assignments_and_students(tmp_path, evidence_factory, monkeypatch):
    """Law: at most eight data SELECTs, however many assignments or students exist."""
    def select_count(directory, students, assignments):
        names = [f"S{i:03d}" for i in range(students)]
        ids = [str(10 + i) for i in range(assignments)]
        service = _discovery_index(directory, evidence_factory, roster=names,
            assignments=tuple((a, True) for a in ids),
            submissions=[{"pseudonym": n, "assignment_id": a} for a in ids for n in names])
        statements = []
        real = service.index.read_connection

        def traced():
            ctx = real()

            class Wrapper:
                def __enter__(self):
                    db = ctx.__enter__()
                    db.set_trace_callback(lambda sql: statements.append(sql))
                    return db

                def __exit__(self, *exc):
                    return ctx.__exit__(*exc)
            return Wrapper()
        monkeypatch.setattr(service.index, "read_connection", traced)
        _discover(service, evidence_factory)
        return len([s for s in statements if s.lstrip().upper().startswith("SELECT")])

    small = select_count(tmp_path / "s", 2, 2)
    large = select_count(tmp_path / "l", 12, 8)
    assert small == large <= 8

