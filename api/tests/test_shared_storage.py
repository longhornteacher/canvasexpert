"""Tests for ``api.shared_storage.scan_conflicts`` (OneDrive conflict-copy scan)."""
import os
import sys
from pathlib import Path

from api import shared_storage
from api.shared_storage import scan_conflicts


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


def _reference_scan(root) -> list[dict]:
    """The pre-optimization algorithm: one ``is_file()`` stat per dash per file."""
    base = shared_storage.shared_root(root)
    if base is None or not os.path.isdir(shared_storage._extended(base)):
        return []
    found: list[dict] = []
    for directory, _, names in os.walk(shared_storage._extended(base)):
        for name in names:
            candidate = Path(directory) / name
            stem, extension = os.path.splitext(name)
            if not extension or "-" not in stem:
                continue
            canonical = None
            split_at = stem.find("-")
            while split_at >= 0:
                possible = Path(directory) / (stem[:split_at] + extension)
                if possible.is_file():
                    canonical = possible
                    break
                split_at = stem.find("-", split_at + 1)
            if canonical is not None:
                found.append({"path": str(candidate), "canonical": str(canonical)})
    return sorted(found, key=lambda item: item["path"].casefold())


def _build_mixed_tree(root: Path) -> None:
    shared = root / "_Shared"
    settings = shared / "kv" / "settings"
    vault = shared / "vault"
    sessions = shared / "sessions" / "2026" / "10"

    # Real conflict copy beside its canonical file.
    _touch(settings / "settings.json")
    _touch(settings / "settings-LAPTOP.json")
    # Multi-dash conflict whose canonical is only found at a later dash.
    _touch(settings / "report-2026.json")
    _touch(settings / "report-2026-LAPTOP.json")
    # Earliest dash wins when several prefixes exist.
    _touch(vault / "a.json")
    _touch(vault / "a-b.json")
    _touch(vault / "a-b-c.json")
    # Timestamp-style names: many dashes, no canonical prefix present.
    for index in range(5):
        _touch(sessions / f"2026-10-06T21-28-1{index}-ab12.json")
    # ...and one timestamp-style conflict copy of an existing file.
    _touch(sessions / "2026-10-06T21-28-10-ab12-DESKTOP.json")
    # A directory is not a canonical file, so this is not a conflict.
    (vault / "folder.json").mkdir(parents=True, exist_ok=True)
    _touch(vault / "folder-LAPTOP.json")
    # Extensionless names never count.
    _touch(vault / "journal")
    _touch(vault / "journal-LAPTOP")
    # Same stem, different extension is not a conflict.
    _touch(vault / "other.txt")
    _touch(vault / "other-LAPTOP.json")
    # Canonical spelled with different case (a conflict only on case-insensitive Windows).
    _touch(shared / "case" / "Mixed.json")
    _touch(shared / "case" / "mixed-LAPTOP.json")


def test_scan_conflicts_matches_per_dash_stat_reference(tmp_path):
    root = tmp_path / "workspace"
    _build_mixed_tree(root)

    result = scan_conflicts(root)

    assert result == _reference_scan(root)
    by_name = {Path(item["path"]).name: Path(item["canonical"]).name for item in result}
    expected = {
        "settings-LAPTOP.json": "settings.json",
        "report-2026-LAPTOP.json": "report-2026.json",
        "a-b.json": "a.json",
        "a-b-c.json": "a.json",
        "2026-10-06T21-28-10-ab12-DESKTOP.json": "2026-10-06T21-28-10-ab12.json",
    }
    if sys.platform == "win32":
        expected["mixed-LAPTOP.json"] = "mixed.json"
    assert by_name == expected


def test_scan_conflicts_without_shared_root_returns_nothing(tmp_path):
    assert scan_conflicts(tmp_path / "missing") == []


def test_scan_conflicts_makes_no_per_candidate_stat_calls(tmp_path, monkeypatch):
    def measure(name: str, count: int) -> tuple[int, int]:
        root = tmp_path / name
        sessions = root / "_Shared" / "sessions"
        for index in range(count):
            _touch(sessions / f"2026-10-06T21-28-{index:04d}-ab12.json")

        stat_calls = {"count": 0, "is_file": 0}
        real_stat, real_is_file = os.stat, Path.is_file

        def counting_stat(*args, **kwargs):
            stat_calls["count"] += 1
            return real_stat(*args, **kwargs)

        def counting_is_file(self, *args, **kwargs):
            stat_calls["is_file"] += 1
            return real_is_file(self, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(os, "stat", counting_stat)
            patch.setattr(Path, "is_file", counting_is_file)
            assert scan_conflicts(root) == []
        return stat_calls["count"], stat_calls["is_file"]

    small_stats, small_is_file = measure("small", 10)
    large_stats, large_is_file = measure("large", 300)

    assert small_is_file == large_is_file == 0
    assert large_stats == small_stats
