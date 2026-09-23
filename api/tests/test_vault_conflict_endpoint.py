import json
import os

import pytest

from api.platform_services import workspace
from api import pseudonym_secret
from api.shared_kv import SharedKVStore
from api.shared_storage import LegacyStorageReappearedError
from api.shared_vault import SharedVault
from api.webui.routes import names


def _mount(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    vault_dir = workspace.identity_vault_dir()
    os.makedirs(vault_dir, exist_ok=True)
    return vault_dir


def test_no_conflict_returns_empty_file_list(tmp_path, monkeypatch):
    vault_dir = _mount(tmp_path, monkeypatch)
    with open(os.path.join(vault_dir, "vault.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"schema_version": 3, "by_canvas_id": {}}))

    response = names.vault_conflict()
    body = json.loads(response.body)

    assert body == {"ok": True, "folder": str(tmp_path / "_Shared"), "files": [],
                    "safety_blocked": False, "safety_reasons": []}


def test_conflict_files_report_name_size_and_mtime_not_contents(tmp_path, monkeypatch):
    vault_dir = _mount(tmp_path, monkeypatch)
    with open(os.path.join(vault_dir, "vault.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"schema_version": 3, "by_canvas_id": {}}))
    conflict_path = os.path.join(vault_dir, "vault-OTHERPC.json")
    with open(conflict_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"by_canvas_id": {"999": {"real_name": "Someone Real", "pseudonym": "Pikachu"}}}))

    response = names.vault_conflict()
    body = json.loads(response.body)

    assert body["ok"] is True
    assert body["folder"] == str(tmp_path / "_Shared")
    assert len(body["files"]) == 1
    entry = body["files"][0]
    assert entry["name"] == "vault-OTHERPC.json"
    assert entry["size_bytes"] == os.path.getsize(conflict_path)
    assert entry["modified_at"]
    dumped = json.dumps(body)
    assert "Someone Real" not in dumped
    assert "Pikachu" not in dumped


@pytest.mark.parametrize("kind", ["vault", "settings"])
def test_reappeared_legacy_storage_fails_closed_and_marks_privacy_card(
        kind, tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    if kind == "vault":
        monkeypatch.setattr(pseudonym_secret, "ensure_primary_secret", lambda: b"s" * 32)
        legacy_dir = tmp_path / "_System" / "Identity Vault"
        legacy_dir.mkdir(parents=True)
        legacy = legacy_dir / "vault.json"
        vault = SharedVault(
            tmp_path / "_Shared" / "vault", legacy_vault_path=legacy,
            workspace_root=tmp_path, secret_provider=lambda: b"s" * 32,
        )
        (legacy_dir / "vault.json.migrated-20260922").write_text(
            "retired", encoding="utf-8")
        read_action = lambda: SharedVault(
            tmp_path / "_Shared" / "vault", legacy_vault_path=legacy,
            workspace_root=tmp_path, secret_provider=lambda: b"s" * 32,
        ).entries()
        write_action = vault.save
    else:
        legacy = tmp_path / "settings.json"
        store = SharedKVStore("settings", root=tmp_path, legacy_path=legacy)
        store.ensure_initial_snapshot({})
        legacy.with_name("settings.json.migrated-20260922").write_text(
            "retired", encoding="utf-8")
        read_action = store.read
        write_action = lambda: store.append_changes({}, {"tier_tags": {"Core": "Red"}})

    original = "malformed private legacy data; must remain untouched"
    legacy.write_text(original, encoding="utf-8")

    with pytest.raises(LegacyStorageReappearedError):
        read_action()
    with pytest.raises(LegacyStorageReappearedError):
        write_action()

    assert legacy.read_text(encoding="utf-8") == original
    status = json.loads(names.vault_conflict().body)
    assert status["safety_blocked"] is True
    assert original not in json.dumps(status)
