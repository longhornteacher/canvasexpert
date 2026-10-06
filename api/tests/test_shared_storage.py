"""Tests for ``api.shared_storage.scan_conflicts`` (OneDrive conflict-copy scan)."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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


# --- The scandir-based walk must list exactly what ``os.walk`` lists ---------------------------

_LONG_PREFIX = "\\\\?\\"


def _walk_listing(top) -> dict[str, list[str]]:
    """``{directory: sorted file names}`` from ``os.walk``: the reference for the traversal law."""
    return {directory: sorted(names) for directory, _, names in os.walk(top)}


def _our_listing(top) -> dict[str, list[str]]:
    return {directory: sorted(names) for directory, names in shared_storage._walk_file_names(top)}


def _build_nested_tree(root: Path) -> Path:
    """Conflicts at several depths, plus an empty directory."""
    shared = root / "_Shared"
    _touch(shared / "top.json")
    _touch(shared / "top-LAPTOP.json")
    _touch(shared / "a" / "one.json")
    _touch(shared / "a" / "one-LAPTOP.json")
    _touch(shared / "a" / "b" / "c" / "deep.json")
    _touch(shared / "a" / "b" / "c" / "deep-DESKTOP.json")
    _touch(shared / "a" / "b" / "plain.json")
    (shared / "empty").mkdir()
    return shared


def test_walk_lists_exactly_what_os_walk_lists_on_a_nested_tree(tmp_path):
    root = tmp_path / "workspace"
    shared = _build_nested_tree(root)

    ours = _our_listing(shared)

    assert ours == _walk_listing(shared)
    assert len(ours) == 5  # _Shared, a, a/b, a/b/c, empty
    result = scan_conflicts(root)
    assert result == _reference_scan(root)
    assert {Path(item["path"]).name for item in result} == {
        "top-LAPTOP.json", "one-LAPTOP.json", "deep-DESKTOP.json"}


def _make_directory_symlink(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")


def _make_file_symlink(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")


def _make_junction(link: Path, target: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("junction directories are a Windows feature")
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if result.returncode:
        pytest.skip("junctions unavailable")


@pytest.mark.parametrize("make_link", [_make_directory_symlink, _make_file_symlink, _make_junction],
                         ids=lambda make: make.__name__.strip("_"))
def test_walk_treats_links_exactly_as_os_walk_does(tmp_path, make_link):
    root = tmp_path / "workspace"
    shared = _build_nested_tree(root)
    outside = tmp_path / "outside"
    _touch(outside / "linked.json")
    _touch(outside / "linked-LAPTOP.json")
    if make_link is _make_file_symlink:
        link = shared / "a" / "one-LINK.json"
        make_link(link, shared / "a" / "one.json")
    else:
        link = shared / "a" / "linked"
        make_link(link, outside)

    ours = _our_listing(shared)
    assert ours == _walk_listing(shared)
    assert scan_conflicts(root) == _reference_scan(root)

    reached = {Path(item["path"]).name for item in scan_conflicts(root)}
    if make_link is _make_directory_symlink:
        # A directory symlink is a directory (not a file name) and is never descended.
        assert str(link) not in ours and "linked" not in ours[str(shared / "a")]
        assert "linked-LAPTOP.json" not in reached
    elif make_link is _make_junction:
        # os.path.islink is False for a junction, so os.walk (and this walk) descend it.
        assert str(link) in ours and "linked-LAPTOP.json" in reached
    else:
        assert "one-LINK.json" in ours[str(shared / "a")] and "one-LINK.json" in reached


def test_walk_lists_a_dangling_file_symlink_like_os_walk(tmp_path):
    root = tmp_path / "workspace"
    shared = _build_nested_tree(root)
    _make_file_symlink(shared / "a" / "dangling.json", tmp_path / "does-not-exist.json")

    ours = _our_listing(shared)
    assert ours == _walk_listing(shared)
    assert "dangling.json" in ours[str(shared / "a")]


class _FlakyListing:
    """A real ``scandir`` iterator whose listing fails with ``OSError`` after ``keep`` entries."""

    def __init__(self, real, keep):
        self._real, self._left = real, keep

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self._real.close()

    def __iter__(self):
        return self

    def __next__(self):
        if self._left <= 0:
            raise PermissionError("listing failed part-way")
        self._left -= 1
        return next(self._real)


class _OpaqueEntry:
    """A ``DirEntry`` whose type cannot be read: ``is_dir`` raises ``OSError``."""

    def __init__(self, entry):
        self._entry = entry
        self.name, self.path = entry.name, entry.path

    def is_dir(self, *args, **kwargs):
        raise PermissionError("type unreadable")

    def is_symlink(self):
        return self._entry.is_symlink()


class _OpaqueListing(_FlakyListing):
    def __init__(self, real, opaque):
        super().__init__(real, keep=1 << 30)
        self._opaque = opaque

    def __next__(self):
        entry = next(self._real)
        return _OpaqueEntry(entry) if entry.name in self._opaque else entry


def _fault_injecting_scandir(real_scandir, *, denied, partial, opaque):
    def scandir(path="."):
        name = Path(os.fspath(path)).name
        if name in denied:
            raise PermissionError("directory unreadable")
        if name in partial:
            return _FlakyListing(real_scandir(path), keep=1)
        return _OpaqueListing(real_scandir(path), opaque)

    return scandir


def test_walk_skips_failing_directories_and_is_dir_errors_like_os_walk(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    shared = _build_nested_tree(root)
    # An unreadable directory (scandir itself fails), with a subtree that must never be reached.
    _touch(shared / "denied" / "x.json")
    _touch(shared / "denied" / "x-LAPTOP.json")
    _touch(shared / "denied" / "below" / "y.json")
    # A listing that fails part-way: the whole directory and its subtree are skipped.
    _touch(shared / "partial" / "p.json")
    _touch(shared / "partial" / "p-LAPTOP.json")
    _touch(shared / "partial" / "below" / "q.json")
    # A directory whose type cannot be read counts as a plain name and is not descended.
    _touch(shared / "opaque" / "z.json")
    _touch(shared / "opaque" / "z-LAPTOP.json")

    with monkeypatch.context() as patch:
        patch.setattr(os, "scandir", _fault_injecting_scandir(
            os.scandir, denied={"denied"}, partial={"partial"}, opaque={"opaque"}))
        ours = _our_listing(shared)
        reference = _walk_listing(shared)
        result = scan_conflicts(root)
        expected = _reference_scan(root)

    assert ours == reference
    assert result == expected
    skipped = tuple(str(shared / name) for name in ("denied", "partial", "opaque"))
    assert not any(directory.startswith(skipped) for directory in ours)
    assert "opaque" in ours[str(shared)]  # listed as a file name, never descended
    assert {Path(item["path"]).name for item in result} == {
        "top-LAPTOP.json", "one-LAPTOP.json", "deep-DESKTOP.json"}


@pytest.mark.skipif(sys.platform != "win32", reason="the long-path prefix is a Windows feature")
def test_walk_keeps_the_long_path_prefix_on_yielded_directories(tmp_path):
    # A base at or past MAX_PATH_LENGTH makes ``_extended`` return the extended form; the walk must
    # keep that form on every directory it yields, as ``os.walk`` does.
    long_root = tmp_path / ("L" * 100) / ("M" * 100) / ("N" * 60)
    shared = long_root / "_Shared"
    assert len(str(shared)) >= shared_storage.workspace.MAX_PATH_LENGTH
    top = shared_storage._extended(shared)
    assert top.startswith(_LONG_PREFIX)
    try:
        os.makedirs(os.path.join(top, "a", "b"))
        for relative in ("one.json", "one-LAPTOP.json", os.path.join("a", "b", "deep.json"),
                         os.path.join("a", "b", "deep-DESKTOP.json")):
            with open(os.path.join(top, relative), "w", encoding="utf-8") as handle:
                handle.write("x")
        ours = _our_listing(top)
        assert ours == _walk_listing(top)
        assert len(ours) == 3 and all(directory.startswith(_LONG_PREFIX) for directory in ours)
        result = scan_conflicts(long_root)
        assert result == _reference_scan(long_root)
        assert {Path(item["path"]).name for item in result} == {"one-LAPTOP.json", "deep-DESKTOP.json"}
    finally:
        shutil.rmtree(shared_storage._extended(tmp_path / ("L" * 100)), ignore_errors=True)


def test_walk_makes_no_per_directory_link_or_stat_calls(tmp_path, monkeypatch):
    def measure(name: str, directories: int) -> dict[str, int]:
        root = tmp_path / name
        shared = root / "_Shared"
        for index in range(directories):
            _touch(shared / f"d{index:04d}" / "inner" / f"s-{index:04d}.json")
        calls = {"islink": 0, "lstat": 0, "stat": 0}
        real = {"islink": os.path.islink, "lstat": os.lstat, "stat": os.stat}

        def counting(kind):
            def wrapper(*args, **kwargs):
                calls[kind] += 1
                return real[kind](*args, **kwargs)
            return wrapper

        with monkeypatch.context() as patch:
            patch.setattr(os.path, "islink", counting("islink"))
            patch.setattr(os, "lstat", counting("lstat"))
            patch.setattr(os, "stat", counting("stat"))
            assert scan_conflicts(root) == []
        return calls

    small = measure("small", 5)
    large = measure("large", 150)

    assert small["islink"] == large["islink"] == 0
    assert large == small  # constant setup cost only; nothing grows with the directory count
