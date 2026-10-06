"""Publication cost laws: one whole-course scan per receipt, linear validation.

Spies wrap the real scan and the real privacy verifier; nothing is stubbed out,
so the counts describe the work production publication actually performs.
"""
import time

import pytest

from api.mirror.evidence_acquisition import (
    CourseAcquisitionReceipt, ScopeReceipt, publish_course_receipt,
)
from api.mirror.evidence_publish import EvidencePublisher
from api.mirror.evidence_store import EvidenceStore
from api.tests.mirror.acquisition_samples import SyntheticVault

SOURCE = "a" * 64
SCOPE_COUNTS = (5, 10, 20, 40)


def _receipt(n_scopes):
    scopes = [ScopeReceipt("course.roster", "1", (
        {"id": "991001", "name": "Avery Sample"},
        {"id": "991002", "name": "Morgan Sample"}), True)]
    for index in range(n_scopes):
        aid = str(100 + index)
        scopes.append(ScopeReceipt("assignment.submissions", aid, ({
            "user_id": "991001", "assignment_id": aid, "attempt": 2,
            "submitted_at": "2026-01-02T00:00:00Z", "body": "Synthetic prose.",
            "submission_history": [{"attempt": 1, "submitted_at": "2026-01-01T00:00:00Z",
                                    "body": "Earlier synthetic prose."}]},), True))
    return CourseAcquisitionReceipt("1", "2026-01-04T00:00:00Z", "2026-01-04T00:01:00Z",
                                    tuple(scopes))


class Meter:
    def __init__(self, monkeypatch):
        self.scans = 0
        self.verifier_calls = 0
        real_scan = EvidenceStore.scan
        real_verify = EvidencePublisher.verify_safe

        def scan(store):
            self.scans += 1
            return real_scan(store)

        def verify(publisher, record):
            self.verifier_calls += 1
            return real_verify(publisher, record)

        monkeypatch.setattr(EvidenceStore, "scan", scan)
        monkeypatch.setattr(EvidencePublisher, "verify_safe", verify)

    def reset(self):
        self.scans = self.verifier_calls = 0


def _run(tmp_path, monkeypatch, n_scopes, mode="legacy"):
    """Two identical receipts (distinct run ids): returns per-receipt cost."""
    meter = Meter(monkeypatch)
    vault = SyntheticVault()
    receipt = _receipt(n_scopes)
    costs = []
    for run in ("run-a", "run-b"):
        if mode == "snapshot":
            # Identity work happens once per receipt under the vault transaction;
            # publication then only looks identities up.
            from api.mirror.evidence_acquisition import prepare_receipt_identities
            meter.reset()
            snapshot = prepare_receipt_identities(vault=vault, receipt=receipt, source_key=SOURCE)
            assert meter.scans == 0 and meter.verifier_calls == 0
            publisher = EvidencePublisher(workspace_root=tmp_path, source_key=SOURCE,
                                          course_id="1", vault=snapshot)
        elif run == "run-a":
            publisher = EvidencePublisher(workspace_root=tmp_path, source_key=SOURCE,
                                          course_id="1", vault=vault)
        meter.reset()
        started = time.perf_counter()
        result = publish_course_receipt(publisher=publisher, receipt=receipt,
                                        writer_key="writer-a", run_id=run)
        elapsed = time.perf_counter() - started
        assert len(result.successful_scopes) == n_scopes + 1 and not result.gaps
        costs.append({"scans": meter.scans, "verifier": meter.verifier_calls,
                      "seconds": elapsed, "stats": getattr(result, "stats", {})})
    return costs


@pytest.fixture(scope="module", params=["legacy", "snapshot"])
def measured(request, tmp_path_factory):
    from _pytest.monkeypatch import MonkeyPatch
    results = {}
    for n in SCOPE_COUNTS:
        patch = MonkeyPatch()
        try:
            results[n] = _run(tmp_path_factory.mktemp(f"cost{n}"), patch, n, request.param)
        finally:
            patch.undo()
    print()
    for n, (first, repeat) in results.items():
        print(f"COST mode={request.param} scopes={n} first: scans={first['scans']} verifier={first['verifier']} "
              f"s={first['seconds']:.3f} | repeat: scans={repeat['scans']} "
              f"verifier={repeat['verifier']} s={repeat['seconds']:.3f}")
    return results


def test_whole_course_scans_per_receipt_are_constant(measured):
    scans = {(n, i): costs[i]["scans"] for n, costs in measured.items() for i in (0, 1)}
    assert set(scans.values()) == {1}, scans


@pytest.mark.parametrize("receipt_index", [0, 1])
def test_validation_work_is_linear_in_scope_count(measured, receipt_index):
    verifier = {n: costs[receipt_index]["verifier"] for n, costs in measured.items()}
    sizes = sorted(verifier)
    for smaller, larger in zip(sizes, sizes[1:]):
        # Doubling the scope count may at most double validation (plus the
        # fixed roster overhead); the quadratic baseline exceeded 3x at 20->40.
        assert verifier[larger] <= 2.05 * verifier[smaller], verifier
    # Absolute bound per scope: new records (<=2 checks per fact, 1 per commit)
    # plus retained records scanned once.
    assert verifier[40] <= 40 * 24, verifier


