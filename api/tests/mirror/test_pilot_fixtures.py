"""D00 fixtures: the shared synthetic pilot workspace is real, sized and isolated."""
import pytest

from api.mirror.evidence_index import MAX_PAGE_SIZE
from api.mirror.evidence_queries import EvidenceQueryService
from api.mirror.evidence_publish import EvidencePublisher
from api.mirror.evidence_acquisition import publish_course_receipt
from api.tests.mirror.acquisition_samples import SyntheticVault
from api.tests.pilot_samples import (
    PilotVault,
    PILOT_ASSIGNMENTS, PILOT_COURSES, PILOT_SESSIONS, PILOT_STUDENTS,
    PILOT_UNPUBLISHED,
)

COUNTED = {"submitted", "pending_review"}


def _rows(evidence, view, course):
    """Every record of a view for one course, through the real paged read."""
    service = EvidenceQueryService(evidence.index.path)
    records, offset = [], 0
    while offset is not None:
        page = service.read(view, source_key=evidence.source, course_id=course,
                            limit=MAX_PAGE_SIZE, offset=offset)
        records.extend(page["records"])
        offset = page.get("next_offset")
    return records


def test_pilot_evidence_has_pilot_scale_for_every_course(pilot_evidence_workspace):
    evidence = pilot_evidence_workspace
    assert evidence.courses == PILOT_COURSES
    for course in PILOT_COURSES:
        assert len(_rows(evidence, "roster", course)) == PILOT_STUDENTS
        assert len(_rows(evidence, "assignment_context", course)) == PILOT_ASSIGNMENTS
        assert len(_rows(evidence, "current_submissions", course)) == PILOT_STUDENTS * PILOT_ASSIGNMENTS


def test_pilot_evidence_matches_the_gradebook_counting_law(pilot_evidence_workspace):
    evidence = pilot_evidence_workspace
    course = PILOT_COURSES[0]
    published = {row["assignment_id"] for row in _rows(evidence, "assignment_context", course)
                 if row["payload"].get("published") is True}
    assert len(published) == PILOT_ASSIGNMENTS - PILOT_UNPUBLISHED
    observed = {}
    for row in _rows(evidence, "current_submissions", course):
        payload = row["payload"]
        if (row["assignment_id"] in published and payload.get("submitted_at")
                and not payload.get("excused") and payload.get("workflow_state") in COUNTED):
            observed[row["assignment_id"]] = observed.get(row["assignment_id"], 0) + 1
    assert observed == {k: v for k, v in evidence.expected_ungraded(course).items() if v}


def test_pilot_evidence_mixes_states_and_keeps_retained_attempts(pilot_evidence_workspace):
    evidence = pilot_evidence_workspace
    course = PILOT_COURSES[1]
    payloads = [row["payload"] for row in _rows(evidence, "current_submissions", course)]
    assert {"graded", "pending_review", "submitted", "unsubmitted"} <= {p["workflow_state"] for p in payloads}
    # The receipt path currently publishes no "excused" fact field (evidence_acquisition
    # _submission copies only body/score/grade/late/missing/workflow_state), so excused
    # rows are indistinguishable here; they stay "unsubmitted" and never count either way.
    assert any(p.get("late") for p in payloads)
    assert any(p.get("score") == 0 for p in payloads) and any(p.get("score") is None for p in payloads)
    assert len(_rows(evidence, "attempt_history", course)) > len(payloads)


def test_pilot_evidence_copy_is_private_and_writable(pilot_evidence_workspace, pilot_evidence_workspace_copy, tmp_path):
    copy = pilot_evidence_workspace_copy
    assert copy.root.is_relative_to(tmp_path) and copy.index.path.is_relative_to(tmp_path)
    assert copy.root != pilot_evidence_workspace.root and copy.revision == pilot_evidence_workspace.revision
    (copy.root / "mutated.txt").write_text("local to this test", encoding="utf-8")
    assert not (pilot_evidence_workspace.root / "mutated.txt").exists()
    page = EvidenceQueryService(copy.index.path).read(
        "roster", source_key=copy.source, course_id=copy.courses[0], limit=5)
    assert page["revision"] == copy.revision


def test_pilot_sessions_are_25_real_records_with_one_large_bundle(pilot_scoring_sessions):
    store, ids = pilot_scoring_sessions["store"], pilot_scoring_sessions["ids"]
    all_ids = ids["actionable"] + ids["superseded"] + ids["terminal"]
    assert len(all_ids) == len(set(all_ids)) == PILOT_SESSIONS
    statuses = {sid: store.load_session(sid)["status"] for sid in all_ids}
    assert {statuses[sid] for sid in ids["superseded"]} == {"superseded"}
    assert {statuses[sid] for sid in ids["terminal"]} <= {"completed", "completed_with_holds"}
    assert all(statuses[sid] not in {"superseded", "completed", "completed_with_holds"}
               for sid in ids["actionable"])
    bundle = pilot_scoring_sessions["root"] / "For AI" / "pilot-large-bundle.json"
    assert bundle.stat().st_size >= 1_000_000
    assert ids["large_bundle"] in ids["actionable"]


@pytest.mark.parametrize("history,students", [(1, 1), (3, 1), (1, 4)])
def test_scale_receipt_sizes_are_explicit(tmp_path, publication_scale_receipt, history, students):
    receipt = publication_scale_receipt(5, history=history, students=students)
    rows = [s for s in receipt.scopes if s.scope == "assignment.submissions"]
    assert len(rows) == 5
    assert all(len(s.rows) == students for s in rows)
    assert all(len(r["submission_history"]) == history for s in rows for r in s.rows)
    publisher = EvidencePublisher(workspace_root=tmp_path, source_key="a" * 64,
                                  course_id="1", vault=PilotVault())
    result = publish_course_receipt(publisher=publisher, receipt=receipt,
                                    writer_key="writer-a", run_id="run-a")
    assert len(result.successful_scopes) == 6 and not result.gaps


def test_publication_meter_spies_on_the_real_scan_and_verifier(tmp_path, publication_scale_receipt,
                                                               publication_meter):
    publisher = EvidencePublisher(workspace_root=tmp_path, source_key="a" * 64,
                                  course_id="1", vault=SyntheticVault())
    publish_course_receipt(publisher=publisher, receipt=publication_scale_receipt(5),
                           writer_key="writer-a", run_id="run-a")
    assert publication_meter.scans == 1 and publication_meter.verifier_calls > 5
