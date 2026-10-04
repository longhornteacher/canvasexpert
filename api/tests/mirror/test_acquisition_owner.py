from __future__ import annotations

import json

import pytest

from api.mirror import acquisition_owner
from api.mirror.acquisition_owner import AcquisitionOwner

SOURCE = "c" * 64
A = "a" * 32
B = "b" * 32


def test_incumbent_progress_protects_owner_and_release_allows_successor(tmp_path):
    clock = [0.0]
    first = AcquisitionOwner(tmp_path, SOURCE, B, monotonic=lambda: clock[0])
    second = AcquisitionOwner(tmp_path, SOURCE, A, monotonic=lambda: clock[0])
    assert first.tick().is_owner
    assert not second.tick().is_owner
    for tick in range(1, 10):
        clock[0] = tick * 30
        first.tick()
        assert second.tick().owner_writer_key == B
    first.release()
    assert second.tick().is_owner
    assert first.observe().owner_writer_key == A


def test_crash_expires_only_after_local_unchanged_window(tmp_path):
    clock = [0.0]
    first = AcquisitionOwner(tmp_path, SOURCE, A, monotonic=lambda: clock[0])
    first.tick()
    clock[0] = 5000
    second = AcquisitionOwner(tmp_path, SOURCE, B, monotonic=lambda: clock[0])
    assert second.tick().owner_writer_key == A
    clock[0] += 119.99
    assert not second.tick().is_owner
    clock[0] += .01
    assert second.tick().is_owner
    assert first.observe().owner_writer_key == B


def test_simultaneous_claims_converge_deterministically_after_sync(tmp_path):
    first = AcquisitionOwner(tmp_path / "first", SOURCE, A)
    second = AcquisitionOwner(tmp_path / "second", SOURCE, B)
    first.tick()
    second.tick()
    first_bytes = first.presence_path.read_bytes()
    second_bytes = second.presence_path.read_bytes()
    (first.presence_directory / f"{B}.json").write_bytes(second_bytes)
    (second.presence_directory / f"{A}.json").write_bytes(first_bytes)
    assert first.observe().is_owner
    assert second.observe().owner_writer_key == A
    assert len(second.observe().competing_claims) == 2
    first.tick()
    assert second.tick().owner_writer_key == A


def test_resume_and_restart_reobserve_before_expiry(tmp_path):
    clock = [0.0]
    first = AcquisitionOwner(tmp_path, SOURCE, A, monotonic=lambda: clock[0])
    second = AcquisitionOwner(tmp_path, SOURCE, B, monotonic=lambda: clock[0])
    first.tick()
    second.observe()
    clock[0] = 10000
    assert second.reobserve().owner_writer_key == A
    assert not second.tick().is_owner
    restarted = AcquisitionOwner(tmp_path, SOURCE, B, monotonic=lambda: clock[0])
    assert not restarted.tick().is_owner
    clock[0] += 120
    assert restarted.tick().is_owner


def test_old_incarnation_cannot_release_or_heartbeat_new_claim(tmp_path):
    clock = [0.0]
    old = AcquisitionOwner(tmp_path, SOURCE, A, monotonic=lambda: clock[0])
    old.tick()
    new = AcquisitionOwner(tmp_path, SOURCE, A, monotonic=lambda: clock[0])
    assert not new.tick().is_owner
    clock[0] += 120
    assert new.tick().is_owner
    current_bytes = new.presence_path.read_bytes()
    old.tick()
    assert new.presence_path.read_bytes() == current_bytes
    old.release()
    assert new.presence_path.read_bytes() == current_bytes
    assert new.observe().is_owner


def test_delayed_ancestor_release_and_regressed_counter_are_ignored(tmp_path):
    clock = [0.0]
    first = AcquisitionOwner(tmp_path, SOURCE, A, monotonic=lambda: clock[0])
    observer = AcquisitionOwner(tmp_path, SOURCE, B, monotonic=lambda: clock[0])
    first.tick()
    original = json.loads(first.presence_path.read_text())
    observer.observe()
    first.tick()
    observer.observe()
    original["sources"][SOURCE]["released"] = True
    first.presence_path.write_text(json.dumps(original))
    assert observer.observe().owner_writer_key == A
    restarted = AcquisitionOwner(tmp_path, SOURCE, A, monotonic=lambda: clock[0])
    restarted.tick()  # released first claim can be replaced immediately
    assert restarted.observe().is_owner
    observer.observe()
    original["sources"][SOURCE]["heartbeat_counter"] = 99999
    first.presence_path.write_text(json.dumps(original))
    assert observer.observe().owner_incarnation == restarted.incarnation


