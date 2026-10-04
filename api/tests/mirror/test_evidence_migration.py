"""Preservation, privacy and restart laws for the one-time additive importer."""
import hashlib
import json

import pytest

from api.mirror.evidence_migration import migrate_legacy_evidence
from api.mirror.evidence_publish import EvidencePublisher
from api.mirror.evidence_index import EvidenceIndex
from api.mirror.evidence_paths import local_source_root
from api.mirror.original_archive import recover_original
from api.tests.mirror.legacy_samples import legacy_root, COURSE, ASSIGNMENT, PSEUDONYM, PAYLOAD, PAYLOAD_SHA256


class SyntheticVault:
    def entries(self):
        return [{"canvas_id": "991001", "real_name": "Avery Sample", "pseudonym": PSEUDONYM,
                 "sis_id": "", "nicknames": []}]

    def all_real_identifiers(self):
        return {"Avery Sample"}, {"991001"}

    def get_or_assign(self, raw, real_name=""):
        if str(raw) != "991001":
            raise ValueError("unresolved")
        return PSEUDONYM

    def require_stable(self, raw):
        if str(raw) != "991001":
            raise ValueError("unresolved")


def _run(source, destination, **kwargs):
    return migrate_legacy_evidence(workspace_root=destination,
        source_workspace_root=source, mirror_cache_root=source / "cache" / "Canvas Mirror",
        source_key="a" * 64, vault=SyntheticVault(), **kwargs)


def _snapshot(destination):
    return EvidencePublisher(workspace_root=destination, source_key="a" * 64,
                             course_id=COURSE, vault=SyntheticVault()).store.scan()


def _source_bytes(source):
    return {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}


def test_dry_run_has_no_writes_and_predicts_exact_content_mapping(tmp_path):
    source = legacy_root(tmp_path, "rich")
    destination = tmp_path / "destination"
    before = _source_bytes(source)
    planned = _run(source, destination)
    assert planned.report_path is None
    assert not destination.exists()
    assert not (local_source_root("a" * 64, destination) / "migration").exists()
    applied = _run(source, destination, dry_run=False)
    assert planned.summary["facts"] == applied.summary["facts"]
    for expected, actual in zip(planned.mappings, applied.mappings):
        assert expected["facts"] == actual["facts"]
        assert expected["commits"] == actual["commits"]
    assert _source_bytes(source) == before
    assert recover_original(destination, PAYLOAD_SHA256) == PAYLOAD
    assert applied.summary["activation"] == "inactive"
    assert applied.summary["observed_course_ids"] == [COURSE]
    assert applied.summary["complete"], applied.gaps
    assert applied.summary["semantic_verification"][0]["status"] == "verified"
    assert applied.summary["semantic_verification"][0]["expected_observations"] > 0
    snapshot = _snapshot(destination)
    assert snapshot.commits
    assert all(commit["mode"] == "import" and not commit["membership_complete"]
               and not commit["parents"] for commit in snapshot.commits.values())
    assert all(commit["acquisition_started_at"] == "2026-08-13T10:21:00Z"
               for commit in snapshot.commits.values())
    assert all(not state.membership_complete for state in snapshot.scopes.values())


@pytest.mark.parametrize("phase", ["fact", "archive", "commit", "source", "verification", "report"])
def test_restart_after_each_durable_step_is_idempotent(tmp_path, phase):
    source = legacy_root(tmp_path, "rich")
    destination = tmp_path / "destination"

    def interrupt(current, mapping):
        if current == phase:
            raise RuntimeError("synthetic interruption")

    with pytest.raises(RuntimeError, match="synthetic interruption"):
        _run(source, destination, dry_run=False, on_checkpoint=interrupt)
    first = _run(source, destination, dry_run=False)
    before = _source_bytes(destination)
    second = _run(source, destination, dry_run=False)
    assert first == second
    assert _source_bytes(destination) == before


def test_conflicting_attempt_timestamps_and_bodies_all_survive(tmp_path):
    source = legacy_root(tmp_path, "timestamp_conflict")
    destination = tmp_path / "destination"
    result = _run(source, destination, dry_run=False)
    facts = [f["payload"] for f in _snapshot(destination).facts.values() if f["kind"] == "attempt_observation"]
    first = [f for f in facts if f["attempt"] == 1]
    assert {f["submitted_at"] for f in first} == {"2026-08-12T09:15:00Z", "2026-08-12T09:16:00Z"}
    assert {f["body"] for f in first} == {"First synthetic draft", "Alternate synthetic first draft"}
    history = next(m for m in result.mappings if m["kind"] == "history_manifest")
    assert len(history["observations"]) == 5
    assert all(o["captured_at"] and o["fact_digest"] for o in history["observations"])
    assert len({f["entity_key"] for f in _snapshot(destination).facts.values()
                if f["kind"] == "attempt_observation" and f["payload"]["attempt"] == 1}) == 1


@pytest.mark.parametrize("variant", ["malformed_manifest", "malformed_file"])
def test_corrupt_source_keeps_valid_siblings_and_explicit_gaps(tmp_path, variant):
    source = legacy_root(tmp_path, variant)
    destination = tmp_path / "destination"
    before = _source_bytes(source)
    result = _run(source, destination, dry_run=False)
    assert result.gaps
    assert not result.summary["complete"]
    assert any(f["kind"] == "attempt_observation" for f in _snapshot(destination).facts.values()), result.gaps
    assert _source_bytes(source) == before


