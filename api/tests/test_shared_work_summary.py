"""Cost and safety laws for the side-effect-free ``read_item_summaries`` read.

Everything is synthetic and confined to ``tmp_path``. Spies wrap the real code
(file opens, blob reads, conflict inventories); none of them replaces the costly
work, so a regression to per-item scans, bundle reads or writes is visible.
"""
from __future__ import annotations

import builtins
import copy
import hashlib
import json
import os
import re
import shutil
import threading
import time
from collections import namedtuple
from pathlib import Path

import pytest

from api import local_runtime, runtime_paths
from api import shared_storage, shared_work
from api.powergrader import session_store
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


@pytest.fixture(autouse=True)
def _empty_read_memo():
    """The read memo is process-wide: every test starts and ends with it empty."""
    shared_work.clear_summary_read_memo()
    yield
    shared_work.clear_summary_read_memo()


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


@pytest.fixture
def work_inventory_spy(monkeypatch):
    """Count the work store's own whole-tree inventories (its direct scan_conflicts calls)."""
    calls = []
    real = shared_work.scan_conflicts

    def wrapped(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(shared_work, "scan_conflicts", wrapped)
    return calls


def test_only_the_summary_pass_takes_a_whole_tree_inventory(store, work_inventory_spy):
    """Cost contract: list_items and the raw event read never walk _Shared themselves."""
    for index in range(3):
        store.save_snapshot(f"s{index}", _state(f"s{index}", assignment=f"a{index}"),
                            kind=KIND, course_id="c1", assignment_id=f"a{index}")
    work_inventory_spy.clear()

    assert len(store.list_items(kind=KIND)) == 3
    assert store._raw_events("s0")
    assert work_inventory_spy == []

    assert len(_read(store)["items"]) == 3
    assert work_inventory_spy == [1]


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


# --- Read memo: exact reuse of validated per-file results ------------------
# The memo may only skip a read whose result a fresh read would reproduce. Files
# are backdated so the racy rule (an mtime within 2 s of the pass start is never
# trusted) treats them as settled; files a test leaves fresh must be re-read.

SETTLED = 3600     # seconds: far past the racy window
RECENT = 1800      # a different mtime than SETTLED, so a rewrite changes the signature


def _backdate(directory, seconds, *, only_recent=False):
    now = time.time()
    for path in Path(directory).rglob("*"):
        if path.is_file() and (not only_recent or path.stat().st_mtime > now - 600):
            os.utime(path, (now - seconds, now - seconds))


def _rel(text):
    """An ``opens`` path relative to the work root (spy paths are lower-cased)."""
    return text.split("_shared\\work\\", 1)[1]


def _outcome(read):
    try:
        return "ok", read()
    except SharedStoreConflictError as exc:
        return "conflict", exc.files


class _StoreTree:
    """A synthetic work tree read through ``SharedWorkStore.read_item_summaries``."""

    def __init__(self, root):
        self.root = root
        self.store = SharedWorkStore(root=root)

    def dir(self, work_id):
        return _dir(self.root, work_id)

    def save(self, work_id, *, status="ready", course="c1", assignment="a1"):
        self.store.save_snapshot(
            work_id, _state(work_id, course=course, assignment=assignment, status=status),
            kind=KIND, course_id=course, assignment_id=assignment)

    def read(self):
        return _read(self.store)


class _SessionTree(_StoreTree):
    """The same tree read through ``session_store.discovery_session_summaries``."""

    def __init__(self, root):
        self.root = root

    def save(self, work_id, *, status="ready", course="c1", assignment="a1"):
        session_store.save_session({
            "session_id": work_id, "session_kind": "scoring_assignment",
            "course_id": course, "assignment_id": assignment, "assignment_name": "Essay",
            "created": "2026-01-01T08:00:00", "status": status, "mode": "packet",
            "storage_model": "shared_work.v1",
            "students": [{"user_id": "synthetic-id", "status": "approved", "posted": False}]})

    def read(self):
        return session_store.discovery_session_summaries()


@pytest.fixture
def session_tree(tmp_path, machine, monkeypatch):
    monkeypatch.setattr(session_store.workspace, "workspace_root", lambda: str(tmp_path))
    # A background heartbeat would legitimately rewrite leases mid-test.
    monkeypatch.setattr(shared_work._heartbeat_service, "register", lambda *a, **k: None)
    return _SessionTree(tmp_path)


@pytest.fixture(params=["work_store", "discovery_summaries"])
def tree(request, tmp_path, machine):
    built = (_StoreTree(tmp_path) if request.param == "work_store"
             else request.getfixturevalue("session_tree"))
    built.save("s1", status="ready")
    built.save("s1", status="staged")
    built.save("s2", course="c2", assignment="a2")
    built.save("s3", assignment="a3")
    return built


# Each change rewrites files so that only the mtime (or size) can reveal it.

def _nothing(tree):
    pass


def _event_appended(tree):
    tree.save("s1", status="needs_teacher_input")


def _lease_rewritten(tree):
    path = tree.dir("s1") / "lease.LAPTOP-TEST.json"
    data = path.read_bytes()
    heartbeat = json.loads(data)["heartbeat_at"]
    replacement = "2001-01-01T00:00:00.000000Z"
    assert len(replacement) == len(heartbeat)
    changed = data.replace(heartbeat.encode(), replacement.encode())
    assert changed != data and len(changed) == len(data)
    path.write_bytes(changed)


def _lease_conflict_added(tree):
    lease = json.loads((tree.dir("s1") / "lease.LAPTOP-TEST.json").read_text(encoding="utf-8"))
    (tree.dir("s1") / "lease.DESKTOP-TEST.json").write_text(
        json.dumps(dict(lease, machine="DESKTOP-TEST")), encoding="utf-8")


def _manifest_replaced(tree):
    path = tree.dir("s1") / "manifest.json"
    data = path.read_bytes()
    changed = data.replace(b'"scoring_session"', b'"scoring_sessioX"')
    assert changed != data and len(changed) == len(data)
    path.write_bytes(changed)


def _manifest_corrupted(tree):
    path = tree.dir("s1") / "manifest.json"
    path.write_bytes(b"{" * path.stat().st_size)


def _blob_corrupted(tree):
    lines = (tree.dir("s1") / "events.LAPTOP-TEST.jsonl").read_text(encoding="utf-8").splitlines()
    blob = tree.dir("s1") / "blobs" / json.loads(lines[-1])["blob_sha256"]
    blob.write_bytes(b"x" * blob.stat().st_size)


def _item_added(tree):
    tree.save("s4", assignment="a4")


def _item_removed(tree):
    shutil.rmtree(tree.dir("s3"))


def _conflict_copy_added(tree):
    (tree.dir("s1") / "events.LAPTOP-TEST-ONEDRIVE.jsonl").write_text("{}\n", encoding="utf-8")


_SCENARIOS = [_nothing, _event_appended, _lease_rewritten, _lease_conflict_added,
              _manifest_replaced, _manifest_corrupted, _blob_corrupted, _item_added,
              _item_removed, _conflict_copy_added]


@pytest.mark.parametrize("scenario", _SCENARIOS, ids=lambda fn: fn.__name__.strip("_"))
def test_warm_memo_result_equals_a_cold_read_after_every_change(tree, tmp_path, scenario):
    """Law: the memo never changes a result, including refusals and attention."""
    _backdate(tmp_path / "_Shared", SETTLED)
    before = _outcome(tree.read)          # warms the memo from settled files
    assert before[0] == "ok"
    assert len(shared_work._SUMMARY_MEMO._entries) >= 12   # 3 items x manifest/lease/events/blob

    scenario(tree)
    _backdate(tmp_path / "_Shared", RECENT, only_recent=True)   # settled, but a new signature
    warm = _outcome(tree.read)
    shared_work.clear_summary_read_memo()
    cold = _outcome(tree.read)

    assert warm == cold
    assert (warm == before) == (scenario is _nothing)   # the change was really observed


# --- Cost: settled files are never reopened -------------------------------

@pytest.fixture
def fs_ops(monkeypatch):
    """Count stat and scandir calls under the work tree (file opens come from ``opens``)."""
    counts = {"stat": 0, "scandir": 0}

    def counted(name, real):
        def wrapper(*args, **kwargs):
            target = str(args[0] if args else kwargs.get("path", "")).lower()
            if "_shared" in target:
                counts[name] += 1
            return real(*args, **kwargs)
        return wrapper

    monkeypatch.setattr(os, "stat", counted("stat", os.stat))
    monkeypatch.setattr(os, "scandir", counted("scandir", os.scandir))
    return counts


def test_settled_repeat_call_opens_no_work_file_and_keeps_the_one_inventory(
        store, tmp_path, opens, fs_ops, inventory_spy, capsys):
    items = 20
    for index in range(items):
        store.save_snapshot(f"s{index}", _state(f"s{index}", assignment=f"a{index}"),
                            kind=KIND, course_id="c1", assignment_id=f"a{index}")
        store.save_snapshot(f"s{index}", _state(f"s{index}", assignment=f"a{index}",
                                                status="staged"))
    _backdate(tmp_path / "_Shared", SETTLED)

    def measure():
        opens.clear()
        inventory_spy.clear()
        fs_ops.update(stat=0, scandir=0)
        result = _read(store)
        assert len(inventory_spy) == 1
        # The ``opens`` spy stats every file it records, so those stats are not the read's.
        return result, {"open": len(opens), "stat": fs_ops["stat"] - len(opens),
                        "scandir": fs_ops["scandir"]}

    cold_result, cold = measure()    # empty memo: the cost of every call before the memo
    warm_result, warm = measure()
    with capsys.disabled():
        print(f"\nRESUME REPEAT CALL ({items} items) cold: {cold}  warm: {warm}")

    assert len(cold_result["items"]) == items and warm_result == cold_result
    assert cold["open"] == 4 * items   # manifest + lease + journal + blob per item
    assert warm["open"] == 0           # one stat per blob and one listing per item remain
    assert warm["stat"] <= items + 2 and warm["scandir"] == cold["scandir"]


def test_racy_files_are_reread_and_settled_files_are_not(store, tmp_path, opens):
    for work_id, assignment, age in (("old", "a1", SETTLED), ("racy", "a2", 0.5),
                                     ("settled", "a3", 10)):
        store.save_snapshot(work_id, _state(work_id, assignment=assignment), kind=KIND,
                            course_id="c1", assignment_id=assignment)
        _backdate(_dir(tmp_path, work_id), age)
    first = _read(store)

    opens.clear()
    again = _read(store)

    assert again == first
    reread = sorted(_rel(entry.text) for entry in opens)
    assert [path.split("\\")[0] for path in reread] == ["racy"] * 4   # manifest, lease, journal, blob
    # Once it has aged (a new signature) its next read is memoized as well.
    _backdate(_dir(tmp_path, "racy"), SETTLED)
    assert _read(store) == first
    opens.clear()
    assert _read(store) == first and opens == []


def test_failures_are_never_memoized(store, tmp_path, opens):
    for work_id, assignment in (("good", "a1"), ("bad-blob", "a2"), ("bad-manifest", "a3"),
                                ("bad-events", "a4")):
        store.save_snapshot(work_id, _state(work_id, assignment=assignment), kind=KIND,
                            course_id="c1", assignment_id=assignment)
    lines = (_dir(tmp_path, "bad-blob") / "events.LAPTOP-TEST.jsonl").read_text(
        encoding="utf-8").splitlines()
    digest = json.loads(lines[-1])["blob_sha256"]
    blob = _dir(tmp_path, "bad-blob") / "blobs" / digest
    blob.write_bytes(b"x" * blob.stat().st_size)
    (_dir(tmp_path, "bad-manifest") / "manifest.json").write_bytes(b"{")
    (_dir(tmp_path, "bad-events") / "events.LAPTOP-TEST.jsonl").write_bytes(b"not json\n")
    _backdate(tmp_path / "_Shared", SETTLED)
    first = _read(store)
    assert {item["work_id"]: item["attention"] for item in first["items"]} == {
        "good": [], "bad-blob": ["snapshot_unavailable"], "bad-events": ["events_unreadable"]}
    assert first["unclassified"] == ["manifest_unreadable"]

    opens.clear()
    second = _read(store)

    assert second == first
    # Healthy files are served from the memo; only the failing reads run again.
    assert sorted(_rel(entry.text) for entry in opens) == sorted([
        "bad-manifest\\manifest.json", f"bad-blob\\blobs\\{digest}",
        "bad-events\\events.laptop-test.jsonl"])


def test_mutating_owner_reads_never_use_the_memo(store, tmp_path, opens):
    for index in range(3):
        store.save_snapshot(f"s{index}", _state(f"s{index}", assignment=f"a{index}"),
                            kind=KIND, course_id="c1", assignment_id=f"a{index}")
    _backdate(tmp_path / "_Shared", SETTLED)
    _read(store)    # memo is warm for every file below
    opens.clear()

    store.summary("s0")
    assert store._raw_events("s0")

    opened = {_rel(entry.text).split("\\", 1)[1].split(".")[0] for entry in opens}
    assert {"manifest", "events", "lease"} <= opened


# --- Shared results: callers cannot corrupt the memo -----------------------

def _scribble(result):
    result["unclassified"].append("scribble")
    for item in result["items"]:
        item["attention"].append("scribble")
        item["notices"].append("scribble")
        item["assignment_id"] = "scribble"
        item["work_item"]["holder"] = "scribble"
        item["work_item"]["sync_progress"]["present"] = -1
        item["state"]["status"] = "scribble"
        item["state"]["students"][0]["status"] = "scribble"
        item["state"]["students"].append({"scribble": 1})


def test_mutating_a_returned_summary_cannot_corrupt_the_memo(store, tmp_path):
    for index in range(3):
        store.save_snapshot(f"s{index}", _state(f"s{index}", assignment=f"a{index}"),
                            kind=KIND, course_id="c1", assignment_id=f"a{index}")
    _backdate(tmp_path / "_Shared", SETTLED)
    identity = lambda state: state     # hands callers the validated blob's own nested objects
    first = _read(store, project=identity)
    pristine = copy.deepcopy(first)
    _scribble(first)

    second = _read(store, project=identity)   # served from the memo
    assert second == pristine
    _scribble(second)
    third = _read(store, project=identity)
    shared_work.clear_summary_read_memo()

    assert third == pristine == _read(store, project=identity)


def test_mutating_discovery_rows_cannot_corrupt_the_memo(session_tree, tmp_path):
    session_tree.save("s1", assignment="a1")
    session_tree.save("s2", assignment="a2", course="c2")
    _backdate(tmp_path / "_Shared", SETTLED)
    first = session_tree.read()
    pristine = copy.deepcopy(first)
    assert len(first["summaries"]) == 2
    for row in first["summaries"]:
        row["status"] = "scribble"
        row["work_item"]["holder"] = "scribble"
        row["work_item"]["sync_progress"]["present"] = -1
    first["attention"].append({"code": "scribble"})

    second = session_tree.read()
    shared_work.clear_summary_read_memo()

    assert second == pristine == session_tree.read()


# --- Memo bounds and concurrency ------------------------------------------

def test_memo_is_bounded_least_recently_used_and_signature_exact(monkeypatch):
    monkeypatch.setattr(shared_work, "_MEMO_MAX_ENTRIES", 3)
    memo = shared_work._SummaryReadMemo()
    for index in range(3):
        memo.remember(f"k{index}", (1, index), "{}")
    assert memo.get("k0", (1, 0)) == "{}"          # k0 is now the most recent
    memo.remember("k3", (1, 3), "{}")                    # evicts k1, the least recently used
    assert memo.get("k1", (1, 1)) is None
    assert [memo.get(f"k{i}", (1, i)) for i in (0, 2, 3)] == ["{}"] * 3
    assert memo.get("k0", (1, 1)) is None and memo.get("k0", (2, 0)) is None

    monkeypatch.setattr(shared_work, "_MEMO_MAX_BYTES", 40)
    memo.remember("big", (1, 1), "x" * 11)               # larger than a quarter of the budget
    assert memo.get("big", (1, 1)) is None
    memo.remember("small", (1, 1), "x" * 10)
    assert memo.get("small", (1, 1)) == "x" * 10


def test_concurrent_summary_reads_agree_with_a_cold_read(store, tmp_path):
    for index in range(6):
        store.save_snapshot(f"s{index}", _state(f"s{index}", assignment=f"a{index}"),
                            kind=KIND, course_id="c1", assignment_id=f"a{index}")
    _backdate(tmp_path / "_Shared", SETTLED)
    expected = _read(store)
    shared_work.clear_summary_read_memo()
    results, errors = [], []

    def worker():
        try:
            for _ in range(5):
                results.append(_read(store))
        except Exception as exc:   # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == [] and len(results) == 40
    assert all(result == expected for result in results)
