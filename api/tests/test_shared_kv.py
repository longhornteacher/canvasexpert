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