@pytest.mark.parametrize("wall_clock", ["9999-12-31T23:59:59Z", "0001-01-01T00:00:00Z"])
def test_wall_clock_fields_cannot_pin_owner(tmp_path, wall_clock):
    clock = [0.0]
    first = AcquisitionOwner(tmp_path, SOURCE, A, monotonic=lambda: clock[0])
    second = AcquisitionOwner(tmp_path, SOURCE, B, monotonic=lambda: clock[0])
    first.tick()
    second.observe()
    document = json.loads(first.presence_path.read_text())
    document["sources"][SOURCE]["wall_clock"] = wall_clock
    first.presence_path.write_text(json.dumps(document))
    assert second.tick().state == "repair_required"
    del document["sources"][SOURCE]["wall_clock"]
    first.presence_path.write_text(json.dumps(document))
    clock[0] = 120
    assert second.tick().is_owner


def test_sources_are_independent_and_other_writer_file_is_untouched(tmp_path):
    first = AcquisitionOwner(tmp_path, SOURCE, A)
    other = AcquisitionOwner(tmp_path, SOURCE, B)
    first.tick(["d" * 64])
    other.tick()
    before = first.presence_path.read_bytes()
    different = AcquisitionOwner(tmp_path, "e" * 64, A)
    assert different.tick().is_owner
    assert not other.presence_path.exists()
    document = json.loads(different.presence_path.read_text())
    assert set(document["sources"]) == {SOURCE, "e" * 64}
    assert json.loads(before)["sources"][SOURCE]["advertised_commit_refs"] == ["d" * 64]
    assert not list(first.presence_directory.glob("*.tmp"))


@pytest.mark.parametrize("identity", ["hostname", "../escape", "C:/private", "g" * 32])
def test_reject_nonopaque_identity(tmp_path, identity):
    with pytest.raises(ValueError, match="invalid_owner_identity"):
        AcquisitionOwner(tmp_path, SOURCE, identity)


def test_released_instance_does_not_reclaim(tmp_path):
    first = AcquisitionOwner(tmp_path, SOURCE, A)
    first.tick()
    first.release()
    assert not first.tick().is_owner
    restarted = AcquisitionOwner(tmp_path, SOURCE, A)
    assert restarted.tick().is_owner


def test_invalid_presence_requires_repair_without_being_changed(tmp_path):
    first = AcquisitionOwner(tmp_path, SOURCE, A)
    first.presence_directory.mkdir(parents=True)
    bad = first.presence_directory / f"{B}.json"
    bad.write_text('{"token":"synthetic-invalid"}')
    assert first.tick().state == "repair_required"
    assert not first.presence_path.exists()
    assert bad.read_text() == '{"token":"synthetic-invalid"}'


def test_corrupt_own_presence_never_erases_other_sources(tmp_path):
    first = AcquisitionOwner(tmp_path, SOURCE, A)
    first.presence_directory.mkdir(parents=True)
    first.presence_path.write_text('{"sources": "broken"}')
    assert first.tick().state == "repair_required"
    first.release()
    assert first.presence_path.read_text() == '{"sources": "broken"}'


def test_provider_conflict_copy_requires_repair(tmp_path):
    first = AcquisitionOwner(tmp_path, SOURCE, A)
    first.tick()
    copy = first.presence_directory / f"{A} (conflicted copy).json"
    copy.write_bytes(first.presence_path.read_bytes())
    status = first.tick()
    assert status.state == "repair_required"
    assert status.issues == ("presence_conflict",)
    assert not status.is_owner
    copy.unlink()
    assert first.tick().is_owner


def test_failed_atomic_replace_preserves_last_presence(tmp_path, monkeypatch):
    first = AcquisitionOwner(tmp_path, SOURCE, A)
    first.tick()
    before = first.presence_path.read_bytes()

    def interrupted_replace(*args):
        raise OSError("synthetic_interruption")

    monkeypatch.setattr(acquisition_owner.os, "replace", interrupted_replace)
    with pytest.raises(OSError, match="synthetic_interruption"):
        first.tick(["d" * 64])
    assert first.presence_path.read_bytes() == before
    assert not list(first.presence_directory.glob("*.tmp"))
