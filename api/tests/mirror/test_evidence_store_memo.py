"""The in-memory scan memo must never change what a scan returns, only what it costs.

Law: a keyed (memoized) scan equals a key-less full scan, and nothing enters a
snapshot without passing the verifier under the current key. Contract: the
equivalence holds for every kind of course change. Example: cost accounting.
"""
import hashlib
import os
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.mirror import evidence_store
from api.mirror.evidence_schema import canonical_bytes, digest_record
from api.mirror.evidence_store import (
    EvidenceStore, _walk_json, clear_scan_memo, is_validated_scan,
)

SOURCE = "a" * 64


class Policy:
    """Verifier whose refusal list is mutable and whose key changes with it."""

    def __init__(self):
        self.refused = {"Blocked"}
        self.calls = 0
        self.drift_pending = False

    def check(self, record):
        if record.get("payload", {}).get("body") in self.refused:
            raise ValueError("refused")

    def __call__(self, record):
        self.calls += 1
        if self.drift_pending:  # a non-frozen verifier changing under a running scan
            self.drift_pending = False
            self.refused |= {"First", "Second"}
        self.check(record)

    def key(self):
        return hashlib.sha256("|".join(sorted(self.refused)).encode()).hexdigest()


class Course:
    """One course root scanned by a memoized store and by a key-less reference store."""

    def __init__(self, tmp_path, factory, course_id="1"):
        self.root = tmp_path / "safe"
        self.factory = factory
        self.policy = Policy()
        private = tmp_path / "private"
        self.keyed = EvidenceStore(self.root, SOURCE, course_id, verify_safe=self.policy,
                                   private_diagnostics_root=private, verification_key=self.policy.key)
        self.plain = EvidenceStore(self.root, SOURCE, course_id, verify_safe=self.policy.check,
                                   private_diagnostics_root=private)

    def plant(self, record, *, commit=False):
        """Write canonical bytes directly, as a sync provider would (no verifier)."""
        digest = digest_record(record)
        path = self.keyed._path("commits" if commit else "objects", digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_bytes(record))
        return digest

    def commit(self, **kwargs):
        return self.plant(self.factory["commit"](**kwargs), commit=True)

    def seed(self):
        fact = self.factory["fact"]
        key = fact()["entity_key"]
        self.refs = SimpleNamespace(
            key=key,
            f1=self.plant(fact(body="First")),
            f2=self.plant(fact(body="Second", attempt=2)),
            f3=self.plant(fact(body="Blocked", attempt=3)),  # refused by the verifier
        )
        self.refs.c1 = self.commit(refs=[self.refs.f1], members=[key])
        self.refs.c2 = self.commit(refs=[self.refs.f2], members=[key], parents=[self.refs.c1], run_id="run-2")
        self.settle()
        return self

    def files(self):
        return sorted(self.root.rglob("*.json"))

    def settle(self, ago=60, *, everything=False):
        """Backdate recently written files (or all) beyond the racy window."""
        now = time.time_ns()
        stamp = now - ago * 10**9
        for path in self.files():
            if everything or path.stat().st_mtime_ns > now - 30 * 10**9:
                os.utime(path, ns=(stamp, stamp))


@pytest.fixture(autouse=True)
def fresh_memo():
    clear_scan_memo()
    yield
    clear_scan_memo()


@pytest.fixture
def course(tmp_path, evidence_factory):
    return Course(tmp_path, evidence_factory).seed()


@pytest.fixture
def reads(monkeypatch, tmp_path):
    """Count of synced-file ``Path.read_bytes`` calls (private diagnostics excluded)."""
    counter = SimpleNamespace(count=0)
    real = Path.read_bytes

    def counted(self):
        if not self.is_relative_to(tmp_path / "private"):
            counter.count += 1
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", counted)
    return counter


def assert_equivalent(course):
    got, want = course.keyed.scan(), course.plain.scan()
    assert list(got.facts.items()) == list(want.facts.items())
    assert list(got.commits.items()) == list(want.commits.items())
    assert got.issues == want.issues
    assert got.revision == want.revision
    assert is_validated_scan(got)
    return got


# --- Law: a memoized scan equals a full scan --------------------------------------

def _unchanged(c):
    pass


def _added_fact(c):
    c.plant(c.factory["fact"](body="Third", attempt=4))


def _added_commit(c):
    f4 = c.plant(c.factory["fact"](body="Fourth", attempt=4))
    c.commit(refs=[f4], members=[c.refs.key], parents=[c.refs.c2], run_id="run-3")


def _removed_file(c):
    c.keyed._path("objects", c.refs.f1).unlink()


def _rewritten_same_name_same_size(c):
    path = c.keyed._path("objects", c.refs.f2)
    path.write_bytes(path.read_bytes().replace(b"Second", b"Secone"))


def _invalid_json(c):
    path = c.keyed._path("objects", "c" * 64)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"{not json")


def _unsupported_schema_commit(c):
    future = c.factory["commit"](scope_id="30")
    future["schema_version"] = 2
    future["future_field"] = "synthetic"
    c.plant(future, commit=True)


