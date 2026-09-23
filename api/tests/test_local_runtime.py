import json

from api import local_runtime, runtime_paths
from api.platform_services import workspace
from api.shared_storage import (
    SharedStoreConflictError,
    append_jsonl,
    compare_and_remove,
    quarantine_conflict,
    scan_conflicts,
)


def test_machine_identity_is_stable_and_contains_host_and_suffix(tmp_path, monkeypatch):
    path = tmp_path / "machine.json"
    monkeypatch.setattr(runtime_paths, "machine_identity_path", lambda: path)
    monkeypatch.setenv("COMPUTERNAME", "test-laptop")

    first = local_runtime.machine_id()
    second = local_runtime.machine_id()

    assert first == second
    assert first.startswith("TEST-LAPTOP-")
    assert len(first.rsplit("-", 1)[1]) == 8
    assert json.loads(path.read_text(encoding="utf-8"))["machine_id"] == first


def test_process_lock_is_exclusive_until_released(tmp_path):
    path = tmp_path / "ce.lock"
    first = local_runtime.ProcessLock(path)
    second = local_runtime.ProcessLock(path)

    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_shared_store_conflict_blocks_append_without_merging(tmp_path):
    root = tmp_path / "workspace"
    store = root / "_Shared" / "kv" / "settings"
    store.mkdir(parents=True)
    canonical = store / "journal.TEST.jsonl"
    conflict = store / "journal.TEST-LAPTOP.jsonl"
    canonical.write_text('{"v":1}\n', encoding="utf-8")
    conflict.write_text('{"v":2}\n', encoding="utf-8")

    found = scan_conflicts(root)
    assert found == [{"path": str(conflict), "canonical": str(canonical)}]

    try:
        append_jsonl(canonical, {"v": 3}, root=root)
    except SharedStoreConflictError as exc:
        assert exc.files == found
    else:
        raise AssertionError("conflicted store accepted an append")
    assert canonical.read_text(encoding="utf-8") == '{"v":1}\n'


def test_compare_removes_only_identical_conflict_and_quarantine_moves_other(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    shared = root / "_Shared" / "vault"
    shared.mkdir(parents=True)
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(root))

    canonical = shared / "seed.v1.json"
    same_copy = shared / "seed.v1-LAPTOP.json"
    canonical.write_bytes(b"same")
    same_copy.write_bytes(b"same")
    assert compare_and_remove(same_copy)["removed"] is True
    assert not same_copy.exists()

    different_copy = shared / "seed.v1-DESKTOP.json"
    different_copy.write_bytes(b"different")
    result = compare_and_remove(different_copy)
    assert result["ok"] is True
    assert result["identical"] is False
    assert different_copy.exists()

    moved = quarantine_conflict(different_copy)
    assert moved["ok"] is True
    assert not different_copy.exists()
    assert (root / "_Shared" / "_conflicts").exists()
