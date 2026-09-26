from pathlib import Path
from types import SimpleNamespace

import pytest

from api.operation_ledger.adapters import forge_files
from api.platform_services import workspace


def _canvas_row(file_id, *, name="Guide.pdf", folder="Unit 1", hidden=False, locked=False):
    return {"id": file_id, "display_name": name, "folder_path": folder,
            "size": 17, "updated_at": "2026-09-25T12:00:00Z",
            "hidden": hidden, "locked": locked}


@pytest.mark.parametrize("rows, folder, outcome", [
    ([], None, "attachment_not_found"),
    ([_canvas_row(1)], None, "one"),
    ([_canvas_row(1), _canvas_row(2, folder="Unit 2")], None, "attachment_ambiguous"),
    ([_canvas_row(1, hidden=True)], None, "hidden"),
    ([_canvas_row(1, locked=True)], None, "locked"),
    ([_canvas_row(1, folder="Unit 1"), _canvas_row(2, folder="Unit 2")], "Unit 2", "one"),
])
def test_canvas_file_lookup_contract(monkeypatch, rows, folder, outcome):
    monkeypatch.setattr(forge_files.canvas_client, "canvas_get_all_complete",
                        lambda path, params: (rows, None, True))
    entry = {"canvas_file": "Guide.pdf", "label": "Guide"}
    if folder:
        entry["folder"] = folder
    if outcome in {"attachment_not_found", "attachment_ambiguous"}:
        with pytest.raises(forge_files.AttachmentRefusal) as caught:
            forge_files.resolve_attachments([entry], course_id="42")
        assert caught.value.code == outcome
        assert len(caught.value.candidates) <= 10
        assert all("id" not in candidate for candidate in caught.value.candidates)
        return
    resolved = forge_files.resolve_attachments([entry], course_id="42")
    assert resolved[0]["canvas_file_id"] == ("2" if folder == "Unit 2" else "1")
    if outcome in {"hidden", "locked"}:
        assert resolved[0]["student_visible"] is False


def test_stage_attachment_private_roots_and_reparse_points_are_never_read(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    app_root = tmp_path / "appdata"
    env_file = tmp_path / "api.env"
    config_path = app_root / "config.json"
    monkeypatch.setattr(forge_files.runtime_paths, "workspace_root", lambda: str(workspace_root))
    monkeypatch.setattr(forge_files.runtime_paths, "local_app_dir", lambda: app_root)
    monkeypatch.setattr(forge_files.config_io, "CONFIG_PATH", str(config_path))
    monkeypatch.setattr(workspace, "identity_vault_dir",
                        lambda root=None: str(Path(root) / "_System" / "Identity Vault"))
    monkeypatch.setattr(workspace, "legacy_identity_vault_dir",
                        lambda root=None: str(Path(root) / "_System" / "Identity Vault"))
    private_file = workspace_root / "Student Work" / "private.pdf"
    private_file.parent.mkdir(parents=True)
    private_file.write_bytes(b"private")
    app_private_file = app_root / "private.pdf"
    app_root.mkdir(parents=True)
    app_private_file.write_bytes(b"private app state")
    link = tmp_path / "linked.pdf"
    try:
        link.symlink_to(private_file)
    except OSError:
        link = None

    original_open = Path.open
    private_paths = {private_file.resolve(), app_private_file.resolve()}
    touched = []
    def guarded_open(path, *args, **kwargs):
        try:
            if Path(path).resolve() in private_paths:
                touched.append(str(path))
                raise AssertionError("private source was opened")
        except FileNotFoundError:
            pass
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)

    assert forge_files.stage_attachment(str(private_file))["ok"] is False
    assert forge_files.stage_attachment(str(app_private_file))["ok"] is False
    if link:
        assert forge_files.stage_attachment(str(link))["ok"] is False
    assert not touched

    junction = tmp_path / "junction"
    junction.mkdir()
    junction_file = junction / "private.pdf"
    junction_file.write_bytes(b"private")
    original_lstat = Path.lstat
    def mark_junction(path):
        info = original_lstat(path)
        if Path(path) == junction:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, "lstat", mark_junction)
    assert forge_files.stage_attachment(str(junction_file))["ok"] is False
    assert not touched


def test_stage_attachment_copies_reuses_and_refuses_same_name_different_bytes(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    app_root = tmp_path / "appdata"
    monkeypatch.setattr(forge_files.runtime_paths, "workspace_root", lambda: str(workspace_root))
    monkeypatch.setattr(forge_files.runtime_paths, "local_app_dir", lambda: app_root)
    monkeypatch.setattr(forge_files.config_io, "CONFIG_PATH", str(app_root / "config.json"))
    monkeypatch.setattr(forge_files, "_private_store_roots", lambda: [workspace_root, app_root])
    source_dir = tmp_path / "host-download"
    source_dir.mkdir()
    source = source_dir / "Teacher Guide.pdf"
    source.write_bytes(b"teacher supplied bytes")

    first = forge_files.stage_attachment(str(source))
    assert first == {"ok": True, "file": source.name, "size_bytes": source.stat().st_size}
    destination = workspace_root / "To Review" / "Attachments" / source.name
    assert destination.read_bytes() == b"teacher supplied bytes"
    assert forge_files.stage_attachment(str(source)) == first

    collision_dir = tmp_path / "another-host"
    collision_dir.mkdir()
    collision = collision_dir / source.name
    collision.write_bytes(b"different bytes")
    refused = forge_files.stage_attachment(str(collision))
    assert refused["ok"] is False
    assert "different file" in refused["error"]