def _graph_invalid_commit(c):  # valid JSON and schema, but it references another scope's fact
    c.commit(refs=[c.refs.f1], members=[c.refs.key], scope_id="20", run_id="bad-graph")


def _key_change_refuses_accepted(c):
    c.policy.refused.add("Second")


def _key_change_accepts_refused(c):
    c.policy.refused.discard("Blocked")


CHANGES = [_unchanged, _added_fact, _added_commit, _removed_file, _rewritten_same_name_same_size,
           _invalid_json, _unsupported_schema_commit, _graph_invalid_commit,
           _key_change_refuses_accepted, _key_change_accepts_refused]


@pytest.mark.parametrize("change", CHANGES, ids=lambda change: change.__name__.strip("_"))
def test_memoized_scan_equals_a_full_scan_after_any_course_change(course, change):
    primed = assert_equivalent(course)
    assert course.refs.f3 not in primed.facts and any(i.code == "invalid_fact" for i in primed.issues)
    change(course)
    course.settle(61)
    assert_equivalent(course)  # the changed pass
    assert_equivalent(course)  # and the pass that is served from the memo


def test_no_record_enters_a_snapshot_unverified_under_the_current_key(course):
    course.keyed.scan()
    files = len(course.files())
    course.policy.calls = 0
    assert course.refs.f2 in course.keyed.scan().facts and course.policy.calls == 0
    for refused, expected in ((True, False), (False, True)):  # the key changes in both directions
        (course.policy.refused.add if refused else course.policy.refused.discard)("Second")
        course.policy.calls = 0
        snapshot = course.keyed.scan()
        assert course.policy.calls == files  # every file reached the verifier again
        assert (course.refs.f2 in snapshot.facts) is expected


def test_a_verifier_that_changes_mid_scan_commits_nothing_to_the_memo(course):
    files = len(course.files())
    course.policy.drift_pending = True
    course.keyed.scan()  # every record is refused: the verifier changed as the scan began
    course.policy.refused -= {"First", "Second"}  # the key is back to the one the scan started with
    course.policy.calls = 0
    snapshot = assert_equivalent(course)  # nothing refused under the drifted key was kept
    assert course.refs.f1 in snapshot.facts and course.policy.calls == files
    course.policy.calls = 0
    course.keyed.scan()
    assert course.policy.calls == 0


def test_an_unusable_key_degrades_to_a_full_scan_without_a_memo(course, reads):
    def broken():
        raise RuntimeError("no key")
    course.keyed.verification_key = broken
    files = len(course.files())
    for _ in range(2):
        reads.count = 0
        snapshot = course.keyed.scan()
        assert reads.count == files  # no memo: every scan reads every file
    assert snapshot.revision == course.plain.scan().revision


# --- Cost ---------------------------------------------------------------------------

def test_repeat_scan_reads_and_verifies_nothing_and_one_new_file_costs_one_each(course, reads):
    first = course.keyed.scan()
    reads.count = course.policy.calls = 0
    again = course.keyed.scan()
    assert (reads.count, course.policy.calls) == (0, 0)
    assert list(again.facts.items()) == list(first.facts.items()) and again.revision == first.revision
    course.plant(course.factory["fact"](body="Third", attempt=4))
    course.settle(61)
    reads.count = course.policy.calls = 0
    assert len(course.keyed.scan().facts) == len(first.facts) + 1
    assert (reads.count, course.policy.calls) == (1, 1)


def test_a_transient_read_error_is_not_remembered(course, monkeypatch):
    course.keyed.scan()
    course.settle(61, everything=True)
    real = Path.read_bytes
    broken = {"on": True}

    def flaky(self):
        if broken["on"] and self.name == f"{course.refs.f1}.json":
            raise PermissionError("locked by a sync provider")
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", flaky)
    assert course.refs.f1 not in course.keyed.scan().facts
    broken["on"] = False
    assert course.refs.f1 in assert_equivalent(course).facts


def test_keyless_store_checks_every_file_every_time(course, reads):
    files = len(course.files())
    for _ in range(2):
        reads.count = 0
        course.plain.scan()
        assert reads.count == files


@pytest.mark.parametrize("age_seconds, trusted", [(0, False), (1, False), (3, True), (60, True)])
def test_only_files_older_than_the_racy_window_are_trusted(course, reads, age_seconds, trusted):
    files = len(course.files())
    course.settle(age_seconds, everything=True)
    course.keyed.scan()
    reads.count = 0
    course.keyed.scan()
    assert reads.count == (0 if trusted else files)


def test_a_racy_file_settles_once_its_mtime_is_old_enough(course, reads):
    course.settle(1, everything=True)
    course.keyed.scan()
    course.settle(60, everything=True)
    reads.count = 0
    course.keyed.scan()  # new mtimes: re-read once, now settled
    assert reads.count == len(course.files())
    reads.count = 0
    course.keyed.scan()
    assert reads.count == 0


def test_a_modified_copy_of_a_sealed_scan_is_not_a_validated_scan(course):
    from dataclasses import replace
    scan = course.keyed.scan()
    assert is_validated_scan(scan) and is_validated_scan(course.plain.scan())
    assert not is_validated_scan(replace(scan, facts={}))
    assert not is_validated_scan(replace(scan))
    with pytest.raises(TypeError):  # no keyword can forge a seal
        replace(scan, sealed=object())
    with pytest.raises(TypeError):
        type(scan)(scan.facts, scan.commits, scan.issues, scan.revision, sealed=object())


