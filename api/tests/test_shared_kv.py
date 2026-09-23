import json

import pytest

from api.shared_kv import SharedKVStore, flatten, unflatten
from api.shared_storage import SharedStoreConflictError


def test_nested_key_flattening_round_trips_dotted_and_escaped_names():
    value = {
        "tier_tags": {"Extend": "Blue", "A.B": "Red", "with\\slash": "Silver"},
        "course_rows": [{"id": "synthetic-course", "name": "Synthetic"}],
    }

    assert unflatten(flatten(value)) == value


def test_settings_migration_drops_key_42_fixture_and_retires_file(tmp_path):
    legacy = tmp_path / "settings.json"
    legacy.write_text(json.dumps({
        "saved_courses": [
            {"id": "42", "name": "Practice - Red/Gold", "nickname": "Practice - Red/Gold"},
            {"id": "synthetic-course", "name": "Synthetic Course", "nickname": "Synthetic"},
        ],
        "sis_grade_bridges": {
            "42": {"family": "Practice - Red/Gold"},
            "synthetic-course": {"family": "Synthetic Family"},
        },
    }), encoding="utf-8")
    store = SharedKVStore("settings", root=tmp_path, legacy_path=legacy)

    migrated = store.read()

    assert [course["id"] for course in migrated["saved_courses"]] == ["synthetic-course"]
    assert "42" not in migrated["sis_grade_bridges"]
    assert not legacy.exists()
    assert list(tmp_path.glob("settings.json.migrated-*"))


def test_shared_journal_merges_nested_keys_and_tombstones(tmp_path):
    store = SharedKVStore("settings", root=tmp_path)
    before = {"tier_tags": {"Extend": "White", "Core": "Red"}}
    store.ensure_initial_snapshot(before)
    store.append_changes(before, {"tier_tags": {"Extend": "Blue"}})

    assert store.read() == {"tier_tags": {"Extend": "Blue"}}


def test_conflict_sibling_blocks_only_its_shared_store_writes(tmp_path):
    store = SharedKVStore("settings", root=tmp_path)
    store.ensure_initial_snapshot({"tier_tags": {"Extend": "Blue"}})
    canonical = store.root / "journal.MACHINE.jsonl"
    conflict = store.root / "journal.MACHINE-TEST.jsonl"
    canonical.write_text("", encoding="utf-8")
    conflict.write_text("", encoding="utf-8")

    with pytest.raises(SharedStoreConflictError):
        store.append_changes({"tier_tags": {"Extend": "Blue"}},
                             {"tier_tags": {"Extend": "Red"}})