def test_targeted_dependency_checks_are_linear_not_scan_sized(measured):
    checks = {n: [c["stats"].get("dependency_checks", 0) for c in costs]
              for n, costs in measured.items()}
    for n, (first, repeat) in checks.items():
        # Facts the receipt published are stat-checked once each, parents once.
        assert first <= 5 * (n + 1) and repeat <= 5 * (n + 1), checks
    assert checks[40][1] <= 2.2 * checks[20][1]


def test_snapshot_publication_never_reaches_the_vault_at_scale(tmp_path, monkeypatch):
    from api.mirror.evidence_acquisition import prepare_receipt_identities
    vault = SyntheticVault()
    receipt = _receipt(40)
    snapshot = prepare_receipt_identities(vault=vault, receipt=receipt, source_key=SOURCE)
    publisher = EvidencePublisher(workspace_root=tmp_path, source_key=SOURCE, course_id="1", vault=snapshot)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("vault used while publishing evidence")
    for name in ("get_or_assign", "require_stable", "entries", "all_real_identifiers", "save",
                 "remember_identity", "add_nicknames"):
        monkeypatch.setattr(vault, name, forbidden)
    result = publish_course_receipt(publisher=publisher, receipt=receipt, writer_key="writer-a", run_id="run-a")
    assert len(result.successful_scopes) == 41 and not result.gaps


def _call_site_text_assignment(publisher):
    publisher.publish_text_assignment(
        course_title="ELA", assignment={"id": "10", "title": "Draft"},
        roster=[{"id": "991001", "name": "Avery Sample"}],
        submissions=[{"user_id": "991001", "attempt": 1, "body": "Draft text."}],
        roster_complete=True, submissions_complete=True, writer_key="writer-a", run_id="run-a")
    return "assignment.submissions", "10"


def _call_site_receipt(publisher):
    publish_course_receipt(publisher=publisher, receipt=_receipt(3), writer_key="writer-a", run_id="run-a")
    return "assignment.submissions", "101"


def _call_site_status(publisher):
    from types import SimpleNamespace
    from api.mirror.evidence_acquisition import publish_attachment_status
    job = SimpleNamespace(assignment_id="10", pseudonym="Pikachu", attempt=1, attachment_key="f" * 64,
                          media_type="application/pdf", size=1)
    publish_attachment_status(publisher=publisher, job=job, status="unavailable",
                              writer_key="writer-a", run_id="run-a")
    return "assignment.attachments", "10"


def _call_site_capture(publisher):
    from types import SimpleNamespace
    from api.mirror.evidence_acquisition import publish_captured_attachment
    job = SimpleNamespace(assignment_id="10", pseudonym="Pikachu", attempt=1, attachment_key="e" * 64,
                          media_type="application/pdf", size=1)
    publish_captured_attachment(publisher=publisher, job=job, digest="a" * 64,
                                writer_key="writer-a", run_id="run-a")
    return "assignment.attachments", "10"


def _call_site_extraction(publisher):
    from api.mirror.evidence_extraction import publish_extraction
    from api.mirror.extraction.text import extract as extract_text
    publish_extraction(publisher=publisher, assignment_id="10", pseudonym="Pikachu", attempt=1,
                       attachment_key="b" * 64, original_digest="c" * 64,
                       result=extract_text(b"Synthetic words."), writer_key="writer-a", run_id="run-a")
    return "assignment.extractions", "10"


def _call_site_note(publisher):
    from api.mirror.evidence_notes import build_revision, publish_note
    publish_note(publisher=publisher, assignment_id="10", writer_key="writer-a", run_id="run-a",
                 revision=build_revision(note_id="n1", category="summary", text="Note.", revision=1))
    return "assignment.notes", "10"


# Every publish_commit caller keeps working unchanged and publishes a ready scope.
# Extraction and notes still use the standalone path (scan + commit scan = 2);
# the others share one validated scan. None scales with the scope count.
@pytest.mark.parametrize("call_site,max_scans", [
    (_call_site_text_assignment, 1), (_call_site_receipt, 1), (_call_site_status, 1),
    (_call_site_capture, 1), (_call_site_extraction, 2), (_call_site_note, 2),
])
def test_every_commit_publisher_call_site_stays_safe_and_bounded(tmp_path, monkeypatch, call_site, max_scans):
    publisher = EvidencePublisher(workspace_root=tmp_path, source_key=SOURCE, course_id="1",
                                  vault=SyntheticVault())
    meter = Meter(monkeypatch)
    scope, scope_id = call_site(publisher)
    assert 1 <= meter.scans <= max_scans
    monkeypatch.undo()
    state = publisher.store.scan().scopes[(SOURCE, "1", scope, scope_id)]
    assert state.status == "ready" and state.heads