def test_two_machine_imports_union_distinct_evidence_without_order_dependence(tmp_path):
    a = legacy_root(tmp_path, "computer_a")
    b = legacy_root(tmp_path, "computer_b")
    one, two = tmp_path / "one", tmp_path / "two"
    _run(a, one, dry_run=False)
    _run(b, one, dry_run=False)
    _run(b, two, dry_run=False)
    _run(a, two, dry_run=False)
    first, second = _snapshot(one), _snapshot(two)
    assert set(first.facts) == set(second.facts)
    assert set(first.commits) == set(second.commits)
    assert {f["payload"]["attempt"] for f in first.facts.values()
            if f["kind"] == "attempt_observation"} == {1, 2, 3, 4}
    preserved_maps = list((local_source_root("a" * 64, one) / "migration" / "sources").glob("*.json"))
    assert len(preserved_maps) == 4
    assert {json.loads(path.read_text())["kind"] for path in preserved_maps} == {"mirror_submissions", "history_manifest"}


def test_new_quiz_sources_stay_inventory_only(tmp_path):
    source = legacy_root(tmp_path, "new_quiz_inventory")
    destination = tmp_path / "destination"
    result = _run(source, destination, dry_run=False)
    assert result.summary["inventory_only"] == 2
    assert all(not m["facts"] and not m["commits"] for m in result.mappings)


def test_missing_local_course_is_pending_and_cannot_complete(tmp_path):
    source = tmp_path / "empty-source"
    source.mkdir()
    destination = tmp_path / "destination"
    result = _run(source, destination, dry_run=False,
                  course_ids=(course for course in [COURSE]))
    assert not result.summary["complete"]
    assert any(g["code"] == "course_missing_local" for g in result.gaps)
    assert not result.mappings


def test_current_vault_rescrubs_text_before_safe_publication(tmp_path):
    source = legacy_root(tmp_path, "rich")
    path = source / "cache" / "Canvas Mirror" / COURSE / "submissions" / f"{ASSIGNMENT}.v1.json"
    document = json.loads(path.read_text())
    document["submissions"][PSEUDONYM]["current"]["body"] = (
        "Avery Sample 991001 https://canvas.example.test/files/1?token=secret C:\\private\\essay.txt")
    path.write_text(json.dumps(document), encoding="utf-8")
    destination = tmp_path / "destination"
    result = _run(source, destination, dry_run=False)
    safe_bytes = b"".join(path.read_bytes() for path in (destination / "CanvasMirror").rglob("*.json"))
    for forbidden in (b"Avery", b"991001", b"token=secret", b"private", b"essay.txt"):
        assert forbidden not in safe_bytes
    assert result.summary["facts"] > 0


def test_ordinary_manifest_originals_are_bounded_and_recoverable(tmp_path):
    source = tmp_path / "source"
    manifest = source / "Student Work" / "Submissions" / f"Synthetic — {COURSE}" / "Assignments" / f"Draft — {ASSIGNMENT}" / "_assignment_evidence_manifest.json"
    manifest.parent.mkdir(parents=True)
    original = manifest.parent / "draft.txt"
    original.write_bytes(PAYLOAD)
    document = {"version": 1, "course_id": COURSE, "assignment_id": ASSIGNMENT,
        "refreshed_at": "2026-08-13T10:21:00Z", "status": "current", "assignment_indicators": {},
        "assignment_name": "Draft", "binary_budget_bytes": 1000, "binary_bytes_reserved": len(PAYLOAD),
        "evidence": [{"kind": "ordinary", "course_id": COURSE, "assignment_id": ASSIGNMENT,
                      "user_id": "991001", "evidence_id": "file-1", "attempt": 1,
                      "relative_path": str(original.relative_to(source))}]}
    manifest.write_text(json.dumps(document), encoding="utf-8")
    destination = tmp_path / "destination"
    result = _run(source, destination, dry_run=False)
    assert result.summary["originals_verified"] == 1
    assert recover_original(destination, hashlib.sha256(PAYLOAD).hexdigest()) == PAYLOAD
    assert not _snapshot(destination).facts


@pytest.mark.parametrize("capture_available", [True, False])
def test_missing_history_envelope_time_uses_observed_capture_or_explicit_gap(tmp_path, capture_available):
    source = legacy_root(tmp_path, "rich")
    history = source / "_System" / "Archive" / "Submission History" / COURSE / ASSIGNMENT / "history.v1.json"
    document = json.loads(history.read_text())
    document["updated_at"] = ""
    if not capture_available:
        for attempt in document["attempts"].values():
            attempt["captured_at"] = ""
            for observation in attempt["observations"]:
                observation["captured_at"] = ""
    history.write_text(json.dumps(document), encoding="utf-8")
    destination = tmp_path / "destination"
    result = _run(source, destination, dry_run=False)
    mapping = next(m for m in result.mappings if m["kind"] == "history_manifest")
    if capture_available:
        assert mapping["commits"]
        assert _snapshot(destination).commits[mapping["commits"][0]]["acquisition_started_at"] == "2026-08-13T10:21:00Z"
    else:
        assert not mapping["commits"]
        assert any(g["code"] == "commit_refused" for g in mapping["gaps"])


def test_semantic_index_mismatch_refuses_complete_without_touching_active_index(tmp_path, monkeypatch):
    source = legacy_root(tmp_path, "rich")
    destination = tmp_path / "destination"
    active = local_source_root("a" * 64, destination) / "query.sqlite3"
    active.parent.mkdir(parents=True)
    active.write_bytes(b"synthetic active index sentinel")
    original = EvidenceIndex.query_page

    def mismatched(self, view, **kwargs):
        page = original(self, view, **kwargs)
        if view == "attempt_history":
            page["records"] = []
            page["next_offset"] = None
        return page

    monkeypatch.setattr(EvidenceIndex, "query_page", mismatched)
    result = _run(source, destination, dry_run=False)
    assert not result.summary["complete"]
    assert any(g["code"] == "semantic_read_mismatch" for g in result.gaps)
    assert active.read_bytes() == b"synthetic active index sentinel"
