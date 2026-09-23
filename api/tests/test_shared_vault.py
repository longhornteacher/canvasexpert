import json
from pathlib import Path

import pytest

from api import feedback_vault, local_runtime, pseudonym_secret
from api.feedback_artifacts import pseudonymize_submissions
from api.shared_vault import PseudonymProvisionalError, SharedVault
from api.shared_storage import compare_and_remove, quarantine_conflict


def _write_legacy_vault(root: Path, entries: dict) -> Path:
    legacy_dir = root / "_System" / "Identity Vault"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    path = legacy_dir / "vault.json"
    path.write_text(json.dumps({
        "schema_version": feedback_vault.SCHEMA_VERSION,
        "by_canvas_id": entries,
        "extra_top_level": "preserved-in-migration-source",
    }), encoding="utf-8")
    return path


def _shared_vault(root: Path, legacy_path=None, *, secret=b"s" * 32) -> SharedVault:
    return SharedVault(
        root / "_Shared" / "vault",
        legacy_vault_path=legacy_path,
        workspace_root=root,
        secret_provider=lambda: secret,
    )


def test_seed_import_preserves_existing_vault_fields_and_retires_source(tmp_path, monkeypatch):
    legacy = _write_legacy_vault(tmp_path, {
        "synthetic-id-1": {
            "pseudonym": feedback_vault._REGISTRY_WORDS[0],
            "real_name": "Synthetic Student",
            "sis_id": "synthetic-sis-1",
            "nicknames": ["Synth"],
            "first_seen": "2026-01-01T00:00:00Z",
            "extra_entry_field": {"kept": True},
        },
    })
    monkeypatch.setattr(pseudonym_secret, "ensure_primary_secret", lambda: b"s" * 32)

    vault = _shared_vault(tmp_path, legacy)

    seed = json.loads((tmp_path / "_Shared" / "vault" / "seed.v1.json").read_text(encoding="utf-8"))
    assert seed["entries"]["synthetic-id-1"]["extra_entry_field"] == {"kept": True}
    assert seed["entries"]["synthetic-id-1"]["real_name"] == "Synthetic Student"
    assert seed["source_metadata"]["extra_top_level"] == "preserved-in-migration-source"
    assert vault.entries()[0]["pseudonym"] == feedback_vault._REGISTRY_WORDS[0]
    assert not legacy.exists()
    assert list(legacy.parent.glob("vault.json.migrated-*"))


def test_assignment_is_deterministic_after_journal_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(local_runtime, "machine_id", lambda: "MACHINE-A")
    monkeypatch.setattr(pseudonym_secret, "ensure_primary_secret", lambda: b"s" * 32)
    first = _shared_vault(tmp_path)
    assigned = first.get_or_assign("synthetic-id-2", "Synthetic Learner")
    first.save()

    monkeypatch.setattr(local_runtime, "machine_id", lambda: "MACHINE-B")
    second = _shared_vault(tmp_path)

    assert second.get_or_assign("synthetic-id-2", "Synthetic Learner") == assigned


def test_cross_machine_assignment_collision_is_provisional_and_refused(tmp_path, monkeypatch):
    first_word = feedback_vault._REGISTRY_WORDS[0]
    legacy = _write_legacy_vault(tmp_path, {
        "synthetic-id-1": {"pseudonym": "", "real_name": "Synthetic One", "sis_id": "", "nicknames": [], "first_seen": ""},
        "synthetic-id-2": {"pseudonym": "", "real_name": "Synthetic Two", "sis_id": "", "nicknames": [], "first_seen": ""},
    })
    monkeypatch.setattr(pseudonym_secret, "ensure_primary_secret", lambda: b"s" * 32)
    _shared_vault(tmp_path, legacy)
    vault_dir = tmp_path / "_Shared" / "vault"
    journal_a = vault_dir / "journal.MACHINE-A.jsonl"
    journal_b = vault_dir / "journal.MACHINE-B.jsonl"
    journal_a.write_text(json.dumps({
        "v": 1, "ts": "2026-01-01T00:00:00Z", "machine": "MACHINE-A",
        "op": "assign", "canvas_user_id": "synthetic-id-1", "pokemon": first_word, "k": 0,
    }) + "\n", encoding="utf-8")
    journal_b.write_text(json.dumps({
        "v": 1, "ts": "2026-01-01T00:00:00Z", "machine": "MACHINE-B",
        "op": "assign", "canvas_user_id": "synthetic-id-2", "pokemon": first_word, "k": 0,
    }) + "\n", encoding="utf-8")

    merged = _shared_vault(tmp_path)

    assert not merged.is_provisional("synthetic-id-1")
    assert merged.is_provisional("synthetic-id-2")
    with pytest.raises(PseudonymProvisionalError, match="pseudonym_provisional"):
        pseudonymize_submissions([{
            "user_id": "synthetic-id-2",
            "assignment": {"id": "assignment-1", "name": "Synthetic Task", "description": "", "points_possible": 10},
            "user": {"name": "Synthetic Two", "sis_user_id": ""},
            "body": "Synthetic response text.",
            "submitted_at": "2026-01-01T00:00:00Z",
        }], merged, "Synthetic Task")


