import json
import os
from pathlib import Path

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
    import builtins

    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    if kind == "vault":
        monkeypatch.setattr(pseudonym_secret, "ensure_primary_secret", lambda: b"s" * 32)
        legacy = Path(workspace.retired_identity_vault_path(tmp_path))
        legacy_dir = legacy.parent
        legacy_dir.mkdir(parents=True)
        vault = SharedVault(
            tmp_path / "_Shared" / "vault", retired_vault_path=legacy,
            workspace_root=tmp_path, secret_provider=lambda: b"s" * 32,
        )
        vault.entries()
        read_action = lambda: SharedVault(
            tmp_path / "_Shared" / "vault", retired_vault_path=legacy,
            workspace_root=tmp_path, secret_provider=lambda: b"s" * 32,
        ).entries()
        write_action = vault.save
    else:
        legacy = Path(workspace.retired_settings_path(tmp_path))
        store = SharedKVStore("settings", root=tmp_path, retired_path=legacy)
        store.ensure_initial_snapshot({})
        read_action = store.read
        write_action = lambda: store.append_changes({}, {"tier_tags": {"Core": "Red"}})

    original = "malformed private legacy data; must remain untouched"
    legacy.write_text(original, encoding="utf-8")

    target = legacy.resolve()
    opened = []
    real_builtin_open = builtins.open
    real_path_open = Path.open

    def refuse_retired_open(path, *args, **kwargs):
        try:
            candidate = Path(path).resolve()
        except TypeError:
            candidate = None
        if candidate == target:
            opened.append(str(candidate))
            raise AssertionError("retired storage path was opened")
        return real_builtin_open(path, *args, **kwargs)

    def refuse_retired_path_open(path, *args, **kwargs):
        if path.resolve() == target:
            opened.append(str(path))
            raise AssertionError("retired storage path was opened")
        return real_path_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", refuse_retired_open)
    monkeypatch.setattr(Path, "open", refuse_retired_path_open)

    with pytest.raises(LegacyStorageReappearedError):
        read_action()
    with pytest.raises(LegacyStorageReappearedError):
        write_action()

    status = json.loads(names.vault_conflict().body)
    assert status["safety_blocked"] is True
    assert opened == []
    monkeypatch.setattr(builtins, "open", real_builtin_open)
    monkeypatch.setattr(Path, "open", real_path_open)
    assert legacy.read_text(encoding="utf-8") == original
    assert original not in json.dumps(status)
