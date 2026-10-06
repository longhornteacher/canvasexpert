"""Discovery's side-effect-free resume lookup over the real work store.

Synthetic sessions are written through the real ``session_store`` into a temp
workspace. The cost spies wrap the real store methods, so they count genuine
loads, blob reads, conflict inventories and lease writes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from api import local_runtime, runtime_paths, shared_storage, shared_work
from api.powergrader import session_store
from api.shared_storage import SharedStoreConflictError
from api.shared_work import SharedWorkStore


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(session_store.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(local_runtime, "machine_id", lambda: "LAPTOP-TEST")
    # A background heartbeat would legitimately rewrite leases mid-test.
    monkeypatch.setattr(shared_work._heartbeat_service, "register", lambda *a, **k: None)
    return session_store


def _session(session_id, *, course="c1", assignment="a1", status="ready",
             created="2026-01-01T08:00:00", generation=None, **extra):
    record = {
        "session_id": session_id, "session_kind": "scoring_assignment",
        "course_id": course, "assignment_id": assignment,
        "assignment_name": "Essay", "created": created, "status": status,
        "mode": "packet", "storage_model": "shared_work.v1",
        "students": [{"user_id": "private-user-id", "status": "approved", "posted": False,
                      "real_name": "Private Name"}],
        **extra,
    }
    if generation is not None:
        record["scope_generation"] = generation
    return record


def _work_dir(tmp_path, session_id):
    return tmp_path / "_Shared" / "work" / session_id


def _ids(result):
    return [row["session_id"] for row in result["summaries"]]


def _tree_digest(*roots):
    digest = {}
    for root in roots:
        for path in sorted(Path(root).rglob("*")):
            if path.is_file() and "locks" not in path.parts:
                stat = path.stat()
                digest[str(path)] = (hashlib.sha256(path.read_bytes()).hexdigest(),
                                     stat.st_size, stat.st_mtime_ns)
    return digest


# --- Ordering parity with _current_summary ---------------------------------

def test_newer_terminal_suppresses_older_actionable_and_superseded_cannot_resume(store):
    store.save_session(_session("old-ready", created="2026-01-01T08:00:00"))
    store.save_session(_session("new-done", status="completed", created="2026-01-02T08:00:00"))
    store.save_session(_session("gen1", assignment="a2", status="superseded", generation=1))
    store.save_session(_session("gen2", assignment="a2", generation=2,
                                created="2026-01-01T08:00:00"))
    store.save_session(_session("only-superseded", assignment="a3", status="superseded"))
    store.save_session(_session("legacy-done", assignment="a4", status="completed",
                                created="2026-01-09T08:00:00"))
    store.save_session(_session("activated", assignment="a4", generation=1))

    result = store.discovery_session_summaries()

    assert sorted(_ids(result)) == ["activated", "gen2"]
    assert result["incomplete"] is False and result["attention"] == []


def test_discovery_rows_match_the_existing_current_actionable_path(store):
    store.activate_scoring_session(_session("first", created="2026-01-01T08:00:00"))
    store.activate_scoring_session(_session("second", created="2026-01-02T08:00:00"))
    store.save_session(_session("c2-ready", course="c2", status="needs_teacher_input"))
    store.save_session(_session("c3-staged", course="c3", status="staged"))

    discovered = store.discovery_session_summaries(course_ids=["c1", "c2"])["summaries"]
    existing = store.current_actionable_sessions(course_ids=["c1", "c2"])

    def normalise(rows):
        return [{**row, "work_item": {k: v for k, v in row["work_item"].items()
                                       if k != "heartbeat_at"}} for row in rows]

    assert normalise(discovered) == normalise(existing)
    assert sorted(row["session_id"] for row in discovered) == ["c2-ready", "second"]
    serialized = json.dumps(discovered)
    assert "private-user-id" not in serialized and "Private Name" not in serialized
    assert "scope_generation" not in serialized


def test_empty_course_list_means_no_courses_and_none_means_all(store):
    store.save_session(_session("s1", course="c1"))

    assert store.discovery_session_summaries(course_ids=[])["summaries"] == []
    assert _ids(store.discovery_session_summaries(course_ids=None)) == ["s1"]


# --- Unknown resume is never an older fallback -----------------------------

def test_corrupt_potentially_newer_record_makes_the_scope_unknown(store, tmp_path):
    store.save_session(_session("old-ready", generation=1))
    store.save_session(_session("newer", generation=2))
    store.save_session(_session("healthy", assignment="a2"))
    events = (_work_dir(tmp_path, "newer") / "events.LAPTOP-TEST.jsonl").read_text(encoding="utf-8")
    digest = json.loads(events.splitlines()[-1])["blob_sha256"]
    (_work_dir(tmp_path, "newer") / "blobs" / digest).unlink()

    result = store.discovery_session_summaries()

    assert _ids(result) == ["healthy"]
    assert result["incomplete"] is True
    assert result["attention"] == [{"code": "snapshot_unavailable", "course_id": "c1",
                                    "assignment_id": "a1", "count": 1}]


def test_unclassifiable_corrupt_manifest_marks_lookup_incomplete_but_keeps_others(store, tmp_path):
    store.save_session(_session("healthy"))
    store.save_session(_session("broken", assignment="a2"))
    (_work_dir(tmp_path, "broken") / "manifest.json").write_text("{", encoding="utf-8")

    result = store.discovery_session_summaries()

    assert _ids(result) == ["healthy"]
    assert result["incomplete"] is True
    assert result["attention"] == [{"code": "manifest_unreadable", "course_id": None,
                                    "assignment_id": None, "count": 1}]


def test_unconfigured_workspace_is_incomplete_not_an_empty_answer(store, monkeypatch):
    monkeypatch.setattr(session_store.workspace, "workspace_root", lambda: "")

    result = store.discovery_session_summaries()

    assert result["summaries"] == [] and result["incomplete"] is True
    assert result["attention"][0]["code"] == "workspace_unavailable"


def test_relevant_conflict_remains_a_refusal(store, tmp_path):
    store.save_session(_session("s1"))
    store.save_session(_session("s2", course="c2"))
    (_work_dir(tmp_path, "s2") / "events.LAPTOP-TEST-ONEDRIVE.jsonl").write_text("{}\n", encoding="utf-8")

    assert _ids(store.discovery_session_summaries(course_ids=["c1"])) == ["s1"]
    with pytest.raises(SharedStoreConflictError):
        store.discovery_session_summaries(course_ids=["c2"])


# --- Cost laws and before/after counters -----------------------------------

class _Counters:
    NAMES = ("load_snapshot", "_read_blob", "_materialize_snapshot", "_heartbeat_unlocked",
             "_write_lease", "_quarantine_late_events", "summary")

    def __init__(self, monkeypatch):
        self.counts = {name: 0 for name in (*self.NAMES, "conflict_inventories",
                                            "bundle_blob_reads")}
        self.bundle_digests: set[str] = set()
        counts = self.counts
        for name in self.NAMES:
            real = getattr(SharedWorkStore, name)

            def wrapped(inner_self, *args, __real=real, __name=name, **kwargs):
                counts[__name] += 1
                if __name == "_read_blob" and args[-1] in self.bundle_digests:
                    counts["bundle_blob_reads"] += 1
                return __real(inner_self, *args, **kwargs)

            monkeypatch.setattr(SharedWorkStore, name, wrapped)
        for module in (shared_work, shared_storage):
            real_scan = module.scan_conflicts

            def scan(*args, __real=real_scan, **kwargs):
                counts["conflict_inventories"] += 1
                return __real(*args, **kwargs)

            monkeypatch.setattr(module, "scan_conflicts", scan)

    def reset(self):
        for key in self.counts:
            self.counts[key] = 0


@pytest.fixture
def pilot_work(store, tmp_path):
    """25 sessions: current, terminal-over-actionable, superseded, one big SAFE bundle."""
    bundle = tmp_path / "For AI" / "bundle.json"
    bundle.parent.mkdir(parents=True)
    bundle.write_text(json.dumps({"students": [{"r": "x" * 2_000_000}]}), encoding="utf-8")
    courses = ("c1", "c2", "c3")
    for index in range(9):          # 9 current actionable scopes, one carries the bundle
        extra = ({"privacy_artifacts": {"safe_bundle": str(bundle),
                                         "safe_folder": str(bundle.parent)}}
                 if index == 0 else {})
        store.save_session(_session(f"cur-{index}", course=courses[index % 3],
                                    assignment=f"a{index}", **extra))
    for index in range(5):          # newer terminal over older actionable
        scope = dict(course=courses[index % 3], assignment=f"t{index}")
        store.save_session(_session(f"old-{index}", **scope))
        store.save_session(_session(f"done-{index}", status="completed",
                                    created="2026-02-01T08:00:00", **scope))
    for index in range(3):          # generation 1 superseded, generation 2 current
        scope = dict(course=courses[index % 3], assignment=f"g{index}")
        store.save_session(_session(f"sup-{index}", status="superseded", generation=1, **scope))
        store.save_session(_session(f"gen-{index}", generation=2, **scope))
    return 25


def test_discovery_cost_before_after_on_a_pilot_sized_fixture(store, pilot_work, tmp_path,
                                                              monkeypatch, capsys):
    counters = _Counters(monkeypatch)
    blobs = list((_work_dir(tmp_path, "cur-0") / "blobs").iterdir())
    counters.bundle_digests.add(max(blobs, key=lambda path: path.stat().st_size).name)
    counters.reset()

    # AFTER: the new summary path, with the whole tree frozen.
    frozen = _tree_digest(tmp_path, runtime_paths.local_app_dir())
    new = store.discovery_session_summaries(course_ids=["c1", "c2", "c3"])
    after = dict(counters.counts)
    assert _tree_digest(tmp_path, runtime_paths.local_app_dir()) == frozen

    # BEFORE: the existing path that discovery used before D02.
    counters.reset()
    old = store.current_actionable_sessions(course_ids=["c1", "c2", "c3"])
    before = dict(counters.counts)

    with capsys.disabled():
        print(f"\nD02 BEFORE (current_actionable_sessions, {pilot_work} sessions): {before}")
        print(f"D02 AFTER  (discovery_session_summaries, {pilot_work} sessions): {after}")

    assert sorted(row["session_id"] for row in new["summaries"]) == sorted(
        row["session_id"] for row in old)
    assert len(new["summaries"]) == 12

    # Cost laws for the new path.
    assert after["load_snapshot"] == 0
    assert after["_materialize_snapshot"] == 0 and after["bundle_blob_reads"] == 0
    assert after["_heartbeat_unlocked"] == 0 and after["_write_lease"] == 0
    assert after["_quarantine_late_events"] == 0 and after["summary"] == 0
    assert after["conflict_inventories"] == 1
    assert after["_read_blob"] == pilot_work   # one session blob per relevant item
    # The old path is what the brief measured: loads, bundle reads, scans, heartbeats.
    assert before["load_snapshot"] == pilot_work
    assert before["bundle_blob_reads"] >= 1
    assert before["_heartbeat_unlocked"] >= pilot_work
    assert before["conflict_inventories"] > 10 * after["conflict_inventories"]


def test_out_of_scope_courses_are_filtered_before_any_blob_read(store, pilot_work, monkeypatch):
    counters = _Counters(monkeypatch)
    counters.reset()

    result = store.discovery_session_summaries(course_ids=["c2"])

    # c2 holds 3 current + 2 terminal pairs + 1 superseded pair = 9 relevant items.
    assert counters.counts["_read_blob"] == 9
    assert counters.counts["conflict_inventories"] == 1
    assert {row["course_id"] for row in result["summaries"]} == {"c2"}
