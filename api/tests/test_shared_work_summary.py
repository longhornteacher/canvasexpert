"""Cost and safety laws for the side-effect-free ``read_item_summaries`` read.

Everything is synthetic and confined to ``tmp_path``. Spies wrap the real code
(file opens, blob reads, conflict inventories); none of them replaces the costly
work, so a regression to per-item scans, bundle reads or writes is visible.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import os
from collections import namedtuple
from pathlib import Path

import pytest

from api import local_runtime, runtime_paths
from api import shared_storage, shared_work
from api.shared_storage import SharedStoreConflictError
from api.shared_work import SharedWorkStore, WorkItemError

KIND = "scoring_session"


def _state(session_id, *, course="c1", assignment="a1", status="ready", **extra):
    return {"session_id": session_id, "session_kind": "scoring_assignment",
            "storage_model": "shared_work.v1", "course_id": course,
            "assignment_id": assignment, "status": status,
            "students": [{"user_id": "synthetic-id", "status": "pending"}], **extra}


def _project(state):
    return {"session_id": state.get("session_id"), "status": state.get("status", ""),
            "course_id": state.get("course_id"), "assignment_id": state.get("assignment_id")}


@pytest.fixture
def machine(monkeypatch):
    def use(name):
        monkeypatch.setattr(local_runtime, "machine_id", lambda: name)
    use("LAPTOP-TEST")
    return use


@pytest.fixture
def store(tmp_path, machine):
    return SharedWorkStore(root=tmp_path)


def _dir(tmp_path, work_id):
    return tmp_path / "_Shared" / "work" / work_id


def _read(store, **kwargs):
    kwargs.setdefault("kind", KIND)
    kwargs.setdefault("project", _project)
    return store.read_item_summaries(**kwargs)


def _tree_digest(*roots):
    """Content, size and mtime of every file under the roots."""
    digest = {}
    for root in roots:
        for path in sorted(Path(root).rglob("*")):
            if path.is_file() and "locks" not in path.parts:
                stat = path.stat()
                digest[str(path)] = (hashlib.sha256(path.read_bytes()).hexdigest(),
                                     stat.st_size, stat.st_mtime_ns)
    return digest


Opened = namedtuple("Opened", "text mode size")


@pytest.fixture
def opens(monkeypatch, tmp_path):
    """Record every file opened for reading under the work tree."""
    recorded = []
    real_open = builtins.open
    work_root = str(tmp_path).replace("/", "\\").lower().lstrip("\\?")

    def spy(file, mode="r", *args, **kwargs):
        text = str(file).replace("/", "\\").lower()
        if work_root in text and "_shared" in text:
            try:
                size = os.path.getsize(file)
            except OSError:
                size = -1
            recorded.append(Opened(text, mode, size))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    return recorded


def _blob_reads(opens):
    return [entry for entry in opens if "\\blobs\\" in entry.text and "r" in entry.mode]


@pytest.fixture
def inventory_spy(monkeypatch):
    calls = []
    for module in (shared_work, shared_storage):
        real = module.scan_conflicts

        def wrapped(*args, __real=real, **kwargs):
            calls.append(1)
            return __real(*args, **kwargs)

        monkeypatch.setattr(module, "scan_conflicts", wrapped)
    return calls


# --- Law: no side effects -------------------------------------------------

def test_summary_read_leaves_work_tree_cache_and_leases_byte_for_byte_unchanged(
        store, tmp_path, machine, monkeypatch):
    bundle_path = tmp_path / "For AI" / "bundle.json"
    bundle_path.parent.mkdir(parents=True)
    bundle_path.write_text(json.dumps({"students": [{"pseudonym": "Bulbasaur"}]}),
                           encoding="utf-8")
    store.save_snapshot("held", _state("held", privacy_artifacts={
        "safe_bundle": str(bundle_path), "safe_folder": str(bundle_path.parent)}),
        kind=KIND, course_id="c1", assignment_id="a1")
    # Takeover with a late event that a mutating owner would quarantine.
    store.save_snapshot("taken", _state("taken"), kind=KIND, course_id="c1", assignment_id="a2")
    store.release("taken")
    machine("DESKTOP-TEST")
    desk = SharedWorkStore(root=tmp_path)
    desk.acquire("taken")
    desk.save_snapshot("taken", _state("taken", status="staged"))
    late = {"v": 1, "ts": "2026-01-01T00:00:00Z", "machine": "LAPTOP-TEST",
            "epoch": 1, "seq": 9, "op": "snapshot", "blob_sha256": "0" * 64}
    with open(_dir(tmp_path, "taken") / "events.LAPTOP-TEST.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(late) + "\n")
    machine("LAPTOP-TEST")

    before = _tree_digest(tmp_path, runtime_paths.local_app_dir())
    forbidden = ("load_snapshot", "_materialize_snapshot", "_heartbeat_unlocked", "_write_lease",
                 "_write_blob", "_quarantine_late_events", "acquire", "release", "require_owner",
                 "save_snapshot", "_append_snapshot", "summary")
    for name in forbidden:
        monkeypatch.setattr(SharedWorkStore, name,
                            lambda *a, __n=name, **k: pytest.fail(f"{__n} called"))
    monkeypatch.setattr(shared_work, "atomic_write_json",
                        lambda *a, **k: pytest.fail("write"))
    monkeypatch.setattr(shared_work, "append_jsonl", lambda *a, **k: pytest.fail("append"))

    result = _read(SharedWorkStore(root=tmp_path))

    assert {item["work_id"] for item in result["items"]} == {"held", "taken"}
    taken = next(item for item in result["items"] if item["work_id"] == "taken")
    assert taken["state"]["status"] == "staged"
    assert taken["notices"] == ["late_events_ignored"]
    assert not list(_dir(tmp_path, "taken").glob("*.orphan.jsonl"))
    assert _tree_digest(tmp_path, runtime_paths.local_app_dir()) == before


# --- Law: bundles and old blobs are never read ----------------------------

def test_bundle_blob_is_never_read_and_cost_does_not_scale_with_bundle_or_history(
        store, tmp_path, opens):
    def bundle(size):
        path = tmp_path / "For AI" / f"bundle-{size}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"students": [{"r": "x" * size}]}), encoding="utf-8")
        return {"safe_bundle": str(path), "safe_folder": str(path.parent)}

    store.save_snapshot("small", _state("small", privacy_artifacts=bundle(10)),
                        kind=KIND, course_id="c1", assignment_id="a1")
    store.save_snapshot("big", _state("big", assignment="a2",
                                      privacy_artifacts=bundle(3_000_000)),
                        kind=KIND, course_id="c1", assignment_id="a2")
    for index in range(20):   # old blob history
        store.save_snapshot("big", _state("big", assignment="a2", status=f"s{index}",
                                          privacy_artifacts=bundle(3_000_000)))
    big_blobs = list((_dir(tmp_path, "big") / "blobs").iterdir())
    assert len(big_blobs) >= 21
    opens.clear()

    result = _read(store)

    assert len(result["items"]) == 2
    reads = _blob_reads(opens)
    assert len(reads) == 2   # exactly the latest session blob per item
    assert all(item["attention"] == [] for item in result["items"])
    latest = {item["work_id"]: item["state"]["status"] for item in result["items"]}
    assert latest["big"] == "s19"
    # Session blobs are small JSON; the multi-megabyte bundle blobs stay closed.
    assert all(0 <= entry.size < 100_000 for entry in reads)


# --- Law: scope filter happens before any journal or blob read --------------

def test_other_kinds_and_courses_are_excluded_before_journals_or_blobs(store, tmp_path, opens):
    store.save_snapshot("mine", _state("mine"), kind=KIND, course_id="c1", assignment_id="a1")
    store.save_snapshot("other-course", _state("other-course", course="c2"),
                        kind=KIND, course_id="c2", assignment_id="a1")
    store.save_snapshot("other-kind", _state("other-kind"), kind="something_else",
                        course_id="c1", assignment_id="a1")
    opens.clear()

    result = _read(store, course_ids=["c1"])

    assert [item["work_id"] for item in result["items"]] == ["mine"]
    touched = [entry.text for entry in opens]
    for excluded in ("other-course", "other-kind"):
        paths = [text for text in touched if f"\\{excluded}\\" in text]
        assert all(text.endswith("manifest.json") for text in paths), paths
    assert _read(store, course_ids=[])["items"] == []


# --- One conflict inventory per pass --------------------------------------

def test_one_conflict_inventory_per_pass_regardless_of_item_count(store, inventory_spy):
    for index in range(12):
        store.save_snapshot(f"s{index}", _state(f"s{index}", assignment=f"a{index}"),
                            kind=KIND, course_id="c1", assignment_id=f"a{index}")
    inventory_spy.clear()

    result = _read(store)

    assert len(result["items"]) == 12
    assert len(inventory_spy) == 1


def test_relevant_conflict_refuses_but_out_of_scope_conflict_does_not(store, tmp_path):
    store.save_snapshot("mine", _state("mine"), kind=KIND, course_id="c1", assignment_id="a1")
    store.save_snapshot("theirs", _state("theirs", course="c2"), kind=KIND,
                        course_id="c2", assignment_id="a1")
    (_dir(tmp_path, "theirs") / "events.LAPTOP-TEST-ONEDRIVE.jsonl").write_text("{}\n", encoding="utf-8")

    assert [item["work_id"] for item in _read(store, course_ids=["c1"])["items"]] == ["mine"]
    with pytest.raises(SharedStoreConflictError):
        _read(store, course_ids=["c2"])
    with pytest.raises(SharedStoreConflictError):
        _read(store)


# --- Bounded per-item attention, healthy items still useful ---------------

def test_one_affected_item_among_healthy_ones(store, tmp_path):
    store.save_snapshot("good-1", _state("good-1"), kind=KIND, course_id="c1", assignment_id="a1")
    store.save_snapshot("bad", _state("bad"), kind=KIND, course_id="c1", assignment_id="a2")
    store.save_snapshot("good-2", _state("good-2"), kind=KIND, course_id="c1", assignment_id="a3")
    event = json.loads((_dir(tmp_path, "bad") / "events.LAPTOP-TEST.jsonl")
                       .read_text(encoding="utf-8").splitlines()[0])
    (_dir(tmp_path, "bad") / "blobs" / event["blob_sha256"]).unlink()

    result = {item["work_id"]: item for item in _read(store)["items"]}

    assert result["good-1"]["attention"] == [] and result["good-2"]["attention"] == []
    assert result["good-1"]["state"]["session_id"] == "good-1"
    assert result["bad"]["attention"] == ["snapshot_unavailable"]
    assert result["bad"]["state"] is None


def test_corrupt_latest_blob_digest_mismatch_is_attention_not_fallback(store, tmp_path):
    store.save_snapshot("s", _state("s", status="ready"), kind=KIND, course_id="c1", assignment_id="a1")
    store.save_snapshot("s", _state("s", status="completed"))
    lines = (_dir(tmp_path, "s") / "events.LAPTOP-TEST.jsonl").read_text(encoding="utf-8").splitlines()
    latest = json.loads(lines[-1])["blob_sha256"]
    (_dir(tmp_path, "s") / "blobs" / latest).write_text(json.dumps(_state("s", status="ready")),
                                                         encoding="utf-8")

    item = _read(store)["items"][0]

    assert item["attention"] == ["snapshot_unavailable"] and item["state"] is None


def test_unclassifiable_manifests_are_reported_not_treated_as_out_of_scope(store, tmp_path):
    store.save_snapshot("ok", _state("ok"), kind=KIND, course_id="c1", assignment_id="a1")
    store.save_snapshot("corrupt", _state("corrupt"), kind=KIND, course_id="c1", assignment_id="a2")
    (_dir(tmp_path, "corrupt") / "manifest.json").write_text("{not json", encoding="utf-8")
    partial = _dir(tmp_path, "partial")
    partial.mkdir(parents=True)
    (partial / "events.DESKTOP.jsonl").write_text("", encoding="utf-8")
    (_dir(tmp_path, "empty-dir")).mkdir(parents=True)

    result = _read(store)

    assert [item["work_id"] for item in result["items"]] == ["ok"]
    assert sorted(result["unclassified"]) == ["manifest_missing", "manifest_unreadable"]


# --- Partial sync, takeover, conflicting duplicates, other machine ----------

def test_incomplete_synced_events_report_attention_without_reading_blobs(store, tmp_path, opens):
    store.save_snapshot("s", _state("s"), kind=KIND, course_id="c1", assignment_id="a1")
    store.save_snapshot("s", _state("s", status="staged"))
    path = _dir(tmp_path, "s") / "events.LAPTOP-TEST.jsonl"
    first = path.read_text(encoding="utf-8").splitlines()[0]
    path.write_text(first + "\n", encoding="utf-8")   # second event not synced yet
    opens.clear()

    item = _read(store)["items"][0]

    assert item["attention"] == ["sync_incomplete"] and item["state"] is None
    assert item["work_item"]["sync_progress"] == {"present": 1, "expected": 2, "complete": False}
    assert _blob_reads(opens) == []


def test_late_events_are_filtered_in_memory_not_quarantined(store, tmp_path, machine):
    store.save_snapshot("s", _state("s"), kind=KIND, course_id="c1", assignment_id="a1")
    store.release("s")
    lease_path = _dir(tmp_path, "s") / "lease.LAPTOP-TEST.json"
    lease = json.loads(lease_path.read_text(encoding="utf-8"))
    lease.update(state="held", heartbeat_at="2000-01-01T00:00:00+00:00")
    lease_path.write_text(json.dumps(lease), encoding="utf-8")
    machine("DESKTOP-TEST")
    desk = SharedWorkStore(root=tmp_path)
    desk.acquire("s", confirm_stale=True)
    desk.save_snapshot("s", _state("s", status="finished"))
    late = {"v": 1, "ts": "2026-01-01T00:00:00Z", "machine": "LAPTOP-TEST",
            "epoch": 1, "seq": 2, "op": "snapshot", "blob_sha256": "0" * 64}
    with open(_dir(tmp_path, "s") / "events.LAPTOP-TEST.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(late) + "\n")

    item = _read(desk)["items"][0]

    assert item["attention"] == [] and item["state"]["status"] == "finished"
    assert item["notices"] == ["late_events_ignored"]
    assert item["work_item"]["orphan_event_count"] == 1
    assert not list(_dir(tmp_path, "s").glob("*.orphan.jsonl"))


def test_conflicting_duplicate_sequence_is_attention(store, tmp_path):
    store.save_snapshot("s", _state("s"), kind=KIND, course_id="c1", assignment_id="a1")
    event = json.loads((_dir(tmp_path, "s") / "events.LAPTOP-TEST.jsonl")
                       .read_text(encoding="utf-8").splitlines()[0])
    twin = dict(event, machine="LAPTOP-TEST2", blob_sha256="1" * 64)
    (_dir(tmp_path, "s") / "events.OTHER.jsonl").write_text(json.dumps(twin) + "\n", encoding="utf-8")

    item = _read(store)["items"][0]

    assert item["attention"] == ["sequence_conflict"] and item["state"] is None


def test_another_machines_lease_is_reported_and_left_untouched(store, tmp_path, machine):
    store.save_snapshot("s", _state("s"), kind=KIND, course_id="c1", assignment_id="a1")
    machine("DESKTOP-TEST")
    before = _tree_digest(tmp_path)

    item = _read(SharedWorkStore(root=tmp_path))["items"][0]

    assert item["work_item"]["holder"] == "LAPTOP-TEST"
    assert item["work_item"]["lease_state"] == "held"
    assert item["state"]["session_id"] == "s"
    assert _tree_digest(tmp_path) == before


def test_conflicting_leases_are_attention(store, tmp_path):
    store.save_snapshot("s", _state("s"), kind=KIND, course_id="c1", assignment_id="a1")
    lease = json.loads((_dir(tmp_path, "s") / "lease.LAPTOP-TEST.json").read_text(encoding="utf-8"))
    (_dir(tmp_path, "s") / "lease.DESKTOP-TEST.json").write_text(
        json.dumps(dict(lease, machine="DESKTOP-TEST")), encoding="utf-8")

    assert _read(store)["items"][0]["attention"] == ["lease_conflict"]


# --- Bounded reread -------------------------------------------------------

def test_reread_happens_once_and_only_when_the_journal_visibly_changed(store, tmp_path, monkeypatch):
    store.save_snapshot("s", _state("s"), kind=KIND, course_id="c1", assignment_id="a1")
    path = _dir(tmp_path, "s") / "events.LAPTOP-TEST.jsonl"
    complete = path.read_text(encoding="utf-8")
    real = SharedWorkStore._read_event_files
    calls = []

    def flaky(paths):
        calls.append(1)
        if len(calls) == 1:
            path.write_text(complete, encoding="utf-8")  # sync completes mid-read
            raise WorkItemError("work_item_events_unreadable")
        return real(paths)

    path.write_text(complete[:-5], encoding="utf-8")     # half-synced line
    monkeypatch.setattr(SharedWorkStore, "_read_event_files", staticmethod(flaky))
    item = _read(store)["items"][0]
    assert len(calls) == 2 and item["attention"] == [] and item["state"]["session_id"] == "s"

    # No visible change: a persistent failure is read exactly once.
    calls.clear()
    monkeypatch.setattr(SharedWorkStore, "_read_event_files",
                        staticmethod(lambda paths: calls.append(1) or (_ for _ in ()).throw(
                            WorkItemError("work_item_events_unreadable"))))
    item = _read(store)["items"][0]
    assert len(calls) == 1 and item["attention"] == ["events_unreadable"]


def test_item_that_keeps_changing_is_reread_at_most_once(store, tmp_path, monkeypatch):
    store.save_snapshot("s", _state("s"), kind=KIND, course_id="c1", assignment_id="a1")
    path = _dir(tmp_path, "s") / "events.LAPTOP-TEST.jsonl"
    calls = []

    def always_changing(paths):
        calls.append(1)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n")
        raise WorkItemError("work_item_events_unreadable")

    monkeypatch.setattr(SharedWorkStore, "_read_event_files", staticmethod(always_changing))
    item = _read(store)["items"][0]
    assert len(calls) == 2 and item["attention"] == ["events_unreadable"]


# --- Cost law: work scales with relevant items, not their contents --------

def test_at_most_one_session_blob_read_per_relevant_item(store, tmp_path, opens):
    for index in range(10):
        store.save_snapshot(f"s{index}", _state(f"s{index}", assignment=f"a{index}"),
                            kind=KIND, course_id="c1", assignment_id=f"a{index}")
        store.save_snapshot(f"s{index}", _state(f"s{index}", assignment=f"a{index}", status="staged"))
    opens.clear()

    result = _read(store)

    assert len(result["items"]) == 10
    assert len(_blob_reads(opens)) == 10
    for index in range(10):
        events = [e for e in opens if f"\\s{index}\\events." in e.text]
        assert len(events) == 1   # each journal opened once