def test_snapshots_get_their_own_dicts_over_shared_records(course):
    first = course.keyed.scan()
    first.facts.clear()
    first.commits.clear()
    second = course.keyed.scan()
    assert second.facts and second.commits and second.facts is not first.facts


# --- Links and listing --------------------------------------------------------------

def _junction(link: Path, target: Path):
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if result.returncode:
        pytest.skip("junctions unavailable")


@pytest.mark.skipif(sys.platform != "win32", reason="junction directories are a Windows feature")
def test_files_reached_through_a_junction_always_take_the_full_path(course, reads):
    objects = course.keyed.course_root / "objects"
    copy = objects / "store-copy"
    copy.mkdir()
    (copy / f"{course.refs.f1}.json").write_bytes(course.keyed._path("objects", course.refs.f1).read_bytes())
    course.settle()
    _junction(objects / "jct", copy)
    course.keyed.scan()
    reads.count = 0
    course.keyed.scan()
    assert reads.count == 1  # only the file seen through the junction is read again
    reads.count = 0
    assert_equivalent(course)


def test_the_walk_lists_exactly_what_rglob_lists_in_the_same_order(course):
    objects = course.keyed.course_root / "objects"
    original = course.keyed._path("objects", course.refs.f1)
    original.with_name(f"{course.refs.f1}-provider arbitrary copy.json").write_bytes(b"x")
    (objects / "UPPER.JSON").write_bytes(b"x")
    (objects / "dir.json").mkdir()
    (objects / "dir.json" / "inner.json").write_bytes(b"x")
    (objects / "a-b").mkdir()
    (objects / "a").mkdir()
    (objects / "a" / "b.json").write_bytes(b"x")
    (objects / "a-b" / "c.json").write_bytes(b"x")
    (objects / "ignored.txt").write_bytes(b"x")
    if sys.platform == "win32":
        _junction(objects / "jct", objects / "a")
    walked = _walk_json(objects)
    assert [path for path, _ in walked] == sorted(objects.rglob("*.json"))
    stats = {path.name: stat for path, stat in walked}
    assert stats["dir.json"] is None and stats[f"{course.refs.f1}.json"] is not None
    if sys.platform == "win32":
        assert [stat for path, stat in walked if "jct" in path.parts] == [None]


# --- Snapshot scopes ----------------------------------------------------------------

def test_scopes_reduce_each_scope_once_per_snapshot_and_are_read_only(course, monkeypatch):
    course.commit(scope_id="20", run_id="other-scope")
    snapshot = course.keyed.scan()
    reductions = []
    real = evidence_store.reduce_scope

    def counted(*args, **kwargs):
        reductions.append(args[1:3])
        return real(*args, **kwargs)
    monkeypatch.setattr(evidence_store, "reduce_scope", counted)
    scopes = snapshot.scopes
    assert snapshot.scopes is scopes and snapshot.scopes is scopes
    assert sorted(reductions) == [("assignment.submissions", "10"), ("assignment.submissions", "20")]
    assert len(scopes) == 2 and isinstance(scopes, Mapping)
    key = (SOURCE, "1", "assignment.submissions", "10")
    assert scopes[key].heads == (course.refs.c2,)
    with pytest.raises(TypeError):
        scopes[key] = None


# --- Concurrency --------------------------------------------------------------------

def test_concurrent_scans_of_one_course_agree_and_validate_once(course):
    files = len(course.files())
    barrier = threading.Barrier(2)
    results = []

    def scan():
        barrier.wait()
        results.append(course.keyed.scan())
    threads = [threading.Thread(target=scan) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len(results) == 2
    assert list(results[0].facts.items()) == list(results[1].facts.items())
    assert list(results[0].commits.items()) == list(results[1].commits.items())
    assert results[0].issues == results[1].issues and results[0].revision == results[1].revision
    assert course.policy.calls == files  # the second scan waited and reused the first's work


def test_a_busy_course_blocks_only_its_own_scans(course, tmp_path):
    entered, release = threading.Event(), threading.Event()
    real = course.policy.__call__

    def gated(record):
        entered.set()
        assert release.wait(10)
        return real(record)
    course.keyed.verify_safe = gated
    other = EvidenceStore(course.root, SOURCE, "2", verify_safe=course.policy.check,
                          private_diagnostics_root=tmp_path / "private", verification_key=course.policy.key)
    busy = threading.Thread(target=course.keyed.scan)
    waiting = threading.Thread(target=course.keyed.scan)
    busy.start()
    assert entered.wait(10)
    waiting.start()
    side = threading.Thread(target=other.scan)
    side.start()
    side.join(5)
    waiting.join(0.3)
    assert not side.is_alive()  # a different course is not held up
    assert waiting.is_alive()  # the same course serializes
    release.set()
    busy.join(30)
    waiting.join(30)
    assert not busy.is_alive() and not waiting.is_alive()