def test_identity_metadata_uses_latest_timestamp_not_filename_order(tmp_path, monkeypatch):
    monkeypatch.setattr(pseudonym_secret, "ensure_primary_secret", lambda: b"s" * 32)
    initial = _shared_vault(tmp_path)
    vault_dir = tmp_path / "_Shared" / "vault"
    (vault_dir / "journal.A.jsonl").write_text(json.dumps({
        "v": 1, "ts": "2026-01-03T00:00:00Z", "machine": "A", "op": "identity",
        "canvas_user_id": "synthetic-id-3", "entry": {"real_name": "New Synthetic Name"},
    }) + "\n", encoding="utf-8")
    (vault_dir / "journal.Z.jsonl").write_text(json.dumps({
        "v": 1, "ts": "2026-01-02T00:00:00Z", "machine": "Z", "op": "identity",
        "canvas_user_id": "synthetic-id-3", "entry": {"real_name": "Earlier Synthetic Name"},
    }) + "\n", encoding="utf-8")

    loaded = _shared_vault(tmp_path)

    assert loaded._by_id["synthetic-id-3"]["real_name"] == "New Synthetic Name"


def test_equal_shared_conflict_can_be_removed_after_compare(tmp_path):
    directory = tmp_path / "_Shared" / "kv" / "settings"
    directory.mkdir(parents=True)
    conflict = directory / "journal.MACHINE-TEST.jsonl"
    (directory / "journal.MACHINE.jsonl").write_bytes(b"same")
    conflict.write_bytes(b"same")

    result = compare_and_remove(conflict, root=tmp_path)

    assert result == {"ok": True, "identical": True, "removed": True}
    assert not conflict.exists()


def test_different_shared_conflict_can_be_quarantined(tmp_path):
    directory = tmp_path / "_Shared" / "kv" / "settings"
    directory.mkdir(parents=True)
    conflict = directory / "journal.MACHINE-TEST.jsonl"
    (directory / "journal.MACHINE.jsonl").write_bytes(b"canonical")
    conflict.write_bytes(b"different")

    result = quarantine_conflict(conflict, root=tmp_path)

    assert result["ok"] is True
    assert result["quarantined"] == conflict.name
    assert not conflict.exists()
    assert list((tmp_path / "_Shared" / "_conflicts").rglob(conflict.name))


def test_recorded_pseudonym_outside_the_registry_stays_permanent(tmp_path, monkeypatch):
    """Law (R1): a pseudonym already in the seed stays valid and owned even if
    the word has since left the registry; the vault must still load, and a new
    student never receives that word."""
    retired = "Zzretiredword"
    assert feedback_vault._canonical_registry_word(retired) is None
    legacy = _write_legacy_vault(tmp_path, {
        "synthetic-id-1": {"pseudonym": retired, "real_name": "Synthetic One",
                           "first_seen": "2026-01-01T00:00:00Z"},
    })
    monkeypatch.setattr(pseudonym_secret, "ensure_primary_secret", lambda: b"s" * 32)

    vault = _shared_vault(tmp_path, legacy)

    assert vault.get_or_assign("synthetic-id-1") == retired
    assert vault.get_or_assign("synthetic-id-2") != retired
