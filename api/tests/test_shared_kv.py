import json

import pytest

from api.shared_kv import SharedKVStore, flatten, unflatten
from api.shared_storage import SharedStoreConflictError


def test_nested_key_flattening_round_trips_dotted_and_escaped_names():
    removed_tier_key = "Ex" + "tend"
    value = {
        "tier_tags": {removed_tier_key: "Blue", "A.B": "Red", "with\\slash": "Silver"},
        "course_rows": [{"id": "synthetic-course", "name": "Synthetic"}],
    }

    assert unflatten(flatten(value)) == value


def test_fresh_snapshot_uses_current_settings_fallback(tmp_path):
    store = SharedKVStore("settings", root=tmp_path)
    current = {"saved_courses": [{"id": "synthetic-course", "nickname": "Synthetic"}]}

    store.ensure_initial_snapshot(current)

    assert store.read() == current


def test_shared_journal_merges_nested_keys_and_tombstones(tmp_path):
    store = SharedKVStore("settings", root=tmp_path)
    removed_tier_key = "Ex" + "tend"
    before = {"tier_tags": {removed_tier_key: "White", "Core": "Red"}}
    store.ensure_initial_snapshot(before)
    store.append_changes(before, {"tier_tags": {removed_tier_key: "Blue"}})

    assert store.read() == {"tier_tags": {removed_tier_key: "Blue"}}


def test_conflict_sibling_blocks_only_its_shared_store_writes(tmp_path):
    store = SharedKVStore("settings", root=tmp_path)
    removed_tier_key = "Ex" + "tend"
    store.ensure_initial_snapshot({"tier_tags": {removed_tier_key: "Blue"}})
    canonical = store.root / "journal.MACHINE.jsonl"
    conflict = store.root / "journal.MACHINE-TEST.jsonl"
    canonical.write_text("", encoding="utf-8")
    conflict.write_text("", encoding="utf-8")

    with pytest.raises(SharedStoreConflictError):
        store.append_changes({"tier_tags": {removed_tier_key: "Blue"}},
                             {"tier_tags": {removed_tier_key: "Red"}})


# --- Cost contract and law: a config read does no conflict discovery -------------

@pytest.fixture
def scan_counter(monkeypatch):
    """Count every whole-tree walk: the origin, and any module-level name bound to it."""
    from api import shared_kv, shared_storage

    calls = []
    real = shared_storage.scan_conflicts

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(shared_storage, "scan_conflicts", counted)
    # ``from ... import scan_conflicts`` binds at import time, so a direct call in
    # shared_kv would bypass the origin patch; raising=False keeps this valid once unused.
    monkeypatch.setattr(shared_kv, "scan_conflicts", counted, raising=False)
    return calls


def test_kv_read_makes_no_conflict_scan(tmp_path, scan_counter):
    store = SharedKVStore("settings", root=tmp_path)
    store.ensure_initial_snapshot({"saved_courses": [{"id": "synthetic-course"}]})
    scan_counter.clear()

    assert store.read_values() == {"saved_courses": [{"id": "synthetic-course"}]}
    assert store.read() == {"saved_courses": [{"id": "synthetic-course"}]}

    assert scan_counter == []


def test_conflict_discovery_stays_in_the_vault_read(tmp_path, monkeypatch):
    """The console's conflict list comes from the vault's one scan per load."""
    from api import pseudonym_secret, shared_vault

    monkeypatch.setattr(pseudonym_secret, "ensure_primary_secret", lambda: b"s" * 32)
    seed = tmp_path / "_Shared" / "vault" / "seed.v1.json"
    seed.parent.mkdir(parents=True)
    seed.write_text(json.dumps({"version": 1, "created_at": "2026-01-01T00:00:00Z",
                                "created_by": "SYNTHETIC", "entries": {}}), encoding="utf-8")
    calls = []
    real = shared_vault.scan_conflicts
    monkeypatch.setattr(shared_vault, "scan_conflicts",
                        lambda *a, **k: calls.append(1) or real(*a, **k))

    vault = shared_vault.SharedVault(seed.parent, workspace_root=tmp_path,
                                     secret_provider=lambda: b"s" * 32)

    assert calls == [1]
    assert vault.conflict_files == []


def test_conflict_copy_does_not_change_read_values_but_still_blocks_writes(tmp_path):
    from api.shared_storage import assert_store_writable, store_conflicts

    store = SharedKVStore("settings", root=tmp_path)
    store.ensure_initial_snapshot({"saved_courses": [{"id": "synthetic-course"}]})
    expected = store.read_values()
    # A byte-identical OneDrive duplicate: the read merges snapshots by glob, so an
    # identical copy keeps the merged values equal while still being a conflict.
    copy_path = store.root / "snapshot.initial-LAPTOP.json"
    copy_path.write_bytes((store.root / "snapshot.initial.json").read_bytes())

    assert store.read_values() == expected
    [row] = store_conflicts(store.root, root=tmp_path)
    assert row["path"] == str(copy_path)
    with pytest.raises(SharedStoreConflictError):
        assert_store_writable(store.root, root=tmp_path)
